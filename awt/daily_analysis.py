"""Analyze a day's conversations as a whole, never one task per message/commit."""
import json
from pathlib import Path
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import db, model_provider
from .collectors import stable
from .git_tools import _as_date, excluded
from .models import CONVERSATION_SOURCES, SOURCE_FAMILIES
from .privacy import redact

from . import activity_time, prompts
from .work_scope import filter_work, POLICY_VERSION


def selected_sources(settings):
    selected = set(settings.analysis_sources)
    # 旧数据把国内外版记在合并的来源名下；两个新来源都选中时才纳入旧记录，
    # 避免只选一个版本时混入另一个版本的对话。
    for legacy, variants in SOURCE_FAMILIES.items():
        if all(variant in selected for variant in variants):
            selected.add(legacy)
    return selected


def day_rows(conn, day, settings, end_day=None):
    from .service import visible_events
    sources = selected_sources(settings)
    return [e for e in visible_events(conn, day, settings, end_day=end_day)
            if e['source'] in sources and not excluded(e['cwd'], settings)]


def is_source_detected(source, settings, conn=None, rows=None):
    if source == 'git':
        if rows and any(e['source'] == 'git' for e in rows):
            return True
        for p in settings.git_roots:
            try:
                if Path(p).expanduser().exists():
                    return True
            except OSError:
                pass
        if conn:
            try:
                if conn.execute(select(db.repositories.c.root)).first():
                    return True
            except Exception:
                pass
        return False
    if rows and any(e['source'] == source for e in rows):
        return True
    cfg = getattr(settings, source, None)
    if cfg and cfg.paths:
        for p in cfg.paths:
            try:
                if Path(p).expanduser().exists():
                    return True
            except OSError:
                pass
    return False


def overview(conn, day, settings, end_day=None, report_day=None):
    from .service import visible_events
    s_date = _as_date(day)
    e_date = _as_date(end_day) if end_day is not None else s_date
    if e_date < s_date:
        s_date, e_date = e_date, s_date
    target_day = str(report_day or day)
    all_rows = [e for e in visible_events(conn, s_date, settings, end_day=e_date) if not excluded(e['cwd'], settings)]
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
    selected_names = selected_sources(settings)
    for source in (*CONVERSATION_SOURCES, 'git'):
        items = [e for e in all_rows if e['source'] == source]
        counts = Counter(e['kind'] for e in items)
        detected = is_source_detected(source, settings, conn, all_rows)
        source_stats.append({'source': source, 'selected': source in selected_names and detected,
                             'detected': detected,
                             'sessions': len({e['session_id'] for e in items}) if source != 'git' else 0,
                             'user_messages': counts['user_prompt'], 'assistant_messages': counts['assistant_message'], 'events': len(items)})
    detected_sources = {s['source'] for s in source_stats if s['detected']}
    selected = [e for e in all_rows if e['source'] in selected_names and e['source'] in detected_sources]

    tz = ZoneInfo(settings.timezone)
    by_day_selected = defaultdict(list)
    by_day_all = defaultdict(list)
    for e in all_rows:
        try:
            ev_day = str(datetime.fromisoformat(e['occurred_at'].replace('Z', '+00:00')).astimezone(tz).date())
        except Exception:
            ev_day = str(e['occurred_at'])[:10]
        by_day_all[ev_day].append(e)
        if e['source'] in selected_names and e['source'] in detected_sources:
            by_day_selected[ev_day].append(e)

    daily_counts = []
    curr = s_date
    step_count = 0
    while curr <= e_date and step_count < 90:
        d_str = str(curr)
        d_sel = by_day_selected.get(d_str, [])
        d_all = by_day_all.get(d_str, [])
        daily_counts.append({
            'date': d_str,
            'sessions': len({(e['source'], e['session_id']) for e in d_sel if e['source'] != 'git'}),
            'user_messages': sum(e['kind'] == 'user_prompt' for e in d_sel if e['source'] != 'git'),
            'assistant_messages': sum(e['kind'] == 'assistant_message' for e in d_sel if e['source'] != 'git'),
            'git_events': sum(e['source'] == 'git' for e in d_all),
            'events': len(d_sel),
        })
        curr += timedelta(days=1)
        step_count += 1

    result = {'date': target_day, 'start_date': str(s_date), 'end_date': str(e_date),
              'daily_counts': daily_counts, 'sources': source_stats,
              'sessions': [{**value, 'projects': sorted(value['projects'])} for value in sessions.values()],
              'session_count': len({(e['source'], e['session_id']) for e in selected if e['source'] != 'git'}),
              'user_messages': sum(e['kind'] == 'user_prompt' for e in selected),
              'assistant_messages': sum(e['kind'] == 'assistant_message' for e in selected),
              'event_count': len(selected),
              'selected_sources': [s for s in settings.analysis_sources if s in detected_sources]}
    snapshot = conn.execute(select(db.analyses.c.snapshot).where(db.analyses.c.date == target_day)).scalar_one_or_none()
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
    return stable(POLICY_VERSION,settings.timezone, sorted(settings.analysis_sources), settings.agent_prompts, settings.title_examples, settings.style_template, settings.activity_gap_minutes, settings.redact_patterns, settings.git_author.model_dump() if settings.git_author else None,{k:v.model_dump() for k,v in settings.git_author_overrides.items()}, sorted(e['id'] for e in rows if e['kind'] != 'session_end'))


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


def pending_item(task, supporting, previously_deleted):
    return {'fingerprint': task['fingerprint'], 'title': task['title'], 'details': task['details'],
            'project': task['project'], 'category': task['category'], 'status': task['status'],
            'confidence': task['confidence'], 'evidence_level': task['evidence_level'],
            'previously_deleted': previously_deleted, 'evidence_ids': sorted(supporting)}


def infer_day(records, refs, settings, reviewed, day):
    from .analysis_progress import emit
    # Every stored conversation message is included. Large days use contiguous chronological batches,
    # followed by a final all-day semantic consolidation; no per-message task generation.
    chunks, current, size = [], [], 0
    chunk_limit = 120000 if settings.llm_provider in ('deepseek', 'glm', 'glm-coding', 'ccswitch') else 65000
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
        emit(f'归纳工作项：第 1/1 批，{len(records)} 条记录')
        text = model_provider.complete(settings, system, json.dumps({**common, 'conversations': chunks[0]}, ensure_ascii=False),purpose='task_writer')
        return parse_result(text, refs), 1
    if len(chunks) > 30:
        raise model_provider.ModelError('当天记录过多，请缩小对话来源或排除无关目录；未发送不完整的全天分析')
    partials = []
    for i, chunk in enumerate(chunks):
        emit(f'归纳工作项：第 {i+1}/{len(chunks)} 批，{len(chunk)} 条记录')
        text = model_provider.complete(settings, system + '\n这是全天连续片段，保留工作目标与refs，最后还会统一合并。', json.dumps({**common, 'part': i+1, 'parts': len(chunks), 'conversations': chunk}, ensure_ascii=False),purpose='task_writer')
        chunk_refs = {r['ref']: refs[r['ref']] for r in chunk}
        for record in chunk:
            for activity in record.get('activities', []):
                chunk_refs.update({ref: refs[ref] for ref in activity['refs']})
        partials.append(parse_result(text, chunk_refs))
    emit(f'合并全天结果：汇总 {len(chunks)} 批候选工作项')
    text = model_provider.complete(settings, system + '\n请将这些连续片段的候选任务按全天实际目标再次合并去重，尤其合并同一目标的跨片段追问。refs只能使用片段已有引用。', json.dumps({**common, 'daily_parts': partials}, ensure_ascii=False),purpose='task_writer')
    return parse_result(text, refs), len(chunks)+1


def analyze(conn, day, start_day=None, end_day=None, target_minutes=None):
    with model_provider.usage_tracker() as usage_calls:
        try:
            return _analyze(conn, day, usage_calls, start_day=start_day, end_day=end_day, target_minutes=target_minutes)
        except model_provider.ModelError as exc:
            usage=model_provider.usage_summary(usage_calls)
            if not usage:
                raise
            prefix='约 ' if usage['estimated'] else ''
            raise model_provider.ModelError(f"{exc}；本次已记录模型用量{prefix}{usage['total_tokens']:,} tokens（输入 {usage['prompt_tokens']:,} / 输出 {usage['completion_tokens']:,}）") from None


def _analyze(conn, day, usage_calls, start_day=None, end_day=None, target_minutes=None):
    from .service import category, get_settings, invalidate, list_tasks, now, state_for, upsert
    settings = get_settings(conn)
    s_day = start_day or day
    e_day = end_day or day
    rows = day_rows(conn, s_day, settings, end_day=e_day)
    summary = overview(conn, s_day, settings, end_day=e_day, report_day=day)
    if not settings.llm_enabled:
        return {'created': 0, 'updated': 0, 'archived': 0, 'pending': 0, 'pending_tasks': [], 'auto_imported': False, 'analysis_required': True, 'warnings': ['已统计对话。请在设置中接通并开启 AI，再分析实际工作任务；不会按提问或提交自动生成任务。'], 'conversations': summary, 'merge_suggestions': []}
    if not any(e['kind'] in ('user_prompt', 'assistant_message') for e in rows):
        msg = f'所选来源在 {s_day} 至 {e_day} 期间没有可分析的对话。' if str(s_day) != str(e_day) else '所选来源在当天没有可分析的对话。'
        return {'created': 0, 'updated': 0, 'archived': 0, 'pending': 0, 'pending_tasks': [], 'auto_imported': False, 'warnings': [msg], 'conversations': summary, 'merge_suggestions': []}
    records, refs, segments = transcript(rows, settings)
    active_tasks = list_tasks(conn, day)
    protected = [t for t in active_tasks if t['origin'] == 'manual' or t['edited'] or (t['confirmed_minutes'] > 0 and t['time_source'] != 'ai_estimate')]
    protected_ids = {e['id'] for t in protected for e in t['evidence']}
    deleted = list(conn.execute(select(db.tasks).where(and_(db.tasks.c.date == str(day), db.tasks.c.deleted == True, db.tasks.c.edited == True))).mappings())
    deleted_ids = {r for t in deleted for r in conn.execute(select(db.evidence.c.event_id).where(db.evidence.c.task_id == t['id'])).scalars()}
    reviewed = [{'title': t['title'], 'project': t['project'], 'decision': '用户已审核保留，不要重复生成'} for t in protected]
    reviewed += [{'title': t['title'], 'project': t['project'], 'decision': '用户历史曾删除；请结合实施证据与操作历史综合推测置信度：若当天有充分确凿的代码修改、工具调用、提交或明确实施动作（排除误删可能），可作为高置信度候选重新列出；若缺乏实施证据或明确不再需要，则尊重删除决定，不生成'} for t in deleted]
    records, work_refs, scope, filter_requests = filter_work(records,refs,segments,settings)
    refs=work_refs
    date_label = f"{s_day} 至 {e_day}" if str(s_day) != str(e_day) else str(day)
    if records:
        result, requests = infer_day(records, refs, settings, reviewed, date_label)
    else:
        result, requests = {'summary':'所选日期范围内没有可自动计入的实际工作；私人、无关及不足时长的知识梳理已排除。','tasks':[]},0
    requests+=filter_requests
    # Model latency must never hold the database's writer lock. Re-read human edits atomically
    # after inference, and refuse stale input if collection/source selection changed meanwhile.
    conn.commit()
    conn.exec_driver_sql('BEGIN IMMEDIATE')
    current_settings = get_settings(conn)
    current_rows = day_rows(conn, s_day, current_settings, end_day=e_day)
    if input_hash(current_rows, current_settings) != input_hash(rows, settings):
        raise model_provider.ModelError('分析期间对话或来源范围发生变化，原任务已保留；请重新分析')
    active_tasks = list_tasks(conn, day)
    report_confirmed = conn.execute(select(db.reports.c.confirmed).where(db.reports.c.date == str(day))).scalar_one_or_none()
    protected = [t for t in active_tasks if report_confirmed or t['origin'] == 'manual' or t['edited'] or (t['confirmed_minutes'] > 0 and t['time_source'] != 'ai_estimate')]
    protected_ids = {e['id'] for t in protected for e in t['evidence']}
    deleted = list(conn.execute(select(db.tasks).where(and_(db.tasks.c.date == str(day), db.tasks.c.deleted == True, db.tasks.c.edited == True))).mappings())
    deleted_ids = {r for t in deleted for r in conn.execute(select(db.evidence.c.event_id).where(db.evidence.c.task_id == t['id'])).scalars()}
    owners={}
    for anchor, events in segments.items():
        if anchor in refs:
            for event in events:
                owners.setdefault(event['id'], anchor)
    generated, warnings = [], []
    if scope.get('unresolved_refs'):
        warnings.append(f"工作筛选有 {len(scope['unresolved_refs'])} 条请求未归类，已放入待判断，不计工时：{','.join(scope['unresolved_refs'][:10])}")
    if scope.get('ambiguous_refs'):
        warnings.append(f"工作筛选有 {len(scope['ambiguous_refs'])} 条请求归类冲突，已放入待判断，不计工时：{','.join(scope['ambiguous_refs'][:10])}")
    used = set()
    for task in result['tasks']:
        supporting = {refs[r]['id']: refs[r] for r in task['refs']}
        for ref in task['refs']:
            for event in segments.get(ref, []):
                if event['id'] in set(scope['work_event_ids']):
                    supporting[event['id']] = event
        if set(supporting) & protected_ids:
            warnings.append(f"已跳过与人工审核任务证据重叠的候选：{task['title']}")
            continue
        if not any(e['kind'] in ('user_prompt', 'tool_call', 'shell_command', 'git_commit') for e in supporting.values()):
            # Assistant replies inherit the work request of their session turn.
            for event_id in list(supporting):
                anchor=owners.get(event_id)
                if anchor:
                    supporting[refs[anchor]['id']]=refs[anchor]
        if not any(e['kind'] in ('user_prompt', 'tool_call', 'shell_command', 'git_commit') for e in supporting.values()):
            raise model_provider.ModelError('AI 候选只有回复文本，缺少工作请求或行为依据；原任务已保留')
        # 与已删除任务证据重叠的候选进行置信度考量：有具体行为依据的高置信度候选予以保留，纯对话低置信度跳过。
        is_previously_deleted = bool(set(supporting) & deleted_ids)
        has_behavior = any(e['kind'] in ('tool_call', 'shell_command', 'git_commit') for e in supporting.values())
        if is_previously_deleted and not has_behavior:
            warnings.append(f"已跳过历史曾删除且只有对话描述的弱证据候选：{task['title']}")
            continue
        projects = {e['project'] for e in supporting.values()}
        project = task.get('project') if task.get('project') in projects else next((e['project'] for e in supporting.values() if e['kind'] == 'user_prompt'), sorted(projects)[0])
        signature = stable(project, task['title'].strip())
        if signature in used:
            continue
        used.add(signature)
        fingerprint = 'daily-v2:' + stable(str(day), sorted(supporting), task['title'].strip())
        previous = conn.execute(select(db.tasks).where(db.tasks.c.fingerprint == fingerprint)).mappings().first()
        if previous and previous['edited'] and not previous['deleted']:
            continue
        generated.append(({'id': previous['id'] if previous else uuid4().hex, 'date': str(day),
                           'title': redact(task['title'].strip(), settings.redact_patterns),
                           'details': redact(task['details'], settings.redact_patterns), 'project': project,
                           'category': redact(task.get('category') or category(task['title']), settings.redact_patterns)[:100],
                           'status': state_for(list(supporting.values())), 'origin': 'auto',
                           'confidence': '全天 AI 分析 · 曾被删除' if is_previously_deleted else '全天 AI 分析 · 待审核',
                           'evidence_level': '已有行为证据' if has_behavior else '只有对话描述',
                           'fingerprint': fingerprint, 'edited': False, 'deleted': False, 'started_at': None, 'ended_at': None},
                          supporting, is_previously_deleted))
    # Everything above is validated before replacing any drafts. The caller commits atomically.
    archived = 0
    pending_tasks = []
    imported = []
    if settings.auto_import_analysis:
        protected_task_ids = {t['id'] for t in protected}
        generated_ids = {t['id'] for t, _, _ in generated}
        for t in active_tasks:
            if t['id'] not in protected_task_ids | generated_ids:
                conn.execute(update(db.tasks).where(db.tasks.c.id == t['id']).values(deleted=True))
                archived += 1
        for task, supporting, previously_deleted in generated:
            if previously_deleted:
                # 曾删除候选不直接写入今日清单：进入 pending_tasks，由前台专用弹窗打勾打叉确认后再导入
                pending_tasks.append(pending_item(task, supporting, True))
                continue
            imported.append(task)
            upsert(conn, db.tasks, task, 'id')
            conn.execute(sqlite_insert(db.worklogs).values(task_id=task['id'], suggested_minutes=None, confirmed_minutes=0, is_confirmed=False).on_conflict_do_nothing())
            for event in supporting.values():
                conn.execute(sqlite_insert(db.evidence).values(task_id=task['id'], event_id=event['id']).on_conflict_do_nothing())
    else:
        # 默认只保存候选，不触碰今日清单；用户在“分析结果”里确认后再写入。
        pending_tasks = [pending_item(task, supporting, previously_deleted) for task, supporting, previously_deleted in generated]
    usage = model_provider.usage_summary(usage_calls)
    snapshot = {'scope':scope, 'prompt_roles':['scope_filter','task_writer'], 'input_hash': input_hash(rows, settings), 'analyzed_at': now(), 'provider': settings.llm_provider,
                'usage': usage, 'pending_tasks': pending_tasks, 'auto_imported': settings.auto_import_analysis,
                'start_date': str(s_day), 'end_date': str(e_day), 'target_day': str(day), 'target_minutes': target_minutes,
                'model': model_provider.status(settings)['model'], 'summary': redact(result.get('summary', ''), settings.redact_patterns),
                'session_count': summary['session_count'], 'user_messages': summary['user_messages'], 'assistant_messages': summary['assistant_messages'],
                'sources': settings.analysis_sources, 'tasks': len(generated), 'model_requests': requests, 'warnings': warnings}
    upsert(conn, db.analyses, {'date': str(day), 'snapshot': snapshot}, 'date')
    if target_minutes:
        upsert(conn, db.reports, {'date': str(day), 'target_minutes': target_minutes, 'confirmed': False}, 'date')
    if settings.auto_import_analysis and (imported or archived):
        invalidate(conn, day)
    from . import similarity
    sim_check = similarity.check_day_similarity(conn, day, settings=settings, force=False)
    return {'created': len(imported), 'updated': 0, 'archived': archived,
            'pending': len(pending_tasks), 'pending_tasks': pending_tasks, 'auto_imported': settings.auto_import_analysis,
            'warnings': warnings, 'summary': snapshot['summary'], 'model_requests': requests, 'usage': usage, 'conversations': overview(conn, s_day, settings, end_day=e_day, report_day=day), 'merge_suggestions': [],
            'similarity_check': sim_check}


def import_pending(conn, day, selections):
    from .service import get_settings, invalidate, list_tasks, now, upsert
    settings = get_settings(conn)
    snapshot = conn.execute(select(db.analyses.c.snapshot).where(db.analyses.c.date == str(day))).scalar_one_or_none()
    if not snapshot or not snapshot.get('pending_tasks'):
        raise ValueError('没有待导入的分析结果，请先分析当天对话')
    pending = {item['fingerprint']: item for item in snapshot['pending_tasks']}
    unknown = [item.fingerprint for item in selections if item.fingerprint not in pending]
    if unknown:
        raise ValueError('分析结果已变化，请关闭窗口后重新打开分析结果')
    active_tasks = list_tasks(conn, day)
    report_confirmed = conn.execute(select(db.reports.c.confirmed).where(db.reports.c.date == str(day))).scalar_one_or_none()
    protected = [t for t in active_tasks if report_confirmed or t['origin'] == 'manual' or t['edited'] or (t['confirmed_minutes'] > 0 and t['time_source'] != 'ai_estimate')]
    protected_ids = {e['id'] for t in protected for e in t['evidence']}
    deleted = list(conn.execute(select(db.tasks).where(and_(db.tasks.c.date == str(day), db.tasks.c.deleted == True, db.tasks.c.edited == True))).mappings())
    deleted_ids = {r for t in deleted for r in conn.execute(select(db.evidence.c.event_id).where(db.evidence.c.task_id == t['id'])).scalars()}
    generated, warnings = [], []
    for selection in selections:
        candidate = pending[selection.fingerprint]
        evidence_ids = list(candidate.get('evidence_ids', []))
        rows = {row['id']: dict(row) for row in conn.execute(select(db.events).where(db.events.c.id.in_(evidence_ids))).mappings()} if evidence_ids else {}
        if len(rows) != len(set(evidence_ids)):
            raise ValueError('部分依据记录已不存在，请重新分析当天对话')
        # 只拦截人工审核保留的任务；疑似重复（与已删除任务证据重叠）的候选由用户在此显式勾选恢复。
        if set(rows) & protected_ids:
            warnings.append(f"已跳过与人工审核任务证据重叠的候选：{candidate['title']}")
            continue
        if not candidate.get('previously_deleted') and set(rows) & deleted_ids:
            warnings.append(f"已跳过与分析后删除的任务重叠的候选：{candidate['title']}")
            continue
        title = selection.title if selection.title is not None else candidate['title']
        details = selection.details if selection.details is not None else candidate['details']
        project = selection.project if selection.project is not None else candidate['project']
        category_name = selection.category if selection.category is not None else candidate['category']
        status = selection.status or candidate['status']
        # fingerprint 全局唯一：曾删除候选恢复时复用原行（同一工作的既有记录），其余候选新建。
        existing = conn.execute(select(db.tasks.c.id).where(db.tasks.c.fingerprint == candidate['fingerprint'])).scalar_one_or_none()
        generated.append(({'id': existing or uuid4().hex, 'date': str(day), 'title': redact(title.strip(), settings.redact_patterns)[:500],
                           'details': redact(details, settings.redact_patterns), 'project': project or '未归属项目',
                           'category': redact(category_name, settings.redact_patterns)[:100], 'status': status,
                           'origin': 'auto', 'confidence': '全天 AI 分析 · 待审核',
                           'evidence_level': candidate.get('evidence_level', '已有行为证据'),
                           'fingerprint': candidate['fingerprint'], 'edited': False, 'deleted': False, 'started_at': None, 'ended_at': None},
                          rows))
    protected_task_ids = {t['id'] for t in protected}
    archived = 0
    for t in active_tasks:
        if t['id'] not in protected_task_ids:
            conn.execute(update(db.tasks).where(db.tasks.c.id == t['id']).values(deleted=True))
            archived += 1
    for task, rows in generated:
        upsert(conn, db.tasks, task, 'id')
        # 导入即草稿：恢复的行沿用旧 worklog，需重置工时，避免带出删除前的确认分钟数。
        conn.execute(sqlite_insert(db.worklogs).values(task_id=task['id'], suggested_minutes=None, confirmed_minutes=0,
                                                       is_confirmed=False, time_locked=False, time_source='manual', time_reason='')
                      .on_conflict_do_update(index_elements=[db.worklogs.c.task_id],
                                             set_={'suggested_minutes': None, 'confirmed_minutes': 0, 'is_confirmed': False,
                                                   'time_locked': False, 'time_source': 'manual', 'time_reason': ''}))
        for event_id in rows:
            conn.execute(sqlite_insert(db.evidence).values(task_id=task['id'], event_id=event_id).on_conflict_do_nothing())
    imported = {task['fingerprint'] for task, _ in generated}
    remaining = [item for item in snapshot['pending_tasks'] if item['fingerprint'] not in imported]
    snapshot = {**snapshot, 'pending_tasks': remaining, 'imported_at': now(), 'imported_tasks': len(generated)}
    upsert(conn, db.analyses, {'date': str(day), 'snapshot': snapshot}, 'date')
    if generated or archived:
        invalidate(conn, day)
    from . import similarity
    sim_check = similarity.check_day_similarity(conn, day, settings=settings, force=False)
    return {'created': len(generated), 'archived': archived, 'remaining': len(remaining),
            'imported': sorted(imported), 'warnings': warnings, 'similarity_check': sim_check}
