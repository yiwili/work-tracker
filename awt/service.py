import json
import os
import re
from dataclasses import replace
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import db
from .collectors import (ClaudeAdapter, CodexAdapter, AntigravityAdapter, GeminiAdapter,
                         DoubaoAdapter, DeepSeekHarnessAdapter, QoderCnAdapter,
                         QoderIntlAdapter, TraeCnAdapter, TraeIntlAdapter, WorkBuddyCnAdapter,
                         WorkBuddyIntlAdapter, CollectionCursor, stable)
from .git_tools import collect_commits, day_bounds, exclusion_prefixes, excluded, resolve_project, filter_git_events
from .models import Settings, TaskInput
from .privacy import redact


def now():
    return datetime.now(timezone.utc).isoformat()


def upsert(conn, table, values, key):
    stmt = sqlite_insert(table).values(**values)
    index_elements = list(key) if isinstance(key, (list, tuple)) else [key]
    key_set = set(key) if isinstance(key, (list, tuple)) else {key}
    conn.execute(stmt.on_conflict_do_update(
        index_elements=index_elements,
        set_={k: v for k, v in values.items() if k not in key_set}
    ))


def get_settings(conn):
    value = conn.execute(select(db.settings_table.c.value)).scalar_one_or_none()
    if value:
        return Settings.model_validate(value)
    home = Path.home()
    settings = Settings()
    settings.claude_code.paths = [str(home / ".claude" / "projects")]
    settings.codex.paths = [str(home / ".codex" / "sessions"), str(home / ".codex" / "archived_sessions")]
    return settings


def save_settings(conn, settings):
    previous = get_settings(conn)
    upsert(conn, db.settings_table, {"id": 1, "value": settings.model_dump(mode='json')}, "id")
    if previous.target_minutes != settings.target_minutes or previous.timezone != settings.timezone:
        conn.execute(update(db.reports).values(confirmed=False, confirmed_at=None, target_minutes=settings.target_minutes))
        conn.execute(update(db.worklogs).values(is_confirmed=False))
    return settings


def invalidate(conn, day):
    from . import multiday
    binding = multiday.get_active_binding(conn, day)
    target_dates = sorted(set(multiday.bound_dates(binding) + [str(day), binding['anchor_date']])) if binding else [str(day)]
    conn.execute(update(db.reports).where(db.reports.c.date.in_(target_dates)).values(confirmed=False, confirmed_at=None))
    ids = select(db.tasks.c.id).where(db.tasks.c.date.in_(target_dates))
    conn.execute(update(db.worklogs).where(db.worklogs.c.task_id.in_(ids)).values(is_confirmed=False))


def event_query(day, settings, end_day=None):
    start, end = day_bounds(day, settings.timezone, end_day=end_day)
    return select(db.events).where(and_(db.events.c.occurred_at >= start, db.events.c.occurred_at < end)).order_by(db.events.c.occurred_at, db.events.c.id)


def visible_events(conn, day, settings, end_day=None):
    return filter_git_events([dict(e) for e in conn.execute(event_query(day, settings, end_day=end_day)).mappings()], settings)


def _inside(path, root):
    value = os.path.normcase(os.path.abspath(str(path)))
    base = os.path.normcase(os.path.abspath(str(root))).rstrip("\\/")
    return value == base or value.startswith(base + os.sep)


def _nested_repo(path, root, cache):
    """工具改的是嵌套仓库里的文件时，不能直接沿用会话所属仓库。"""
    current = Path(os.path.abspath(str(path)))
    if not current.is_dir():
        current = current.parent
    base = Path(os.path.abspath(str(root)))
    key = (os.path.normcase(str(base)), os.path.normcase(str(current)))
    cached = cache.get(("nested", key))
    if cached is not None:
        return cached
    nested = False
    while current != base and base in current.parents:
        if (current / ".git").exists():
            nested = True
            break
        current = current.parent
    cache[("nested", key)] = nested
    return nested


def attribute_events(item, settings, cache=None, prefixes=None):
    paths = item.metadata.get('affected_paths', [])
    if not paths:
        return [item]
    if any(excluded(path, settings, prefixes) for path in paths):
        return []
    if cache is None:
        cache = {}
    # 绝大多数工具操作都落在会话自己的仓库内，先做一次廉价判断，避免逐条调用 git。
    _, home_repo = resolve_project(item.cwd, settings, cache) if item.cwd else (None, None)
    attributed = {}
    for path in paths:
        if home_repo and _inside(path, home_repo['root']) and not _nested_repo(path, home_repo['root'], cache):
            cwd = home_repo['root']
        else:
            _, repo = resolve_project(path, settings, cache)
            cwd = repo['root'] if repo else str(Path(path).parent)
        attributed[cwd] = replace(item, id=stable(item.id, cwd), cwd=cwd,
                                  metadata={**item.metadata, 'attributed_path': path})
    return list(attributed.values())


def collect(conn, day, progress=None, start_day=None, end_day=None):
    settings = get_settings(conn)
    s_day = start_day or day
    e_day = end_day or day
    prefixes = exclusion_prefixes(settings)
    excluded_cache = {}

    def is_excluded(path):
        if path not in excluded_cache:
            excluded_cache[path] = excluded(path, settings, prefixes)
        return excluded_cache[path]

    warnings, added, project_cache, repos = [], 0, {}, {}
    resolution_cache = {}
    health = []
    if progress:
        progress({"source": "", "files_done": 0, "files_total": 0, "new_events": 0})
    for cls, config in [(ClaudeAdapter, settings.claude_code), (CodexAdapter, settings.codex),
                        (AntigravityAdapter, settings.antigravity), (GeminiAdapter, settings.gemini),
                        (DoubaoAdapter, settings.doubao),
                        (WorkBuddyCnAdapter, settings.workbuddy_cn), (WorkBuddyIntlAdapter, settings.workbuddy_intl),
                        (DeepSeekHarnessAdapter, settings.deepseek_harness),
                        (QoderCnAdapter, settings.qoder_cn), (QoderIntlAdapter, settings.qoder_intl),
                        (TraeCnAdapter, settings.trae_cn), (TraeIntlAdapter, settings.trae_intl)]:
        adapter = cls(config.paths, settings.redact_patterns, settings.timezone)
        count, files = 0, 0
        if config.enabled:
            locations = list(adapter.discover())
            total, done = len(locations), 0
            if progress:
                progress({"source": adapter.name, "files_done": 0, "files_total": total, "new_events": added})
            for location in locations:
                if is_excluded(str(location.path)):
                    done += 1
                    continue
                files += 1
                source_key = f"{adapter.name}:{location.path.resolve()}"
                saved = conn.execute(select(db.cursors).where(db.cursors.c.source_key == source_key)).mappings().first()
                try:
                    stat = location.path.stat()
                    file_id = f"{stat.st_dev}:{stat.st_ino}"
                    with location.path.open("rb") as stream:
                        prefix = stable(stream.read(min(4096, saved['offset'] if saved else 4096)).hex())
                    valid = saved and saved['file_id'] == file_id and stat.st_size >= saved['offset'] and prefix == saved['prefix_hash']
                    if valid and stat.st_size == saved['offset']:
                        done += 1
                        if progress:
                            progress({"source": adapter.name, "files_done": done, "files_total": total, "new_events": added + count})
                        continue
                    offset = saved['offset'] if valid else 0
                    cursor = CollectionCursor(offset, file_id, prefix)
                    adapter.start_offset = offset
                    pending, session_cwds = [], {}
                    for raw in adapter.collect(location, cursor):
                        for item in attribute_events(raw, settings, resolution_cache, prefixes):
                            if is_excluded(item.cwd):
                                continue
                            if item.cwd not in project_cache:
                                project_cache[item.cwd] = resolve_project(item.cwd, settings, resolution_cache)
                            project, repo = project_cache[item.cwd]
                            if repo:
                                repos[repo['id']] = repo
                            values = vars(item).copy()
                            values.update(project=project, repo_id=repo['id'] if repo else None)
                            pending.append(values)
                            session_cwds[(item.source, item.session_id)] = item.cwd
                        if len(pending) >= 1000:
                            count += conn.execute(sqlite_insert(db.events).on_conflict_do_nothing(), pending).rowcount
                            pending.clear()
                    if pending:
                        count += conn.execute(sqlite_insert(db.events).on_conflict_do_nothing(), pending).rowcount
                    for (source, session), cwd in session_cwds.items():
                        upsert(conn, db.sessions, {"id": f"{source}:{session}", "source": source, "cwd": cwd, "last_seen_at": now()}, "id")
                    with location.path.open("rb") as stream:
                        prefix = stable(stream.read(min(4096, cursor.offset)).hex())
                    upsert(conn, db.cursors, {"source_key": source_key, "file_id": file_id, "offset": cursor.offset, "prefix_hash": prefix, "last_seen_at": now()}, "source_key")
                except (OSError, ValueError) as exc:
                    warnings.append(f"{location.path.name}: 无法读取 ({type(exc).__name__})")
                done += 1
                if progress:
                    progress({"source": adapter.name, "files_done": done, "files_total": total, "new_events": added + count})
        warnings.extend(adapter.warnings[:30])
        state = {"enabled": config.enabled, "files": files, "new_events": count, "warnings": len(adapter.warnings), "checked_at": now()}
        upsert(conn, db.sources, {"name": adapter.name, "config": config.model_dump(), "health": state}, "name")
        health.append({"source": adapter.name, **state})
        added += count
    # Include repositories found in past scans, not just newly appended sessions.
    for row in conn.execute(event_query(s_day, settings, end_day=e_day)).mappings():
        if row['cwd'] and row['cwd'] not in project_cache and not is_excluded(row['cwd']):
            project_cache[row['cwd']] = resolve_project(row['cwd'], settings, resolution_cache)
    for _, repo in project_cache.values():
        if repo:
            repos[repo['id']] = repo
    for root in settings.git_roots:
        if is_excluded(root):
            continue
        _, repo = resolve_project(root, settings, resolution_cache)
        if repo:
            repos[repo['id']] = repo
        else:
            warnings.append(f"Git 根目录无效或未初始化：{root}")
    git_added = 0
    for repo in repos.values():
        if is_excluded(repo['root']):
            continue
        upsert(conn, db.repositories, repo, "id")
        for commit in collect_commits(repo, s_day, settings, warnings, end_day=e_day):
            upsert(conn, db.commits, {k:v for k,v in commit.items() if k not in ('author_name','author_email')}, "id")
            values = {"id": stable("git", commit['id']), "source": "git", "session_id": repo['id'],
                      "occurred_at": commit['committed_at'], "kind": "git_commit", "summary": commit['subject'],
                      "cwd": repo['root'], "project": repo['project'], "repo_id": repo['id'],
                      "raw_ref": f"{repo['root']}@{commit['sha']}",
                      "metadata": {"sha": commit['sha'], "changes": commit['changes'], "authored_at": commit['authored_at'],
                                   'author_name':commit['author_name'],'author_email':commit['author_email']}}
            git_added += conn.execute(sqlite_insert(db.events).values(**values).on_conflict_do_nothing()).rowcount
            conn.execute(update(db.events).where(db.events.c.id==values['id']).values(metadata=values['metadata']))
    return {
        "new_events": added + git_added,
        "git_events": git_added,
        "sources": health,
        "warnings": warnings[:60],
        "date": str(day),
        "start_date": str(s_day),
        "end_date": str(e_day),
    }


def category(title):
    for keywords, name in [(r"修复|bug|故障|异常|报错", "Bug 修复"), (r"测试|test|回归", "测试"),
                           (r"审查|review", "代码审查"), (r"部署|上线|发布", "部署"),
                           (r"会议|开会", "会议"), (r"沟通|讨论|需求", "需求沟通"),
                           (r"文档|说明|readme", "文档"), (r"研究|调研|分析", "技术研究")]:
        if re.search(keywords, title, re.I):
            return name
    return "开发"


def title_for(text):
    text = re.sub(r"<[^>]+>", " ", text).strip()
    return next((line.strip(" #*-\t")[:100] for line in text.splitlines() if line.strip(" #*-\t")), "审核工作记录")


def state_for(group):
    # Only machine tool results with explicit runner success patterns qualify as verified.
    results = [e for e in group if e['kind'] == 'tool_result' and not e['metadata'].get('is_error')]
    verified = any(re.search(r"\b\d+ passed\b|Tests:\s+\d+ passed|BUILD SUCCESS|\bAll tests passed\b", e['summary']) for e in results)
    changed = any(re.search(r"Success\. Updated the following files|has been (?:updated|created) successfully|The file .* has been", e['summary']) for e in results)
    if verified:
        return "verified"
    if changed or any(e['kind'] == 'git_commit' for e in group):
        return "changed"
    return "attempted" if any(e['kind'] in ('tool_call', 'shell_command', 'tool_result') for e in group) else "requested"


def list_todos(conn):
    """查询跨日期的未完成待办事项（is_todo=True 且 status='requested' 且未删除），按日期倒序。"""
    rows = conn.execute(
        select(
            db.tasks,
            db.worklogs.c.suggested_minutes,
            db.worklogs.c.confirmed_minutes,
            db.worklogs.c.time_locked,
            db.worklogs.c.time_source,
            db.worklogs.c.time_reason,
        )
        .join(db.worklogs)
        .where(and_(db.tasks.c.status == "requested", db.tasks.c.deleted == False, db.tasks.c.is_todo == True))
        .order_by(db.tasks.c.date.desc(), db.tasks.c.id.desc())
    ).mappings().all()
    return [dict(r) for r in rows]


def list_tasks_in_range(conn, start_day, end_day):
    s_str, e_str = str(start_day), str(end_day)
    if e_str < s_str:
        s_str, e_str = e_str, s_str
    cond = (
        and_(db.tasks.c.date == s_str, db.tasks.c.deleted == False)
        if s_str == e_str
        else and_(db.tasks.c.date >= s_str, db.tasks.c.date <= e_str, db.tasks.c.deleted == False)
    )
    order_cols = (db.tasks.c.id,) if s_str == e_str else (db.tasks.c.date, db.tasks.c.id)
    rows = conn.execute(select(db.tasks, db.worklogs.c.suggested_minutes, db.worklogs.c.confirmed_minutes, db.worklogs.c.time_locked, db.worklogs.c.time_source, db.worklogs.c.time_reason)
                        .join(db.worklogs).where(cond)
                        .order_by(*order_cols)).mappings().all()
    output = []
    gap = get_settings(conn).activity_gap_minutes
    from .activity_time import profile
    for row in rows:
        task = dict(row)
        task['evidence'] = [dict(e) for e in conn.execute(select(db.events).join(db.evidence, db.evidence.c.event_id == db.events.c.id)
                           .where(db.evidence.c.task_id == row['id']).order_by(db.events.c.occurred_at)).mappings()]
        task['timing'] = profile(task['evidence'], gap_minutes=gap)
        output.append(task)
    return output


def list_tasks(conn, day):
    from . import multiday
    binding = multiday.get_active_binding(conn, day)
    if binding:
        return list_tasks_in_range(conn, binding['start_date'], binding['end_date'])
    return list_tasks_in_range(conn, day, day)


def analyze(conn, day, start_day=None, end_day=None, target_minutes=None):
    from .daily_analysis import analyze as analyze_daily
    return analyze_daily(conn, day, start_day=start_day, end_day=end_day, target_minutes=target_minutes)


def merge_suggestions(tasks):
    suggestions = []
    for i, a in enumerate(tasks):
        for j, b in enumerate(tasks[i+1:], start=i+1):
            ratio = SequenceMatcher(None, a['title'], b['title']).ratio()
            if a['project'] == b['project'] and ratio >= .72:
                ev_dates = sorted({
                    str(e.get('occurred_at') or '')[:10]
                    for t in (a, b)
                    for e in t.get('evidence', [])
                    if e.get('occurred_at')
                })
                task_date = str(a.get('date') or '')
                proj = a.get('project') or '未归属项目'
                pct = int(round(ratio * 100))
                date_label = f"{task_date}（证据日期 {'/'.join(ev_dates)}）" if ev_dates and ev_dates != [task_date] else task_date
                reason = (
                    f"【{date_label} · 今日工作清单 · {proj}】"
                    f"第 #{i + 1} 条「{a['title']}」与第 #{j + 1} 条「{b['title']}」"
                    f"项目和工作目标高度相似（{pct}%），请核对是否重复；建议工时不可直接叠加。"
                )
                suggestions.append({
                    "ids": [a['id'], b['id']],
                    "indices": [i + 1, j + 1],
                    "titles": [a['title'], b['title']],
                    "project": proj,
                    "date": task_date,
                    "evidence_dates": ev_dates,
                    "location": "今日工作清单",
                    "kind": "similarity",
                    "similarity": round(ratio, 2),
                    "reason": reason,
                })
    return suggestions[:50]


def get_task(conn, task_id):
    row = conn.execute(select(db.tasks).where(and_(db.tasks.c.id == task_id, db.tasks.c.deleted == False))).mappings().first()
    if not row:
        raise LookupError("任务不存在")
    return dict(row)


def save_task(conn, payload: TaskInput, task_id=None):
    existing = get_task(conn, task_id) if task_id else None
    settings = get_settings(conn)
    values = payload.model_dump(mode='json')
    minutes = values.pop('confirmed_minutes')
    suggested = values.pop('suggested_minutes', None)
    has_suggested = 'suggested_minutes' in payload.model_fields_set or suggested is not None
    locked = values.pop('time_locked')
    locked = bool(minutes) if locked is None else locked
    for key in ('title', 'details', 'project', 'category'):
        values[key] = redact(values[key], settings.redact_patterns)
    title_changed = (
        not existing
        or str(payload.title or "").strip() != str(existing.get("title") or "").strip()
        or str(values.get("title") or "").strip() != str(existing.get("title") or "").strip()
    )
    if existing:
        conn.execute(update(db.tasks).where(db.tasks.c.id == task_id).values(**values, edited=True))
        old_log=conn.execute(select(db.worklogs).where(db.worklogs.c.task_id==task_id)).mappings().one()
        source='manual' if minutes!=old_log['confirmed_minutes'] or locked else old_log['time_source']
        log_updates = {'confirmed_minutes': minutes, 'time_locked': locked, 'time_source': source, 'time_reason': '' if source=='manual' else old_log['time_reason'], 'is_confirmed': False}
        if has_suggested:
            log_updates['suggested_minutes'] = suggested
        conn.execute(update(db.worklogs).where(db.worklogs.c.task_id == task_id).values(**log_updates))
        invalidate(conn, existing['date'])
    else:
        task_id = uuid4().hex
        conn.execute(insert(db.tasks).values(id=task_id, **values, origin='manual', edited=True, deleted=False,
                                            confidence='人工录入', evidence_level='人工说明'))
        conn.execute(insert(db.worklogs).values(task_id=task_id, confirmed_minutes=minutes, suggested_minutes=suggested, time_locked=locked, time_source="manual", is_confirmed=False))
    if title_changed:
        from . import similarity
        similarity.mark_titles_modified(conn, values['date'])
    invalidate(conn, values['date'])
    return task_id


def tasks_from_events(conn, day, event_ids, start_day=None, end_day=None):
    settings = get_settings(conn)
    ids = list(dict.fromkeys(event_ids))
    s_day = start_day or day
    e_day = end_day or day
    bounds_start, bounds_end = day_bounds(s_day, settings.timezone, end_day=e_day)
    rows = {row['id']: dict(row) for row in conn.execute(
        select(db.events).where(and_(db.events.c.id.in_(ids), db.events.c.occurred_at >= bounds_start,
                                     db.events.c.occurred_at < bounds_end))).mappings()}
    if set(rows) != set(ids):
        raise ValueError('部分采集记录不属于当天或已不存在，请刷新采集详情')
    linked = set(conn.execute(select(db.evidence.c.event_id).where(db.evidence.c.event_id.in_(ids))).scalars())
    created = []
    for event_id in ids:
        if event_id in linked:
            continue
        event = rows[event_id]
        if event['source'] != 'git' or event['kind'] != 'git_commit':
            raise ValueError('只有 Git 提交可以直接添加为工作项')
        changes = str(event['metadata'].get('changes') or '')
        title = redact((event['summary'] or '').strip()[:500], settings.redact_patterns) or 'Git 提交'
        task_id = uuid4().hex
        conn.execute(insert(db.tasks).values(
            id=task_id, date=str(day), title=title, details=changes, project=event['project'] or '未归属项目',
            category=category(title), status='changed', origin='auto', confidence='Git 提交 · 人工选择',
            evidence_level='Git 提交', fingerprint='git-adopt:' + event_id, edited=True, deleted=False, is_todo=False))
        conn.execute(insert(db.worklogs).values(task_id=task_id, suggested_minutes=None, confirmed_minutes=0,
                                                is_confirmed=False, time_locked=False, time_source='manual', time_reason=''))
        conn.execute(insert(db.evidence).values(task_id=task_id, event_id=event_id))
        created.append(task_id)
    if created:
        from . import similarity
        similarity.mark_titles_modified(conn, day)
        invalidate(conn, day)
    return {'created': len(created), 'skipped': len(ids) - len(created), 'task_ids': created}


def delete_task(conn, task_id):
    task = get_task(conn, task_id)
    conn.execute(update(db.tasks).where(db.tasks.c.id == task_id).values(deleted=True, edited=True))
    invalidate(conn, task['date'])


def protected_gitee_task_ids(conn, task_ids):
    if not task_ids:
        return set()
    rows = (
        conn.execute(
            select(db.gitee_links.c.task_id, db.gitee_links.c.status, db.gitee_links.c.issue_id)
            .where(db.gitee_links.c.task_id.in_(task_ids))
        )
        .mappings()
        .all()
    )
    protected = set()
    for row in rows:
        status = str(row.get('status') or '')
        issue_id = str(row.get('issue_id') or '').strip()
        if (status == 'created' and bool(issue_id)) or status in ('pending', 'uncertain', 'creating'):
            protected.add(row['task_id'])
    return protected


def is_clearable_ai_task(task, protected_gitee_ids=None):
    if task.get('origin') != 'auto':
        return False
    fp = str(task.get('fingerprint') or '')
    if fp.startswith('git-adopt:'):
        return False
    if protected_gitee_ids and task.get('id') in protected_gitee_ids:
        return False
    return True


def clear_ai_tasks(conn, day):
    """Soft-delete clearable AI tasks & pending candidates and exclude their conversation events."""
    from . import exclusions, multiday

    binding = multiday.get_active_binding(conn, day)
    canonical_day = binding['anchor_date'] if binding else str(day)
    analysis_days = (
        sorted(set(multiday.bound_dates(binding) + [str(day), canonical_day]))
        if binding
        else [canonical_day]
    )

    active_tasks = list_tasks(conn, day)
    protected_gitee = protected_gitee_task_ids(conn, [t['id'] for t in active_tasks])
    to_clear = [t for t in active_tasks if is_clearable_ai_task(t, protected_gitee)]
    to_retain = [t for t in active_tasks if not is_clearable_ai_task(t, protected_gitee)]
    retained_protected_ai = sum(
        1 for t in to_retain if t.get('origin') == 'auto' and not is_clearable_ai_task(t, protected_gitee)
    )

    analysis_rows_db = (
        conn.execute(
            select(db.analyses.c.date, db.analyses.c.snapshot).where(db.analyses.c.date.in_(analysis_days))
        )
        .mappings()
        .all()
    )
    pending_event_ids = set()
    cleared_pending = 0
    days_with_pending = []
    for a_row in analysis_rows_db:
        snap = a_row.get('snapshot')
        if not isinstance(snap, dict):
            continue
        p_tasks = snap.get('pending_tasks') or []
        if p_tasks:
            cleared_pending += len(p_tasks)
            days_with_pending.append(str(a_row['date']))
            for item in p_tasks:
                for eid in (item.get('evidence_ids') or []):
                    if eid:
                        pending_event_ids.add(str(eid))

    if not to_clear and cleared_pending == 0:
        return {
            'cleared_tasks': 0,
            'cleared_pending': 0,
            'excluded_events': 0,
            'newly_excluded_events': 0,
            'retained_protected_ai': retained_protected_ai,
            'date': canonical_day,
        }

    clear_ids = [t['id'] for t in to_clear]
    for start in range(0, len(clear_ids), 500):
        chunk = clear_ids[start:start + 500]
        if chunk:
            # Mark edited=False so bulk-cleared AI tasks never trigger the "previously deleted" recovery prompt.
            conn.execute(
                update(db.tasks)
                .where(db.tasks.c.id.in_(chunk))
                .values(deleted=True, edited=False)
            )

    days_to_clear_analysis = sorted(set([canonical_day, *days_with_pending]))
    conn.execute(delete(db.analyses).where(db.analyses.c.date.in_(days_to_clear_analysis)))
    if not to_retain:
        conn.execute(delete(db.allocations).where(db.allocations.c.date.in_(analysis_days)))

    clear_event_ids = {
        str(e['id'])
        for t in to_clear
        for e in (t.get('evidence') or [])
        if e.get('id')
    }
    clear_event_ids.update(pending_event_ids)
    retained_event_ids = {
        str(e['id'])
        for t in to_retain
        for e in (t.get('evidence') or [])
        if e.get('id')
    }
    exclude_target_ids = sorted(clear_event_ids - retained_event_ids)

    events_to_exclude = []
    for start in range(0, len(exclude_target_ids), 500):
        chunk = exclude_target_ids[start:start + 500]
        if chunk:
            events_to_exclude.extend(
                dict(r)
                for r in conn.execute(select(db.events).where(db.events.c.id.in_(chunk))).mappings().all()
            )

    newly_excluded = exclusions.exclude_events(
        conn,
        events_to_exclude,
        reason='cleared_ai_task',
        note=f'清空全部 AI 项 ({canonical_day})',
    )
    invalidate(conn, canonical_day)
    return {
        'cleared_tasks': len(to_clear),
        'cleared_pending': cleared_pending,
        'excluded_events': len(events_to_exclude),
        'newly_excluded_events': newly_excluded,
        'retained_protected_ai': retained_protected_ai,
        'date': canonical_day,
    }


def merge_tasks(conn, ids):
    from . import multiday
    if len(set(ids)) != len(ids):
        raise ValueError("请选择不同任务")
    selected = [get_task(conn, task_id) for task_id in ids]
    binding = None
    if len({item['date'] for item in selected}) != 1:
        binding = multiday.same_binding_group(conn, [item['date'] for item in selected])
        if not binding:
            raise ValueError("只能合并同一天的任务")
    if len({item['project'] for item in selected}) != 1:
        raise ValueError("跨项目任务请分别保留")
    day = binding['anchor_date'] if binding else selected[0]['date']
    logs = list(conn.execute(select(db.worklogs).where(db.worklogs.c.task_id.in_(ids))).mappings())
    minutes = sum(item['confirmed_minutes'] for item in logs)
    if minutes > 1440:
        raise ValueError("合并后的工时超过单项上限 24h")
    first = selected[0]
    conn.execute(update(db.tasks).where(db.tasks.c.id == ids[0]).values(details='\n\n'.join(t['title'] + '\n' + t['details'] for t in selected), edited=True))
    conn.execute(update(db.worklogs).where(db.worklogs.c.task_id == ids[0]).values(confirmed_minutes=minutes, suggested_minutes=None,time_locked=bool(minutes),time_source="manual",time_reason=""))
    for task in selected[1:]:
        for event_id in conn.execute(select(db.evidence.c.event_id).where(db.evidence.c.task_id == task['id'])).scalars():
            conn.execute(sqlite_insert(db.evidence).values(task_id=ids[0], event_id=event_id).on_conflict_do_nothing())
        delete_task(conn, task['id'])
    invalidate(conn, day)
    return ids[0]


def split_task(conn, task_id, parts):
    original = get_task(conn, task_id)
    minutes = conn.execute(select(db.worklogs.c.confirmed_minutes).where(db.worklogs.c.task_id == task_id)).scalar_one()
    if sum(part.confirmed_minutes for part in parts) != minutes:
        raise ValueError("拆分后的确认工时合计必须等于原任务")
    refs = list(conn.execute(select(db.evidence.c.event_id).where(db.evidence.c.task_id == task_id)).scalars())
    ids = []
    for part in parts:
        new_id = save_task(conn, part)
        conn.execute(update(db.tasks).where(db.tasks.c.id == new_id).values(origin=original['origin'], is_todo=original.get('is_todo', False), evidence_level='拆分继承证据，请核对归属'))
        for event_id in refs:
            conn.execute(insert(db.evidence).values(task_id=new_id, event_id=event_id))
        ids.append(new_id)
    delete_task(conn, task_id)
    from . import similarity
    similarity.mark_titles_modified(conn, original['date'])
    return ids


def report(conn, day):
    from . import multiday
    settings = get_settings(conn)
    binding = multiday.get_active_binding(conn, day)
    canonical_day = binding['anchor_date'] if binding else str(day)
    tasks = list_tasks(conn, day)
    total = sum(t['confirmed_minutes'] for t in tasks)
    stored = conn.execute(select(db.reports).where(db.reports.c.date == canonical_day)).mappings().first()
    warnings = []
    review_items = []
    observed = {}
    for task in tasks:
        times = sorted({datetime.fromisoformat(e['occurred_at']) for e in task['evidence']
                        if e['kind'] in ('tool_call', 'shell_command', 'tool_result')})
        observed[task['id']] = [(a, b) for a, b in zip(times, times[1:]) if 0 < (b-a).total_seconds() <= 300]
    for i, a in enumerate(tasks):
        for j, b in enumerate(tasks[i+1:], start=i+1):
            overlaps = [
                (max(x, z), min(y, w))
                for x, y in observed[a['id']]
                for z, w in observed[b['id']]
                if max(x, z) < min(y, w)
            ]
            if overlaps:
                ov_start, ov_end = overlaps[0]
                ov_date = ov_start.strftime("%Y-%m-%d")
                ov_window = f"{ov_start.strftime('%H:%M')}~{ov_end.strftime('%H:%M')}"
                proj = a.get('project') if a.get('project') == b.get('project') else f"{a.get('project') or '未归属'} / {b.get('project') or '未归属'}"
                msg = (
                    f"【{ov_date} · 今日工作清单 · {proj}】"
                    f"第 #{i + 1} 条「{a['title']}」与第 #{j + 1} 条「{b['title']}」"
                    f"工具活动区间重叠（{ov_window}）：{a['title']} / {b['title']}。建议工时不可直接叠加，请核对实际人工投入。"
                )
                warnings.append(msg)
                review_items.append({
                    "kind": "tool_overlap",
                    "location": "今日工作清单",
                    "date": str(a.get('date') or day),
                    "evidence_dates": [ov_date],
                    "project": proj,
                    "indices": [i + 1, j + 1],
                    "ids": [a['id'], b['id']],
                    "titles": [a['title'], b['title']],
                    "detail": f"工具活动重叠时段 {ov_date} {ov_window}",
                    "reason": msg,
                })
        if a['started_at'] and a['ended_at']:
            for j, b in enumerate(tasks[i+1:], start=i+1):
                if b['started_at'] and b['ended_at']:
                    ov_s = max(datetime.fromisoformat(a['started_at']), datetime.fromisoformat(b['started_at']))
                    ov_e = min(datetime.fromisoformat(a['ended_at']), datetime.fromisoformat(b['ended_at']))
                    if ov_s < ov_e:
                        ov_date = str(a.get('date') or day)
                        ov_window = f"{ov_s.strftime('%H:%M')}~{ov_e.strftime('%H:%M')}"
                        proj = a.get('project') or '未归属项目'
                        msg = (
                            f"【{ov_date} · 今日工作清单 · {proj}】"
                            f"第 #{i + 1} 条「{a['title']}」与第 #{j + 1} 条「{b['title']}」"
                            f"时间重叠（{ov_window}）：{a['title']} / {b['title']}"
                        )
                        warnings.append(msg)
                        review_items.append({
                            "kind": "time_overlap",
                            "location": "今日工作清单",
                            "date": ov_date,
                            "evidence_dates": [ov_s.strftime("%Y-%m-%d")],
                            "project": proj,
                            "indices": [i + 1, j + 1],
                            "ids": [a['id'], b['id']],
                            "titles": [a['title'], b['title']],
                            "detail": f"起止时间重叠 {ov_window}",
                            "reason": msg,
                        })
    suggestions = merge_suggestions(tasks)
    for suggestion in suggestions:
        warnings.append(suggestion['reason'])
        review_items.append({
            "kind": "similarity",
            "location": suggestion.get("location", "今日工作清单"),
            "date": suggestion.get("date", str(day)),
            "evidence_dates": suggestion.get("evidence_dates", []),
            "project": suggestion.get("project", ""),
            "indices": suggestion.get("indices", []),
            "ids": suggestion.get("ids", []),
            "titles": suggestion.get("titles", []),
            "similarity": suggestion.get("similarity"),
            "detail": f"同项目标题相似度 {int(round((suggestion.get('similarity') or 0) * 100))}%",
            "reason": suggestion["reason"],
        })
    from .daily_analysis import overview
    conversations = (
        overview(conn, binding['start_date'], settings, end_day=binding['end_date'], report_day=canonical_day)
        if binding
        else overview(conn, day, settings)
    )
    analysis_snap = conversations.get('analysis') or {}
    s_day = analysis_snap.get('start_date') or (binding['start_date'] if binding else day)
    e_day = analysis_snap.get('end_date') or (binding['end_date'] if binding else day)
    for aw in (analysis_snap.get('warnings') or []):
        if aw not in warnings:
            warnings.append(aw)
        review_items.append({
            "kind": "analysis_warning",
            "location": "AI 分析候选",
            "date": f"{s_day} ~ {e_day}" if str(s_day) != str(e_day) else str(day),
            "evidence_dates": [str(s_day), str(e_day)] if str(s_day) != str(e_day) else [str(day)],
            "project": "",
            "indices": [],
            "ids": [],
            "titles": [],
            "detail": "AI 分析过滤提示",
            "reason": aw,
        })
    timeline = visible_events(conn, s_day, settings, end_day=e_day)
    from .activity_time import profile
    for task in tasks:
        task['timing']=profile(task['evidence'],timeline,settings.activity_gap_minutes)
    effective_target = (
        binding['target_minutes']
        if (binding and binding.get('target_minutes'))
        else (stored['target_minutes'] if (stored and stored.get('target_minutes')) else settings.target_minutes)
    )
    from .time_allocation import state_hash
    allocation=conn.execute(select(db.allocations.c.snapshot).where(db.allocations.c.date==canonical_day)).scalar_one_or_none()
    if allocation:
        allocation={**allocation,'stale':allocation['state_hash']!=state_hash(tasks,settings,effective_target)}

    from . import similarity
    similarity_status = similarity.get_similarity_status(conn, canonical_day, tasks)

    protected_gitee = protected_gitee_task_ids(conn, [t['id'] for t in tasks])
    for task in tasks:
        task['gitee_linked'] = task['id'] in protected_gitee
    clearable_ai_count = sum(1 for t in tasks if is_clearable_ai_task(t, protected_gitee))
    protected_ai_count = sum(
        1 for t in tasks if t.get('origin') == 'auto' and not is_clearable_ai_task(t, protected_gitee)
    )

    legacy_count = sum(t['origin'] == 'auto' and not t['edited'] and not t['confirmed_minutes'] and not (t['fingerprint'] or '').startswith('daily-v2:') for t in tasks)
    return {"date": str(day), "canonical_date": canonical_day, "binding": binding, "tasks": tasks, "conversations": conversations, "time_allocation":allocation, "legacy_count": legacy_count, "clearable_ai_count": clearable_ai_count, "protected_ai_count": protected_ai_count, "total_minutes": total, "target_minutes": effective_target,
            "remaining_minutes": effective_target-total, "confirmed": bool(stored and stored['confirmed'] and total == effective_target),
            "confirmed_at": stored['confirmed_at'] if stored else None, "warnings": warnings,
            "review_items": review_items,
            "merge_suggestions": suggestions, "projects": len({t['project'] for t in tasks}),
            "sources": len({e['source'] for t in tasks for e in t['evidence'] if e['source'] != 'git'}),
            "similarity_status": similarity_status}


def set_report_target(conn, day, target_minutes):
    from . import multiday
    binding = multiday.get_active_binding(conn, day)
    canonical = binding['anchor_date'] if binding else str(day)
    upsert(conn, db.reports, {"date": canonical, "target_minutes": target_minutes, "confirmed": False}, "date")
    if binding:
        multiday.update_binding_target(conn, binding, target_minutes)
    return report(conn, day)


def confirm(conn, day):
    from . import multiday
    canonical = multiday.canonical_day(conn, day)
    value = report(conn, day)
    if value['total_minutes'] != value['target_minutes']:
        raise ValueError(f"确认失败：当前 {value['total_minutes']} 分钟，目标 {value['target_minutes']} 分钟")
    upsert(conn, db.reports, {"date": canonical, "target_minutes": value['target_minutes'], "confirmed": True, "confirmed_at": now()}, "date")
    ids = [t['id'] for t in value['tasks']]
    conn.execute(update(db.worklogs).where(db.worklogs.c.task_id.in_(ids)).values(is_confirmed=True))
    return report(conn, day)


def export_markdown(conn, day):
    from . import multiday
    canonical = multiday.canonical_day(conn, day)
    value = report(conn, day)
    def esc(text):
        return str(text).replace('\\', '\\\\').replace('|', '\\|').replace('\r', '').replace('\n', '<br>').replace('<script', '&lt;script')
    status_names = {'requested': '待办的', 'attempted': '进行中', 'changed': '已修改', 'verified': '已完成', 'delivered': '已完成', 'rejected': '已拒绝'}
    lines = [f"# 工作日报 · {day}", '', f"状态：{'已确认' if value['confirmed'] else '草稿（未确认）'}", '',
             '| 项目 | 任务标题 | 分类 | 状态 | 来源 | 确认工时 |', '| --- | --- | --- | --- | --- | ---: |']
    for t in value['tasks']:
        lines.append(f"| {esc(t['project'])} | {esc(t['title'])} | {esc(t['category'])} | {status_names[t['status']]} | {'人工' if t['origin']=='manual' else 'AI / Git'} | {t['confirmed_minutes']/60:g}h |")
    lines += ['', f"**合计 {value['total_minutes']/60:g}h / 目标 {value['target_minutes']/60:g}h**", '']
    for t in value['tasks']:
        safe_details = t['details'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        lines += [f"## {esc(t['title'])}", '', safe_details, '', f"证据级别：{t['evidence_level']}", '']
        if t['time_source']=='ai_estimate':
            lines += ['工时依据：AI目标工时分配建议（非人工实测）；'+t['time_reason'],'']
        for e in t['evidence']:
            lines.append(f"- {e['source']} · {e['occurred_at']} · {e['kind']} · 会话 {esc(e['session_id'])} · 事件 {e['id'][:12]} · {esc(e['raw_ref'])}")
        lines.append('')
    if value['warnings']:
        lines += ['## 待审核提示', '', *['- ' + esc(w) for w in value['warnings']]]
    upsert(conn, db.reports, {"date": canonical, "target_minutes": value['target_minutes'], "confirmed": value['confirmed'], "confirmed_at": value['confirmed_at'], "exported_at": now()}, "date")
    return '\n'.join(lines)
