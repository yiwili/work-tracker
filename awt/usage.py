"""Local AI token usage ledger and per-model period summaries.

Only token counts, model names and timestamps are stored; prompts, responses
and credentials never enter this table.
"""
import logging
from datetime import datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import db

PERIODS = ('day', 'week', 'month', 'year', 'all')
HISTORY_PREFIX = 'history:'
_engine = None


def bind(engine):
    global _engine
    _engine = engine


def record_call(call):
    """Persist one completed model request. Never raises into the model flow."""
    if _engine is None:
        return
    try:
        with _engine.begin() as conn:
            conn.execute(insert(db.model_usage).values(
                id=uuid4().hex,
                recorded_at=datetime.now(timezone.utc).isoformat(),
                model=(call.get('model') or '').strip() or '未知模型',
                purpose=call.get('purpose') or 'general',
                prompt_tokens=int(call.get('prompt_tokens') or 0),
                completion_tokens=int(call.get('completion_tokens') or 0),
                calls=1,
                estimated=bool(call.get('estimated')),
            ))
    except Exception:
        logging.getLogger('awt.usage').warning('模型用量写入失败，本次统计可能缺少记录', exc_info=True)


def backfill(engine):
    """Seed one aggregate row per saved analysis that predates this ledger.

    History rows are ignored by report() as soon as a real call is recorded on
    the same local day, so re-analysing an old date cannot double count it.
    """
    try:
        with engine.begin() as conn:
            rows = conn.execute(select(db.analyses)).mappings().all()
            for row in rows:
                snapshot = row['snapshot'] or {}
                totals = snapshot.get('usage') or {}
                if int(totals.get('total_tokens') or 0) <= 0:
                    continue
                recorded_at = snapshot.get('analyzed_at') or f"{row['date']}T00:00:00+00:00"
                if not isinstance(recorded_at, str):
                    recorded_at = str(recorded_at)
                values = {
                    'recorded_at': recorded_at,
                    'model': snapshot.get('model') or '未知模型',
                    'purpose': 'history',
                    'prompt_tokens': int(totals.get('prompt_tokens') or 0),
                    'completion_tokens': int(totals.get('completion_tokens') or 0),
                    'calls': max(1, int(totals.get('calls') or 1)),
                    'estimated': bool(totals.get('estimated')),
                }
                conn.execute(sqlite_insert(db.model_usage).values(
                    id=HISTORY_PREFIX + str(row['date']), **values,
                ).on_conflict_do_update(index_elements=['id'], set_=values))
    except Exception:
        logging.getLogger('awt.usage').warning('历史用量回填失败，统计可能不包含旧分析', exc_info=True)


def _local_date(stamp, tz):
    try:
        value = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(tz)).date()


def period_start(period, tz):
    today = datetime.now(ZoneInfo(tz)).date()
    if period == 'day':
        return today
    if period == 'week':
        return today - timedelta(days=today.weekday())
    if period == 'month':
        return today.replace(day=1)
    if period == 'year':
        return today.replace(month=1, day=1)
    return None


def report(conn, period, tz='Asia/Shanghai'):
    if period not in PERIODS:
        raise ValueError('unknown usage period')
    start = period_start(period, tz)
    query = select(db.model_usage)
    if start is not None:
        start_utc = datetime.combine(start, time.min, ZoneInfo(tz)).astimezone(timezone.utc).isoformat()
        query = query.where(db.model_usage.c.recorded_at >= start_utc)
    rows = conn.execute(query).mappings().all()
    live_dates = {_local_date(row['recorded_at'], tz) for row in rows if row['purpose'] != 'history'}
    rows = [row for row in rows if row['purpose'] != 'history'
            or _local_date(row['recorded_at'], tz) not in live_dates]
    models = {}
    prompt = completion = calls = 0
    estimated = False
    for row in rows:
        prompt_tokens = int(row['prompt_tokens'] or 0)
        completion_tokens = int(row['completion_tokens'] or 0)
        entry = models.setdefault(row['model'], {
            'model': row['model'], 'prompt_tokens': 0, 'completion_tokens': 0,
            'total_tokens': 0, 'calls': 0, 'estimated': False,
        })
        entry['prompt_tokens'] += prompt_tokens
        entry['completion_tokens'] += completion_tokens
        entry['total_tokens'] += prompt_tokens + completion_tokens
        entry['calls'] += int(row['calls'] or 1)
        entry['estimated'] = entry['estimated'] or bool(row['estimated'])
        prompt += prompt_tokens
        completion += completion_tokens
        calls += int(row['calls'] or 1)
        estimated = estimated or bool(row['estimated'])
    total = prompt + completion
    items = sorted(models.values(), key=lambda item: (-item['total_tokens'], item['model']))
    for item in items:
        item['share'] = round(item['total_tokens'] / total, 4) if total else 0
    return {
        'period': period,
        'start': str(start) if start else None,
        'prompt_tokens': prompt,
        'completion_tokens': completion,
        'total_tokens': total,
        'calls': calls,
        'estimated': estimated,
        'models': items,
    }
