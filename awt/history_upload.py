"""Cloud history work items upload and preview service.

Handles:
1. Local history preview and source distribution statistics.
2. Idempotent, resumable batched upload of historical work items to the cloud server.
3. Preserves SQLite concurrency: network calls are executed completely outside of SQLite write locks.
"""
from datetime import datetime, timezone
from typing import Any, Callable
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import db
from .cloud_client import CloudClient

CURSOR_KEY = 'history_upload_cursor'


def get_upload_cursor(conn) -> dict[str, Any]:
    val = conn.execute(select(db.sync_meta.c.value).where(db.sync_meta.c.key == CURSOR_KEY)).scalar_one_or_none()
    if isinstance(val, dict):
        return {
            'last_id': str(val.get('last_id') or ''),
            'uploaded_count': int(val.get('uploaded_count') or 0),
            'completed': bool(val.get('completed', False)),
            'updated_at': str(val.get('updated_at') or ''),
        }
    return {
        'last_id': '',
        'uploaded_count': 0,
        'completed': False,
        'updated_at': '',
    }


def set_upload_cursor(conn, cursor: dict[str, Any]) -> None:
    stmt = sqlite_insert(db.sync_meta).values(key=CURSOR_KEY, value=cursor)
    conn.execute(stmt.on_conflict_do_update(index_elements=['key'], set_={'value': cursor}))


def history_preview(engine, client: CloudClient | None = None) -> dict[str, Any]:
    """Preview local history stats and current sync progress, optionally querying cloud summary."""
    with engine.connect() as conn:
        total_records = conn.execute(select(func.count()).select_from(db.history)).scalar_one()

        missing_date_count = conn.execute(
            select(func.count())
            .select_from(db.history)
            .where((db.history.c.work_date == None) | (db.history.c.work_date == ''))
        ).scalar_one()

        dated_row = conn.execute(
            select(func.min(db.history.c.work_date), func.max(db.history.c.work_date))
            .where((db.history.c.work_date != None) & (db.history.c.work_date != ''))
        ).first()

        sources_rows = conn.execute(
            select(db.history.c.source, func.count())
            .group_by(db.history.c.source)
        ).all()

        sources = {str(row[0] or 'unknown'): int(row[1]) for row in sources_rows}
        cursor = get_upload_cursor(conn)

    cloud_summary = None
    if client is not None:
        try:
            cloud_summary = client.cloud_history_summary()
        except Exception as exc:
            cloud_summary = {'error': str(exc)}

    return {
        'local': {
            'total_records': total_records,
            'missing_date_count': missing_date_count,
            'earliest_date': dated_row[0] if dated_row and dated_row[0] else None,
            'latest_date': dated_row[1] if dated_row and dated_row[1] else None,
            'sources': sources,
        },
        'cursor': cursor,
        'cloud': cloud_summary,
    }


def upload_history_batch(
    engine,
    client: CloudClient,
    batch_size: int = 200,
    force_restart: bool = False,
) -> dict[str, Any]:
    """Upload one batch of historical work items to the cloud.

    Reads next slice based on last_id from sync_meta.
    Performs network API call outside SQLite write transactions.
    Updates cursor upon success.
    """
    # 1. Read slice from DB in a short read-only transaction
    with engine.connect() as conn:
        if force_restart:
            cursor = {'last_id': '', 'uploaded_count': 0, 'completed': False, 'updated_at': ''}
        else:
            cursor = get_upload_cursor(conn)

        last_id = cursor['last_id']
        total_records = conn.execute(select(func.count()).select_from(db.history)).scalar_one()

        stmt = (
            select(db.history)
            .where(db.history.c.id > last_id)
            .order_by(db.history.c.id.asc())
            .limit(batch_size)
        )
        rows = [dict(r) for r in conn.execute(stmt).mappings()]

    total_batches = max(1, (total_records + batch_size - 1) // batch_size) if total_records > 0 else 1
    batch_index = cursor['uploaded_count'] // batch_size

    # 2. Check if already finished or empty
    if not rows:
        with engine.begin() as conn:
            cursor['completed'] = True
            cursor['updated_at'] = datetime.now(timezone.utc).isoformat()
            set_upload_cursor(conn, cursor)
        return {
            'completed': True,
            'batch_index': batch_index,
            'total_batches': total_batches,
            'items_in_batch': 0,
            'batch_result': {
                'batch_index': batch_index,
                'received': 0,
                'inserted': 0,
                'updated': 0,
                'unchanged': 0,
                'failed': 0,
            },
            'uploaded_count': cursor['uploaded_count'],
            'total_records': total_records,
        }

    # 3. Format items (outside DB lock)
    items = []
    for r in rows:
        wd = r.get('work_date')
        if not wd or not str(wd).strip():
            wd_val = None
        else:
            wd_val = str(wd).strip()

        items.append({
            'id': str(r['id'])[:64],
            'title': str(r.get('title') or '')[:500],
            'details': str(r.get('details') or ''),
            'project': str(r.get('project') or '')[:100],
            'work_date': wd_val,
            'source': str(r.get('source') or 'file')[:50],
            'source_id': str(r.get('source_id') or '')[:255],
            'url': str(r.get('url') or '')[:1000],
            'imported_at': r.get('imported_at') or None,
        })

    # 4. Make cloud network call (no DB locks held)
    batch_result = client.import_history_batch(
        batch_index=batch_index,
        total_batches=total_batches,
        items=items,
    )

    # 5. Persist updated cursor in a fast write transaction
    new_last_id = rows[-1]['id']
    new_uploaded_count = cursor['uploaded_count'] + len(rows)
    is_completed = (len(rows) < batch_size) or (new_uploaded_count >= total_records)

    new_cursor = {
        'last_id': new_last_id,
        'uploaded_count': new_uploaded_count,
        'completed': is_completed,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }

    with engine.begin() as conn:
        set_upload_cursor(conn, new_cursor)

    return {
        'completed': is_completed,
        'batch_index': batch_index,
        'total_batches': total_batches,
        'items_in_batch': len(rows),
        'batch_result': batch_result,
        'uploaded_count': new_uploaded_count,
        'total_records': total_records,
    }


def upload_all_history(
    engine,
    client: CloudClient,
    batch_size: int = 200,
    force_restart: bool = False,
    max_batches: int = 500,
    on_batch: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Sequentially upload all batches until complete, safely handling interrupts."""
    batches_processed = 0
    total_received = 0
    total_inserted = 0
    total_updated = 0
    total_unchanged = 0
    total_failed = 0

    first = True
    while batches_processed < max_batches:
        step = upload_history_batch(
            engine,
            client,
            batch_size=batch_size,
            force_restart=(force_restart and first),
        )
        first = False
        batches_processed += 1

        res = step.get('batch_result') or {}
        total_received += res.get('received', 0)
        total_inserted += res.get('inserted', 0)
        total_updated += res.get('updated', 0)
        total_unchanged += res.get('unchanged', 0)
        total_failed += res.get('failed', 0)

        if on_batch:
            on_batch(step)

        if step['completed']:
            break

    return {
        'completed': step['completed'] if batches_processed > 0 else True,
        'batches_processed': batches_processed,
        'uploaded_count': step['uploaded_count'] if batches_processed > 0 else 0,
        'total_records': step['total_records'] if batches_processed > 0 else 0,
        'received': total_received,
        'inserted': total_inserted,
        'updated': total_updated,
        'unchanged': total_unchanged,
        'failed': total_failed,
    }
