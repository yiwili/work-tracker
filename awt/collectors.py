"""Read-only, version-tolerant JSONL adapters. Offsets advance only on complete lines."""
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .privacy import redact


def stable(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def utc(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc).isoformat()
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Missing timezone")
    return dt.astimezone(timezone.utc).isoformat()


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(content_text(item) for item in value)
    if isinstance(value, dict):
        return content_text(value.get("text", value.get("content", value.get("output", ""))))
    return str(value or "")


def tool_context(arguments, cwd):
    """Use explicit structured paths; never infer a repository merely from a project name."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (ValueError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    explicit = args.get('workdir') or args.get('cwd')
    affected = []
    for key in ('file_path', 'path', 'filename'):
        path = args.get(key)
        if isinstance(path, str) and Path(path).is_absolute():
            affected.append(path)
    patch = arguments if isinstance(arguments, str) else ''
    for path in re.findall(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$', patch, re.M):
        if Path(path.strip()).is_absolute():
            affected.append(path.strip())
    effective = str(explicit) if isinstance(explicit, str) and Path(explicit).is_absolute() else cwd
    return {'event_cwd': effective, 'affected_paths': affected}


@dataclass
class SourceLocation:
    path: Path
    source: str


@dataclass
class CollectionCursor:
    offset: int = 0
    file_id: str = ""
    prefix_hash: str = ""


@dataclass
class RawEvent:
    value: dict
    offset: int


@dataclass
class WorkEvent:
    id: str
    source: str
    session_id: str
    occurred_at: str
    kind: str
    summary: str
    cwd: str
    raw_ref: str
    metadata: dict = field(default_factory=dict)


class AIAdapter(Protocol):
    name: str
    def discover(self) -> list[SourceLocation]: ...
    def collect(self, location: SourceLocation, cursor: CollectionCursor): ...
    def normalize(self, event: RawEvent, location: SourceLocation) -> list[WorkEvent]: ...


class JSONLAdapter:
    name = "unknown"

    def __init__(self, paths, patterns=()):
        self.paths = paths
        self.patterns = patterns
        self.session_id = ""
        self.cwd = ""
        self.calls = {}
        self.call_contexts = {}
        self.warnings = []
        self.start_offset = 0

    def discover(self):
        found = {}
        for configured in self.paths:
            path = Path(configured).expanduser()
            if path.is_file() and path.suffix == ".jsonl":
                found[str(path.resolve())] = path
            elif path.is_dir():
                for item in path.rglob("*.jsonl"):
                    found[str(item.resolve())] = item
            else:
                self.warnings.append(f"路径不存在：{path}")
        return [SourceLocation(path, self.name) for path in found.values()]

    def collect(self, location, cursor):
        # Replay metadata before the offset to restore cwd/session/call context; do not yield old events.
        self.session_id = location.path.stem
        self.cwd = ""
        self.calls = {}
        self.call_contexts = {}
        with location.path.open("rb") as stream:
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    break
                cursor.offset = stream.tell()
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("not an object")
                    raw = RawEvent(value, offset)
                    normalized = self.normalize(raw, location)
                    if offset >= self.start_offset:
                        yield from normalized
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    self.warnings.append(f"{location.path.name}:{offset} 无效或不支持的记录已跳过")

    def make(self, raw, location, kind, text, timestamp, meta=None):
        if not text.strip() or not timestamp:
            return []
        occurred_at = utc(timestamp)
        safe = redact(text, self.patterns)[:12000]
        # File path is intentionally absent: copies of a session share stable event IDs.
        identity = stable(self.name, self.session_id, occurred_at, kind, text)
        meta = dict(meta or {})
        event_cwd = meta.pop('event_cwd', self.cwd)
        return [WorkEvent(identity, self.name, self.session_id, occurred_at, kind, safe,
                          event_cwd, f"{location.path}:{raw.offset}", meta)]


class CodexAdapter(JSONLAdapter):
    name = "codex"

    def normalize(self, raw, location):
        row = raw.value
        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            return []
        kind = row.get("type")
        if kind == "session_meta":
            self.session_id = str(payload.get("id", self.session_id))
            self.cwd = str(payload.get("cwd", self.cwd))
            return []
        if kind == "turn_context":
            self.cwd = str(payload.get("cwd", self.cwd))
            return []
        ts = row.get("timestamp")
        if kind == "response_item":
            subtype = payload.get("type")
            if subtype == "message":
                role = payload.get("role")
                if role not in ("user", "assistant"):
                    return []
                text = content_text(payload.get("content"))
                if text.lstrip().startswith(("# AGENTS.md", "<environment_context>", "<permissions instructions>")):
                    return []
                return self.make(raw, location, "user_prompt" if role == "user" else "assistant_message", text, ts)
            if subtype in ("function_call", "custom_tool_call"):
                name = payload.get("name", "tool")
                args = payload.get("arguments", payload.get("input", ""))
                self.calls[payload.get("call_id")] = name
                context = tool_context(args, self.cwd)
                self.call_contexts[payload.get('call_id')] = context
                return self.make(raw, location, "shell_command" if "exec_command" in name else "tool_call", f"{name}\n{args}", ts, {"tool": name, **context})
            if subtype in ("function_call_output", "custom_tool_call_output"):
                name = self.calls.get(payload.get("call_id"), "tool")
                return self.make(raw, location, "tool_result", content_text(payload.get("output")), ts, {"tool": name, **self.call_contexts.get(payload.get('call_id'), {})})
        if kind == "event_msg" and payload.get("type") in ("task_complete", "turn_aborted"):
            return self.make(raw, location, "session_end", payload.get("type"), ts)
        return []


class ClaudeAdapter(JSONLAdapter):
    name = "claude_code"

    def normalize(self, raw, location):
        row = raw.value
        self.session_id = str(row.get("sessionId", self.session_id))
        self.cwd = str(row.get("cwd", self.cwd))
        message = row.get("message", {})
        if not isinstance(message, dict) or row.get("type") not in ("user", "assistant"):
            return []
        content = message.get("content", [])
        content = [{"type": "text", "text": content}] if isinstance(content, str) else content
        result = []
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind == "text":
                result += self.make(raw, location, "user_prompt" if row["type"] == "user" else "assistant_message", part.get("text", ""), row.get("timestamp"))
            elif kind == "tool_use":
                name = part.get("name", "tool")
                self.calls[part.get("id")] = name
                args = part.get("input", {})
                context = tool_context(args, self.cwd)
                self.call_contexts[part.get('id')] = context
                result += self.make(raw, location, "shell_command" if name == "Bash" else "tool_call", name + "\n" + json.dumps(args, ensure_ascii=False), row.get("timestamp"), {"tool": name, **context})
            elif kind == "tool_result":
                result += self.make(raw, location, "tool_result", content_text(part.get("content")), row.get("timestamp"), {"tool": self.calls.get(part.get("tool_use_id"), "tool"), "is_error": part.get("is_error", False), **self.call_contexts.get(part.get('tool_use_id'), {})})
        return result
