"""Local/low-cost semantic compression for AI conversation memories."""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from . import db, model_provider, prompts
from .analysis_progress import emit
from .collectors import stable
from .models import CONVERSATION_SOURCES, ModelConnection, SOURCE_FAMILIES, Settings
from .time_allocation import _extract_json_object

COMPRESSION_POLICY_VERSION = 'memory-compress-v1'
COMPRESSION_KEY_ID = 'memory-compression'
BATCH_CHAR_LIMIT = 24000
MAX_TURN_ASSISTANT_CHARS = 4000
MAX_TURN_USER_CHARS = 6000

_KIND_LABELS = {
    'work': '工作实施',
    'learning': '知识梳理',
    'personal': '个人或无关',
    'mixed': '混合内容',
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def enabled_compression_sources(settings: Settings) -> set[str]:
    cfg = getattr(settings, 'memory_compression', None)
    if not cfg or not cfg.enabled:
        return set()
    selected = {s for s in (cfg.sources or []) if s in CONVERSATION_SOURCES}
    for legacy, variants in SOURCE_FAMILIES.items():
        if all(variant in selected for variant in variants):
            selected.add(legacy)
    return selected


def _build_custom_connection(cfg) -> ModelConnection:
    return ModelConnection(
        id=COMPRESSION_KEY_ID,
        label='语义压缩模型',
        provider=cfg.provider,
        endpoint=cfg.endpoint.strip(),
        protocol=cfg.protocol,
        key_env=(cfg.key_env or 'AWT_COMPRESSION_API_KEY').strip(),
        ccswitch_config_path=(cfg.ccswitch_config_path or '').strip(),
        model=(cfg.model or '').strip(),
    )


def compression_candidates(
    settings: Settings,
    override_cfg=None,
    override_key: str | None = None,
    override_connections: list[ModelConnection] | None = None,
) -> list[tuple[str, Settings, str | None]]:
    """Resolve primary and fallback proxy Settings for semantic compression."""
    cfg = override_cfg or getattr(settings, 'memory_compression', None)
    if cfg is None:
        raise model_provider.ModelError('语义压缩配置缺失')
    conns = list(override_connections if override_connections is not None else settings.llm_connections)
    base_settings = settings.model_copy(update={'llm_connections': conns}) if override_connections is not None else settings

    candidates: list[tuple[str, Settings, str | None]] = []
    primary_conn_id = ''
    if cfg.mode == 'connection':
        if not cfg.connection_id:
            raise model_provider.ModelError('请先选择用于语义压缩的已有模型连接')
        conn = next((c for c in conns if c.id == cfg.connection_id), None)
        if conn is None:
            raise model_provider.ModelError('所选的语义压缩模型连接不存在，可能已被删除')
        chosen_model = (cfg.connection_model or '').strip() or conn.model
        if conn.provider != 'ccswitch' and not chosen_model:
            raise model_provider.ModelError('请先为语义压缩模型连接选择或填写模型名称')
        proxy = model_provider.connection_settings(base_settings, conn, model_override=chosen_model)
        primary_conn_id = conn.id
        candidates.append((conn.id, proxy, None))
    else:
        if cfg.provider != 'ccswitch':
            if not cfg.endpoint.strip():
                raise model_provider.ModelError('请先填写语义压缩模型接口地址')
            if not cfg.model.strip():
                raise model_provider.ModelError('请先填写语义压缩模型名称')
        synthetic = _build_custom_connection(cfg)
        proxy = model_provider.connection_settings(base_settings, synthetic)
        candidates.append((COMPRESSION_KEY_ID, proxy, override_key))

    for fb_id in (cfg.fallback_connection_ids or []):
        if not fb_id or fb_id == primary_conn_id:
            continue
        fb_conn = next((c for c in conns if c.id == fb_id), None)
        if fb_conn is None:
            continue
        if fb_conn.provider != 'ccswitch' and not fb_conn.model:
            continue
        candidates.append((fb_conn.id, model_provider.connection_settings(base_settings, fb_conn), None))
    return candidates


def _primary_model_name(settings: Settings) -> str:
    cfg = settings.memory_compression
    if cfg.mode == 'connection':
        conn = next((c for c in settings.llm_connections if c.id == cfg.connection_id), None)
        return ((cfg.connection_model or '').strip() or (conn.model if conn else '') or '')
    return (cfg.model or '').strip()


def status(settings: Settings, conn=None) -> dict:
    cfg = settings.memory_compression
    storage = local_stats(conn) if conn is not None else None
    try:
        candidates = compression_candidates(settings)
        status_id, proxy, _ = candidates[0]
        resolved = model_provider.connection(proxy)
        info = {
            'enabled': bool(cfg.enabled),
            'mode': cfg.mode,
            'connection_id': cfg.connection_id,
            'connection_model': cfg.connection_model,
            'configured': True,
            'provider': proxy.llm_provider,
            'endpoint': resolved['endpoint'],
            'model': resolved['model'],
            'protocol': resolved['protocol'],
            'key_configured': True,
            'error': '',
            'status': model_provider.connection_status(status_id),
        }
    except model_provider.ModelError as exc:
        if cfg.mode == 'custom':
            try:
                _, data = model_provider._vault()
            except model_provider.ModelError:
                data = {}
            key_ok = (
                cfg.provider == 'ccswitch'
                or model_provider.is_local_endpoint(cfg.endpoint)
                or COMPRESSION_KEY_ID in (data or {})
                or bool(cfg.key_env and os.environ.get(cfg.key_env))
            )
            endpoint = cfg.endpoint if cfg.provider != 'ccswitch' else ''
            model = cfg.model
            provider = cfg.provider
            protocol = cfg.protocol
            st = model_provider.connection_status(COMPRESSION_KEY_ID)
        else:
            conn_obj = next((c for c in settings.llm_connections if c.id == cfg.connection_id), None)
            key_ok = bool(conn_obj and model_provider._key_configured(conn_obj))
            endpoint = conn_obj.endpoint if conn_obj else ''
            model = cfg.connection_model or (conn_obj.model if conn_obj else '')
            provider = conn_obj.provider if conn_obj else cfg.provider
            protocol = conn_obj.protocol if conn_obj else cfg.protocol
            st = model_provider.connection_status(cfg.connection_id) if cfg.connection_id else model_provider.connection_status(COMPRESSION_KEY_ID)
        info = {
            'enabled': bool(cfg.enabled),
            'mode': cfg.mode,
            'connection_id': cfg.connection_id,
            'connection_model': cfg.connection_model,
            'configured': False,
            'provider': provider,
            'endpoint': endpoint,
            'model': model,
            'protocol': protocol,
            'key_configured': key_ok,
            'error': str(exc),
            'status': st,
        }
    if storage is not None:
        info['storage'] = storage
    return info


def test_compression_model(
    settings: Settings,
    override_cfg=None,
    override_key: str | None = None,
    override_connections: list[ModelConnection] | None = None,
) -> dict:
    candidates = compression_candidates(
        settings,
        override_cfg=override_cfg,
        override_key=override_key,
        override_connections=override_connections,
    )
    status_id, proxy, key = candidates[0]
    record_id = COMPRESSION_KEY_ID if override_cfg is None and status_id == COMPRESSION_KEY_ID else (status_id if override_cfg is None else '')
    try:
        res = model_provider.test_connection(proxy, key=key)
    except model_provider.ModelError as exc:
        if record_id:
            model_provider.record_status(record_id, 'test', False, str(exc))
        raise
    if record_id:
        model_provider.record_status(record_id, 'test', True, res['message'])
    return {**res, 'model': res.get('model') or proxy.llm_model, 'provider': proxy.llm_provider}


def complete_with_fallbacks(settings: Settings, system: str, text: str, *, max_tokens: int = 32768) -> tuple[str, str]:
    candidates = compression_candidates(settings)
    last_exc: model_provider.ModelError | None = None
    for idx, (status_id, proxy, key) in enumerate(candidates):
        try:
            answer = model_provider.complete(
                proxy,
                system,
                text,
                max_tokens=max_tokens,
                purpose='memory_compressor',
                key=key,
            )
            return answer, proxy.llm_model
        except model_provider.ModelError as exc:
            last_exc = exc
            if idx + 1 < len(candidates):
                next_model = candidates[idx + 1][1].llm_model or '备用连接'
                logging.getLogger('awt.compression').warning(
                    '压缩模型 %s 调用失败，切换备用接口 %s: %s',
                    proxy.llm_model,
                    next_model,
                    exc,
                )
                emit(f'主压缩模型失败，尝试备用压缩接口 {next_model}')
                continue
            raise
    assert last_exc is not None
    raise last_exc


def local_stats(conn, limit: int = 12) -> dict:
    totals = conn.execute(
        select(
            func.count(db.compressed_memories.c.id).label('count'),
            func.coalesce(func.sum(db.compressed_memories.c.raw_chars), 0).label('raw_chars'),
            func.coalesce(func.sum(db.compressed_memories.c.compressed_chars), 0).label('compressed_chars'),
        )
    ).mappings().one()
    raw_chars = int(totals['raw_chars'] or 0)
    compressed_chars = int(totals['compressed_chars'] or 0)
    saved_chars = max(0, raw_chars - compressed_chars)
    saved_ratio = round(saved_chars / raw_chars, 3) if raw_chars > 0 else 0.0
    rows = conn.execute(
        select(
            db.compressed_memories.c.id,
            db.compressed_memories.c.source,
            db.compressed_memories.c.session_id,
            db.compressed_memories.c.date_key,
            db.compressed_memories.c.model,
            db.compressed_memories.c.raw_chars,
            db.compressed_memories.c.compressed_chars,
            db.compressed_memories.c.raw_events_count,
            db.compressed_memories.c.summary_text,
            db.compressed_memories.c.updated_at,
        )
        .order_by(db.compressed_memories.c.updated_at.desc())
        .limit(limit)
    ).mappings().all()
    return {
        'total_records': int(totals['count'] or 0),
        'total_raw_chars': raw_chars,
        'total_compressed_chars': compressed_chars,
        'saved_chars': saved_chars,
        'saved_ratio': saved_ratio,
        'recent': [dict(r) for r in rows],
    }


def clear_cache(conn, *, date_key: str | None = None, source: str | None = None) -> int:
    stmt = delete(db.compressed_memories)
    if date_key:
        stmt = stmt.where(db.compressed_memories.c.date_key == date_key)
    if source:
        stmt = stmt.where(db.compressed_memories.c.source == source)
    return int(conn.execute(stmt).rowcount or 0)


def _clip(text: str, limit: int) -> str:
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    return text[:limit] + '…(已截断)'


def _merge_activities(records_list: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    for rec in records_list:
        for act in rec.get('activities', []):
            key = (act['project'], act['tool'])
            bucket = merged.setdefault(
                key,
                {'project': act['project'], 'tool': act['tool'], 'refs': [], 'states': {}},
            )
            for r in act['refs']:
                if r not in bucket['refs']:
                    bucket['refs'].append(r)
            for st, cnt in act['states'].items():
                bucket['states'][st] = bucket['states'].get(st, 0) + cnt
    return list(merged.values())


def _parse_batch_memories(text: str, batch_turns: list[dict]) -> list[dict]:
    """Normalize compression model output into disjoint memory items covering every turn in the batch."""
    valid_turns = {t['turn']: t for t in batch_turns}
    turn_order = [t['turn'] for t in batch_turns]
    parsed_items: list[dict] = []
    claimed: set[int] = set()

    try:
        obj = _extract_json_object(text)
        raw_memories = obj.get('memories') if isinstance(obj, dict) else None
        if isinstance(raw_memories, list):
            for entry in raw_memories:
                if not isinstance(entry, dict):
                    continue
                summary = str(entry.get('summary') or '').strip()
                if not summary:
                    continue
                kind = str(entry.get('kind') or 'work').strip().lower()
                if kind not in _KIND_LABELS:
                    kind = 'work'
                turns = []
                for raw_t in entry.get('turns') or []:
                    if type(raw_t) is int and raw_t in valid_turns and raw_t not in claimed:
                        claimed.add(raw_t)
                        turns.append(raw_t)
                    elif isinstance(raw_t, str) and raw_t.strip().isdigit():
                        num = int(raw_t.strip())
                        if num in valid_turns and num not in claimed:
                            claimed.add(num)
                            turns.append(num)
                if turns:
                    parsed_items.append({'turns': sorted(turns), 'kind': kind, 'summary': summary[:600]})
    except ValueError:
        pass

    if not parsed_items:
        # Plain-text response fallback for small local models that omit JSON wrapper
        cleaned = re.sub(r'<think\b[^>]*>.*?</think>', '', str(text or ''), flags=re.IGNORECASE | re.DOTALL).strip()
        cleaned = re.sub(r'^```(?:\w+)?\s*|\s*```$', '', cleaned).strip()
        if cleaned and not cleaned.startswith('{'):
            return [{'turns': turn_order, 'kind': 'work', 'summary': cleaned[:600]}]

    # Preserve any turns missed by the compressor model as condensed fallback segments
    unclaimed = [t for t in turn_order if t not in claimed]
    if unclaimed:
        for t_num in unclaimed:
            item = valid_turns[t_num]
            snippet = _clip(item['user'] or item['assistant'] or '工具执行活动', 180)
            parsed_items.append({'turns': [t_num], 'kind': 'work', 'summary': snippet})
    parsed_items.sort(key=lambda m: m['turns'][0])
    return parsed_items


def _build_turn_items(session_records: list[dict], refs: dict, segments: dict) -> tuple[list[dict], set[str]]:
    anchors = [r for r in session_records if r['kind'] in ('user_prompt', 'observed_activity')]
    covered_assistant_ids: set[str] = set()
    turn_items: list[dict] = []
    for idx, anchor in enumerate(anchors, start=1):
        anchor_ev = refs[anchor['ref']]
        seg_events = [anchor_ev, *segments.get(anchor['ref'], [])]
        unique_seg_events = list({e['id']: e for e in seg_events}.values())
        assistant_texts = []
        for ev in unique_seg_events:
            if ev['kind'] == 'assistant_message':
                covered_assistant_ids.add(ev['id'])
                if ev['id'] != anchor_ev['id'] and ev.get('summary'):
                    assistant_texts.append(ev['summary'])
        turn_items.append({
            'turn': idx,
            'anchor_ref': anchor['ref'],
            'anchor_event_id': anchor_ev['id'],
            'anchor_record': anchor,
            'segment_event_ids': [e['id'] for e in unique_seg_events],
            'time': anchor['time'],
            'project': anchor['project'],
            'user': _clip(anchor.get('text', ''), MAX_TURN_USER_CHARS),
            'assistant': _clip('\n'.join(assistant_texts), MAX_TURN_ASSISTANT_CHARS),
            'activities': [
                {'project': act['project'], 'tool': act['tool'], 'states': act['states']}
                for act in anchor.get('activities', [])
            ],
        })
    return turn_items, covered_assistant_ids


def _compress_session_segments(
    conn,
    source: str,
    session_id: str,
    session_records: list[dict],
    turn_items: list[dict],
    settings: Settings,
    date_key: str,
    force: bool,
) -> tuple[list[dict], bool, str, int]:
    from .service import upsert

    system_prompt = prompts.prompt(settings, 'memory_compressor')
    primary_model = _primary_model_name(settings)
    content_hash = stable(
        COMPRESSION_POLICY_VERSION,
        source,
        session_id,
        primary_model,
        system_prompt,
        [(t['anchor_event_id'], t['segment_event_ids'], t['user'], t['assistant']) for t in turn_items],
    )
    mem_id = stable(source, session_id, date_key)
    if not force:
        existing = conn.execute(
            select(db.compressed_memories).where(db.compressed_memories.c.id == mem_id)
        ).mappings().first()
        if existing and existing['content_hash'] == content_hash and isinstance(existing['segments_json'], list):
            return list(existing['segments_json']), True, existing['model'] or primary_model, 0

    batches: list[list[dict]] = []
    current_batch: list[dict] = []
    current_size = 0
    for item in turn_items:
        payload_item = {
            'turn': item['turn'],
            'time': item['time'],
            'project': item['project'],
            'user': item['user'],
            **({'assistant': item['assistant']} if item['assistant'] else {}),
            **({'activities': item['activities']} if item['activities'] else {}),
        }
        length = len(json.dumps(payload_item, ensure_ascii=False))
        if current_batch and current_size + length > BATCH_CHAR_LIMIT:
            batches.append(current_batch)
            current_batch, current_size = [], 0
        current_batch.append(item)
        current_size += length
    if current_batch:
        batches.append(current_batch)

    raw_chars = sum(len(json.dumps(r, ensure_ascii=False)) for r in session_records)
    turn_by_num = {t['turn']: t for t in turn_items}
    stored_segments: list[dict] = []
    used_model = primary_model
    model_requests = 0

    for b_idx, batch in enumerate(batches, start=1):
        emit(f'语义压缩记忆（{source} · {b_idx}/{len(batches)}）：{len(batch)} 轮对话')
        batch_payload = [
            {
                'turn': t['turn'],
                'time': t['time'],
                'project': t['project'],
                'user': t['user'],
                **({'assistant': t['assistant']} if t['assistant'] else {}),
                **({'activities': t['activities']} if t['activities'] else {}),
            }
            for t in batch
        ]
        answer, used_model = complete_with_fallbacks(
            settings,
            system_prompt,
            json.dumps({'session': f'{source}:{session_id}', 'turns': batch_payload}, ensure_ascii=False),
            max_tokens=32768,
        )
        model_requests += 1
        memories = _parse_batch_memories(answer, batch)
        for mem in memories:
            covered_turns = [turn_by_num[n] for n in mem['turns'] if n in turn_by_num]
            if not covered_turns:
                continue
            anchor_ids = [t['anchor_event_id'] for t in covered_turns]
            event_ids: list[str] = []
            seen_ev: set[str] = set()
            for t in covered_turns:
                for eid in t['segment_event_ids']:
                    if eid not in seen_ev:
                        seen_ev.add(eid)
                        event_ids.append(eid)
            projects = [t['project'] for t in covered_turns if t['project']]
            dominant_project = Counter(projects).most_common(1)[0][0] if projects else ''
            stored_segments.append({
                'anchor_event_id': anchor_ids[0],
                'covered_anchor_ids': anchor_ids,
                'covered_event_ids': event_ids,
                'kind': mem['kind'],
                'summary': mem['summary'],
                'project': dominant_project,
                'time': covered_turns[0]['time'],
                'raw_turns': len(covered_turns),
            })

    summary_lines = [
        f"[{_KIND_LABELS.get(seg['kind'], seg['kind'])}] {seg['summary']}"
        for seg in stored_segments
    ]
    summary_text = '\n'.join(summary_lines)
    compressed_chars = sum(len(seg['summary']) for seg in stored_segments)
    total_events = len({eid for t in turn_items for eid in t['segment_event_ids']})
    now_str = _now_iso()
    existing_created = conn.execute(
        select(db.compressed_memories.c.created_at).where(db.compressed_memories.c.id == mem_id)
    ).scalar_one_or_none()
    upsert(
        conn,
        db.compressed_memories,
        {
            'id': mem_id,
            'source': source,
            'session_id': session_id,
            'date_key': date_key,
            'content_hash': content_hash,
            'model': used_model,
            'raw_chars': raw_chars,
            'compressed_chars': compressed_chars,
            'raw_events_count': total_events,
            'segments_json': stored_segments,
            'summary_text': summary_text,
            'created_at': existing_created or now_str,
            'updated_at': now_str,
        },
        'id',
    )
    conn.commit()
    return stored_segments, False, used_model, model_requests


def _rebuild_session_records(
    sess: str,
    sess_records: list[dict],
    stored_segments: list[dict],
    turn_items: list[dict],
    covered_assistant_ids: set[str],
    refs: dict,
    segments: dict,
    new_segments: dict | None = None,
) -> list[dict]:
    event_id_to_ref = {ev['id']: ref for ref, ev in refs.items()}
    turn_by_anchor_eid = {t['anchor_event_id']: t for t in turn_items}
    rebuilt: list[dict] = []
    for r in sess_records:
        if r['kind'] == 'assistant_message' and refs[r['ref']]['id'] not in covered_assistant_ids:
            rebuilt.append(r)

    for seg in stored_segments:
        anchor_ref = event_id_to_ref.get(seg['anchor_event_id'])
        if not anchor_ref:
            continue
        covered_turns = [turn_by_anchor_eid[eid] for eid in seg.get('covered_anchor_ids', []) if eid in turn_by_anchor_eid]
        if not covered_turns:
            continue
        first_anchor_rec = covered_turns[0]['anchor_record']
        merged_activities = _merge_activities([t['anchor_record'] for t in covered_turns])

        if new_segments is not None:
            combined_events: dict[str, dict] = {}
            for t in covered_turns:
                t_ref = t['anchor_ref']
                combined_events[refs[t_ref]['id']] = refs[t_ref]
                for ev in segments.get(t_ref, []):
                    combined_events[ev['id']] = ev
                if t_ref != anchor_ref:
                    new_segments.pop(t_ref, None)
            new_segments[anchor_ref] = list(combined_events.values())

        kind_label = _KIND_LABELS.get(seg.get('kind', 'work'), '工作实施')
        text = f"[压缩记忆 · {kind_label} · 覆盖{len(covered_turns)}轮对话] {seg['summary']}"
        comp_rec = {
            'ref': anchor_ref,
            'session': sess,
            'time': seg.get('time') or first_anchor_rec['time'],
            'project': seg.get('project') or first_anchor_rec['project'],
            'kind': first_anchor_rec['kind'],
            'text': text,
            'compressed': True,
            'raw_turns': len(covered_turns),
        }
        if merged_activities:
            comp_rec['activities'] = merged_activities
        rebuilt.append(comp_rec)
    return rebuilt


def compress_transcript(
    conn,
    records: list[dict],
    refs: dict,
    segments: dict,
    settings: Settings,
    *,
    date_key: str,
    force: bool = False,
) -> tuple[list[dict], dict, dict]:
    """Compress enabled agents' conversation sessions, persist locally, and rewrite records for analysis."""
    enabled_sources = enabled_compression_sources(settings)
    if not enabled_sources or not records:
        return records, segments, {
            'enabled': bool(getattr(settings.memory_compression, 'enabled', False)),
            'compressed_sessions': 0,
            'cached_sessions': 0,
            'skipped_sessions': 0,
            'raw_chars': 0,
            'compressed_chars': 0,
            'model_requests': 0,
        }

    session_buckets: dict[str, list[dict]] = {}
    session_order: list[str] = []
    for rec in records:
        sess = rec['session']
        if sess not in session_buckets:
            session_buckets[sess] = []
            session_order.append(sess)
        session_buckets[sess].append(rec)

    new_segments = {k: list(v) for k, v in segments.items()}
    compressed_by_session: dict[str, list[dict]] = {}
    stats = {
        'enabled': True,
        'compressed_sessions': 0,
        'cached_sessions': 0,
        'skipped_sessions': 0,
        'raw_chars': 0,
        'compressed_chars': 0,
        'model_requests': 0,
    }

    for sess in session_order:
        source, _, session_id = sess.partition(':')
        sess_records = session_buckets[sess]
        if source == 'git' or source not in enabled_sources:
            stats['skipped_sessions'] += 1
            continue
        has_text = any(r['kind'] in ('user_prompt', 'assistant_message') and (r.get('text') or '').strip() for r in sess_records)
        if not has_text:
            stats['skipped_sessions'] += 1
            continue
        turn_items, covered_assistant_ids = _build_turn_items(sess_records, refs, segments)
        if not turn_items:
            stats['skipped_sessions'] += 1
            continue

        stored_segments, from_cache, _, reqs = _compress_session_segments(
            conn,
            source,
            session_id,
            sess_records,
            turn_items,
            settings,
            date_key,
            force,
        )
        stats['model_requests'] += reqs
        if from_cache:
            stats['cached_sessions'] += 1
        else:
            stats['compressed_sessions'] += 1

        rebuilt = _rebuild_session_records(
            sess,
            sess_records,
            stored_segments,
            turn_items,
            covered_assistant_ids,
            refs,
            segments,
            new_segments=new_segments,
        )

        raw_session_chars = sum(len(json.dumps(r, ensure_ascii=False)) for r in sess_records)
        comp_session_chars = sum(len(json.dumps(r, ensure_ascii=False)) for r in rebuilt)
        stats['raw_chars'] += raw_session_chars
        stats['compressed_chars'] += comp_session_chars
        compressed_by_session[sess] = rebuilt

    new_records: list[dict] = []
    emitted_sessions: set[str] = set()
    for rec in records:
        sess = rec['session']
        if sess not in compressed_by_session:
            new_records.append(rec)
        elif sess not in emitted_sessions:
            emitted_sessions.add(sess)
            new_records.extend(compressed_by_session[sess])
    return new_records, new_segments, stats


def _estimate_record_tokens(records_list: list[dict]) -> tuple[int, int]:
    total_chars = 0
    total_tokens = 0
    for rec in records_list:
        serialized = json.dumps(rec, ensure_ascii=False)
        total_chars += len(serialized)
        total_tokens += model_provider._estimate_tokens(serialized)
    return total_chars, total_tokens


def _empirical_summary_tokens(conn) -> dict[str, int]:
    """Read-only lookup of historical average summary tokens per segment by source."""
    if conn is None:
        return {}
    rows = conn.execute(
        select(
            db.compressed_memories.c.source,
            db.compressed_memories.c.segments_json,
        )
        .order_by(db.compressed_memories.c.updated_at.desc())
        .limit(60)
    ).mappings().all()
    buckets: dict[str, list[int]] = {}
    for row in rows:
        segs = row['segments_json']
        if not isinstance(segs, list):
            continue
        for seg in segs:
            if isinstance(seg, dict) and seg.get('summary'):
                toks = model_provider._estimate_tokens(str(seg['summary']))
                buckets.setdefault(row['source'], []).append(toks)
                buckets.setdefault('__all__', []).append(toks)
    return {
        key: max(35, min(160, sum(vals) // len(vals)))
        for key, vals in buckets.items()
        if vals
    }


def _predict_uncached_session(
    sess_records: list[dict],
    turn_items: list[dict],
    covered_assistant_ids: set[str],
    refs: dict,
    base_summary_tokens: int,
) -> tuple[int, int]:
    num_turns = len(turn_items)
    seg_count = 1 if num_turns <= 2 else max(1, min(num_turns, (num_turns + 3) // 4))
    raw_text_tokens = sum(
        model_provider._estimate_tokens((t.get('user') or '') + (t.get('assistant') or ''))
        for t in turn_items
    )
    per_seg_summary = min(base_summary_tokens, max(20, int(raw_text_tokens * 0.75)))
    merged_activities = _merge_activities([t['anchor_record'] for t in turn_items])
    act_chars = 0
    act_tokens = 0
    if merged_activities:
        act_json = json.dumps(merged_activities, ensure_ascii=False)
        act_chars = len(act_json)
        act_tokens = model_provider._estimate_tokens(act_json)

    orphan_records = [
        r for r in sess_records
        if r['kind'] == 'assistant_message' and refs[r['ref']]['id'] not in covered_assistant_ids
    ]
    orphan_chars, orphan_tokens = _estimate_record_tokens(orphan_records)

    # Envelope + "[压缩记忆 · 工作实施 · 覆盖N轮对话] " prefix per segment
    envelope_tokens_per_seg = 38
    envelope_chars_per_seg = 140
    pred_tokens = seg_count * (envelope_tokens_per_seg + per_seg_summary) + act_tokens + orphan_tokens
    pred_chars = seg_count * (envelope_chars_per_seg + per_seg_summary * 2) + act_chars + orphan_chars
    return pred_chars, pred_tokens


def predict_from_rows(
    conn,
    rows: list[dict],
    settings: Settings,
    *,
    date_key: str,
    override_cfg=None,
) -> dict:
    """Strictly read-only prediction of input tokens before vs after compression.

    Never invokes any LLM and never writes to the database, so actual compression
    output and final compressed token counts are completely unaffected.
    """
    from . import daily_analysis

    eff_settings = settings.model_copy(update={'memory_compression': override_cfg}) if override_cfg is not None else settings
    cfg = eff_settings.memory_compression
    enabled = bool(cfg.enabled)
    # Sources selected for compression (simulated even when master toggle is off so UI can preview savings)
    configured_sources = {s for s in (cfg.sources or []) if s in CONVERSATION_SOURCES}
    for legacy, variants in SOURCE_FAMILIES.items():
        if all(variant in configured_sources for variant in variants):
            configured_sources.add(legacy)

    if not rows:
        return {
            'date_key': date_key,
            'enabled': enabled,
            'raw_input_tokens': 0,
            'predicted_input_tokens': 0,
            'simulated_compressed_tokens': 0,
            'saved_tokens': 0,
            'saved_ratio': 0.0,
            'simulated_saved_tokens': 0,
            'simulated_saved_ratio': 0.0,
            'raw_chars': 0,
            'predicted_chars': 0,
            'session_count': 0,
            'will_compress_sessions': 0,
            'cached_sessions': 0,
            'per_source': [],
            'note': '仅作本地预测估算，不会触发模型调用，也不影响最终实际压缩的 token 数。',
        }

    records, refs, segments = daily_analysis.transcript(rows, eff_settings)
    session_buckets: dict[str, list[dict]] = {}
    session_order: list[str] = []
    for rec in records:
        sess = rec['session']
        if sess not in session_buckets:
            session_buckets[sess] = []
            session_order.append(sess)
        session_buckets[sess].append(rec)

    system_prompt = prompts.prompt(eff_settings, 'memory_compressor')
    primary_model = _primary_model_name(eff_settings)
    empirical = _empirical_summary_tokens(conn)
    global_summary_tokens = empirical.get('__all__', 90)

    total_raw_chars = 0
    total_raw_tokens = 0
    total_pred_chars = 0
    total_pred_tokens = 0
    total_sim_tokens = 0
    will_compress_sessions = 0
    cached_sessions = 0
    session_count = 0
    per_source_map: dict[str, dict] = {}

    for sess in session_order:
        source, _, session_id = sess.partition(':')
        sess_records = session_buckets[sess]
        raw_chars, raw_tokens = _estimate_record_tokens(sess_records)
        total_raw_chars += raw_chars
        total_raw_tokens += raw_tokens

        if source != 'git':
            session_count += 1
        src_bucket = per_source_map.setdefault(
            source,
            {
                'source': source,
                'sessions': 0,
                'compression_selected': source != 'git' and source in configured_sources,
                'raw_tokens': 0,
                'predicted_tokens': 0,
                'simulated_tokens': 0,
                'cached_sessions': 0,
            },
        )
        if source != 'git':
            src_bucket['sessions'] += 1
        src_bucket['raw_tokens'] += raw_tokens

        has_text = any(r['kind'] in ('user_prompt', 'assistant_message') and (r.get('text') or '').strip() for r in sess_records)
        turn_items, covered_assistant_ids = _build_turn_items(sess_records, refs, segments) if (source != 'git' and has_text) else ([], set())

        source_selected = source != 'git' and source in configured_sources and bool(turn_items)
        if not source_selected:
            sim_chars, sim_tokens = raw_chars, raw_tokens
            is_cached = False
        else:
            content_hash = stable(
                COMPRESSION_POLICY_VERSION,
                source,
                session_id,
                primary_model,
                system_prompt,
                [(t['anchor_event_id'], t['segment_event_ids'], t['user'], t['assistant']) for t in turn_items],
            )
            mem_id = stable(source, session_id, date_key)
            existing = conn.execute(
                select(db.compressed_memories).where(db.compressed_memories.c.id == mem_id)
            ).mappings().first() if conn is not None else None
            if existing and existing['content_hash'] == content_hash and isinstance(existing['segments_json'], list):
                rebuilt = _rebuild_session_records(
                    sess,
                    sess_records,
                    list(existing['segments_json']),
                    turn_items,
                    covered_assistant_ids,
                    refs,
                    segments,
                    new_segments=None,
                )
                sim_chars, sim_tokens = _estimate_record_tokens(rebuilt)
                is_cached = True
                cached_sessions += 1
                src_bucket['cached_sessions'] += 1
            else:
                base_tok = empirical.get(source, global_summary_tokens)
                sim_chars, sim_tokens = _predict_uncached_session(
                    sess_records,
                    turn_items,
                    covered_assistant_ids,
                    refs,
                    base_tok,
                )
                is_cached = False

        if enabled and source_selected:
            will_compress_sessions += 1
            pred_chars, pred_tokens = sim_chars, sim_tokens
        else:
            pred_chars, pred_tokens = raw_chars, raw_tokens

        total_pred_chars += pred_chars
        total_pred_tokens += pred_tokens
        total_sim_tokens += sim_tokens
        src_bucket['predicted_tokens'] += pred_tokens
        src_bucket['simulated_tokens'] += sim_tokens

    saved_tokens = total_raw_tokens - total_pred_tokens
    saved_ratio = round(saved_tokens / total_raw_tokens, 3) if total_raw_tokens > 0 else 0.0
    sim_saved_tokens = total_raw_tokens - total_sim_tokens
    sim_saved_ratio = round(sim_saved_tokens / total_raw_tokens, 3) if total_raw_tokens > 0 else 0.0
    return {
        'date_key': date_key,
        'enabled': enabled,
        'raw_input_tokens': total_raw_tokens,
        'predicted_input_tokens': total_pred_tokens,
        'simulated_compressed_tokens': total_sim_tokens,
        'saved_tokens': saved_tokens,
        'saved_ratio': saved_ratio,
        'simulated_saved_tokens': sim_saved_tokens,
        'simulated_saved_ratio': sim_saved_ratio,
        'raw_chars': total_raw_chars,
        'predicted_chars': total_pred_chars,
        'session_count': session_count,
        'will_compress_sessions': will_compress_sessions,
        'cached_sessions': cached_sessions,
        'per_source': list(per_source_map.values()),
        'note': '仅作本地预测估算，不会触发模型调用，也不影响最终实际压缩的 token 数。',
    }


def predict_day(
    conn,
    day,
    *,
    start_day=None,
    end_day=None,
    override_cfg=None,
    override_analysis_sources: list[str] | None = None,
) -> dict:
    from . import daily_analysis, multiday
    from .service import get_settings

    settings = get_settings(conn)
    if override_analysis_sources is not None:
        settings = settings.model_copy(update={'analysis_sources': list(dict.fromkeys(override_analysis_sources))})
    binding = multiday.get_active_binding(conn, day) if (start_day is None and end_day is None) else None
    s_day, e_day = multiday.normalize_range(
        start_day or (binding['start_date'] if binding else day),
        end_day or (binding['end_date'] if binding else day),
    )
    rows, _, _ = daily_analysis.analysis_rows(conn, s_day, settings, end_day=e_day)
    date_key = str(s_day) if s_day == e_day else f'{s_day}..{e_day}'
    return predict_from_rows(conn, rows, settings, date_key=date_key, override_cfg=override_cfg)


def compress_day(conn, day, start_day=None, end_day=None, force: bool = False) -> dict:
    """Explicitly compress and store local semantic memories for a day or date range."""
    from . import daily_analysis, multiday
    from .service import get_settings

    settings = get_settings(conn)
    if not settings.memory_compression.enabled:
        raise ValueError('请先在设置中开启对话记忆语义压缩')
    s_day, e_day = multiday.normalize_range(start_day or day, end_day or day)
    rows, _, _ = daily_analysis.analysis_rows(conn, s_day, settings, end_day=e_day)
    if not any(e['kind'] in ('user_prompt', 'assistant_message') for e in rows):
        return {
            'date_key': str(s_day) if s_day == e_day else f'{s_day}..{e_day}',
            'stats': {'enabled': True, 'compressed_sessions': 0, 'cached_sessions': 0, 'skipped_sessions': 0, 'raw_chars': 0, 'compressed_chars': 0, 'model_requests': 0},
            'storage': local_stats(conn),
        }
    records, refs, segments = daily_analysis.transcript(rows, settings)
    date_key = str(s_day) if s_day == e_day else f'{s_day}..{e_day}'
    _, _, stats = compress_transcript(conn, records, refs, segments, settings, date_key=date_key, force=force)
    return {
        'date_key': date_key,
        'stats': stats,
        'storage': local_stats(conn),
    }
