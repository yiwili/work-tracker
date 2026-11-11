"""Analyze a day's conversations as a whole, never one task per message/commit."""
import json
from pathlib import Path
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import db, model_provider
from .collectors import stable
from .git_tools import _as_date, excluded
from .models import CONVERSATION_SOURCES, SOURCE_FAMILIES
from .privacy import redact

from . import activity_time, matching, prompts
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


def analysis_rows(conn, day, settings, end_day=None):
    from . import exclusions
    all_selected = day_rows(conn, day, settings, end_day=end_day)
    filtered, excl_stats = exclusions.filter_excluded_rows(conn, all_selected)
    return filtered, all_selected, excl_stats


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
    from . import exclusions
    from .service import visible_events
    s_date = _as_date(day)
    e_date = _as_date(end_day) if end_day is not None else s_date
    if e_date < s_date:
        s_date, e_date = e_date, s_date
    target_day = str(report_day or day)
    all_rows = [e for e in visible_events(conn, s_date, settings, end_day=e_date) if not excluded(e['cwd'], settings)]
    analyzable_all, excl_stats = exclusions.filter_excluded_rows(conn, all_rows)
    excluded_row_ids = excl_stats['excluded_row_ids']
    excluded_sessions_set = excl_stats['excluded_sessions']
    sessions = {}
    for e in all_rows:
        if e['source'] == 'git':
            continue
        key = (e['source'], e['session_id'])
        session = sessions.setdefault(
            key,
            {
                'id': e['session_id'],
                'source': e['source'],
                'projects': set(),
                'first_at': e['occurred_at'],
                'last_at': e['occurred_at'],
                'user_messages': 0,
                'assistant_messages': 0,
                'events': 0,
                'active_user_messages': 0,
                'active_assistant_messages': 0,
                'active_events': 0,
                'excluded_user_messages': 0,
                'excluded_events': 0,
                'session_excluded': key in excluded_sessions_set,
                'preview': '',
            },
        )
        session['projects'].add(e['project'])
        session['last_at'] = e['occurred_at']
        session['events'] += 1
        is_excluded_ev = e['id'] in excluded_row_ids
        if is_excluded_ev:
            session['excluded_events'] += 1
        else:
            session['active_events'] += 1
        if e['kind'] in ('user_prompt', 'assistant_message'):
            session['user_messages' if e['kind'] == 'user_prompt' else 'assistant_messages'] += 1
            if is_excluded_ev:
                if e['kind'] == 'user_prompt':
                    session['excluded_user_messages'] += 1
            else:
                session['active_user_messages' if e['kind'] == 'user_prompt' else 'active_assistant_messages'] += 1
            if e['kind'] == 'user_prompt' and not session['preview']:
                session['preview'] = e['summary'][:200]
    for session in sessions.values():
        active_msgs = session['active_user_messages'] + session['active_assistant_messages']
        total_msgs = session['user_messages'] + session['assistant_messages']
        if session['session_excluded'] or (session['events'] > 0 and session['active_events'] == 0) or (total_msgs > 0 and active_msgs == 0):
            session['exclusion_state'] = 'full'
        elif session['excluded_events'] > 0:
            session['exclusion_state'] = 'partial'
        else:
            session['exclusion_state'] = 'none'
    source_stats = []
    selected_names = selected_sources(settings)
    for source in (*CONVERSATION_SOURCES, 'git'):
        all_items = [e for e in all_rows if e['source'] == source]
        active_items = [e for e in analyzable_all if e['source'] == source]
        counts = Counter(e['kind'] for e in active_items)
        detected = is_source_detected(source, settings, conn, all_rows)
        if source != 'git':
            src_sessions = [s for (src, _), s in sessions.items() if src == source]
            total_sess_cnt = len(src_sessions)
            excluded_sess_cnt = sum(1 for s in src_sessions if s['exclusion_state'] == 'full')
            active_sess_cnt = total_sess_cnt - excluded_sess_cnt
        else:
            total_sess_cnt = excluded_sess_cnt = active_sess_cnt = 0
        source_stats.append({
            'source': source,
            'selected': source in selected_names and detected,
            'detected': detected,
            'sessions': active_sess_cnt,
            'total_sessions': total_sess_cnt,
            'excluded_sessions': excluded_sess_cnt,
            'user_messages': counts['user_prompt'],
            'assistant_messages': counts['assistant_message'],
            'events': len(active_items),
            'total_events': len(all_items),
        })
    detected_sources = {s['source'] for s in source_stats if s['detected']}
    selected_all = [e for e in all_rows if e['source'] in selected_names and e['source'] in detected_sources]
    selected = [e for e in analyzable_all if e['source'] in selected_names and e['source'] in detected_sources]

    tz = ZoneInfo(settings.timezone)
    by_day_selected = defaultdict(list)
    by_day_all = defaultdict(list)
    for e in all_rows:
        try:
            ev_day = str(datetime.fromisoformat(e['occurred_at'].replace('Z', '+00:00')).astimezone(tz).date())
        except Exception:
            ev_day = str(e['occurred_at'])[:10]
        by_day_all[ev_day].append(e)
        if e['source'] in selected_names and e['source'] in detected_sources and e['id'] not in excluded_row_ids:
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

    selected_session_items = [s for s in sessions.values() if s['source'] in selected_names and s['source'] in detected_sources]
    full_excluded_sessions = sum(1 for s in selected_session_items if s['exclusion_state'] == 'full')
    partial_excluded_sessions = sum(1 for s in selected_session_items if s['exclusion_state'] == 'partial')
    excluded_events_in_selected = sum(s['excluded_events'] for s in selected_session_items)

    result = {'date': target_day, 'start_date': str(s_date), 'end_date': str(e_date),
              'daily_counts': daily_counts, 'sources': source_stats,
              'sessions': [{**value, 'projects': sorted(value['projects'])} for value in sessions.values()],
              'session_count': len({(e['source'], e['session_id']) for e in selected if e['source'] != 'git'}),
              'total_session_count': len(selected_session_items),
              'excluded_session_count': full_excluded_sessions,
              'partial_excluded_session_count': partial_excluded_sessions,
              'excluded_event_count': excluded_events_in_selected,
              'user_messages': sum(e['kind'] == 'user_prompt' for e in selected),
              'total_user_messages': sum(e['kind'] == 'user_prompt' for e in selected_all),
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
    return stable(POLICY_VERSION,settings.timezone, sorted(settings.analysis_sources), settings.agent_prompts, settings.title_examples, settings.style_template, getattr(settings, 'task_details_mode', 'standard'), settings.activity_gap_minutes, settings.redact_patterns, settings.git_author.model_dump() if settings.git_author else None,{k:v.model_dump() for k,v in settings.git_author_overrides.items()}, sorted(e['id'] for e in rows if e['kind'] != 'session_end'))


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


def _format_task_details(raw_details: str, settings) -> str:
    mode = getattr(settings, 'task_details_mode', 'standard')
    if mode == 'none':
        return ''
    text = raw_details.strip() if isinstance(raw_details, str) else ''
    if mode == 'concise':
        first = re.split(r'[。！？!?；;\n]|\.(?:\s|$)', text, maxsplit=1)[0].strip()
        return first[:60]
    return redact(text, settings.redact_patterns)


def parse_result(text, valid_refs):
    try:
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
        value = json.loads(cleaned)
        if not isinstance(value, dict) or not isinstance(value.get('tasks'), list) or len(value['tasks']) > 100:
            raise ValueError()
        for task in value['tasks']:
            if not isinstance(task, dict) or not isinstance(task.get('title'), str) or not task['title'].strip() or len(task['title']) > 500:
                raise ValueError()
            if task.get('details') is None:
                task['details'] = ''
            if not isinstance(task.get('details'), str) or len(task['details']) > 20000:
                raise ValueError()
            if not isinstance(task.get('refs'), list) or not task['refs'] or any(not isinstance(r, str) or r not in valid_refs for r in task['refs']):
                raise ValueError()
            if task.get('project') is None:
                task['project'] = ''
            if task.get('category') is None:
                task['category'] = '任务'
            if not isinstance(task.get('project', ''), str) or not isinstance(task.get('category', '任务'), str):
                raise ValueError()
        if value.get('summary') is None:
            value['summary'] = ''
        if not isinstance(value.get('summary', ''), str):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError):
        raise model_provider.ModelError('AI 返回格式或证据引用无效，未替换原任务；请重新分析或更换模型') from None


_UNASSIGNED_PROJECTS = {"", "未归属项目", "空", "其他", "其它", "None", "none", "null"}


def _load_enterprise_catalog(conn, settings) -> list[dict]:
    ent_id = getattr(getattr(settings, "gitee_enterprise", None), "enterprise_id", 0) or 0
    if not ent_id:
        return []
    cached = conn.execute(
        select(db.integrations.c.value).where(db.integrations.c.id == f"gitee-enterprise-programs:{ent_id}")
    ).scalar() or {}
    items = cached.get("items", []) if isinstance(cached, dict) else []
    return [p for p in items if isinstance(p, dict)]


def _has_blank_program(catalog: list[dict], settings) -> bool:
    for r in catalog:
        if str(r.get("name") or "").strip() == "空" or str(r.get("ident") or "").strip() == "空":
            return True
    return any(str(p or "").strip() == "空" for p in (getattr(settings, "project_options", []) or []))


def _default_unassigned_project(catalog: list[dict], settings) -> str:
    return "空" if _has_blank_program(catalog, settings) else "未归属项目"


def _find_shanghai_program_name(catalog: list[dict], settings) -> str:
    rules = getattr(getattr(settings, "gitee_enterprise", None), "rules", []) or []
    by_id = {r.get("id"): str(r.get("name") or "").strip() for r in catalog if r.get("id") and r.get("name")}
    for rule in rules:
        pat = str(getattr(rule, "pattern", "") or "").strip()
        if "上海工程" in pat:
            mapped = by_id.get(getattr(rule, "program_id", None), "")
            if mapped and "2026" not in mapped:
                return mapped
    for r in catalog:
        name = str(r.get("name") or "").strip()
        ident = str(r.get("ident") or "").strip()
        if ("上海工程" in name or "上海工程" in ident) and "2026" not in name:
            return name
    for opt in (getattr(settings, "project_options", []) or []):
        opt_str = str(opt or "").strip()
        if "上海工程" in opt_str and "2026" not in opt_str:
            return opt_str
    return "上海工程"


def _match_enterprise_program_for_key(key: str, catalog: list[dict], settings) -> str:
    if not key or key in _UNASSIGNED_PROJECTS:
        return ""
    norm_key = re.sub(r"[\W_]+", "", key.casefold())
    if not norm_key:
        return ""
    rules = getattr(getattr(settings, "gitee_enterprise", None), "rules", []) or []
    by_id = {r.get("id"): str(r.get("name") or "").strip() for r in catalog if r.get("id") and r.get("name")}
    for rule in rules:
        pat = str(getattr(rule, "pattern", "") or "").strip()
        if not pat:
            continue
        norm_pat = re.sub(r"[\W_]+", "", pat.casefold())
        if norm_pat and (norm_key == norm_pat or norm_key.startswith(norm_pat)):
            mapped = by_id.get(getattr(rule, "program_id", None), "")
            if mapped:
                return mapped
    exact = [
        str(r.get("name") or "").strip()
        for r in catalog
        if norm_key in (
            re.sub(r"[\W_]+", "", str(r.get("name") or "").casefold()),
            re.sub(r"[\W_]+", "", str(r.get("ident") or "").casefold()),
        )
        and str(r.get("name") or "").strip()
    ]
    if len(exact) == 1:
        return exact[0]
    if len(norm_key) >= 2:
        cands = [
            str(r.get("name") or "").strip()
            for r in catalog
            if (
                norm_key in re.sub(r"[\W_]+", "", str(r.get("name") or "").casefold())
                or norm_key in re.sub(r"[\W_]+", "", str(r.get("ident") or "").casefold())
            )
            and str(r.get("name") or "").strip()
        ]
        if len(cands) == 1:
            return cands[0]
    return ""


def _prepend_bracket_prefix(raw_title: str, pfx: str, bracket_style: str = "【】") -> str:
    clean_pfx = (pfx or "其他").strip() or "其他"
    open_b = bracket_style[0] if len(bracket_style) == 2 else "【"
    close_b = bracket_style[1] if len(bracket_style) == 2 else "】"
    content = (raw_title or "").strip()
    if content.startswith(clean_pfx):
        content = content[len(clean_pfx):].lstrip(" :：-_—·")
    return f"{open_b}{clean_pfx}{close_b}{content}"


def _resolve_ai_task_project_and_title(
    raw_title: str,
    ai_project: str,
    supporting: dict,
    settings,
    catalog: list[dict],
    meta: dict,
    *,
    allow_event_fallback: bool = True,
) -> tuple[str, str]:
    from . import matching

    title = redact((raw_title or "").strip(), settings.redact_patterns)[:500]
    ai_proj = str(ai_project or "").strip()
    bracket_style = meta.get("bracket_style") or "【】"
    has_pfx = matching.has_bracket_prefix(title)
    title_pfx = matching.extract_prefix_and_content({"title": title})[0] if has_pfx else ""

    ev_projects = {str(e.get("project") or "").strip() for e in supporting.values() if e.get("project")} if supporting else set()
    ev_project = ""
    if supporting:
        ev_project = next(
            (str(e.get("project") or "").strip() for e in supporting.values() if e.get("kind") == "user_prompt" and e.get("project")),
            next((str(p) for p in sorted(ev_projects) if p), ""),
        )

    # 【上海工程】强关联企业中的上海工程项目，不能被仓库目录 2026 / archive-system-2026 覆盖。
    if "上海工程" in title_pfx or "上海工程" in ai_proj or (not has_pfx and "上海工程" in title):
        resolved_proj = _find_shanghai_program_name(catalog, settings)
        resolved_title = title if has_pfx else _prepend_bracket_prefix(title, "上海工程", bracket_style)
        return resolved_title, resolved_proj

    # 显式【其他】或无项目归属：匹配企业中的「空」项目（无企业配置时保留「未归属项目」）。
    if title_pfx in ("其他", "其它") or ai_proj in ("其他", "其它", "空"):
        resolved_proj = _default_unassigned_project(catalog, settings)
        resolved_title = title if has_pfx else _prepend_bracket_prefix(title, "其他", bracket_style)
        return resolved_title, resolved_proj

    proj_prefixes = meta.get("project_prefixes") or {}
    known_opts = set(getattr(settings, "project_options", []) or [])

    if has_pfx:
        matched_ent = _match_enterprise_program_for_key(title_pfx, catalog, settings) or _match_enterprise_program_for_key(ai_proj, catalog, settings)
        if matched_ent:
            return title, matched_ent
        if ai_proj and ai_proj not in _UNASSIGNED_PROJECTS:
            if ai_proj in ev_projects or ai_proj in proj_prefixes or ai_proj in known_opts or not catalog:
                return title, ai_proj
        if allow_event_fallback and ev_project and ev_project not in _UNASSIGNED_PROJECTS:
            if not catalog:
                return title, ev_project
            ev_ent = _match_enterprise_program_for_key(ev_project, catalog, settings)
            ev_pfx = proj_prefixes.get(ev_project, "")
            if title_pfx and (title_pfx == ev_pfx or title_pfx in ev_project or ev_project in title_pfx):
                return title, ev_ent or ev_project
            return title, _default_unassigned_project(catalog, settings)
        if ai_proj and ai_proj not in _UNASSIGNED_PROJECTS:
            return title, ai_proj
        return title, _default_unassigned_project(catalog, settings)

    # 无方括号前缀时优先匹配企业项目 / 已配置映射，找不到匹配前缀或无项目时默认使用【其他】+「空」
    matched_ent = _match_enterprise_program_for_key(ai_proj, catalog, settings)
    if matched_ent:
        matched_pfx = proj_prefixes.get(matched_ent, "") or proj_prefixes.get(ai_proj, "")
        resolved_t = _prepend_bracket_prefix(title, matched_pfx, bracket_style) if (matched_pfx and matched_pfx not in ("其他", "其它")) else title
        return resolved_t, matched_ent
    if ai_proj and ai_proj not in _UNASSIGNED_PROJECTS:
        if not catalog:
            return (title, ai_proj) if (not allow_event_fallback or not ev_projects or ai_proj in ev_projects) else (title, ev_project or ai_proj)
        proj_pfx = proj_prefixes.get(ai_proj, "")
        if proj_pfx and proj_pfx not in ("其他", "其它"):
            mapped_from_pfx = _match_enterprise_program_for_key(proj_pfx, catalog, settings)
            return _prepend_bracket_prefix(title, proj_pfx, bracket_style), mapped_from_pfx or ai_proj
    if not catalog and allow_event_fallback and ev_project and ev_project not in _UNASSIGNED_PROJECTS:
        return title, ev_project
    return _prepend_bracket_prefix(title, "其他", bracket_style), _default_unassigned_project(catalog, settings)


def _build_project_hints(catalog: list[dict], meta: dict, settings) -> dict | None:
    program_names = [str(r.get("name") or "").strip() for r in catalog if r.get("name")]
    rules = getattr(getattr(settings, "gitee_enterprise", None), "rules", []) or []
    if not program_names and not rules:
        return None
    hints: dict[str, object] = {"default_prefix": "其他"}
    if _has_blank_program(catalog, settings):
        hints["default_project"] = "空"
    if program_names:
        hints["enterprise_programs"] = program_names[:40]
    shanghai = _find_shanghai_program_name(catalog, settings)
    if shanghai and (
        any("上海工程" in n for n in program_names)
        or any("上海工程" in getattr(r, "pattern", "") for r in rules)
    ):
        hints["strong_mappings"] = [{"prefix": "上海工程", "program": shanghai}]
    return hints


def pending_item(task, supporting, previously_deleted, matched_task=None):
    return {'fingerprint': task['fingerprint'], 'title': task['title'], 'details': task['details'],
            'project': task['project'], 'category': task['category'], 'status': task['status'],
            'confidence': task['confidence'], 'evidence_level': task['evidence_level'],
            'previously_deleted': previously_deleted, 'evidence_ids': sorted(supporting),
            'matched_task': matched_task}


def _available_fingerprint(conn, desired_fp: str, exclude_task_id: str | None = None) -> str:
    holder = conn.execute(
        select(db.tasks.c.id).where(db.tasks.c.fingerprint == desired_fp)
    ).scalar_one_or_none()
    if holder is None or holder == exclude_task_id:
        return desired_fp
    return f"{desired_fp}:r:{uuid4().hex[:8]}"


def _active_gitee_links_by_task(conn, task_ids: list[str], enterprise_id: int = 0) -> dict[str, dict]:
    if not task_ids:
        return {}
    rows = conn.execute(
        select(db.gitee_links).where(db.gitee_links.c.task_id.in_(task_ids))
    ).mappings().all()
    links: dict[str, dict] = {}
    for row in rows:
        if row['status'] == 'created' and row.get('issue_id') and (not enterprise_id or row['enterprise_id'] == enterprise_id):
            links[row['task_id']] = dict(row)
    return links


def _matched_task_payload(
    task: dict,
    link: dict | None = None,
    similarity: float = 1.0,
    overlap_count: int = 0,
    reason: str = '',
) -> dict:
    return {
        'id': task['id'],
        'title': task['title'],
        'project': task['project'],
        'confirmed_minutes': int(task.get('confirmed_minutes') or 0),
        'suggested_minutes': task.get('suggested_minutes'),
        'time_source': task.get('time_source') or 'manual',
        'edited': bool(task.get('edited')),
        'status': task.get('status') or 'changed',
        'gitee_issue_id': str(link['issue_id']) if link and link.get('issue_id') else '',
        'gitee_url': str(link.get('url') or '') if link else '',
        'similarity': round(float(similarity), 3),
        'evidence_overlap': int(overlap_count),
        'match_reason': reason or ('依据重叠' if overlap_count else '标题相似'),
    }


def _match_candidate_to_ai_task(
    candidate_title: str,
    candidate_project: str,
    candidate_fingerprint: str,
    supporting_ids: set[str],
    active_auto_tasks: list[dict],
    links_by_task: dict[str, dict],
    used_task_ids: set[str],
) -> tuple[dict | None, dict | None]:
    if not active_auto_tasks:
        return None, None
    for t in active_auto_tasks:
        if t.get('fingerprint') == candidate_fingerprint:
            t_ev_ids = {e['id'] for e in (t.get('evidence') or [])}
            return t, _matched_task_payload(
                t,
                links_by_task.get(t['id']),
                1.0,
                len(supporting_ids & t_ev_ids),
                '与已有 AI 生成工作项完全对应',
            )

    cand_pfx, cand_content = matching.extract_prefix_and_content({'title': candidate_title, 'project': candidate_project})
    best_task = None
    best_meta = None
    best_score = 0.0
    for allow_used in (False, True):
        for t in active_auto_tasks:
            if not allow_used and t['id'] in used_task_ids:
                continue
            t_ev_ids = {e['id'] for e in (t.get('evidence') or [])}
            overlap = len(supporting_ids & t_ev_ids)
            union = len(supporting_ids | t_ev_ids)
            ev_ratio = (overlap / union) if union else 0.0

            t_pfx, t_content = matching.extract_prefix_and_content({'title': t['title'], 'project': t['project']})
            content_sim, _, _, _ = matching.calculate_content_similarity(t_content, cand_content)
            pfx_status, _ = matching.check_prefix_match(t_pfx, cand_pfx)

            if pfx_status == 'conflict' and content_sim < 0.55 and overlap == 0:
                continue
            if overlap > 0 and (pfx_status != 'conflict' or content_sim >= 0.45):
                score = max(0.50, 0.50 * ev_ratio + 0.50 * content_sim + (0.10 if pfx_status == 'match' else 0.0))
                reason = '依据重叠与标题映射' if content_sim >= 0.30 else '依据记录重叠'
            elif content_sim >= 0.45 and pfx_status != 'conflict':
                if not t_ev_ids and not str(t.get('fingerprint') or '').startswith('daily-v2:') and content_sim < 0.85:
                    continue
                score = content_sim + (0.10 if pfx_status == 'match' else 0.0)
                reason = '标题与项目前缀匹配'
            else:
                continue

            if score > best_score:
                best_score = score
                best_task = t
                best_meta = _matched_task_payload(
                    t,
                    links_by_task.get(t['id']),
                    min(1.0, score),
                    overlap,
                    reason,
                )
        if best_task is not None:
            break
    return best_task, best_meta


def infer_day(records, refs, settings, reviewed, day, project_hints=None):
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
    if project_hints:
        common['project_hints'] = project_hints
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
            error=model_provider.ModelError(f"{exc}；本次已记录模型用量{prefix}{usage['total_tokens']:,} tokens（输入 {usage['prompt_tokens']:,} / 输出 {usage['completion_tokens']:,}）")
            if getattr(exc,'user_reason',''):
                error.user_reason=str(error)
            raise error from None


def _analyze(conn, day, usage_calls, start_day=None, end_day=None, target_minutes=None):
    from . import multiday
    from .service import get_settings, invalidate, list_tasks_in_range, now, state_for, upsert
    settings = get_settings(conn)
    binding = multiday.get_active_binding(conn, day)
    if binding:
        if start_day is not None or end_day is not None:
            exp_s, exp_e = multiday.normalize_range(start_day or day, end_day or day)
            if (exp_s, exp_e) != (binding['start_date'], binding['end_date']):
                raise ValueError(
                    f"当前已与 {binding['start_date']} ~ {binding['end_date']} 形成多天绑定，除非手动解绑，否则不能更改分析区间"
                )
        s_day = binding['start_date']
        e_day = binding['end_date']
        analysis_day = binding['anchor_date']
        effective_target = target_minutes or binding.get('target_minutes')
    else:
        s_day, e_day = multiday.normalize_range(start_day or day, end_day or day)
        if s_day != e_day:
            overlaps = multiday.find_overlapping_bindings(conn, s_day, e_day)
            if overlaps:
                b = overlaps[0]
                raise ValueError(
                    f"所选日期区间与现有多天绑定（{b['start_date']} ~ {b['end_date']}）重叠，请先手动解绑后再重新分析"
                )
        analysis_day = str(_as_date(day))
        effective_target = target_minutes
    rows, all_selected_rows, _ = analysis_rows(conn, s_day, settings, end_day=e_day)
    summary = overview(conn, s_day, settings, end_day=e_day, report_day=analysis_day)
    if not settings.llm_enabled:
        return {'created': 0, 'updated': 0, 'archived': 0, 'pending': 0, 'pending_tasks': [], 'auto_imported': False, 'analysis_required': True, 'warnings': ['已统计对话。请在设置中接通并开启 AI，再分析实际工作任务；不会按提问或提交自动生成任务。'], 'conversations': summary, 'merge_suggestions': [], 'binding': binding}
    if not any(e['kind'] in ('user_prompt', 'assistant_message') for e in rows):
        if any(e['kind'] in ('user_prompt', 'assistant_message') for e in all_selected_rows):
            msg = (
                f'所选来源在 {s_day} 至 {e_day} 期间的已采集对话已全部排除（不参与下次 AI 分析）；如需重新分析，请在“对话与来源”会话列表中恢复参与分析。'
                if str(s_day) != str(e_day)
                else '所选来源当天的已采集对话已全部排除（不参与下次 AI 分析）；如需重新分析，请在“对话与来源”会话列表中恢复参与分析。'
            )
        else:
            msg = f'所选来源在 {s_day} 至 {e_day} 期间没有可分析的对话。' if str(s_day) != str(e_day) else '所选来源在当天没有可分析的对话。'
        return {'created': 0, 'updated': 0, 'archived': 0, 'pending': 0, 'pending_tasks': [], 'auto_imported': False, 'warnings': [msg], 'conversations': summary, 'merge_suggestions': [], 'binding': binding}
    records, refs, segments = transcript(rows, settings)
    active_tasks = list_tasks_in_range(conn, s_day, e_day)
    ent_id = getattr(getattr(settings, 'gitee_enterprise', None), 'enterprise_id', 0) or 0
    links_by_task = _active_gitee_links_by_task(conn, [t['id'] for t in active_tasks], ent_id)
    protected = [t for t in active_tasks if t['origin'] != 'auto' or t['edited'] or (t['confirmed_minutes'] > 0 and t['time_source'] != 'ai_estimate') or t['id'] in links_by_task]
    protected_ids = {e['id'] for t in protected for e in t['evidence']}
    deleted_cond = (
        and_(db.tasks.c.date == analysis_day, db.tasks.c.deleted == True, db.tasks.c.edited == True)
        if s_day == e_day
        else and_(db.tasks.c.date >= s_day, db.tasks.c.date <= e_day, db.tasks.c.deleted == True, db.tasks.c.edited == True)
    )
    deleted = list(conn.execute(select(db.tasks).where(deleted_cond)).mappings())
    deleted_ids = {r for t in deleted for r in conn.execute(select(db.evidence.c.event_id).where(db.evidence.c.task_id == t['id'])).scalars()}
    reviewed = [
        {
            'title': t['title'],
            'project': t['project'],
            'decision': '用户已审核保留，不要重复生成' if t['origin'] != 'auto' else '此前 AI 已生成；可输出更新候选以便用户选择是否替换或新建',
        }
        for t in protected
    ]
    reviewed += [{'title': t['title'], 'project': t['project'], 'decision': '用户历史曾删除；请结合实施证据与操作历史综合推测置信度：若当天有充分确凿的代码修改、工具调用、提交或明确实施动作（排除误删可能），可作为高置信度候选重新列出；若缺乏实施证据或明确不再需要，则尊重删除决定，不生成'} for t in deleted]
    records, work_refs, scope, filter_requests = filter_work(records,refs,segments,settings)
    refs=work_refs
    date_label = f"{s_day} 至 {e_day}" if str(s_day) != str(e_day) else analysis_day
    initial_catalog = _load_enterprise_catalog(conn, settings)
    initial_meta = matching.get_prefix_metadata(conn, settings)
    project_hints = _build_project_hints(initial_catalog, initial_meta, settings)
    if records:
        result, requests = infer_day(records, refs, settings, reviewed, date_label, project_hints=project_hints)
    else:
        result, requests = {'summary':'所选日期范围内没有可自动计入的实际工作；私人、无关及不足时长的知识梳理已排除。','tasks':[]},0
    requests+=filter_requests
    # Model latency must never hold the database's writer lock. Re-read human edits atomically
    # after inference, and refuse stale input if collection/source selection changed meanwhile.
    conn.commit()
    conn.exec_driver_sql('BEGIN IMMEDIATE')
    current_settings = get_settings(conn)
    current_rows, _, _ = analysis_rows(conn, s_day, current_settings, end_day=e_day)
    if input_hash(current_rows, current_settings) != input_hash(rows, settings):
        raise model_provider.ModelError('分析期间对话或来源范围发生变化，原任务已保留；请重新分析')
    current_catalog = _load_enterprise_catalog(conn, current_settings)
    current_meta = matching.get_prefix_metadata(conn, current_settings)
    active_tasks = list_tasks_in_range(conn, s_day, e_day)
    cur_ent_id = getattr(getattr(current_settings, 'gitee_enterprise', None), 'enterprise_id', 0) or 0
    links_by_task = _active_gitee_links_by_task(conn, [t['id'] for t in active_tasks], cur_ent_id)
    report_confirmed = conn.execute(select(db.reports.c.confirmed).where(db.reports.c.date == analysis_day)).scalar_one_or_none()
    protected = [t for t in active_tasks if report_confirmed or t['origin'] != 'auto' or t['edited'] or (t['confirmed_minutes'] > 0 and t['time_source'] != 'ai_estimate') or t['id'] in links_by_task]
    protected_task_ids = {t['id'] for t in protected}
    manual_protected = [t for t in active_tasks if t['origin'] != 'auto']
    manual_protected_ids = {e['id'] for t in manual_protected for e in t['evidence']}
    auto_active_tasks = [t for t in active_tasks if t.get('origin') == 'auto']
    auto_protected_ids = {e['id'] for t in auto_active_tasks if t['id'] in protected_task_ids for e in t['evidence']}
    deleted = list(conn.execute(select(db.tasks).where(deleted_cond)).mappings())
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
    used_matched_ids = set()
    for cand_idx, task in enumerate(result['tasks'], start=1):
        supporting = {refs[r]['id']: refs[r] for r in task['refs']}
        for ref in task['refs']:
            for event in segments.get(ref, []):
                if event['id'] in set(scope['work_event_ids']):
                    supporting[event['id']] = event
        if not any(e['kind'] in ('user_prompt', 'tool_call', 'shell_command', 'git_commit') for e in supporting.values()):
            # Assistant replies inherit the work request of their session turn.
            for event_id in list(supporting):
                anchor=owners.get(event_id)
                if anchor:
                    supporting[refs[anchor]['id']]=refs[anchor]
        if not any(e['kind'] in ('user_prompt', 'tool_call', 'shell_command', 'git_commit') for e in supporting.values()):
            raise model_provider.ModelError('AI 候选只有回复文本，缺少工作请求或行为依据；原任务已保留')
        ev_dates = sorted({str(e.get('occurred_at') or '')[:10] for e in supporting.values() if e.get('occurred_at')})
        ev_date_label = '/'.join(ev_dates) if ev_dates else analysis_day
        if set(supporting) & manual_protected_ids:
            matched_prot = [
                f"第 #{idx + 1} 条「{pt['title']}」"
                for idx, pt in enumerate(active_tasks)
                if pt in manual_protected and ({e['id'] for e in pt['evidence']} & set(supporting))
            ]
            prot_desc = "、".join(matched_prot) if matched_prot else "已审核任务"
            warnings.append(
                f"【{ev_date_label} · AI 分析候选】已跳过第 #{cand_idx} 条与人工审核任务（今日工作清单 {prot_desc}）证据重叠的候选：{task['title']}"
            )
            continue
        resolved_title, project = _resolve_ai_task_project_and_title(
            task['title'], task.get('project', ''), supporting, current_settings, current_catalog, current_meta
        )
        signature = stable(project, resolved_title)
        if signature in used:
            continue
        used.add(signature)
        desired_fp = 'daily-v2:' + stable(analysis_day, sorted(supporting), resolved_title)
        matched_t, matched_meta = _match_candidate_to_ai_task(
            resolved_title,
            project,
            desired_fp,
            set(supporting),
            auto_active_tasks,
            links_by_task,
            used_matched_ids,
        )
        if matched_t is None and (set(supporting) & auto_protected_ids):
            for pt in auto_active_tasks:
                pt_ev = {e['id'] for e in (pt.get('evidence') or [])}
                overlap = len(pt_ev & set(supporting))
                if overlap and pt['id'] in protected_task_ids:
                    matched_t = pt
                    matched_meta = _matched_task_payload(pt, links_by_task.get(pt['id']), 0.6, overlap, '依据记录重叠')
                    break
        if matched_t is not None:
            used_matched_ids.add(matched_t['id'])
        # 与已删除任务证据重叠的候选进行置信度考量：仅在没有现成活跃 AI 项对应时作为曾删除候选提示。
        is_previously_deleted = bool(set(supporting) & deleted_ids) and matched_t is None
        has_behavior = any(e['kind'] in ('tool_call', 'shell_command', 'git_commit') for e in supporting.values())
        if is_previously_deleted and not has_behavior:
            warnings.append(
                f"【{ev_date_label} · AI 分析候选】已跳过第 #{cand_idx} 条历史曾删除且只有对话描述的弱证据候选：{task['title']}"
            )
            continue
        previous_deleted = None
        if is_previously_deleted:
            previous_deleted = conn.execute(
                select(db.tasks).where(and_(db.tasks.c.fingerprint == desired_fp, db.tasks.c.deleted == True))
            ).mappings().first()
        target_id = (
            matched_t['id']
            if (matched_t is not None and settings.auto_import_analysis and matched_t['id'] not in protected_task_ids)
            else (previous_deleted['id'] if previous_deleted else uuid4().hex)
        )
        unique_fp = (
            desired_fp
            if not settings.auto_import_analysis
            else _available_fingerprint(conn, desired_fp, exclude_task_id=target_id)
        )
        generated.append(({'id': target_id, 'date': analysis_day,
                           'title': resolved_title,
                           'details': _format_task_details(task['details'], settings), 'project': project,
                           'category': '任务',
                           'status': state_for(list(supporting.values())), 'origin': 'auto',
                           'confidence': '全天 AI 分析 · 曾被删除' if is_previously_deleted else '全天 AI 分析 · 待审核',
                           'evidence_level': '已有行为证据' if has_behavior else '只有对话描述',
                           'fingerprint': unique_fp, 'edited': False, 'deleted': False, 'is_todo': False, 'started_at': None, 'ended_at': None},
                          supporting, is_previously_deleted, matched_t, matched_meta))
    # Everything above is validated before replacing any drafts. The caller commits atomically.
    archived = 0
    pending_tasks = []
    imported = []
    if settings.auto_import_analysis:
        touched_ids = set()
        for task, supporting, previously_deleted, matched_t, matched_meta in generated:
            if previously_deleted:
                # 曾删除候选不直接写入今日清单：进入 pending_tasks，由前台专用弹窗打勾打叉确认后再导入
                pending_tasks.append(pending_item(task, supporting, True, matched_task=None))
                continue
            if matched_t is not None and matched_t['id'] in protected_task_ids:
                # 已人工修改、已填工时或已关联 Gitee 的 AI 项不自动覆盖，放入 pending_tasks 让用户选择是否替换或新建
                pending_tasks.append(pending_item(task, supporting, False, matched_task=matched_meta))
                warnings.append(
                    f"【{analysis_day} · AI 分析候选】新候选「{task['title']}」已映射至已有工作项「{matched_t['title']}」，请在「分析结果」中选择替换或保留原项新建。"
                )
                continue
            task_fp = _available_fingerprint(conn, task['fingerprint'], exclude_task_id=task['id'])
            task['fingerprint'] = task_fp
            imported.append(task)
            touched_ids.add(task['id'])
            upsert(conn, db.tasks, task, 'id')
            conn.execute(sqlite_insert(db.worklogs).values(task_id=task['id'], suggested_minutes=None, confirmed_minutes=0, is_confirmed=False).on_conflict_do_nothing())
            if matched_t is not None and matched_t['id'] == task['id']:
                conn.execute(delete(db.evidence).where(db.evidence.c.task_id == task['id']))
            for event in supporting.values():
                conn.execute(sqlite_insert(db.evidence).values(task_id=task['id'], event_id=event['id']).on_conflict_do_nothing())
        for t in active_tasks:
            if t['id'] not in protected_task_ids | touched_ids:
                conn.execute(update(db.tasks).where(db.tasks.c.id == t['id']).values(deleted=True))
                archived += 1
    else:
        # 默认只保存候选并携带 matched_task 映射关系，用户在“分析结果”中选择替换或新建后写入。
        seen_fps = set()
        for task, supporting, previously_deleted, _, matched_meta in generated:
            fp = task['fingerprint']
            while fp in seen_fps:
                fp = f"{task['fingerprint']}:c:{len(seen_fps)}"
            seen_fps.add(fp)
            task['fingerprint'] = fp
            pending_tasks.append(pending_item(task, supporting, previously_deleted, matched_task=matched_meta))
    usage = model_provider.usage_summary(usage_calls)
    snapshot = {'scope':scope, 'prompt_roles':['scope_filter','task_writer'], 'input_hash': input_hash(rows, settings), 'analyzed_at': now(), 'provider': settings.llm_provider,
                'usage': usage, 'pending_tasks': pending_tasks, 'auto_imported': settings.auto_import_analysis,
                'start_date': str(s_day), 'end_date': str(e_day), 'target_day': analysis_day, 'target_minutes': effective_target,
                'model': model_provider.status(settings)['model'], 'summary': redact(result.get('summary', ''), settings.redact_patterns),
                'session_count': summary['session_count'], 'user_messages': summary['user_messages'], 'assistant_messages': summary['assistant_messages'],
                'sources': settings.analysis_sources, 'tasks': len(generated), 'model_requests': requests, 'warnings': warnings}
    upsert(conn, db.analyses, {'date': analysis_day, 'snapshot': snapshot}, 'date')
    if str(s_day) != str(e_day):
        if not binding:
            binding = multiday.bind_days(conn, s_day, e_day, anchor_date=analysis_day, target_minutes=effective_target)
        elif effective_target and effective_target != binding.get('target_minutes'):
            binding = multiday.update_binding_target(conn, binding, effective_target)
            upsert(conn, db.reports, {'date': analysis_day, 'target_minutes': effective_target, 'confirmed': False}, 'date')
    elif effective_target:
        upsert(conn, db.reports, {'date': analysis_day, 'target_minutes': effective_target, 'confirmed': False}, 'date')
    if settings.auto_import_analysis and (imported or archived):
        invalidate(conn, analysis_day)
    from . import similarity
    sim_check = similarity.check_day_similarity(conn, analysis_day, settings=settings, force=False)
    return {'created': len(imported), 'updated': 0, 'archived': archived, 'date': analysis_day, 'binding': binding,
            'pending': len(pending_tasks), 'pending_tasks': pending_tasks, 'auto_imported': settings.auto_import_analysis,
            'warnings': warnings, 'summary': snapshot['summary'], 'model_requests': requests, 'usage': usage, 'conversations': overview(conn, s_day, settings, end_day=e_day, report_day=analysis_day), 'merge_suggestions': [],
            'similarity_check': sim_check}


def import_pending(conn, day, selections):
    from . import multiday
    from .service import get_settings, invalidate, list_tasks, now, upsert
    settings = get_settings(conn)
    canonical_day = multiday.canonical_day(conn, day)
    snapshot = conn.execute(select(db.analyses.c.snapshot).where(db.analyses.c.date == canonical_day)).scalar_one_or_none()
    if not snapshot or not snapshot.get('pending_tasks'):
        raise ValueError('没有待导入的分析结果，请先分析当天对话')
    pending = {item['fingerprint']: item for item in snapshot['pending_tasks']}
    unknown = [item.fingerprint for item in selections if item.fingerprint not in pending]
    if unknown:
        raise ValueError('分析结果已变化，请关闭窗口后重新打开分析结果')
    active_tasks = list_tasks(conn, canonical_day)
    active_by_id = {t['id']: t for t in active_tasks}
    active_auto_by_id = {t['id']: t for t in active_tasks if t.get('origin') == 'auto'}
    protected_task_ids = {
        t['id'] for t in active_tasks
        if t['origin'] != 'auto' or t['edited'] or (t['confirmed_minutes'] > 0 and t['time_source'] != 'ai_estimate')
    }
    catalog = _load_enterprise_catalog(conn, settings)
    meta = matching.get_prefix_metadata(conn, settings)
    upserts, warnings = [], []
    handled_fingerprints = set()
    replaced_target_ids = set()
    replaced_count = 0
    for selection in selections:
        candidate = pending[selection.fingerprint]
        sel_action = getattr(selection, 'action', None)
        if sel_action == 'skip':
            handled_fingerprints.add(selection.fingerprint)
            continue
        evidence_ids = list(candidate.get('evidence_ids', []))
        rows = {row['id']: dict(row) for row in conn.execute(select(db.events).where(db.events.c.id.in_(evidence_ids))).mappings()} if evidence_ids else {}
        if len(rows) != len(set(evidence_ids)):
            raise ValueError('部分依据记录已不存在，请重新分析当天对话')
        raw_title = selection.title if selection.title is not None else candidate['title']
        details = selection.details if selection.details is not None else candidate['details']
        raw_project = selection.project if selection.project is not None else candidate['project']
        title, project = _resolve_ai_task_project_and_title(
            raw_title, raw_project, rows, settings, catalog, meta, allow_event_fallback=False
        )
        status = selection.status or candidate['status']

        explicit_target = getattr(selection, 'target_task_id', None)
        matched_hint_id = (candidate.get('matched_task') or {}).get('id')
        resolved_mode = 'create'
        final_target_id = None

        if sel_action == 'create':
            resolved_mode = 'create'
        elif sel_action == 'replace' or (sel_action is None and explicit_target):
            candidate_target = explicit_target or matched_hint_id
            if candidate_target and candidate_target in active_by_id:
                if candidate_target not in active_auto_by_id:
                    raise ValueError('只能替换 AI 生成的工作项，不能覆盖人工或外部同步工作项；请改为“保留原项并新建”')
                if candidate_target in replaced_target_ids:
                    raise ValueError('同一已有工作项只能被一条候选替换，其余候选请改为“保留原项并新建”')
                resolved_mode = 'replace'
                final_target_id = candidate_target
                replaced_target_ids.add(candidate_target)
        elif sel_action is None and matched_hint_id and matched_hint_id in active_auto_by_id and matched_hint_id not in replaced_target_ids:
            resolved_mode = 'replace'
            final_target_id = matched_hint_id
            replaced_target_ids.add(matched_hint_id)

        if resolved_mode == 'replace' and final_target_id:
            prev_task = active_auto_by_id[final_target_id]
            task_id = final_target_id
            reset_worklog = False
            preserved_edited = bool(prev_task.get('edited'))
            replaced_count += 1
        else:
            deleted_match_id = None
            if candidate.get('previously_deleted'):
                deleted_match_id = conn.execute(
                    select(db.tasks.c.id).where(and_(db.tasks.c.fingerprint == candidate['fingerprint'], db.tasks.c.deleted == True))
                ).scalar_one_or_none()
            task_id = deleted_match_id or uuid4().hex
            reset_worklog = True
            preserved_edited = False

        safe_fp = _available_fingerprint(conn, candidate['fingerprint'], exclude_task_id=task_id)
        handled_fingerprints.add(selection.fingerprint)
        upserts.append((
            {
                'id': task_id,
                'date': canonical_day,
                'title': title,
                'details': _format_task_details(details, settings),
                'project': project or _default_unassigned_project(catalog, settings),
                'category': '任务',
                'status': status,
                'origin': 'auto',
                'confidence': '全天 AI 分析 · 待审核',
                'evidence_level': candidate.get('evidence_level', '已有行为证据'),
                'fingerprint': safe_fp,
                'edited': preserved_edited,
                'deleted': False,
                'is_todo': False,
                'started_at': None,
                'ended_at': None,
            },
            rows,
            resolved_mode,
            reset_worklog,
        ))
    archived = 0
    for t in active_tasks:
        # 仅清理未审核且未映射替换的旧规则草稿；保留用户先前 AI 生成或人工项，支持多次增量生成与并存。
        is_legacy_draft = not str(t.get('fingerprint') or '').startswith('daily-v2:')
        if is_legacy_draft and t['id'] not in protected_task_ids | replaced_target_ids:
            conn.execute(update(db.tasks).where(db.tasks.c.id == t['id']).values(deleted=True))
            archived += 1
    for task, rows, resolved_mode, reset_worklog in upserts:
        upsert(conn, db.tasks, task, 'id')
        if reset_worklog:
            # 导入即草稿 / 曾删除恢复的行重置工时，避免带出删除前的确认分钟数。
            conn.execute(
                sqlite_insert(db.worklogs)
                .values(task_id=task['id'], suggested_minutes=None, confirmed_minutes=0,
                        is_confirmed=False, time_locked=False, time_source='manual', time_reason='')
                .on_conflict_do_update(
                    index_elements=[db.worklogs.c.task_id],
                    set_={'suggested_minutes': None, 'confirmed_minutes': 0, 'is_confirmed': False,
                          'time_locked': False, 'time_source': 'manual', 'time_reason': ''},
                )
            )
        else:
            # 替换已有 AI 项时保留已有工时记录，仅在缺失 worklog 时补齐默认 0 分钟。
            conn.execute(
                sqlite_insert(db.worklogs)
                .values(task_id=task['id'], suggested_minutes=None, confirmed_minutes=0,
                        is_confirmed=False, time_locked=False, time_source='manual', time_reason='')
                .on_conflict_do_nothing()
            )
        if resolved_mode == 'replace':
            conn.execute(delete(db.evidence).where(db.evidence.c.task_id == task['id']))
        for event_id in rows:
            conn.execute(sqlite_insert(db.evidence).values(task_id=task['id'], event_id=event_id).on_conflict_do_nothing())
    remaining = [item for item in snapshot['pending_tasks'] if item['fingerprint'] not in handled_fingerprints]
    snapshot = {**snapshot, 'pending_tasks': remaining, 'imported_at': now(), 'imported_tasks': len(upserts)}
    upsert(conn, db.analyses, {'date': canonical_day, 'snapshot': snapshot}, 'date')
    if upserts or archived:
        invalidate(conn, canonical_day)
    from . import similarity
    sim_check = similarity.check_day_similarity(conn, canonical_day, settings=settings, force=False)
    return {'created': len(upserts), 'updated': replaced_count, 'replaced': replaced_count, 'archived': archived, 'remaining': len(remaining),
            'imported': sorted(handled_fingerprints), 'warnings': warnings, 'similarity_check': sim_check}
