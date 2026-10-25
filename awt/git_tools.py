import subprocess
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .collectors import stable, utc
from .privacy import redact


def git(path, *args):
    try:
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=15,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def excluded(path, settings):
    if not path:
        return False
    absolute = str(Path(path).expanduser().resolve()).casefold().replace("\\", "/").rstrip("/")
    for item in settings.excluded_paths:
        prefix = str(Path(item).expanduser().resolve()).casefold().replace("\\", "/").rstrip("/")
        if absolute == prefix or absolute.startswith(prefix + "/"):
            return True
    return False


def resolve_project(cwd, settings):
    if not cwd:
        return "未归属项目", None
    path = Path(cwd).expanduser()
    mappings = sorted(settings.project_mappings.items(), key=lambda item: len(item[0]), reverse=True)
    project = None
    for prefix, name in mappings:
        try:
            if path.resolve().is_relative_to(Path(prefix).expanduser().resolve()):
                project = name
                break
        except (OSError, ValueError):
            pass
    root = git(path if path.is_dir() else path.parent, "rev-parse", "--show-toplevel")
    if not root:
        return project or path.name or "未归属项目", None
    root = str(Path(root).resolve())
    repo = {"id": stable(root.casefold()), "root": root, "project": project or Path(root).name,
            "remote": redact(git(root, "remote", "get-url", "origin"), settings.redact_patterns),
            "branch": git(root, "branch", "--show-current"),
            "current_changes": redact(git(root, "status", "--short"), settings.redact_patterns),
            "observed_at": datetime.now(timezone.utc).isoformat()}
    return repo["project"], repo


def day_bounds(day, tz):
    start = datetime.combine(day, time.min, ZoneInfo(tz))
    end = datetime.combine(day + timedelta(days=1), time.min, ZoneInfo(tz))
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def collect_commits(repo, day, settings):
    start, end = day_bounds(day, settings.timezone)
    # Git's commit-date filter uses committer time. Store both author and committer timestamps.
    raw = git(repo["root"], "log", "--all", f"--since={start}", f"--until={end}", "--format=%H%x1f%aI%x1f%cI%x1f%s")
    rows = []
    for line in raw.splitlines():
        fields = line.split("\x1f", 3)
        if len(fields) != 4:
            continue
        sha, authored, committed, subject = fields
        committed = utc(committed)
        if not start <= committed < end:
            continue
        rows.append({"id": stable(repo["id"], sha), "repo_id": repo["id"], "sha": sha,
                     "authored_at": utc(authored), "committed_at": committed,
                     "subject": redact(subject, settings.redact_patterns),
                     "changes": redact(git(repo["root"], "show", "--format=", "--numstat", sha), settings.redact_patterns)})
    return rows
