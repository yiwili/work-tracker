"""Persistent exclusion rules for events and sessions excluded from future AI analysis."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def session_target_key(source: str, session_id: str) -> str:
    return f"{source.strip()}:{session_id.strip()}"


def exclude_events(
    conn,
    events: list[dict],
    *,
    reason: str = 'cleared_ai_task',
    note: str = '',
) -> int:
    """Persist event-level exclusions for non-git conversation events.

    Returns count of newly added event exclusions.
    """
    if not events:
        return 0
    created_at = _now_iso()
    clean_reason = (reason or 'cleared_ai_task').strip()[:100]
    clean_note = (note or '').strip()[:500]
    newly_added = 0
    seen_ids: set[str] = set()
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ev_id = str(ev.get('id') or '').strip()
        src = str(ev.get('source') or '').strip()
        sid = str(ev.get('session_id') or '').strip()
        kind = str(ev.get('kind') or '').strip()
        if not ev_id or ev_id in seen_ids:
            continue
        if not src or src == 'git' or kind == 'git_commit' or not sid:
            continue
        seen_ids.add(ev_id)
        stmt = (
            sqlite_insert(db.analysis_exclusions)
            .values(
                id=uuid4().hex,
                scope='event',
                target_key=ev_id,
                source=src,
                session_id=sid,
                event_id=ev_id,
                reason=clean_reason,
                note=clean_note,
                created_at=created_at,
            )
            .on_conflict_do_nothing(index_elements=['scope', 'target_key'])
        )
        res = conn.execute(stmt)
        if res.rowcount and res.rowcount > 0:
            newly_added += res.rowcount
    return newly_added


def exclude_session(
    conn,
    source: str,
    session_id: str,
    *,
    reason: str = 'manual_session_exclude',
    note: str = '',
) -> dict:
    """Persist a session-level exclusion so an entire conversation session is skipped."""
    src = (source or '').strip()
    sid = (session_id or '').strip()
    if not src or not sid:
        raise ValueError('会话来源与会话 ID 不能为空')
    if src == 'git':
        raise ValueError('Git 提交不支持按会话排除')
    tkey = session_target_key(src, sid)
    stmt = (
        sqlite_insert(db.analysis_exclusions)
        .values(
            id=uuid4().hex,
            scope='session',
            target_key=tkey,
            source=src,
            session_id=sid,
            event_id=None,
            reason=(reason or 'manual_session_exclude').strip()[:100],
            note=(note or '').strip()[:500],
            created_at=_now_iso(),
        )
        .on_conflict_do_nothing(index_elements=['scope', 'target_key'])
    )
    res = conn.execute(stmt)
    return {
        'excluded': True,
        'newly_excluded': bool(res.rowcount and res.rowcount > 0),
        'source': src,
        'session_id': sid,
    }


def restore_session(conn, source: str, session_id: str) -> dict:
    """Remove both session-level and event-level exclusions for (source, session_id)."""
    src = (source or '').strip()
    sid = (session_id or '').strip()
    if not src or not sid:
        raise ValueError('会话来源与会话 ID 不能为空')
    tkey = session_target_key(src, sid)
    removed = 0
    res_sess = conn.execute(
        delete(db.analysis_exclusions).where(
            and_(
                db.analysis_exclusions.c.scope == 'session',
                or_(
                    db.analysis_exclusions.c.target_key == tkey,
                    and_(
                        db.analysis_exclusions.c.source == src,
                        db.analysis_exclusions.c.session_id == sid,
                    ),
                ),
            )
        )
    )
    removed += int(res_sess.rowcount or 0)

    res_ev = conn.execute(
        delete(db.analysis_exclusions).where(
            and_(
                db.analysis_exclusions.c.scope == 'event',
                db.analysis_exclusions.c.source == src,
                db.analysis_exclusions.c.session_id == sid,
            )
        )
    )
    removed += int(res_ev.rowcount or 0)

    ev_ids = list(
        conn.execute(
            select(db.events.c.id).where(
                and_(db.events.c.source == src, db.events.c.session_id == sid)
            )
        ).scalars()
    )
    for start in range(0, len(ev_ids), 500):
        chunk = ev_ids[start:start + 500]
        if not chunk:
            continue
        res_extra = conn.execute(
            delete(db.analysis_exclusions).where(
                and_(
                    db.analysis_exclusions.c.scope == 'event',
                    or_(
                        db.analysis_exclusions.c.event_id.in_(chunk),
                        db.analysis_exclusions.c.target_key.in_(chunk),
                    ),
                )
            )
        )
        removed += int(res_extra.rowcount or 0)

    return {
        'restored': True,
        'removed_rules': removed,
        'source': src,
        'session_id': sid,
    }


def get_exclusion_sets(conn) -> tuple[set[str], set[tuple[str, str]]]:
    """Return (excluded_event_ids, excluded_sessions) from analysis_exclusions."""
    rows = conn.execute(
        select(
            db.analysis_exclusions.c.scope,
            db.analysis_exclusions.c.target_key,
            db.analysis_exclusions.c.source,
            db.analysis_exclusions.c.session_id,
            db.analysis_exclusions.c.event_id,
        )
    ).mappings().all()
    excluded_event_ids: set[str] = set()
    excluded_sessions: set[tuple[str, str]] = set()
    for row in rows:
        if row['scope'] == 'session':
            excluded_sessions.add((str(row['source'] or ''), str(row['session_id'] or '')))
        elif row['scope'] == 'event':
            eid = str(row.get('event_id') or row.get('target_key') or '').strip()
            if eid:
                excluded_event_ids.add(eid)
    return excluded_event_ids, excluded_sessions


def filter_excluded_rows(conn, rows: list[dict]) -> tuple[list[dict], dict]:
    """Filter excluded sessions and events from rows chronologically.

    When an excluded `user_prompt` is skipped, subsequent non-`user_prompt`
    events in that same `(source, session_id)` are also skipped until the next
    non-excluded `user_prompt` begins a fresh turn. Git events are never skipped
    by conversation exclusions.
    """
    excluded_event_ids, excluded_sessions = get_exclusion_sets(conn)
    if not excluded_event_ids and not excluded_sessions:
        return list(rows), {
            'excluded_event_ids': excluded_event_ids,
            'excluded_sessions': excluded_sessions,
            'excluded_row_ids': set(),
            'excluded_event_count': 0,
            'excluded_user_messages': 0,
        }

    ordered = sorted(
        rows,
        key=lambda e: (str(e.get('occurred_at') or ''), str(e.get('id') or '')),
    )
    turn_excluded: dict[tuple[str, str], bool] = {}
    excluded_row_ids: set[str] = set()
    excluded_user_messages = 0

    for ev in ordered:
        src = str(ev.get('source') or '')
        if src == 'git' or ev.get('kind') == 'git_commit':
            continue
        key = (src, str(ev.get('session_id') or ''))
        ev_id = str(ev.get('id') or '')
        kind = str(ev.get('kind') or '')

        if key in excluded_sessions:
            excluded_row_ids.add(ev_id)
            if kind == 'user_prompt':
                excluded_user_messages += 1
            continue

        if ev_id in excluded_event_ids:
            excluded_row_ids.add(ev_id)
            if kind == 'user_prompt':
                turn_excluded[key] = True
                excluded_user_messages += 1
            continue

        if kind == 'user_prompt':
            turn_excluded[key] = False
        elif turn_excluded.get(key):
            excluded_row_ids.add(ev_id)
            continue

    kept = [ev for ev in rows if str(ev.get('id') or '') not in excluded_row_ids]
    return kept, {
        'excluded_event_ids': excluded_event_ids,
        'excluded_sessions': excluded_sessions,
        'excluded_row_ids': excluded_row_ids,
        'excluded_event_count': len(excluded_row_ids),
        'excluded_user_messages': excluded_user_messages,
    }
