import subprocess
import os
import re
from collections import Counter
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


def _canonical(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def exclusion_prefixes(settings):
    return tuple(_canonical(item).replace("\\", "/").rstrip("/") for item in settings.excluded_paths)


def excluded(path, settings, prefixes=None):
    if not path:
        return False
    absolute = _canonical(path).replace("\\", "/").rstrip("/")
    for prefix in prefixes if prefixes is not None else exclusion_prefixes(settings):
        if absolute == prefix or absolute.startswith(prefix + "/"):
            return True
    return False


def resolve_project(cwd, settings, cache=None):
    if not cwd:
        return "未归属项目", None
    path = Path(cwd).expanduser()
    base = path if path.is_dir() else path.parent
    try:
        dir_key = os.path.normcase(str(base.resolve()))
    except OSError:
        dir_key = os.path.normcase(str(base))
    cached = cache.get(("dir", dir_key)) if cache is not None else None
    if cached is not None:
        project, root = cached
    else:
        mappings = sorted(settings.project_mappings.items(), key=lambda item: len(item[0]), reverse=True)
        project = None
        for prefix, name in mappings:
            try:
                if path.resolve().is_relative_to(Path(prefix).expanduser().resolve()):
                    project = name
                    break
            except (OSError, ValueError):
                pass
        root = git(base, "rev-parse", "--show-toplevel")
        root = str(Path(root).resolve()) if root else ""
        if cache is not None:
            cache[("dir", dir_key)] = (project, root)
    if not root:
        return project or path.name or "未归属项目", None
    if cache is not None and ("repo", root) in cache:
        return cache[("repo", root)]
    repo = {"id": stable(root.casefold()), "root": root, "project": project or Path(root).name,
            "remote": redact(git(root, "remote", "get-url", "origin"), settings.redact_patterns),
            "branch": git(root, "branch", "--show-current"),
            "current_changes": redact(git(root, "status", "--short"), settings.redact_patterns),
            "observed_at": datetime.now(timezone.utc).isoformat()}
    result = (repo["project"], repo)
    if cache is not None:
        cache[("repo", root)] = result
    return result


def day_bounds(day, tz):
    start = datetime.combine(day, time.min, ZoneInfo(tz))
    end = datetime.combine(day + timedelta(days=1), time.min, ZoneInfo(tz))
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def root_key(root):
    return os.path.normcase(str(Path(root).expanduser().resolve()))


def configured_author(root):
    local={key:git(root,'config','--local','--get','user.'+key) for key in ('name','email')}
    value={key:local[key] or git(root,'config','--get','user.'+key) for key in ('name','email')}
    return {**value,'source':'local' if any(local.values()) else 'inherited' if any(value.values()) else 'unavailable'}


def author_override_key(root, settings):
    key=root_key(root)
    return next((path for path in settings.git_author_overrides if root_key(path)==key),None)


def selected_author(root, settings):
    key=author_override_key(root,settings)
    override=settings.git_author_overrides.get(key)
    if override:return {**override.model_dump(),'source':'override'}
    if settings.git_author:return {**settings.git_author.model_dump(),'source':'custom'}
    return configured_author(root)


def author_matches(author, name, email):
    # Email is the stable identity when configured. Never use Git's regex or
    # substring author filter: similar names/emails can belong to coworkers.
    if author.get('email'):return author['email'].strip().casefold()==email.strip().casefold()
    return bool(author.get('name')) and author['name'].strip().casefold()==name.strip().casefold()


def commit_author(root, sha):
    if not re.fullmatch(r'[0-9a-fA-F]{40,64}',sha or ''):return None
    fields=git(root,'show','-s','--format=%an%x1f%ae',sha).split('\x1f')
    return {'name':fields[0],'email':fields[1]} if len(fields)==2 else None


def filter_git_events(events, settings):
    identities={};authors={};kept=[]
    for item in events:
        if item['source']!='git':kept.append(item);continue
        root=item['cwd']
        if not root or excluded(root,settings):continue
        if root not in identities:identities[root]=selected_author(root,settings)
        identity=identities[root];meta=item.get('metadata') or {}
        if not(identity.get('name') or identity.get('email')):continue
        if 'author_email' in meta or 'author_name' in meta:
            author={'name':meta.get('author_name',''),'email':meta.get('author_email','')}
        else:
            key=(root,meta.get('sha',''))
            if key not in authors:authors[key]=commit_author(*key)
            author=authors[key]
        if author and author_matches(identity,author['name'],author['email']):kept.append(item)
    return kept


def identity_options(roots, settings):
    result=[];seen=set();paths=set()
    for root in roots:
        if not root.strip() or excluded(root,settings):continue
        key=root_key(root)
        if key in paths:continue
        paths.add(key)
        _,repo=resolve_project(root,settings)
        if not repo or repo['id'] in seen:continue
        seen.add(repo['id'])
        raw=git(repo['root'],'log','--all','-n','1000','--format=%an%x1f%ae')
        counts=Counter(tuple(line.split('\x1f')) for line in raw.splitlines() if len(line.split('\x1f'))==2)
        authors=[{'name':name,'email':email,'commits':count} for (name,email),count in counts.most_common(100)]
        result.append({'root':repo['root'],'project':repo['project'],'override_key':author_override_key(repo['root'],settings),'detected':configured_author(repo['root']),
                       'selected':selected_author(repo['root'],settings),'authors':authors})
    return result


def collect_commits(repo, day, settings, warnings=None):
    start, end = day_bounds(day, settings.timezone)
    author=selected_author(repo['root'],settings)
    if not(author.get('name') or author.get('email')):
        if warnings is not None:warnings.append(f"{repo['project']}：无法获取Git用户，已跳过提交采集；请在设置中选择或自定义用户")
        return []
    # Git's commit-date filter uses committer time. Store both author and committer timestamps.
    raw = git(repo["root"], "log", "--all", f"--since={start}", f"--until={end}", "--format=%H%x1f%aI%x1f%cI%x1f%an%x1f%ae%x1f%s")
    rows = []
    total=0
    for line in raw.splitlines():
        fields = line.split("\x1f", 5)
        if len(fields) != 6:
            continue
        sha, authored, committed, name, email, subject = fields
        committed = utc(committed)
        if not start <= committed < end:
            continue
        total+=1
        if not author_matches(author,name,email):continue
        rows.append({"id": stable(repo["id"], sha), "repo_id": repo["id"], "sha": sha,
                     "authored_at": utc(authored), "committed_at": committed,
                     "subject": redact(subject, settings.redact_patterns),'author_name':name,'author_email':email,
                     "changes": redact(git(repo["root"], "show", "--format=", "--numstat", sha), settings.redact_patterns)})
    if total and not rows and warnings is not None:
        warnings.append(f"{repo['project']}：当天提交未匹配所选Git用户，请检查本地配置或自定义用户；未纳入其他人的提交")
    return rows
