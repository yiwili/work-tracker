"""Persistent multi-day binding state for unified view, AI re-runs, manual entries, and todos."""
from __future__ import annotations

from datetime import date as Date, datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, insert, select, update

from . import db
from .git_tools import _as_date


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_range(day_a: Date | str, day_b: Date | str | None = None) -> tuple[str, str]:
    s = _as_date(day_a)
    e = _as_date(day_b) if day_b is not None else s
    if e < s:
        s, e = e, s
    return str(s), str(e)


def _row_to_dict(row) -> dict:
    return {
        'id': row['id'],
        'start_date': row['start_date'],
        'end_date': row['end_date'],
        'anchor_date': row['anchor_date'],
        'target_minutes': row['target_minutes'],
        'active': bool(row['active']),
        'created_at': row['created_at'],
        'unbound_at': row['unbound_at'],
    }


def get_active_binding(conn, day: Date | str) -> dict | None:
    d_str = str(_as_date(day))
    row = conn.execute(
        select(db.multiday_bindings)
        .where(
            and_(
                db.multiday_bindings.c.active == True,
                db.multiday_bindings.c.start_date <= d_str,
                db.multiday_bindings.c.end_date >= d_str,
            )
        )
        .order_by(db.multiday_bindings.c.created_at.desc())
    ).mappings().first()
    return _row_to_dict(row) if row else None


def find_overlapping_bindings(conn, start_date: Date | str, end_date: Date | str) -> list[dict]:
    s_str, e_str = normalize_range(start_date, end_date)
    rows = conn.execute(
        select(db.multiday_bindings)
        .where(
            and_(
                db.multiday_bindings.c.active == True,
                db.multiday_bindings.c.start_date <= e_str,
                db.multiday_bindings.c.end_date >= s_str,
            )
        )
        .order_by(db.multiday_bindings.c.created_at.desc())
    ).mappings().all()
    return [_row_to_dict(r) for r in rows]


def canonical_day(conn, day: Date | str) -> str:
    d_str = str(_as_date(day))
    binding = get_active_binding(conn, d_str)
    return binding['anchor_date'] if binding else d_str


def bound_dates(binding: dict) -> list[str]:
    s = _as_date(binding['start_date'])
    e = _as_date(binding['end_date'])
    days: list[str] = []
    cur = s
    count = 0
    while cur <= e and count < 90:
        days.append(str(cur))
        cur += timedelta(days=1)
        count += 1
    return days


def same_binding_group(conn, dates: list[Date | str]) -> dict | None:
    if not dates:
        return None
    norm = [str(_as_date(d)) for d in dates]
    binding = get_active_binding(conn, norm[0])
    if not binding:
        return None
    if all(binding['start_date'] <= d <= binding['end_date'] for d in norm):
        return binding
    return None


def update_binding_target(conn, binding: dict, target_minutes: int | None) -> dict:
    if target_minutes and target_minutes > 0:
        conn.execute(
            update(db.multiday_bindings)
            .where(db.multiday_bindings.c.id == binding['id'])
            .values(target_minutes=int(target_minutes))
        )
        binding = {**binding, 'target_minutes': int(target_minutes)}
    return binding


def bind_days(
    conn,
    start_date: Date | str,
    end_date: Date | str,
    anchor_date: Date | str | None = None,
    target_minutes: int | None = None,
) -> dict:
    from .service import get_settings, invalidate, upsert

    s_str, e_str = normalize_range(start_date, end_date)
    if s_str == e_str:
        raise ValueError('多天合并绑定至少需要选择连续两天或以上')
    span_days = (_as_date(e_str) - _as_date(s_str)).days + 1
    if span_days > 90:
        raise ValueError('多天绑定跨度不能超过 90 天')

    overlaps = find_overlapping_bindings(conn, s_str, e_str)
    for existing in overlaps:
        if existing['start_date'] == s_str and existing['end_date'] == e_str:
            if target_minutes and target_minutes > 0 and existing.get('target_minutes') != int(target_minutes):
                existing = update_binding_target(conn, existing, int(target_minutes))
                upsert(
                    conn,
                    db.reports,
                    {'date': existing['anchor_date'], 'target_minutes': int(target_minutes), 'confirmed': False},
                    'date',
                )
            return existing
        raise ValueError(
            f"所选日期区间与现有多天绑定（{existing['start_date']} ~ {existing['end_date']}）重叠，请先手动解绑后再重新绑定"
        )

    settings = get_settings(conn)
    anchor_str = str(_as_date(anchor_date)) if anchor_date else e_str
    if not (s_str <= anchor_str <= e_str):
        anchor_str = e_str

    if target_minutes and int(target_minutes) > 0:
        eff_target = int(target_minutes)
    else:
        existing_target = conn.execute(
            select(db.reports.c.target_minutes).where(db.reports.c.date == anchor_str)
        ).scalar_one_or_none()
        if existing_target and existing_target > settings.target_minutes:
            eff_target = int(existing_target)
        else:
            eff_target = min(1440 * 30, span_days * settings.target_minutes)

    record = {
        'id': uuid4().hex,
        'start_date': s_str,
        'end_date': e_str,
        'anchor_date': anchor_str,
        'target_minutes': eff_target,
        'active': True,
        'created_at': _now_iso(),
        'unbound_at': None,
    }
    conn.execute(insert(db.multiday_bindings).values(**record))
    upsert(conn, db.reports, {'date': anchor_str, 'target_minutes': eff_target, 'confirmed': False}, 'date')
    invalidate(conn, anchor_str)
    return record


def unbind_days(conn, day: Date | str | None = None, binding_id: str | None = None) -> dict | None:
    from .service import get_settings, invalidate

    target_row = None
    if binding_id:
        row = conn.execute(
            select(db.multiday_bindings).where(
                and_(db.multiday_bindings.c.id == binding_id, db.multiday_bindings.c.active == True)
            )
        ).mappings().first()
        target_row = _row_to_dict(row) if row else None
    elif day is not None:
        target_row = get_active_binding(conn, day)

    if not target_row:
        return None

    unbound_at = _now_iso()
    conn.execute(
        update(db.multiday_bindings)
        .where(db.multiday_bindings.c.id == target_row['id'])
        .values(active=False, unbound_at=unbound_at)
    )
    settings = get_settings(conn)
    dates = bound_dates(target_row)
    bound_target = target_row.get('target_minutes')
    for d_str in dates:
        row_rep = conn.execute(select(db.reports).where(db.reports.c.date == d_str)).mappings().first()
        if row_rep:
            patch = {'confirmed': False, 'confirmed_at': None}
            if bound_target and row_rep.get('target_minutes') == bound_target and bound_target != settings.target_minutes:
                patch['target_minutes'] = settings.target_minutes
            conn.execute(update(db.reports).where(db.reports.c.date == d_str).values(**patch))
    # Clear invalidated state across all bound days explicitly before returning
    conn.execute(
        update(db.worklogs)
        .where(db.worklogs.c.task_id.in_(select(db.tasks.c.id).where(db.tasks.c.date.in_(dates))))
        .values(is_confirmed=False)
    )
    target_row['active'] = False
    target_row['unbound_at'] = unbound_at
    return target_row
