"""Scheduled background collection and AI work-item generation."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import logging
from threading import Lock
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select

from . import analysis_progress, daily_analysis, db, model_provider, multiday, service
from .collectors import stable
from .models import Settings

logger = logging.getLogger('awt.auto_schedule')

INTEGRATION_KEY = 'auto-generate'
DAILY_WINDOW_SECONDS = 180
MAX_HANDLED_SLOTS = 32


class BusyError(RuntimeError):
    """Raised when the target day already has an active analysis or allocation lock."""


@dataclass
class AnalysisRuntime:
    analysis_locks: dict = field(default_factory=dict)
    collect_states: dict = field(default_factory=dict)
    analysis_runs: dict = field(default_factory=dict)
    allocation_runs: dict = field(default_factory=dict)
    on_tasks_changed: Callable[[], None] | None = None


@contextmanager
def _immediate(engine):
    with engine.connect() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _utc_now(now_utc: datetime | None = None) -> datetime:
    if now_utc is None:
        return datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        return now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(timezone.utc)


def local_now(timezone_name: str, now_utc: datetime | None = None) -> datetime:
    return _utc_now(now_utc).astimezone(ZoneInfo(timezone_name))


def read_state(conn) -> dict:
    value = conn.execute(
        select(db.integrations.c.value).where(db.integrations.c.id == INTEGRATION_KEY)
    ).scalar_one_or_none()
    return dict(value) if isinstance(value, dict) else {}


def write_state(engine, state: dict) -> dict:
    with _immediate(engine) as conn:
        service.upsert(conn, db.integrations, {'id': INTEGRATION_KEY, 'value': state}, 'id')
    return state


def _update_state(engine, **updates) -> dict:
    with _immediate(engine) as conn:
        current = read_state(conn)
        merged = {**current, **updates}
        service.upsert(conn, db.integrations, {'id': INTEGRATION_KEY, 'value': merged}, 'id')
        return merged


def _merge_slot_fields(state: dict, slot_key: str | None) -> dict:
    if not slot_key or slot_key.startswith('interval:'):
        return {}
    handled = [str(s) for s in (state.get('handled_slots') or []) if isinstance(s, str)]
    if slot_key not in handled:
        handled.append(slot_key)
    return {
        'last_slot': slot_key,
        'handled_slots': handled[-MAX_HANDLED_SLOTS:],
    }


def resolve_target(conn, target_day: date) -> tuple[date, date, date, dict | None]:
    binding = multiday.get_active_binding(conn, target_day)
    if binding:
        s_day = date.fromisoformat(binding['start_date'])
        e_day = date.fromisoformat(binding['end_date'])
        c_day = date.fromisoformat(binding['anchor_date'])
        return s_day, e_day, c_day, binding
    return target_day, target_day, target_day, None


def compute_input_fingerprint(
    rows: list[dict],
    settings: Settings,
    canonical_day: date,
    start_day: date,
    end_day: date,
) -> str:
    ent_cfg = getattr(settings, 'gitee_enterprise', None)
    ent_id = getattr(ent_cfg, 'enterprise_id', 0) or 0
    rules = [
        (r.mode, r.pattern, r.program_id)
        for r in (getattr(ent_cfg, 'rules', None) or [])
    ]
    return stable(
        'auto-gen-v1',
        daily_analysis.input_hash(rows, settings),
        str(canonical_day),
        str(start_day),
        str(end_day),
        bool(settings.auto_import_analysis),
        ent_id,
        rules,
    )


def evaluate_due(
    settings: Settings,
    state: dict,
    now_utc: datetime | None = None,
) -> tuple[bool, str | None, str]:
    now_u = _utc_now(now_utc)
    cfg = settings.auto_generate
    if not cfg.enabled:
        return False, None, '未启用定时自动生成'
    if not settings.llm_enabled:
        return False, None, '模型分析未开启'

    tz = ZoneInfo(settings.timezone)
    now_l = now_u.astimezone(tz)
    if cfg.weekdays_only and now_l.weekday() >= 5:
        return False, None, '仅工作日执行，当前为周末'

    handled_slots = set(state.get('handled_slots') or [])
    if state.get('last_slot'):
        handled_slots.add(str(state['last_slot']))

    if cfg.mode == 'daily_times':
        today_local = now_l.date()
        for hhmm in sorted(cfg.daily_times):
            hh, mm = map(int, hhmm.split(':'))
            slot_dt = datetime(today_local.year, today_local.month, today_local.day, hh, mm, tzinfo=tz)
            delta = (now_u - slot_dt.astimezone(timezone.utc)).total_seconds()
            slot_key = f'{today_local} {hhmm}'
            if 0 <= delta < DAILY_WINDOW_SECONDS and slot_key not in handled_slots:
                return True, slot_key, f'到达每日定时时间 {hhmm}'
        return False, None, '未到设定的每日执行时间'

    stamp = state.get('last_attempt_at') or state.get('last_run_at')
    if not stamp:
        return True, f"interval:{now_l.strftime('%Y-%m-%d %H:%M')}", '首次到达定时间隔触发条件'
    try:
        last_dt = datetime.fromisoformat(str(stamp))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return True, f"interval:{now_l.strftime('%Y-%m-%d %H:%M')}", '上次记录时间无效，重新触发'
    if now_u - last_dt.astimezone(timezone.utc) >= timedelta(minutes=cfg.interval_minutes):
        return True, f"interval:{now_l.strftime('%Y-%m-%d %H:%M')}", f'距上次执行已满 {cfg.interval_minutes} 分钟'
    return False, None, '未达到间隔时间'


def next_run_at(
    settings: Settings,
    state: dict,
    now_utc: datetime | None = None,
) -> str | None:
    cfg = settings.auto_generate
    if not cfg.enabled or not settings.llm_enabled:
        return None
    now_u = _utc_now(now_utc)
    tz = ZoneInfo(settings.timezone)
    now_l = now_u.astimezone(tz)

    handled_slots = set(state.get('handled_slots') or [])
    if state.get('last_slot'):
        handled_slots.add(str(state['last_slot']))

    if cfg.mode == 'daily_times':
        if not cfg.daily_times:
            return None
        for offset in range(8):
            cand_day = now_l.date() + timedelta(days=offset)
            if cfg.weekdays_only and cand_day.weekday() >= 5:
                continue
            for hhmm in sorted(cfg.daily_times):
                hh, mm = map(int, hhmm.split(':'))
                slot_dt = datetime(cand_day.year, cand_day.month, cand_day.day, hh, mm, tzinfo=tz)
                slot_key = f'{cand_day} {hhmm}'
                if slot_key in handled_slots:
                    continue
                delta = (now_u - slot_dt.astimezone(timezone.utc)).total_seconds()
                if slot_dt.astimezone(timezone.utc) >= now_u or (0 <= delta < DAILY_WINDOW_SECONDS):
                    return slot_dt.isoformat()
        return None

    stamp = state.get('last_attempt_at') or state.get('last_run_at')
    if not stamp:
        candidate = now_l.replace(second=0, microsecond=0)
    else:
        try:
            last_dt = datetime.fromisoformat(str(stamp))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            candidate = last_dt.astimezone(tz) + timedelta(minutes=cfg.interval_minutes)
        except ValueError:
            candidate = now_l.replace(second=0, microsecond=0)
    if candidate < now_l:
        candidate = now_l.replace(second=0, microsecond=0)
    if cfg.weekdays_only:
        while candidate.weekday() >= 5:
            next_day = candidate.date() + timedelta(days=1)
            candidate = datetime(next_day.year, next_day.month, next_day.day, 0, 0, tzinfo=tz)
    return candidate.isoformat()


def get_status(
    engine,
    runtime: AnalysisRuntime | None = None,
    now_utc: datetime | None = None,
) -> dict:
    now_u = _utc_now(now_utc)
    with engine.connect() as conn:
        settings = service.get_settings(conn)
        state = read_state(conn)
        today_day = local_now(settings.timezone, now_u).date()
        _, _, canonical_day, binding = resolve_target(conn, today_day)

    lock = runtime.analysis_locks.get(canonical_day) if runtime else None
    analysis_run = runtime.analysis_runs.get(canonical_day) if runtime else None
    running = bool(lock and lock.locked()) or bool(analysis_run and analysis_run.state == 'running')

    raw_status = str(state.get('last_status') or 'idle')
    if raw_status == 'running' and not running:
        raw_status = 'interrupted'

    cfg_dump = settings.auto_generate.model_dump(mode='json')
    return {
        **cfg_dump,
        'llm_enabled': bool(settings.llm_enabled),
        'auto_import_analysis': bool(settings.auto_import_analysis),
        'timezone': settings.timezone,
        'running': running,
        'target_date': str(canonical_day),
        'binding': binding,
        'next_run_at': next_run_at(settings, state, now_u),
        'last_attempt_at': state.get('last_attempt_at'),
        'last_run_at': state.get('last_run_at'),
        'last_status': raw_status,
        'last_reason': str(state.get('last_reason') or ''),
        'last_error': str(state.get('last_error') or ''),
        'last_date': str(state.get('last_date') or ''),
        'last_slot': str(state.get('last_slot') or ''),
        'last_summary': state.get('last_summary'),
    }


def _format_success_reason(result: dict, new_events: int) -> str:
    created = int(result.get('created') or 0)
    archived = int(result.get('archived') or 0)
    pending = int(result.get('pending') or 0)
    auto_imported = bool(result.get('auto_imported'))
    if auto_imported:
        parts = [f'已自动写入 {created} 项工作']
        if archived:
            parts.append(f'更新替换 {archived} 项草稿')
        if pending:
            parts.append(f'另有 {pending} 条候选待确认')
        text = '，'.join(parts)
    else:
        if pending:
            text = f'已生成 {pending} 条待审核工作项候选'
        else:
            warnings = result.get('warnings') or []
            text = str(warnings[0]) if warnings else 'AI 分析已完成，未发现新增可归纳工作项'
    if new_events > 0:
        text += f'（新采集 {new_events} 条记录）'
    return text


def execute_auto_generate(
    engine,
    runtime: AnalysisRuntime,
    *,
    force: bool = False,
    respect_unchanged: bool | None = None,
    now_utc: datetime | None = None,
) -> dict | None:
    now_u = _utc_now(now_utc)
    now_iso = now_u.isoformat()

    with engine.connect() as conn:
        settings = service.get_settings(conn)
        state = read_state(conn)
        today_day = local_now(settings.timezone, now_u).date()
        start_day, end_day, canonical_day, _ = resolve_target(conn, today_day)

    if force:
        if not settings.llm_enabled:
            raise ValueError('请先在“模型分析”接通并开启模型分析后再执行自动生成')
        slot_key = None
    else:
        is_due, slot_key, _ = evaluate_due(settings, state, now_u)
        if not is_due:
            return None

    lock = runtime.analysis_locks.setdefault(canonical_day, Lock())
    if not lock.acquire(blocking=False):
        if force:
            raise BusyError('该日期正在进行 AI 分析或工时估算，请等待完成后再试')
        return None

    check_unchanged = settings.auto_generate.skip_if_unchanged if respect_unchanged is None else respect_unchanged

    try:
        with engine.connect() as conn:
            settings = service.get_settings(conn)
            state = read_state(conn)
            start_day, end_day, canonical_day, _ = resolve_target(conn, today_day)
            report_confirmed = conn.execute(
                select(db.reports.c.confirmed).where(db.reports.c.date == str(canonical_day))
            ).scalar_one_or_none()

        if force and not settings.llm_enabled:
            raise ValueError('请先在“模型分析”接通并开启模型分析后再执行自动生成')
        if not force and not settings.auto_generate.enabled:
            return None
        if not settings.llm_enabled:
            return None

        if settings.auto_generate.skip_if_confirmed and report_confirmed:
            reason = '今日日报已确认，已跳过自动生成'
            _update_state(
                engine,
                last_attempt_at=now_iso,
                last_status='skipped',
                last_reason=reason,
                last_error='',
                last_date=str(canonical_day),
                **_merge_slot_fields(state, slot_key),
            )
            return {'triggered': False, 'status_code': 'skipped', 'reason': reason, 'date': str(canonical_day)}

        new_events = 0
        if settings.auto_generate.collect_before_analyze:
            c_state = runtime.collect_states[str(canonical_day)] = {
                'running': True,
                'source': '',
                'files_done': 0,
                'files_total': 0,
                'new_events': 0,
            }
            try:
                with _immediate(engine) as conn:
                    collect_res = service.collect(
                        conn,
                        canonical_day,
                        c_state.update,
                        start_day=start_day,
                        end_day=end_day,
                    )
                    new_events = int(collect_res.get('new_events') or 0)
            except Exception as exc:
                msg = str(exc) or type(exc).__name__
                _update_state(
                    engine,
                    last_attempt_at=now_iso,
                    last_status='error',
                    last_reason=f'自动采集失败：{msg[:200]}',
                    last_error=msg[:500],
                    last_date=str(canonical_day),
                    **_merge_slot_fields(state, slot_key),
                )
                if force:
                    raise
                logger.warning('定时自动采集失败 date=%s error=%s', canonical_day, msg)
                return {'triggered': True, 'status_code': 'error', 'reason': msg, 'date': str(canonical_day)}
            finally:
                c_state['running'] = False

        with engine.connect() as conn:
            current_settings = service.get_settings(conn)
            rows, all_selected_rows, _ = daily_analysis.analysis_rows(conn, start_day, current_settings, end_day=end_day)
            has_dialog = any(e['kind'] in ('user_prompt', 'assistant_message') for e in rows)
            if not has_dialog:
                all_excl = any(e['kind'] in ('user_prompt', 'assistant_message') for e in all_selected_rows)
                reason = '今日对话已全部排除，跳过自动生成' if all_excl else '当天暂无可分析的工作对话记录，已跳过'
                if new_events > 0:
                    reason += f'（新采集 {new_events} 条非对话记录）'
                _update_state(
                    engine,
                    last_attempt_at=now_iso,
                    last_status='skipped',
                    last_reason=reason,
                    last_error='',
                    last_date=str(canonical_day),
                    **_merge_slot_fields(state, slot_key),
                )
                return {
                    'triggered': False,
                    'status_code': 'skipped',
                    'reason': reason,
                    'new_events': new_events,
                    'date': str(canonical_day),
                }

            current_fp = compute_input_fingerprint(rows, current_settings, canonical_day, start_day, end_day)
            cur_input_hash = daily_analysis.input_hash(rows, current_settings)
            existing_snap = conn.execute(
                select(db.analyses.c.snapshot).where(db.analyses.c.date == str(canonical_day))
            ).scalar_one_or_none()
            snap_matches = bool(
                isinstance(existing_snap, dict)
                and existing_snap.get('input_hash') == cur_input_hash
                and bool(existing_snap.get('auto_imported')) == bool(current_settings.auto_import_analysis)
                and str(existing_snap.get('start_date') or '') == str(start_day)
                and str(existing_snap.get('end_date') or '') == str(end_day)
            )

        if check_unchanged and (
            (
                state.get('last_fingerprint_date') == str(canonical_day)
                and state.get('last_fingerprint') == current_fp
                and existing_snap is not None
            )
            or snap_matches
        ):
            reason = '无新增工作对话或设置变化，已跳过重复生成'
            _update_state(
                engine,
                last_attempt_at=now_iso,
                last_status='skipped',
                last_reason=reason,
                last_error='',
                last_date=str(canonical_day),
                **_merge_slot_fields(state, slot_key),
            )
            return {
                'triggered': False,
                'status_code': 'skipped',
                'reason': reason,
                'new_events': new_events,
                'date': str(canonical_day),
            }

        progress = analysis_progress.Progress()
        runtime.analysis_runs[canonical_day] = progress
        for old in list(runtime.analysis_runs):
            if len(runtime.analysis_runs) > 32 and old != canonical_day and runtime.analysis_runs[old].state != 'running':
                del runtime.analysis_runs[old]

        label = f'{start_day} 至 {end_day}' if start_day != end_day else '全天'
        progress.add(
            f'手动触发自动生成：开始分析{label}对话'
            if force
            else f'定时自动生成：开始分析{label}对话'
        )
        try:
            with analysis_progress.track(progress), engine.connect() as conn:
                result = service.analyze(conn, canonical_day, start_day=start_day, end_day=end_day)
                conn.commit()
        except model_provider.ModelError as exc:
            msg = getattr(exc, 'user_reason', '') or str(exc)
            progress.add(f'自动生成失败：{msg}')
            _update_state(
                engine,
                last_attempt_at=now_iso,
                last_status='error',
                last_reason=f'自动生成失败：{msg}',
                last_error=msg[:500],
                last_date=str(canonical_day),
                **_merge_slot_fields(state, slot_key),
            )
            progress.finish('error')
            if force:
                raise
            logger.warning('定时自动生成失败 date=%s reason=%s', canonical_day, str(exc))
            return {'triggered': True, 'status_code': 'error', 'reason': msg, 'date': str(canonical_day)}
        except Exception as exc:
            msg = str(exc) or type(exc).__name__
            progress.add('自动生成未完成，原有记录已保留')
            _update_state(
                engine,
                last_attempt_at=now_iso,
                last_status='error',
                last_reason=f'自动生成失败：{msg[:200]}',
                last_error=msg[:500],
                last_date=str(canonical_day),
                **_merge_slot_fields(state, slot_key),
            )
            progress.finish('error')
            if force:
                raise
            logger.warning('定时自动生成异常 date=%s error=%s', canonical_day, msg)
            return {'triggered': True, 'status_code': 'error', 'reason': msg, 'date': str(canonical_day)}

        completed_iso = datetime.now(timezone.utc).isoformat()
        reason = _format_success_reason(result, new_events)
        summary = {
            'created': int(result.get('created') or 0),
            'archived': int(result.get('archived') or 0),
            'pending': int(result.get('pending') or 0),
            'auto_imported': bool(result.get('auto_imported')),
            'new_events': new_events,
            'usage': result.get('usage'),
        }
        _update_state(
            engine,
            last_attempt_at=completed_iso,
            last_run_at=completed_iso,
            last_status='success',
            last_reason=reason,
            last_error='',
            last_date=str(canonical_day),
            last_fingerprint=current_fp,
            last_fingerprint_date=str(canonical_day),
            last_summary=summary,
            **_merge_slot_fields(state, slot_key),
        )
        progress.add('自动生成完成，结果已保存')
        progress.finish('success')
        if result.get('auto_imported') and (summary['created'] or summary['archived']) and runtime.on_tasks_changed:
            try:
                runtime.on_tasks_changed()
            except Exception:
                pass
        return {
            'triggered': True,
            'status_code': 'success',
            'reason': reason,
            'date': str(canonical_day),
            **summary,
        }
    finally:
        lock.release()


def scheduled_generate(engine, runtime: AnalysisRuntime) -> dict | None:
    """Safe wrapper invoked periodically from lifespan background worker."""
    try:
        return execute_auto_generate(engine, runtime, force=False)
    except Exception as exc:
        logger.warning('后台定时检查异常: %s', exc)
        return None
