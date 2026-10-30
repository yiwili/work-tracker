"""Read-only, version-tolerant JSONL adapters. Offsets advance only on complete lines."""
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from .privacy import redact

try:
    import zstandard
except ImportError:  # 缺少依赖时其他来源仍可用，只跳过压缩会话
    zstandard = None


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

    def __init__(self, paths, patterns=(), timezone_name="Asia/Shanghai"):
        self.paths = paths
        self.patterns = patterns
        self.timezone_name = timezone_name
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


# WorkBuddy 会在用户消息前面塞一大段 <system-reminder>，里面是工作区、时间和身份注入。
# 那是工具自己写的上下文，不是用户说的话；留着会把整段对话淹掉，必须先摘掉。
SYSTEM_REMINDER = re.compile(r"<system-reminder\b[^>]*>.*?</system-reminder>", re.S)


class WorkBuddyAdapter(JSONLAdapter):
    """WorkBuddy 会话记录（~/.workbuddy-ai/projects/<项目>/<session>.jsonl）。

    每行一个对象，靠 type 区分：message / function_call / function_call_result /
    reasoning / file-history-snapshot / ai-title / session-meta。
    只取对话与工具调用：reasoning 是模型思考，不能当成工作证据。
    """

    name = "workbuddy"

    def normalize(self, raw, location):
        row = raw.value
        self.session_id = str(row.get("sessionId") or self.session_id)
        self.cwd = str(row.get("cwd") or self.cwd)
        kind = row.get("type")
        ts = row.get("timestamp")

        if kind == "message":
            role = row.get("role")
            if role not in ("user", "assistant"):
                return []
            text = content_text(row.get("content"))
            if role == "user":
                text = SYSTEM_REMINDER.sub("", text).strip()
                if not text:
                    return []
                return self.make(raw, location, "user_prompt", text, ts)
            return self.make(raw, location, "assistant_message", text, ts)

        if kind == "function_call":
            name = row.get("name", "tool")
            args = row.get("arguments", "")
            call_id = row.get("callId")
            context = tool_context(args, self.cwd)
            self.calls[call_id] = name
            self.call_contexts[call_id] = context
            rendered = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            return self.make(raw, location, "shell_command" if name in ("Bash", "Shell") else "tool_call",
                             name + "\n" + rendered, ts, {"tool": name, **context})

        if kind == "function_call_result":
            call_id = row.get("callId")
            name = self.calls.get(call_id) or row.get("name") or "tool"
            status = row.get("status")
            return self.make(raw, location, "tool_result", content_text(row.get("output")), ts,
                             {"tool": name, "is_error": bool(status) and status != "completed",
                              **self.call_contexts.get(call_id, {})})
        return []


class QoderAdapter(ClaudeAdapter):
    """Qoder（国内外版）会话记录：~/.qoder[-cn]/projects/<cwd>/<session>.jsonl。

    行格式与 Claude Code 基本一致：user/assistant + message.content 的
    text / tool_use / tool_result 块；attachment、active-leaf 等行直接忽略。
    """

    name = "qoder"


class ZstdJSONLAdapter(JSONLAdapter):
    """以 .jsonl.zstd 追加写入的会话日志，每个压缩帧内是若干行 JSON。"""

    suffix = ".jsonl.zstd"

    def discover(self):
        found = {}
        for configured in self.paths:
            path = Path(configured).expanduser()
            if path.is_file() and path.name.endswith(self.suffix):
                found[str(path.resolve())] = path
            elif path.is_dir():
                for item in path.rglob("*" + self.suffix):
                    found[str(item.resolve())] = item
            else:
                self.warnings.append(f"路径不存在：{path}")
        return [SourceLocation(path, self.name) for path in found.values()]

    def collect(self, location, cursor):
        self.session_id = location.path.parent.name
        self.cwd = ""
        self.calls = {}
        self.call_contexts = {}
        if zstandard is None:
            self.warnings.append("读取压缩会话需要安装 zstandard 依赖")
            return
        try:
            data = location.path.read_bytes()
        except OSError:
            self.warnings.append(f"{location.path.name}: 无法读取")
            return
        remaining = data
        while remaining:
            decoder = zstandard.ZstdDecompressor().decompressobj()
            try:
                payload = decoder.decompress(remaining)
            except zstandard.ZstdError:
                break
            consumed = len(remaining) - len(decoder.unused_data)
            # 只推进到最后一个完整帧；未写完的帧等下次采集再读，避免丢记录。
            if not decoder.eof or consumed <= 0:
                break
            frame_offset = len(data) - len(remaining)
            for line in payload.splitlines():
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("not an object")
                    raw = RawEvent(value, frame_offset)
                    normalized = self.normalize(raw, location)
                    if frame_offset >= self.start_offset:
                        yield from normalized
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeDecodeError):
                    self.warnings.append(f"{location.path.name}:{frame_offset} 无效或不支持的记录已跳过")
            cursor.offset = frame_offset + consumed
            remaining = decoder.unused_data


SHELL_TOOLS = {"bash", "shell", "exec_command", "run_command", "shell_command"}


class DeepSeekHarnessAdapter(ZstdJSONLAdapter):
    """DeepSeek Harness 会话（~/.dsh/sessions/<cwd>/session-*/session*.jsonl.zstd）。

    只取 user/message、assistant/message 的正文、tool/call 与 tool/result；
    assistant/chunk、text-chunks、reasoning-chunks 是流式重复片段，忽略。
    """

    name = "deepseek_harness"

    def normalize(self, raw, location):
        row = raw.value
        kind = row.get("type")
        data = row.get("data") or {}
        if not isinstance(data, dict):
            return []
        ts = row.get("time")

        if kind == "session":
            self.session_id = str(data.get("id") or self.session_id)
            self.cwd = str(data.get("cwd") or self.cwd)
            return []

        if kind == "user/message":
            if (data.get("source") or {}).get("kind") not in (None, "user", "human"):
                return []
            return self.make(raw, location, "user_prompt", content_text(data.get("content")), ts)

        if kind == "assistant/message":
            message = data.get("message") or {}
            result = []
            for part in message.get("content") or []:
                # reasoning 是模型思考，不是工作证据；工具调用由 tool/call 记录。
                if isinstance(part, dict) and part.get("type") == "text":
                    result += self.make(raw, location, "assistant_message", part.get("text", ""), ts)
            return result

        if kind == "tool/call":
            name = data.get("name", "tool")
            call_id = data.get("callId")
            args = data.get("arguments", "")
            context = tool_context(args, self.cwd)
            self.calls[call_id] = name
            self.call_contexts[call_id] = context
            rendered = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            return self.make(raw, location, "shell_command" if str(name).lower() in SHELL_TOOLS else "tool_call",
                             name + "\n" + rendered, ts, {"tool": name, **context})

        if kind == "tool/result":
            message = data.get("message") or data
            call_id = data.get("toolCallId") or message.get("toolCallId") or (message.get("source") or {}).get("callId")
            name = self.calls.get(call_id) or "tool"
            is_error = bool(data.get("isError") or message.get("isError"))
            return self.make(raw, location, "tool_result", content_text(message.get("content")), ts,
                             {"tool": name, "is_error": is_error, **self.call_contexts.get(call_id, {})})

        return []


TRAE_TIME = "%Y-%m-%d %H:%M:%S"


class TraeAdapter(JSONLAdapter):
    """Trae（国内外版）会话摘要：~/.trae[-cn]/memory/projects/<项目>/<日期>/session_memory_*.jsonl。

    Trae 的完整会话库是加密 SQLite，本机唯一可读的是它生成的会话摘要，
    因此这里把 intent 作为用户请求、actions/outcome/learned 作为 AI 归纳证据。
    """

    name = "trae"

    def __init__(self, paths, patterns=(), timezone_name="Asia/Shanghai"):
        super().__init__(paths, patterns, timezone_name)
        self.cwd_cache = {}

    def discover(self):
        found = {}
        for configured in self.paths:
            path = Path(configured).expanduser()
            if path.is_file() and path.name.startswith("session_memory_"):
                found[str(path.resolve())] = path
            elif path.is_dir():
                for item in path.rglob("session_memory_*.jsonl"):
                    found[str(item.resolve())] = item
            else:
                self.warnings.append(f"路径不存在：{path}")
        return [SourceLocation(path, self.name) for path in found.values()]

    def _decode_cwd(self, folder):
        if folder in self.cwd_cache:
            return self.cwd_cache[folder]
        key = folder.split("--p2-", 1)[0]
        match = re.fullmatch(r"-([a-zA-Z])-(.+)", key)
        result = ""
        if match:
            rest = match.group(2).strip("-")

            def walk(base, remaining):
                if not remaining:
                    return base if base.is_dir() else None
                try:
                    children = list(base.iterdir())
                except OSError:
                    return None
                for child in children:
                    if not child.is_dir():
                        continue
                    encoded = re.sub(r"[^a-z0-9]+", "-", child.name.lower()).strip("-")
                    if encoded and remaining == encoded:
                        return child
                    if encoded and remaining.startswith(encoded + "-"):
                        found = walk(child, remaining[len(encoded) + 1:])
                        if found:
                            return found
                return None

            found = walk(Path(match.group(1).upper() + ":\\"), rest)
            result = str(found) if found else ""
        self.cwd_cache[folder] = result
        return result

    def normalize(self, raw, location):
        row = raw.value
        if not isinstance(row.get("intent"), str) and "outcome" not in row:
            return []
        name = location.path.stem
        self.session_id = name.removeprefix("session_memory_") or name
        self.cwd = self._decode_cwd(location.path.parent.parent.name)
        stamp = row.get("message_summary_time")
        try:
            occurred_at = datetime.strptime(stamp, TRAE_TIME).replace(tzinfo=ZoneInfo(self.timezone_name)).isoformat()
        except (TypeError, ValueError):
            occurred_at = datetime.fromtimestamp(location.path.stat().st_mtime, timezone.utc).isoformat()

        meta = {"derived": "trae_session_memory", "event_cwd": self.cwd}
        result = []
        intent = str(row.get("intent") or "").strip()
        if intent:
            result += self.make(raw, location, "user_prompt", intent, occurred_at, meta)
        details = []
        actions = row.get("actions") or []
        if actions:
            details.append("操作：" + "；".join(str(item) for item in actions))
        if row.get("outcome"):
            details.append("结果：" + str(row.get("outcome")))
        learned = row.get("learned") or []
        if learned:
            details.append("要点：" + "；".join(str(item) for item in learned))
        if details:
            result += self.make(raw, location, "assistant_message", "\n".join(details), occurred_at, meta)
        return result
