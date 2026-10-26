"""Analyze a day's conversations as a whole, never one task per message/commit."""
import json
import re
from collections import Counter, defaultdict
from uuid import uuid4

from sqlalchemy import and_, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import db, model_provider
from .collectors import stable
from .git_tools import excluded
from .privacy import redact

from . import activity_time, prompts
from .work_scope import filter_work, POLICY_VERSION


def day_rows(conn, day, settings):
    from .service import event_query
    return [dict(e) for e in conn.execute(event_query(day, settings)).mappings()
            if e['source'] in settings.analysis_sources and not excluded(e['cwd'], settings)]


def overview(conn, day, settings):
    from .service import event_query
    all_rows = [dict(e) for e in conn.execute(event_query(day, settings)).mappings() if not excluded(e['cwd'], settings)]
    sessions = {}
    for e in all_rows:
        if e['source'] == 'git':
            continue
        key = (e['source'], e['session_id'])
        session = sessions.setdefault(key, {'id': e['session_id'], 'source': e['source'], 'projects': set(), 'first_at': e['occurred_at'], 'last_at': e['occurred_at'], 'user_messages': 0, 'assistant_messages': 0, 'events': 0, 'preview': ''})
        session['projects'].add(e['project'])
        session['last_at'] = e['occurred_at']
        session['events'] += 1
        if e['kind'] in ('user_prompt', 'assistant_message'):
            session['user_messages' if e['kind'] == 'user_prompt' else 'assistant_messages'] += 1
            if e['kind'] == 'user_prompt' and not session['preview']:
                session['preview'] = e['summary'][:200]
    source_stats = []
    for source in ('codex', 'claude_code', 'git'):
        items = [e for e in all_rows if e['source'] == source]
        counts = Counter(e['kind'] for e in items)
        source_stats.append({'source': source, 'selected': source in settings.analysis_sources,
                             'sessions': len({e['session_id'] for e in items}) if source != 'git' else 0,
                             'user_messages': counts['user_prompt'], 'assistant_messages': counts['assistant_message'], 'events': len(items)})
    selected = [e for e in all_rows if e['source'] in settings.analysis_sources]
    result = {'date': str(day), 'sources': source_stats,
              'sessions': [{**value, 'projects': sorted(value['projects'])} for value in sessions.values()],
              'session_count': len({(e['source'], e['session_id']) for e in selected if e['source'] != 'git'}),
              'user_messages': sum(e['kind'] == 'user_prompt' for e in selected),
              'assistant_messages': sum(e['kind'] == 'assistant_message' for e in selected),
              'event_count': len(selected), 'selected_sources': settings.analysis_sources}
    snapshot = conn.execute(select(db.analyses.c.snapshot).where(db.analyses.c.date == str(day))).scalar_one_or_none()
    result['analysis'] = snapshot
    for session in result['sessions']:
        session_events=[e for e in all_rows if e['source']==session['source'] and e['session_id']==session['id']]
        session['timing']=activity_time.profile(session_events,gap_minutes=settings.activity_gap_minutes)
    work_ids=set((snapshot or {}).get('scope',{}).get('work_event_ids',[]))
    result['timing']=activity_time.profile(selected,gap_minutes=settings.activity_gap_minutes)
    result['work_timing']=activity_time.profile([e for e in selected if e['id'] in work_ids],selected,settings.activity_gap_minutes) if snapshot and 'scope' in snapshot else None

    result['stale'] = bool(snapshot and snapshot.get('input_hash') != input_hash(selected, settings))
    return result


def input_hash(rows, settings):
    return stable(POLICY_VERSION,settings.timezone, sorted(settings.analysis_sources), settings.agent_prompts, settings.title_examples, settings.style_template, settings.activity_gap_minutes, settings.redact_patterns, sorted(e['id'] for e in rows if e['kind'] != 'session_end'))


def transcript(rows, settings):
    refs, records, segments = {}, [], defaultdict(list)
    active = {}
    for index, event in enumerate(rows):
        ref = 'e' + str(index+1)
        if event['kind'] == 'session_end':
            continue
        refs[ref] = event
        session = event['source'] + ':' + event['session_id']
        record = {'ref': ref, 'session': session, 'time': event['occurred_at'], 'project': event['project'], 'kind': event['kind']}
        if event['kind'] in ('user_prompt', 'assistant_message', 'git_commit'):
            record['text'] = redact(event['summary'], settings.redact_patterns)
        else:
            from .service import state_for
            record['tool'] = event['metadata'].get('tool', 'unknown')
            record['observed_state'] = state_for([event])
        records.append(record)
        if event['kind'] == 'user_prompt':
            active[session] = ref
        if session in active:
            segments[active[session]].append(event)
    compact, anchors, activities = [], {}, {}
    for record in records:
        session = record['session']
        if record['kind'] in ('user_prompt', 'assistant_message', 'git_commit'):
            compact.append(record)
            if record['kind'] == 'user_prompt':
                anchors[session] = record
            continue
        anchor = anchors.get(session)
        if anchor is None:
            anchor = {k: v for k, v in record.items() if k not in ('tool', 'observed_state')}
            anchor['kind'] = 'observed_activity'
            compact.append(anchor)
            anchors[session] = anchor
        key = (anchor['ref'], record['project'], record['tool'])
        if key not in activities:
            activity = {'project': record['project'], 'tool': record['tool'], 'refs': [], 'states': {}}
            activities[key] = activity
            anchor.setdefault('activities', []).append(activity)
        activity = activities[key]
        activity['refs'].append(record['ref'])
        state = record['observed_state']
        activity['states'][state] = activity['states'].get(state, 0) + 1
    return compact, refs, segments


def parse_result(text, valid_refs):
    try:
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
        value = json.loads(cleaned)
        if not isinstance(value, dict) or not isinstance(value.get('tasks'), list) or len(value['tasks']) > 100:
            raise ValueError()
        for task in value['tasks']:
            if not isinstance(task, dict) or not isinstance(task.get('title'), str) or not task['title'].strip() or len(task['title']) > 500:
                raise ValueError()
            if not isinstance(task.get('details'), str) or len(task['details']) > 20000:
                raise ValueError()
            if not isinstance(task.get('refs'), list) or not task['refs'] or any(not isinstance(r, str) or r not in valid_refs for r in task['refs']):
                raise ValueError()
            if not isinstance(task.get('project', ''), str) or not isinstance(task.get('category', '其他'), str):
                raise ValueError()
        if not isinstance(value.get('summary', ''), str):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError):
        raise model_provider.ModelError('AI 返回格式或证据引用无效，未替换原任务；请重新分析或更换模型') from None


def infer_day(records, refs, settings, reviewed, day):
    # Every stored conversation message is included. Large days use contiguous chronological batches,
    # followed by a final all-day semantic consolidation; no per-message task generation.
    chunks, current, size = [], [], 0
    chunk_limit = 120000 if settings.llm_provider in ('deepseek', 'glm', 'ccswitch') else 65000
    for record in records:
        length = len(json.dumps(record, ensure_ascii=False))
        if current and size + length > chunk_limit:
            chunks.append(current)
            current, size = [], 0
        current.append(record)
        size += length
    if current:
        chunks.append(current)
    system=prompts.prompt(settings,'task_writer')
    common = {'date': str(day), 'timezone': settings.timezone, 'reviewed': reviewed, 'style_examples':settings.title_examples}
    if len(chunks) == 1:
        text = model_provider.complete(settings, system, json.dumps({**common, 'conversations': chunks[0]}, ensure_ascii=False),purpose='task_writer')
        return parse_result(text, refs), 1
    if len(chunks) > 30:
        raise model_provider.ModelError('当天记录过多，请缩小对话来源或排除无关目录；未发送不完整的全天分析')
    partials = []
    for i, chunk in enumerate(chunks):
        text = model_provider.complete(settings, system + '\n这是全天连续片段，保留工作目标与refs，最后还会统一合并。', json.dumps({**common, 'part': i+1, 'parts': len(chunks), 'conversations': chunk}, ensure_ascii=False),purpose='task_writer')
        chunk_refs = {r['ref']: refs[r['ref']] for r in chunk}
        for record in chunk:
            for activity in record.get('activities', []):
                chunk_refs.update({ref: refs[ref] for ref in activity['refs']})
        partials.append(parse_result(text, chunk_refs))
    text = model_provider.complete(settings, system + '\n请将这些连续片段的候选任务按全天实际目标再次合并去重，尤其合并同一目标的跨片段追问。refs只能使用片段已有引用。', json.dumps({**common, 'daily_parts': partials}, ensure_ascii=False),purpose='task_writer')
    return parse_result(text, refs), len(chunks)+1


def analyze(conn, day):
    from .service import category, get_settings, invalidate, list_tasks, now, state_for, upsert
    settings = get_settings(conn)
    rows = day_rows(conn, day, settings)
    summary = overview(conn, day, settings)
    if not settings.llm_enabled:
        return {'created': 0, 'updated': 0, 'archived': 0, 'analysis_required': True, 'warnings': ['已统计全天对话。请在设置中接通并开启 AI，再分析实际工作任务；不会按提问或提交自动生成任务。'], 'conversations': summary, 'merge_suggestions': []}
    if not any(e['kind'] in ('user_prompt', 'assistant_message') for e in rows):
        return {'created': 0, 'updated': 0, 'archived': 0, 'warnings': ['所选来源在当天没有可分析的对话。'], 'conversations': summary, 'merge_suggestions': []}
    records, refs, segments = transcript(rows, settings)
    active_tasks = list_tasks(conn, day)
    protected = [t for t in active_tasks if t['origin'] == 'manual' or t['edited'] or (t['confirmed_minutes'] > 0 and t['time_source'] != 'ai_estimate')]
    protected_ids = {e['id'] for t in protected for e in t['evidence']}
    deleted = list(conn.execute(select(db.tasks).where(and_(db.tasks.c.date == str(day), db.tasks.c.deleted == True, db.tasks.c.edited == True))).mappings())
    deleted_ids = {r for t in deleted for r in conn.execute(select(db.evidence.c.event_id).where(db.evidence.c.task_id == t['id'])).scalars()}
    reviewed = [{'title': t['title'], 'project': t['project'], 'decision': '保留已审核，不重复生成'} for t in protected]
    reviewed += [{'title': t['title'], 'project': t['project'], 'decision': '用户已删除，不再生成'} for t in deleted]
    records, work_refs, scope, filter_requests = filter_work(records,refs,segments,settings)
    refs=work_refs
    if records:
        result, requests = infer_day(records, refs, settings, reviewed, day)
    else:
        result, requests = {'summary':'当天没有可自动计入的实际工作；私人、无关及不足时长的知识梳理已排除。','tasks':[]},0
    requests+=filter_requests
    # Model latency must never hold the database's writer lock. Re-read human edits atomically
    # after inference, and refuse stale input if collection/source selection changed meanwhile.
    conn.commit()
    conn.exec_driver_sql('BEGIN IMMEDIATE')
    current_settings = get_settings(conn)
    current_rows = day_rows(conn, day, current_settings)
    if input_hash(current_rows, current_settings) != input_hash(rows, settings):
        raise model_provider.ModelError('分析期间对话或来源范围发生变化，原任务已保留；请重新分析')
    active_tasks = list_tasks(conn, day)
    report_confirmed = conn.execute(select(db.reports.c.confirmed).where(db.reports.c.date == str(day))).scalar_one_or_none()
    protected = [t for t in active_tasks if report_confirmed or t['origin'] == 'manual' or t['edited'] or (t['confirmed_minutes'] > 0 and t['time_source'] != 'ai_estimate')]
    protected_ids = {e['id'] for t in protected for e in t['evidence']}
    deleted = list(conn.execute(select(db.tasks).where(and_(db.tasks.c.date == str(day), db.tasks.c.deleted == True, db.tasks.c.edited == True))).mappings())
    deleted_ids = {r for t in deleted for r in conn.execute(select(db.evidence.c.event_id).where(db.evidence.c.task_id == t['id'])).scalars()}
    generated, warnings = [], []
    used = set()
    for task in result['tasks']:
        supporting = {refs[r]['id']: refs[r] for r in task['refs']}
        for ref in task['refs']:
            for event in segments.get(ref, []):
                if event['id'] in set(scope['work_event_ids']):
                    supporting[event['id']] = event
        if set(supporting) & (protected_ids | deleted_ids):
            warnings.append(f"已跳过与人工审核/删除记录重叠的候选：{task['title']}")
            continue
        if not any(e['kind'] in ('user_prompt', 'tool_call', 'shell_command', 'git_commit') for e in supporting.values()):
            raise model_provider.ModelError('AI 候选只有回复文本，缺少工作请求或行为依据；原任务已保留')
        projects = {e['project'] for e in supporting.values()}
        project = task.get('project') if task.get('project') in projects else next((e['project'] for e in supporting.values() if e['kind'] == 'user_prompt'), sorted(projects)[0])
        signature = stable(project, task['title'].strip())
        if signature in used:
            continue
        used.add(signature)
        fingerprint = 'daily-v2:' + stable(str(day), sorted(supporting), task['title'].strip())
        previous = conn.execute(select(db.tasks).where(db.tasks.c.fingerprint == fingerprint)).mappings().first()
        if previous and previous['edited']:
            continue
        generated.append(({'id': previous['id'] if previous else uuid4().hex, 'date': str(day),
                           'title': redact(task['title'].strip(), settings.redact_patterns),
                           'details': redact(task['details'], settings.redact_patterns), 'project': project,
                           'category': redact(task.get('category') or category(task['title']), settings.redact_patterns)[:100],
                           'status': state_for(list(supporting.values())), 'origin': 'auto', 'confidence': '全天 AI 分析 · 待审核',
                           'evidence_level': '已有行为证据' if any(e['kind'] in ('tool_call', 'shell_command', 'git_commit') for e in supporting.values()) else '只有对话描述',
                           'fingerprint': fingerprint, 'edited': False, 'deleted': False, 'started_at': None, 'ended_at': None}, supporting))
    # Everything above is validated before replacing any drafts. The caller commits atomically.
    archived = 0
    protected_task_ids = {t['id'] for t in protected}
    generated_ids = {t['id'] for t, _ in generated}
    for t in active_tasks:
        if t['id'] not in protected_task_ids | generated_ids:
            conn.execute(update(db.tasks).where(db.tasks.c.id == t['id']).values(deleted=True))
            archived += 1
    for task, supporting in generated:
        upsert(conn, db.tasks, task, 'id')
        conn.execute(sqlite_insert(db.worklogs).values(task_id=task['id'], suggested_minutes=None, confirmed_minutes=0, is_confirmed=False).on_conflict_do_nothing())
        for event in supporting.values():
            conn.execute(sqlite_insert(db.evidence).values(task_id=task['id'], event_id=event['id']).on_conflict_do_nothing())
    snapshot = {'scope':scope, 'prompt_roles':['scope_filter','task_writer'], 'input_hash': input_hash(rows, settings), 'analyzed_at': now(), 'provider': settings.llm_provider,
                'model': model_provider.status(settings)['model'], 'summary': redact(result.get('summary', ''), settings.redact_patterns),
                'session_count': summary['session_count'], 'user_messages': summary['user_messages'], 'assistant_messages': summary['assistant_messages'],
                'sources': settings.analysis_sources, 'tasks': len(generated), 'model_requests': requests, 'warnings': warnings}
    upsert(conn, db.analyses, {'date': str(day), 'snapshot': snapshot}, 'date')
    if generated or archived:
        invalidate(conn, day)
    return {'created': len(generated), 'updated': 0, 'archived': archived, 'warnings': warnings, 'summary': snapshot['summary'], 'model_requests': requests, 'conversations': overview(conn, day, settings), 'merge_suggestions': []}
