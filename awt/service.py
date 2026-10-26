import json
import os
import re
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import db
from .collectors import ClaudeAdapter, CodexAdapter, CollectionCursor, stable
from .git_tools import collect_commits, day_bounds, excluded, resolve_project, filter_git_events
from .models import Settings, TaskInput
from .privacy import redact


def now():
    return datetime.now(timezone.utc).isoformat()


def upsert(conn, table, values, key):
    stmt = sqlite_insert(table).values(**values)
    conn.execute(stmt.on_conflict_do_update(index_elements=[key], set_={k: v for k, v in values.items() if k != key}))


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
    conn.execute(update(db.reports).where(db.reports.c.date == str(day)).values(confirmed=False, confirmed_at=None))
    ids = select(db.tasks.c.id).where(db.tasks.c.date == str(day))
    conn.execute(update(db.worklogs).where(db.worklogs.c.task_id.in_(ids)).values(is_confirmed=False))


def event_query(day, settings):
    start, end = day_bounds(day, settings.timezone)
    return select(db.events).where(and_(db.events.c.occurred_at >= start, db.events.c.occurred_at < end)).order_by(db.events.c.occurred_at, db.events.c.id)


def visible_events(conn, day, settings):
    return filter_git_events([dict(e) for e in conn.execute(event_query(day,settings)).mappings()],settings)


def attribute_events(item, settings):
    paths = item.metadata.get('affected_paths', [])
    if not paths:
        return [item]
    if any(excluded(path, settings) for path in paths):
        return []
    attributed = {}
    for path in paths:
        if excluded(path, settings):
            continue
        project, repo = resolve_project(path, settings)
        cwd = repo['root'] if repo else str(Path(path).parent)
        attributed[cwd] = replace(item, id=stable(item.id, cwd), cwd=cwd,
                                  metadata={**item.metadata, 'attributed_path': path})
    return list(attributed.values())


def collect(conn, day):
    settings = get_settings(conn)
    warnings, added, project_cache, repos = [], 0, {}, {}
    health = []
    for cls, config in [(ClaudeAdapter, settings.claude_code), (CodexAdapter, settings.codex)]:
        adapter = cls(config.paths, settings.redact_patterns)
        count, files = 0, 0
        if config.enabled:
            for location in adapter.discover():
                if excluded(str(location.path), settings):
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
                        continue
                    offset = saved['offset'] if valid else 0
                    cursor = CollectionCursor(offset, file_id, prefix)
                    adapter.start_offset = offset
                    for item in (attributed for raw in adapter.collect(location, cursor) for attributed in attribute_events(raw, settings)):
                        if excluded(item.cwd, settings):
                            continue
                        if item.cwd not in project_cache:
                            project_cache[item.cwd] = resolve_project(item.cwd, settings)
                        project, repo = project_cache[item.cwd]
                        if repo:
                            repos[repo['id']] = repo
                        values = asdict(item)
                        values.update(project=project, repo_id=repo['id'] if repo else None)
                        inserted = conn.execute(sqlite_insert(db.events).values(**values).on_conflict_do_nothing()).rowcount
                        count += inserted
                        session_id = f"{item.source}:{item.session_id}"
                        upsert(conn, db.sessions, {"id": session_id, "source": item.source, "cwd": item.cwd, "last_seen_at": now()}, "id")
                    with location.path.open("rb") as stream:
                        prefix = stable(stream.read(min(4096, cursor.offset)).hex())
                    upsert(conn, db.cursors, {"source_key": source_key, "file_id": file_id, "offset": cursor.offset, "prefix_hash": prefix, "last_seen_at": now()}, "source_key")
                except (OSError, ValueError) as exc:
                    warnings.append(f"{location.path.name}: 无法读取 ({type(exc).__name__})")
        warnings.extend(adapter.warnings[:30])
        state = {"enabled": config.enabled, "files": files, "new_events": count, "warnings": len(adapter.warnings), "checked_at": now()}
        upsert(conn, db.sources, {"name": adapter.name, "config": config.model_dump(), "health": state}, "name")
        health.append({"source": adapter.name, **state})
        added += count
    # Include repositories found in past scans, not just newly appended sessions.
    for row in conn.execute(event_query(day, settings)).mappings():
        if row['cwd'] and row['cwd'] not in project_cache and not excluded(row['cwd'], settings):
            project_cache[row['cwd']] = resolve_project(row['cwd'], settings)
    for _, repo in project_cache.values():
        if repo:
            repos[repo['id']] = repo
    for root in settings.git_roots:
        if excluded(root, settings):
            continue
        _, repo = resolve_project(root, settings)
        if repo:
            repos[repo['id']] = repo
        else:
            warnings.append(f"Git 根目录无效或未初始化：{root}")
    git_added = 0
    for repo in repos.values():
        if excluded(repo['root'], settings):
            continue
        upsert(conn, db.repositories, repo, "id")
        for commit in collect_commits(repo, day, settings,warnings):
            upsert(conn, db.commits, {k:v for k,v in commit.items() if k not in ('author_name','author_email')}, "id")
            values = {"id": stable("git", commit['id']), "source": "git", "session_id": repo['id'],
                      "occurred_at": commit['committed_at'], "kind": "git_commit", "summary": commit['subject'],
                      "cwd": repo['root'], "project": repo['project'], "repo_id": repo['id'],
                      "raw_ref": f"{repo['root']}@{commit['sha']}",
                      "metadata": {"sha": commit['sha'], "changes": commit['changes'], "authored_at": commit['authored_at'],
                                   'author_name':commit['author_name'],'author_email':commit['author_email']}}
            git_added += conn.execute(sqlite_insert(db.events).values(**values).on_conflict_do_nothing()).rowcount
            conn.execute(update(db.events).where(db.events.c.id==values['id']).values(metadata=values['metadata']))
    return {"new_events": added + git_added, "git_events": git_added, "sources": health, "warnings": warnings[:60], "date": str(day)}


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


def list_tasks(conn, day):
    rows = conn.execute(select(db.tasks, db.worklogs.c.suggested_minutes, db.worklogs.c.confirmed_minutes, db.worklogs.c.time_locked, db.worklogs.c.time_source, db.worklogs.c.time_reason)
                        .join(db.worklogs).where(and_(db.tasks.c.date == str(day), db.tasks.c.deleted == False))
                        .order_by(db.tasks.c.id)).mappings().all()
    output = []
    for row in rows:
        task = dict(row)
        task['evidence'] = [dict(e) for e in conn.execute(select(db.events).join(db.evidence, db.evidence.c.event_id == db.events.c.id)
                           .where(db.evidence.c.task_id == row['id']).order_by(db.events.c.occurred_at)).mappings()]
        from .activity_time import profile
        task['timing']=profile(task['evidence'],gap_minutes=get_settings(conn).activity_gap_minutes)
        output.append(task)
    return output


def analyze(conn, day):
    from .daily_analysis import analyze as analyze_daily
    return analyze_daily(conn, day)


def merge_suggestions(tasks):
    suggestions = []
    for i, a in enumerate(tasks):
        for b in tasks[i+1:]:
            if a['project'] == b['project'] and SequenceMatcher(None, a['title'], b['title']).ratio() >= .72:
                suggestions.append({"ids": [a['id'], b['id']], "reason": "项目和工作目标相似，请核对是否重复；建议工时不可直接叠加。"})
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
    locked = values.pop('time_locked')
    locked = bool(minutes) if locked is None else locked
    for key in ('title', 'details', 'project', 'category'):
        values[key] = redact(values[key], settings.redact_patterns)
    if existing:
        conn.execute(update(db.tasks).where(db.tasks.c.id == task_id).values(**values, edited=True))
        old_log=conn.execute(select(db.worklogs).where(db.worklogs.c.task_id==task_id)).mappings().one()
        source='manual' if minutes!=old_log['confirmed_minutes'] or locked else old_log['time_source']
        conn.execute(update(db.worklogs).where(db.worklogs.c.task_id == task_id).values(confirmed_minutes=minutes, time_locked=locked, time_source=source, time_reason='' if source=='manual' else old_log['time_reason'], is_confirmed=False))
        invalidate(conn, existing['date'])
    else:
        task_id = uuid4().hex
        conn.execute(insert(db.tasks).values(id=task_id, **values, origin='manual', edited=True, deleted=False,
                                            confidence='人工录入', evidence_level='人工说明'))
        conn.execute(insert(db.worklogs).values(task_id=task_id, confirmed_minutes=minutes, suggested_minutes=None, time_locked=locked, time_source="manual", is_confirmed=False))
    invalidate(conn, values['date'])
    return task_id


def delete_task(conn, task_id):
    task = get_task(conn, task_id)
    conn.execute(update(db.tasks).where(db.tasks.c.id == task_id).values(deleted=True, edited=True))
    invalidate(conn, task['date'])


def merge_tasks(conn, ids):
    if len(set(ids)) != len(ids):
        raise ValueError("请选择不同任务")
    selected = [get_task(conn, task_id) for task_id in ids]
    if len({item['date'] for item in selected}) != 1:
        raise ValueError("只能合并同一天的任务")
    if len({item['project'] for item in selected}) != 1:
        raise ValueError("跨项目任务请分别保留")
    day = selected[0]['date']
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
        conn.execute(update(db.tasks).where(db.tasks.c.id == new_id).values(origin=original['origin'], evidence_level='拆分继承证据，请核对归属'))
        for event_id in refs:
            conn.execute(insert(db.evidence).values(task_id=new_id, event_id=event_id))
        ids.append(new_id)
    delete_task(conn, task_id)
    return ids


def report(conn, day):
    settings = get_settings(conn)
    tasks = list_tasks(conn, day)
    total = sum(t['confirmed_minutes'] for t in tasks)
    stored = conn.execute(select(db.reports).where(db.reports.c.date == str(day))).mappings().first()
    warnings = []
    observed = {}
    for task in tasks:
        times = sorted({datetime.fromisoformat(e['occurred_at']) for e in task['evidence']
                        if e['kind'] in ('tool_call', 'shell_command', 'tool_result')})
        observed[task['id']] = [(a, b) for a, b in zip(times, times[1:]) if 0 < (b-a).total_seconds() <= 300]
    for i, a in enumerate(tasks):
        for b in tasks[i+1:]:
            if any(max(x, z) < min(y, w) for x, y in observed[a['id']] for z, w in observed[b['id']]):
                warnings.append(f"工具活动区间重叠：{a['title']} / {b['title']}。建议工时不可直接叠加，请核对实际人工投入。")
        if a['started_at'] and a['ended_at']:
            for b in tasks[i+1:]:
                if b['started_at'] and b['ended_at'] and max(datetime.fromisoformat(a['started_at']), datetime.fromisoformat(b['started_at'])) < min(datetime.fromisoformat(a['ended_at']), datetime.fromisoformat(b['ended_at'])):
                    warnings.append(f"时间重叠：{a['title']} / {b['title']}")
    for suggestion in merge_suggestions(tasks):
        warnings.append(suggestion['reason'])
    from .daily_analysis import overview
    conversations = overview(conn, day, settings)
    timeline=visible_events(conn,day,settings)
    from .activity_time import profile
    for task in tasks:
        task['timing']=profile(task['evidence'],timeline,settings.activity_gap_minutes)
    from .time_allocation import state_hash
    allocation=conn.execute(select(db.allocations.c.snapshot).where(db.allocations.c.date==str(day))).scalar_one_or_none()
    if allocation:
        allocation={**allocation,'stale':allocation['state_hash']!=state_hash(tasks,settings)}

    legacy_count = sum(t['origin'] == 'auto' and not t['edited'] and not t['confirmed_minutes'] and not (t['fingerprint'] or '').startswith('daily-v2:') for t in tasks)
    return {"date": str(day), "tasks": tasks, "conversations": conversations, "time_allocation":allocation, "legacy_count": legacy_count, "total_minutes": total, "target_minutes": settings.target_minutes,
            "remaining_minutes": settings.target_minutes-total, "confirmed": bool(stored and stored['confirmed'] and total == settings.target_minutes),
            "confirmed_at": stored['confirmed_at'] if stored else None, "warnings": warnings,
            "merge_suggestions": merge_suggestions(tasks), "projects": len({t['project'] for t in tasks}),
            "sources": len({e['source'] for t in tasks for e in t['evidence'] if e['source'] != 'git'})}


def confirm(conn, day):
    value = report(conn, day)
    if value['total_minutes'] != value['target_minutes']:
        raise ValueError(f"确认失败：当前 {value['total_minutes']} 分钟，目标 {value['target_minutes']} 分钟")
    upsert(conn, db.reports, {"date": str(day), "target_minutes": value['target_minutes'], "confirmed": True, "confirmed_at": now()}, "date")
    ids = [t['id'] for t in value['tasks']]
    conn.execute(update(db.worklogs).where(db.worklogs.c.task_id.in_(ids)).values(is_confirmed=True))
    return report(conn, day)


def export_markdown(conn, day):
    value = report(conn, day)
    def esc(text):
        return str(text).replace('\\', '\\\\').replace('|', '\\|').replace('\r', '').replace('\n', '<br>').replace('<script', '&lt;script')
    status_names = {'requested': '待执行', 'attempted': '尝试中', 'changed': '已修改', 'verified': '已验证', 'delivered': '已交付（人工确认）'}
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
    upsert(conn, db.reports, {"date": str(day), "target_minutes": value['target_minutes'], "confirmed": value['confirmed'], "confirmed_at": value['confirmed_at'], "exported_at": now()}, "date")
    return '\n'.join(lines)
