"""Desktop Cloud Sync Engine.

Implements protocol §2, §3, §4 and first-time migration (§6).
Maintains local shadow, dirty tracking, three-way merge, and conflict resolution.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
from threading import Event, Lock, RLock, Thread
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import db, gitee, service
from .cloud_client import CloudClient, CloudError, CloudUnavailable
from .collectors import stable
from .models import Settings

logger = logging.getLogger('awt.cloud_sync')
sync_lock = RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def suppressed(conn):
    """Suppresses local change capture triggers during remote apply."""
    conn.execute(sqlite_insert(db.sync_suppress).values(id=1).on_conflict_do_nothing())
    try:
        yield
    finally:
        conn.execute(delete(db.sync_suppress).where(db.sync_suppress.c.id == 1))


def get_meta(conn, key: str, default: Any = None) -> Any:
    val = conn.execute(select(db.sync_meta.c.value).where(db.sync_meta.c.key == key)).scalar_one_or_none()
    return val if val is not None else default


def set_meta(conn, key: str, value: Any) -> None:
    stmt = sqlite_insert(db.sync_meta).values(key=key, value=value)
    conn.execute(stmt.on_conflict_do_update(index_elements=['key'], set_={'value': value}))


# --- Entity Conversion (Local <-> Protocol §2) ---

def mapping_id(mode: str, pattern: str) -> str:
    h = hashlib.sha1(f"{mode}\n{pattern}".encode()).hexdigest()[:24]
    return f"map_{h}"


def local_work_item(conn, task_id: str) -> dict | None:
    row = conn.execute(
        select(db.tasks, db.worklogs.c.suggested_minutes, db.worklogs.c.confirmed_minutes,
               db.worklogs.c.time_locked, db.worklogs.c.time_source, db.worklogs.c.time_reason)
        .join(db.worklogs, db.worklogs.c.task_id == db.tasks.c.id)
        .where(db.tasks.c.id == task_id)
    ).mappings().first()

    if not row:
        return None

    return {
        'date': str(row['date']),
        'title': str(row['title'] or ''),
        'details': str(row['details'] or ''),
        'project': str(row['project'] or '未归属项目'),
        'category': str(row['category'] or '其他'),
        'status': str(row['status'] or 'requested'),
        'origin': str(row['origin'] or 'manual'),
        'suggested_minutes': row['suggested_minutes'],
        'confirmed_minutes': int(row['confirmed_minutes'] or 0),
        'time_locked': bool(row['time_locked']),
        'time_source': str(row['time_source'] or 'manual'),
        'time_reason': str(row['time_reason'] or ''),
        'started_at': row['started_at'],
        'ended_at': row['ended_at'],
        'deleted': bool(row['deleted']),
    }


def local_daily_report(conn, date_str: str) -> dict | None:
    row = conn.execute(select(db.reports).where(db.reports.c.date == date_str)).mappings().first()
    if not row:
        return None
    settings = service.get_settings(conn)
    target = row['target_minutes'] if row['target_minutes'] else settings.target_minutes
    return {
        'target_minutes': int(target),
        'confirmed': bool(row['confirmed']),
        'confirmed_at': row['confirmed_at'],
        'deleted': False,
    }


def local_settings_entities(conn) -> dict[str, dict]:
    s = service.get_settings(conn)
    return {
        'setting:work_time': {
            'value': {
                'target_minutes': s.target_minutes,
                'step_minutes': s.step_minutes,
                'timezone': s.timezone,
            },
            'deleted': False,
        },
        'setting:prompts': {
            'value': {
                'agent_prompts': s.agent_prompts,
                'title_examples': s.title_examples,
                'style_template': s.style_template,
            },
            'deleted': False,
        },
        'setting:submit': {
            'value': {
                'enterprise_id': s.gitee_enterprise.enterprise_id,
                'include_details': s.gitee_enterprise.include_details,
                'dedup': s.gitee_dedup.model_dump(mode='json'),
            },
            'deleted': False,
        },
    }


def local_mappings(conn) -> dict[str, dict]:
    s = service.get_settings(conn)
    result = {}
    for r in s.gitee_enterprise.rules:
        mid = mapping_id(r.mode, r.pattern)
        result[mid] = {
            'match_mode': r.mode,
            'pattern': r.pattern,
            'target_type': 'enterprise',
            'enterprise_id': s.gitee_enterprise.enterprise_id,
            'program_id': r.program_id,
            'issue_type_id': None,
            'deleted': False,
        }
    return result


def apply_remote_work_item(conn, task_id: str, fields: dict) -> None:
    deleted = bool(fields.get('deleted', False))
    task_values = {
        'id': task_id,
        'date': fields.get('date', ''),
        'title': fields.get('title', ''),
        'details': fields.get('details', ''),
        'project': fields.get('project', '未归属项目'),
        'category': fields.get('category', '其他'),
        'status': fields.get('status', 'requested'),
        'origin': fields.get('origin', 'manual'),
        'confidence': '云端同步',
        'evidence_level': '同步记录',
        'edited': True,
        'deleted': deleted,
        'started_at': fields.get('started_at'),
        'ended_at': fields.get('ended_at'),
    }

    service.upsert(conn, db.tasks, task_values, 'id')

    worklog_values = {
        'task_id': task_id,
        'suggested_minutes': fields.get('suggested_minutes'),
        'confirmed_minutes': int(fields.get('confirmed_minutes') or 0),
        'time_locked': bool(fields.get('time_locked', False)),
        'time_source': str(fields.get('time_source') or 'manual'),
        'time_reason': str(fields.get('time_reason') or ''),
        'is_confirmed': False,
    }
    service.upsert(conn, db.worklogs, worklog_values, 'task_id')

    if fields.get('date'):
        service.invalidate(conn, fields['date'])


def apply_remote_daily_report(conn, date_str: str, fields: dict) -> None:
    report_values = {
        'date': date_str,
        'target_minutes': int(fields.get('target_minutes') or 480),
        'confirmed': bool(fields.get('confirmed', False)),
        'confirmed_at': fields.get('confirmed_at'),
    }
    service.upsert(conn, db.reports, report_values, 'date')


def apply_remote_setting(conn, category: str, fields: dict) -> None:
    val = fields.get('value', {})
    if not isinstance(val, dict):
        return

    s = service.get_settings(conn)
    changed = False

    if category == 'work_time':
        if 'target_minutes' in val:
            s.target_minutes = int(val['target_minutes'])
            changed = True
        if 'step_minutes' in val:
            s.step_minutes = int(val['step_minutes'])
            changed = True
        if 'timezone' in val:
            s.timezone = str(val['timezone'])
            changed = True
    elif category == 'prompts':
        if 'agent_prompts' in val:
            s.agent_prompts = dict(val['agent_prompts'])
            changed = True
        if 'title_examples' in val:
            s.title_examples = list(val['title_examples'])
            changed = True
        if 'style_template' in val:
            s.style_template = str(val['style_template'])
            changed = True
    elif category == 'submit':
        if 'enterprise_id' in val:
            s.gitee_enterprise.enterprise_id = int(val['enterprise_id'])
            changed = True
        if 'include_details' in val:
            s.gitee_enterprise.include_details = bool(val['include_details'])
            changed = True
        if 'dedup' in val and isinstance(val['dedup'], dict):
            from .models import GiteeDedupConfig
            s.gitee_dedup = GiteeDedupConfig.model_validate(val['dedup'])
            changed = True

    if changed:
        service.upsert(conn, db.settings_table, {'id': 1, 'value': s.model_dump(mode='json')}, 'id')


def rebuild_project_mappings(conn) -> None:
    """Rebuilds Settings.gitee_enterprise.rules from non-deleted shadows."""
    rows = conn.execute(
        select(db.sync_shadow)
        .where(db.sync_shadow.c.entity_type == 'project_mapping')
    ).mappings().all()

    rules = []
    from .models import GiteeProjectRule

    for row in rows:
        fields = row['fields']
        if fields.get('deleted'):
            continue
        if fields.get('target_type') == 'enterprise' and fields.get('program_id'):
            try:
                rule = GiteeProjectRule(
                    mode=fields.get('match_mode', 'prefix'),
                    pattern=fields.get('pattern', ''),
                    program_id=int(fields['program_id']),
                )
                rules.append(rule)
            except Exception:
                pass

    rules.sort(key=lambda r: (r.pattern, r.mode))

    s = service.get_settings(conn)
    s.gitee_enterprise.rules = rules
    service.upsert(conn, db.settings_table, {'id': 1, 'value': s.model_dump(mode='json')}, 'id')


def apply_remote_gitee_link(conn, link_id: str, fields: dict) -> None:
    if fields.get('deleted'):
        return

    work_item_id = fields.get('work_item_id')
    target_scope = fields.get('target_scope', '')
    remote_id = fields.get('remote_id', '')
    url = fields.get('url', '')

    if not work_item_id or not remote_id:
        return

    if target_scope.startswith('ent:'):
        try:
            ent_id = int(target_scope[4:])
        except ValueError:
            return

        identity = stable(ent_id, work_item_id)
        record = {
            'id': identity,
            'enterprise_id': ent_id,
            'task_id': work_item_id,
            'program_id': 0,
            'status': 'created',
            'issue_id': str(remote_id),
            'url': url,
            'error': '',
            'updated_at': now_iso(),
        }
        service.upsert(conn, db.gitee_links, record, 'id')


# --- Sync Cycle ---

def sync_once(engine, client: CloudClient) -> dict:
    """Executes a single two-way sync cycle. Network calls are made outside DB transactions."""
    with sync_lock:
        return _sync_once_locked(engine, client)


def _sync_once_locked(engine, client: CloudClient) -> dict:
    with engine.begin() as conn:
        bound = get_meta(conn, 'bound', False)
        if not bound:
            return {'status': 'not_bound', 'pushed': 0, 'applied': 0, 'conflicts': 0, 'pulled': 0}

    # Step 1: Build operations from dirty items
    ops_to_push = []
    with engine.begin() as conn:
        dirty_rows = list(conn.execute(select(db.sync_dirty).order_by(db.sync_dirty.c.seq)).mappings())

        for d in dirty_rows:
            etype = d['entity_type']
            eid = d['entity_id']

            # Check for existing in-flight op
            existing_op = conn.execute(
                select(db.sync_ops).where(and_(db.sync_ops.c.entity_type == etype, db.sync_ops.c.entity_id == eid))
            ).mappings().first()
            if existing_op:
                continue

            # Check for unresolved conflict
            conflict = conn.execute(
                select(db.sync_conflicts).where(and_(
                    db.sync_conflicts.c.entity_type == etype,
                    db.sync_conflicts.c.entity_id == eid,
                    db.sync_conflicts.c.resolved_at.is_(None)
                ))
            ).mappings().first()
            if conflict:
                continue

            shadow_row = conn.execute(
                select(db.sync_shadow).where(and_(db.sync_shadow.c.entity_type == etype, db.sync_shadow.c.entity_id == eid))
            ).mappings().first()

            shadow_fields = shadow_row['fields'] if shadow_row else {}
            base_ver = shadow_row['version'] if shadow_row else 0

            # Gather local fields
            if etype == 'work_item':
                local_f = local_work_item(conn, eid)
                if not local_f:
                    conn.execute(delete(db.sync_dirty).where(and_(db.sync_dirty.c.entity_type == etype, db.sync_dirty.c.entity_id == eid)))
                    continue

                changed = {}
                if not shadow_row:
                    changed = local_f
                else:
                    for k, v in local_f.items():
                        if shadow_fields.get(k) != v:
                            changed[k] = v

                if changed:
                    op_id = uuid4().hex
                    op_record = {
                        'op_id': op_id,
                        'entity_type': etype,
                        'entity_id': eid,
                        'base_version': base_ver,
                        'fields': changed,
                        'dirty_seq': d['seq'],
                        'created_at': now_iso(),
                        'attempts': 0,
                    }
                    service.upsert(conn, db.sync_ops, op_record, 'op_id')
                    ops_to_push.append(op_record)
                else:
                    conn.execute(delete(db.sync_dirty).where(and_(db.sync_dirty.c.entity_type == etype, db.sync_dirty.c.entity_id == eid)))

            elif etype == 'daily_report':
                local_f = local_daily_report(conn, eid)
                if not local_f:
                    conn.execute(delete(db.sync_dirty).where(and_(db.sync_dirty.c.entity_type == etype, db.sync_dirty.c.entity_id == eid)))
                    continue
                changed = local_f if not shadow_row else {k: v for k, v in local_f.items() if shadow_fields.get(k) != v}
                if changed:
                    op_id = uuid4().hex
                    op_record = {
                        'op_id': op_id,
                        'entity_type': etype,
                        'entity_id': eid,
                        'base_version': base_ver,
                        'fields': changed,
                        'dirty_seq': d['seq'],
                        'created_at': now_iso(),
                        'attempts': 0,
                    }
                    service.upsert(conn, db.sync_ops, op_record, 'op_id')
                    ops_to_push.append(op_record)
                else:
                    conn.execute(delete(db.sync_dirty).where(and_(db.sync_dirty.c.entity_type == etype, db.sync_dirty.c.entity_id == eid)))

            elif etype == 'setting':
                # Expand setting:* to categories and mappings
                cats = local_settings_entities(conn)
                for cat_key, cat_data in cats.items():
                    c_type, c_id = cat_key.split(':')
                    s_row = conn.execute(select(db.sync_shadow).where(and_(db.sync_shadow.c.entity_type == c_type, db.sync_shadow.c.entity_id == c_id))).mappings().first()
                    s_f = s_row['fields'] if s_row else {}
                    b_ver = s_row['version'] if s_row else 0
                    if not s_row or s_f != cat_data:
                        op_id = uuid4().hex
                        op_rec = {
                            'op_id': op_id,
                            'entity_type': c_type,
                            'entity_id': c_id,
                            'base_version': b_ver,
                            'fields': cat_data,
                            'dirty_seq': d['seq'],
                            'created_at': now_iso(),
                            'attempts': 0,
                        }
                        service.upsert(conn, db.sync_ops, op_rec, 'op_id')
                        ops_to_push.append(op_rec)

                # Mappings
                maps = local_mappings(conn)
                for mid, mdata in maps.items():
                    s_row = conn.execute(select(db.sync_shadow).where(and_(db.sync_shadow.c.entity_type == 'project_mapping', db.sync_shadow.c.entity_id == mid))).mappings().first()
                    s_f = s_row['fields'] if s_row else {}
                    b_ver = s_row['version'] if s_row else 0
                    if not s_row or s_f != mdata:
                        op_id = uuid4().hex
                        op_rec = {
                            'op_id': op_id,
                            'entity_type': 'project_mapping',
                            'entity_id': mid,
                            'base_version': b_ver,
                            'fields': mdata,
                            'dirty_seq': d['seq'],
                            'created_at': now_iso(),
                            'attempts': 0,
                        }
                        service.upsert(conn, db.sync_ops, op_rec, 'op_id')
                        ops_to_push.append(op_rec)

                conn.execute(delete(db.sync_dirty).where(and_(db.sync_dirty.c.entity_type == 'setting', db.sync_dirty.c.entity_id == '*')))

    # Step 2: Push in chunks of 200
    pushed_count = len(ops_to_push)
    applied_count = 0
    conflict_count = 0

    if ops_to_push:
        chunk_size = 200
        for i in range(0, len(ops_to_push), chunk_size):
            batch_ops = ops_to_push[i:i + chunk_size]
            api_ops = [{
                'op_id': op['op_id'],
                'entity_type': op['entity_type'],
                'entity_id': op['entity_id'],
                'base_version': op['base_version'],
                'fields': op['fields'],
            } for op in batch_ops]

            try:
                push_res = client.push(api_ops)
            except CloudError as exc:
                if exc.status == 401:
                    with engine.begin() as conn:
                        set_meta(conn, 'bound', False)
                        set_meta(conn, 'last_error', '认证已失效，请重新登录')
                    return {'status': 'auth_failed'}
                with engine.begin() as conn:
                    set_meta(conn, 'last_error', f"Push失败: {exc.message}")
                raise

            with engine.begin() as conn:
                with suppressed(conn):
                    for res in push_res.get('results', []):
                        op_id = res['op_id']
                        status = res['status']
                        op_row = next((o for o in batch_ops if o['op_id'] == op_id), None)
                        if not op_row:
                            continue

                        if status == 'applied' and res.get('entity'):
                            applied_count += 1
                            ent = res['entity']
                            # Check whether a newer local modification arrived while push was in-flight
                            current_dirty_seq = conn.execute(
                                select(db.sync_dirty.c.seq).where(and_(
                                    db.sync_dirty.c.entity_type == op_row['entity_type'],
                                    db.sync_dirty.c.entity_id == op_row['entity_id'],
                                ))
                            ).scalar_one_or_none()

                            # Always update shadow to server-applied version and fields
                            service.upsert(conn, db.sync_shadow, {
                                'entity_type': ent['entity_type'],
                                'entity_id': ent['entity_id'],
                                'version': ent['version'],
                                'fields': ent['fields'],
                            }, ('entity_type', 'entity_id'))

                            conn.execute(delete(db.sync_ops).where(db.sync_ops.c.op_id == op_id))

                            if current_dirty_seq is not None and current_dirty_seq > op_row['dirty_seq']:
                                # A newer local change occurred during push!
                                # Preserve local table as-is and leave sync_dirty intact for next cycle.
                                pass
                            else:
                                apply_pulled_entity(conn, ent)
                                conn.execute(delete(db.sync_dirty).where(and_(
                                    db.sync_dirty.c.entity_type == op_row['entity_type'],
                                    db.sync_dirty.c.entity_id == op_row['entity_id'],
                                    db.sync_dirty.c.seq == op_row['dirty_seq'],
                                )))

                        elif status == 'conflict' and res.get('entity'):
                            conflict_count += 1
                            remote_ent = res['entity']
                            # Three-way merge: base = shadow, remote = remote_ent, local = local
                            shadow_row = conn.execute(select(db.sync_shadow).where(and_(
                                db.sync_shadow.c.entity_type == op_row['entity_type'],
                                db.sync_shadow.c.entity_id == op_row['entity_id'],
                            ))).mappings().first()
                            shadow_f = shadow_row['fields'] if shadow_row else {}

                            # Save conflict record
                            conf_id = uuid4().hex
                            service.upsert(conn, db.sync_conflicts, {
                                'id': conf_id,
                                'entity_type': op_row['entity_type'],
                                'entity_id': op_row['entity_id'],
                                'fields': res.get('conflict_fields', []),
                                'local': op_row['fields'],
                                'remote': remote_ent['fields'],
                                'created_at': now_iso(),
                                'resolved_at': None,
                            }, 'id')

                            # Shadow moves to remote version
                            service.upsert(conn, db.sync_shadow, {
                                'entity_type': remote_ent['entity_type'],
                                'entity_id': remote_ent['entity_id'],
                                'version': remote_ent['version'],
                                'fields': remote_ent['fields'],
                            }, ('entity_type', 'entity_id'))

                            conn.execute(delete(db.sync_ops).where(db.sync_ops.c.op_id == op_id))

                        elif status == 'rejected':
                            conn.execute(delete(db.sync_ops).where(db.sync_ops.c.op_id == op_id))
                            set_meta(conn, 'last_error', f"操作被拒绝: {res.get('error', {}).get('message')}")

    # Step 3: Pull changes
    pulled_count = 0
    has_more = True

    while has_more:
        with engine.begin() as conn:
            cursor = get_meta(conn, 'cursor', 0)

        try:
            pull_res = client.pull(cursor)
        except CloudError as exc:
            if exc.status == 410:
                # Cursor expired -> snapshot recovery
                snapshot_res = client.snapshot()
                with engine.begin() as conn:
                    with suppressed(conn):
                        for ent in snapshot_res.get('entities', []):
                            merge_or_apply_pulled_entity(conn, ent)
                        set_meta(conn, 'cursor', snapshot_res.get('cursor', 0))
                break
            elif exc.status == 401:
                with engine.begin() as conn:
                    set_meta(conn, 'bound', False)
                    set_meta(conn, 'last_error', '认证已失效，请重新登录')
                return {'status': 'auth_failed'}
            raise

        changes = pull_res.get('changes', [])
        new_cursor = pull_res.get('cursor', cursor)
        has_more = pull_res.get('has_more', False)

        with engine.begin() as conn:
            with suppressed(conn):
                for ent in changes:
                    pulled_count += 1
                    res_status = merge_or_apply_pulled_entity(conn, ent)
                    if res_status == 'conflict':
                        conflict_count += 1
                set_meta(conn, 'cursor', new_cursor)

    with engine.begin() as conn:
        set_meta(conn, 'last_sync_at', now_iso())
        set_meta(conn, 'last_error', '')

    return {
        'status': 'ok',
        'pushed': pushed_count,
        'applied': applied_count,
        'conflicts': conflict_count,
        'pulled': pulled_count,
    }


def apply_pulled_entity(conn, ent: dict) -> None:
    etype = ent['entity_type']
    eid = ent['entity_id']
    fields = ent['fields']

    if etype == 'work_item':
        apply_remote_work_item(conn, eid, fields)
    elif etype == 'daily_report':
        apply_remote_daily_report(conn, eid, fields)
    elif etype == 'setting':
        apply_remote_setting(conn, eid, fields)
    elif etype == 'project_mapping':
        pass  # Rebuilt below
    elif etype == 'gitee_link':
        apply_remote_gitee_link(conn, eid, fields)

    # Save to shadow
    service.upsert(conn, db.sync_shadow, {
        'entity_type': etype,
        'entity_id': eid,
        'version': ent['version'],
        'fields': fields,
    }, ('entity_type', 'entity_id'))

    if etype == 'project_mapping':
        rebuild_project_mappings(conn)


def merge_or_apply_pulled_entity(conn, ent: dict) -> str:
    """Three-way merge for pulled entities. Preserves local dirty modifications unconflicted with remote."""
    etype = ent['entity_type']
    eid = ent['entity_id']
    remote_f = ent['fields']

    dirty_seq = conn.execute(
        select(db.sync_dirty.c.seq).where(and_(
            db.sync_dirty.c.entity_type == etype,
            db.sync_dirty.c.entity_id == eid,
        ))
    ).scalar_one_or_none()

    if dirty_seq is None:
        apply_pulled_entity(conn, ent)
        return 'applied'

    shadow_row = conn.execute(
        select(db.sync_shadow).where(and_(
            db.sync_shadow.c.entity_type == etype,
            db.sync_shadow.c.entity_id == eid,
        ))
    ).mappings().first()
    shadow_f = shadow_row['fields'] if shadow_row else {}

    local_f = None
    if etype == 'work_item':
        local_f = local_work_item(conn, eid)
    elif etype == 'daily_report':
        local_f = local_daily_report(conn, eid)

    if not local_f:
        apply_pulled_entity(conn, ent)
        return 'applied'

    remote_changes = {k for k, v in remote_f.items() if shadow_f.get(k) != v}
    local_changes = {k for k, v in local_f.items() if shadow_f.get(k) != v}
    conflicts = [k for k in (remote_changes & local_changes) if remote_f[k] != local_f[k]]

    if conflicts:
        conf_id = uuid4().hex
        service.upsert(conn, db.sync_conflicts, {
            'id': conf_id,
            'entity_type': etype,
            'entity_id': eid,
            'fields': conflicts,
            'local': local_f,
            'remote': remote_f,
            'created_at': now_iso(),
            'resolved_at': None,
        }, 'id')
        service.upsert(conn, db.sync_shadow, {
            'entity_type': etype,
            'entity_id': eid,
            'version': ent['version'],
            'fields': remote_f,
        }, ('entity_type', 'entity_id'))
        return 'conflict'

    # Apply remote-only changes to local
    remote_only = remote_changes - local_changes
    if remote_only:
        patch = {k: remote_f[k] for k in remote_only}
        if etype == 'work_item':
            task_patch = {k: v for k, v in patch.items() if k in ('date', 'title', 'details', 'project', 'category', 'status', 'deleted', 'started_at', 'ended_at')}
            if task_patch:
                conn.execute(update(db.tasks).where(db.tasks.c.id == eid).values(**task_patch))
            worklog_patch = {k: v for k, v in patch.items() if k in ('suggested_minutes', 'confirmed_minutes', 'time_locked', 'time_source', 'time_reason')}
            if worklog_patch:
                conn.execute(update(db.worklogs).where(db.worklogs.c.task_id == eid).values(**worklog_patch))
        elif etype == 'daily_report':
            rep_patch = {k: v for k, v in patch.items() if k in ('target_minutes', 'confirmed', 'confirmed_at')}
            if rep_patch:
                conn.execute(update(db.reports).where(db.reports.c.date == eid).values(**rep_patch))

    service.upsert(conn, db.sync_shadow, {
        'entity_type': etype,
        'entity_id': eid,
        'version': ent['version'],
        'fields': remote_f,
    }, ('entity_type', 'entity_id'))

    return 'merged'


# --- Bind and Migration ---

def bind(engine, server_url: str, email: str, password: str, device_name: str, client: CloudClient | None = None) -> dict:
    from awt import __version__
    if client is None:
        client = CloudClient(base_url=server_url)
    session = client.login(email, password, {
        'name': device_name,
        'platform': 'desktop',
        'client_version': __version__,
    })

    token = session['token']
    client.token = token
    try:
        gitee.save_token(token, service='cloud-session')
    except Exception:
        pass

    with engine.begin() as conn:
        set_meta(conn, 'server_url', server_url)
        set_meta(conn, 'account', session['account'])
        set_meta(conn, 'device_id', session['device']['id'])
        set_meta(conn, 'bound', True)
        set_meta(conn, 'cursor', 0)
        set_meta(conn, 'last_error', '')
        set_meta(conn, 'unified_publish_enabled', session['account'].get('unified_publish_enabled', False))
        if get_meta(conn, 'mode') is None:
            set_meta(conn, 'mode', {'mode': 'auto', 'interval_minutes': 5, 'times': []})

    return session


def unbind(engine) -> None:
    with engine.begin() as conn:
        server_url = get_meta(conn, 'server_url', '')

    token = gitee.token(service='cloud-session')
    if server_url and token:
        try:
            client = CloudClient(base_url=server_url, token=token)
            client.logout()
        except Exception:
            pass

    path = db.data_dir() / 'cloud-session-credential.json'
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass

    with engine.begin() as conn:
        conn.execute(delete(db.sync_meta))
        conn.execute(delete(db.sync_shadow))
        conn.execute(delete(db.sync_dirty))
        conn.execute(delete(db.sync_ops))
        conn.execute(delete(db.sync_conflicts))


def migration_preview(engine, client: CloudClient | None = None) -> dict:
    with engine.begin() as conn:
        wi_cnt = len(list(conn.execute(select(db.tasks.c.id).where(db.tasks.c.deleted == False)).scalars()))
        rep_cnt = len(list(conn.execute(select(db.reports.c.date)).scalars()))
        maps = local_mappings(conn)
        links_cnt = len(list(conn.execute(select(db.gitee_links.c.id).where(db.gitee_links.c.status == 'created')).scalars()))
        uncertain_cnt = len(list(conn.execute(select(db.gitee_links.c.id).where(db.gitee_links.c.status.in_(['pending', 'uncertain']))).scalars()))
        server_url = get_meta(conn, 'server_url', '')

    token = gitee.token(service='cloud-session')
    server_status = {}
    if client is not None:
        try:
            server_status = client.migration_status()
        except Exception:
            pass
    elif server_url and token:
        try:
            c = CloudClient(base_url=server_url, token=token)
            server_status = c.migration_status()
        except Exception:
            pass

    return {
        'local': {
            'work_items': wi_cnt,
            'daily_reports': rep_cnt,
            'settings': 3,
            'project_mappings': len(maps),
            'links': links_cnt,
            'uncertain': uncertain_cnt,
        },
        'server': server_status,
    }


def migrate(engine, client: CloudClient | None = None) -> dict:
    with engine.begin() as conn:
        server_url = get_meta(conn, 'server_url', '')

    token = gitee.token(service='cloud-session')
    if client is None:
        if not server_url or not token:
            raise ValueError("请先完成云端账号绑定")
        client = CloudClient(base_url=server_url, token=token)

    # 1. Snapshot: save remote shadows
    snap = client.snapshot()
    with engine.begin() as conn:
        with suppressed(conn):
            for ent in snap.get('entities', []):
                service.upsert(conn, db.sync_shadow, {
                    'entity_type': ent['entity_type'],
                    'entity_id': ent['entity_id'],
                    'version': ent['version'],
                    'fields': ent['fields'],
                }, ('entity_type', 'entity_id'))
            set_meta(conn, 'cursor', snap.get('cursor', 0))

    # 2. Mark all local entities dirty
    with engine.begin() as conn:
        task_ids = list(conn.execute(select(db.tasks.c.id)).scalars())
        for tid in task_ids:
            service.upsert(conn, db.sync_dirty, {
                'entity_type': 'work_item',
                'entity_id': tid,
                'changed_at': now_iso(),
                'seq': 1,
            }, ('entity_type', 'entity_id'))

        report_dates = list(conn.execute(select(db.reports.c.date)).scalars())
        for rdate in report_dates:
            service.upsert(conn, db.sync_dirty, {
                'entity_type': 'daily_report',
                'entity_id': rdate,
                'changed_at': now_iso(),
                'seq': 1,
            }, ('entity_type', 'entity_id'))

        service.upsert(conn, db.sync_dirty, {
            'entity_type': 'setting',
            'entity_id': '*',
            'changed_at': now_iso(),
            'seq': 1,
        }, ('entity_type', 'entity_id'))

    # 3. Run sync cycles until dirty is cleared
    max_loops = 50
    for _ in range(max_loops):
        res = sync_once(engine, client)
        with engine.begin() as conn:
            dirty_cnt = len(list(conn.execute(select(db.sync_dirty.c.entity_id)).scalars()))
            ops_cnt = len(list(conn.execute(select(db.sync_ops.c.op_id)).scalars()))
            if dirty_cnt == 0 and ops_cnt == 0:
                break

    # 4. Import links
    with engine.begin() as conn:
        links_rows = list(conn.execute(select(db.gitee_links)).mappings())

    import_links = []
    import_pending = []
    for r in links_rows:
        scope = f"ent:{r['enterprise_id']}"
        if r['status'] == 'created' and r['issue_id']:
            import_links.append({
                'work_item_id': r['task_id'],
                'target_scope': scope,
                'remote_id': str(r['issue_id']),
                'url': r['url'] or '',
                'published_at': r['updated_at'] or now_iso(),
            })
        elif r['status'] in ('pending', 'uncertain'):
            import_pending.append({
                'work_item_id': r['task_id'],
                'target_scope': scope,
                'note': r['error'] or '',
            })

    if import_links or import_pending:
        client.import_links(import_links, import_pending)

    # 5. Complete migration
    preview = migration_preview(engine)
    expected = {
        'work_items': preview['local']['work_items'],
        'links': len(import_links),
        'uncertain': len(import_pending),
    }

    complete_res = client.complete_migration(expected)
    with engine.begin() as conn:
        set_meta(conn, 'unified_publish_enabled', complete_res.get('unified_publish_enabled', True))

    return complete_res


# --- Conflict Management ---

def list_conflicts(conn) -> list[dict]:
    rows = conn.execute(
        select(db.sync_conflicts)
        .where(db.sync_conflicts.c.resolved_at.is_(None))
        .order_by(db.sync_conflicts.c.created_at.desc())
    ).mappings().all()
    return [dict(r) for r in rows]


def resolve_conflict(engine, conflict_id: str, choices: dict[str, str]) -> dict:
    """Resolves a conflict. Choices map field -> 'local' | 'remote'."""
    with engine.begin() as conn:
        row = conn.execute(select(db.sync_conflicts).where(db.sync_conflicts.c.id == conflict_id)).mappings().first()
        if not row:
            raise LookupError("冲突记录不存在")

        remote_f = row['remote']
        etype = row['entity_type']
        eid = row['entity_id']

        with suppressed(conn):
            for field, choice in choices.items():
                if choice == 'remote':
                    val = remote_f.get(field)
                    if etype == 'work_item':
                        if field in ('title', 'details', 'project', 'category', 'status', 'deleted'):
                            conn.execute(update(db.tasks).where(db.tasks.c.id == eid).values(**{field: val}))
                        elif field in ('confirmed_minutes', 'suggested_minutes', 'time_locked'):
                            conn.execute(update(db.worklogs).where(db.worklogs.c.task_id == eid).values(**{field: val}))

            conn.execute(update(db.sync_conflicts).where(db.sync_conflicts.c.id == conflict_id).values(resolved_at=now_iso()))

    return {'status': 'resolved', 'id': conflict_id}


# --- Scheduler ---

class SyncScheduler:
    def __init__(self, engine):
        self.engine = engine
        self._lock = Lock()
        self._stop_event = Event()
        self._trigger_event = Event()
        self._thread: Thread | None = None
        self._backoff = 0
        self._last_pull = 0

    def start(self):
        self._stop_event.clear()
        self._thread = Thread(target=self._run, daemon=True, name="awt-sync-scheduler")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._trigger_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def trigger_now(self):
        self._trigger_event.set()

    def _run(self):
        while not self._stop_event.is_set():
            triggered = self._trigger_event.wait(timeout=1.0)
            if self._stop_event.is_set():
                break

            if triggered:
                self._trigger_event.clear()

            with self.engine.connect() as conn:
                bound = get_meta(conn, 'bound', False)
                mode_info = get_meta(conn, 'mode', {'mode': 'auto', 'interval_minutes': 5, 'times': []})
                dirty_cnt = len(list(conn.execute(select(db.sync_dirty.c.entity_id)).scalars()))

            if not bound:
                continue

            mode = mode_info.get('mode', 'auto')
            should_sync = triggered

            now_ts = time.time()
            if mode == 'auto':
                if dirty_cnt > 0 or now_ts - self._last_pull > 60:
                    should_sync = True
            elif mode == 'interval':
                interval = max(60, mode_info.get('interval_minutes', 5) * 60)
                if now_ts - self._last_pull > interval:
                    should_sync = True
            elif mode == 'fixed_time':
                now_hm = datetime.now().strftime('%H:%M')
                times = mode_info.get('times', [])
                if now_hm in times and now_ts - self._last_pull > 60:
                    should_sync = True
            elif mode == 'manual':
                should_sync = triggered

            if should_sync and sync_lock.acquire(blocking=False):
                try:
                    with self.engine.connect() as conn:
                        server_url = get_meta(conn, 'server_url', '')
                    token = gitee.token(service='cloud-session')
                    if server_url and token:
                        client = CloudClient(base_url=server_url, token=token)
                        sync_once(self.engine, client)
                        self._last_pull = time.time()
                        self._backoff = 0
                except CloudUnavailable:
                    self._backoff = min(60, (self._backoff or 5) * 2)
                    time.sleep(self._backoff)
                except Exception as exc:
                    logger.warning("Background sync cycle error: %s", exc)
                finally:
                    sync_lock.release()
