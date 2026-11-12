import json
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import insert, select

from awt import auto_schedule, db, model_provider, multiday, service
from awt.app import create_app
from awt.models import CONVERSATION_SOURCES, AutoGenerateConfig, Settings


def _fake_complete_factory(counter: dict, *, title: str = '实现定时自动生成工作项'):
    def fake(settings, system, text, **kwargs):
        counter['calls'] += 1
        payload = json.loads(text)
        if 'required_refs' in payload:
            return json.dumps({'work_refs': payload['required_refs'], 'excluded': [], 'uncertain_refs': []})
        prompts = [r for r in payload.get('conversations', []) if r['kind'] == 'user_prompt']
        return json.dumps(
            {
                'summary': f'完成 {title}。',
                'tasks': [
                    {
                        'title': title,
                        'details': '补齐配置、调度逻辑与状态追踪。',
                        'project': 'ai-work-tracker',
                        'category': '任务',
                        'refs': [r['ref'] for r in prompts[:1]] if prompts else [],
                    }
                ] if prompts else [],
            },
            ensure_ascii=False,
        )
    return fake


def _write_codex_jsonl(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')


def _codex_session(day_utc: datetime, session_id: str, user_text: str, cwd: str):
    t0 = day_utc.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
    t1 = (day_utc + timedelta(minutes=1)).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
    t2 = (day_utc + timedelta(minutes=2)).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
    return [
        {'type': 'session_meta', 'payload': {'id': session_id, 'cwd': cwd}},
        {
            'type': 'response_item',
            'timestamp': t0,
            'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': user_text}]},
        },
        {
            'type': 'response_item',
            'timestamp': t1,
            'payload': {'type': 'function_call', 'name': 'exec_command', 'arguments': 'pytest', 'call_id': f'call-{session_id}'},
        },
        {
            'type': 'response_item',
            'timestamp': t2,
            'payload': {'type': 'function_call_output', 'call_id': f'call-{session_id}', 'output': 'ok'},
        },
    ]


@pytest.fixture
def auto_client(tmp_path, monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR', str(tmp_path / 'data'))
    engine = db.make_engine(tmp_path / 'auto.db')
    with TestClient(create_app(engine)) as client:
        s = client.get('/api/settings').json()
        for key in CONVERSATION_SOURCES:
            s[key]['paths'] = []
        s['analysis_sources'] = ['codex']
        s['llm_enabled'] = True
        s['llm_model'] = 'test-auto-model'
        client.put('/api/settings', json=s).raise_for_status()
        yield client, engine, tmp_path
    engine.dispose()


def test_auto_generate_config_validation_and_legacy_migration():
    cfg = AutoGenerateConfig()
    assert cfg.enabled is False
    assert cfg.mode == 'daily_times'
    assert cfg.daily_times == ['18:00']
    assert cfg.interval_minutes == 60
    assert cfg.weekdays_only is False
    assert cfg.collect_before_analyze is True
    assert cfg.skip_if_unchanged is True
    assert cfg.skip_if_confirmed is True

    normalized = AutoGenerateConfig(enabled=True, daily_times=['20:30', '9:05', '09:05', '18:00'])
    assert normalized.daily_times == ['09:05', '18:00', '20:30']

    disabled_empty = AutoGenerateConfig(enabled=False, mode='daily_times', daily_times=[])
    assert disabled_empty.daily_times == []

    with pytest.raises(ValidationError):
        AutoGenerateConfig(enabled=True, mode='daily_times', daily_times=[])

    for bad_time in (['24:00'], ['12:60'], ['noon'], ['18:5']):
        with pytest.raises(ValidationError):
            AutoGenerateConfig(enabled=True, daily_times=bad_time)

    for bad_interval in (14, 721, True):
        with pytest.raises(ValidationError):
            AutoGenerateConfig(enabled=True, mode='interval', interval_minutes=bad_interval)

    legacy_settings = Settings.model_validate({'timezone': 'Asia/Shanghai', 'auto_generate': None})
    assert legacy_settings.auto_generate.enabled is False
    assert legacy_settings.auto_generate.daily_times == ['18:00']


def test_evaluate_due_and_next_run_for_daily_and_interval_modes():
    tz = ZoneInfo('Asia/Shanghai')
    # Thursday 2026-11-12 18:01:30 Asia/Shanghai
    thu_1801 = datetime(2026, 11, 12, 18, 1, 30, tzinfo=tz).astimezone(timezone.utc)
    thu_1759 = datetime(2026, 11, 12, 17, 59, 59, tzinfo=tz).astimezone(timezone.utc)
    thu_1803 = datetime(2026, 11, 12, 18, 3, 0, tzinfo=tz).astimezone(timezone.utc)
    sat_1801 = datetime(2026, 11, 14, 18, 1, 0, tzinfo=tz).astimezone(timezone.utc)

    base = Settings(
        timezone='Asia/Shanghai',
        llm_enabled=True,
        auto_generate=AutoGenerateConfig(
            enabled=True,
            mode='daily_times',
            daily_times=['12:00', '18:00'],
            weekdays_only=True,
        ),
    )

    # Off switches block scheduling
    assert auto_schedule.evaluate_due(base.model_copy(update={'llm_enabled': False}), {}, thu_1801)[0] is False
    disabled = base.model_copy(update={'auto_generate': AutoGenerateConfig(enabled=False)})
    assert auto_schedule.evaluate_due(disabled, {}, thu_1801)[0] is False
    assert auto_schedule.next_run_at(disabled, {}, thu_1801) is None

    # Outside window / inside 180s catch-up window / slot dedup
    assert auto_schedule.evaluate_due(base, {}, thu_1759)[0] is False
    due, slot, _ = auto_schedule.evaluate_due(base, {}, thu_1801)
    assert due is True and slot == '2026-11-12 18:00'
    assert auto_schedule.evaluate_due(base, {'handled_slots': ['2026-11-12 18:00']}, thu_1801)[0] is False
    assert auto_schedule.evaluate_due(base, {}, thu_1803)[0] is False

    # Weekdays only blocks Saturday and pushes next_run_at to Monday 12:00
    assert auto_schedule.evaluate_due(base, {}, sat_1801)[0] is False
    nxt = auto_schedule.next_run_at(base, {}, sat_1801)
    assert nxt is not None and nxt.startswith('2026-11-16T12:00:00')

    # Interval mode
    interval_cfg = Settings(
        timezone='Asia/Shanghai',
        llm_enabled=True,
        auto_generate=AutoGenerateConfig(enabled=True, mode='interval', interval_minutes=60, weekdays_only=False),
    )
    due0, slot0, _ = auto_schedule.evaluate_due(interval_cfg, {}, thu_1759)
    assert due0 is True and slot0.startswith('interval:')
    state_recent = {'last_attempt_at': (thu_1801 - timedelta(minutes=59)).isoformat()}
    assert auto_schedule.evaluate_due(interval_cfg, state_recent, thu_1801)[0] is False
    state_due = {'last_attempt_at': (thu_1801 - timedelta(minutes=60)).isoformat()}
    assert auto_schedule.evaluate_due(interval_cfg, state_due, thu_1801)[0] is True
    assert auto_schedule.evaluate_due(interval_cfg, {'last_attempt_at': 'not-a-timestamp'}, thu_1801)[0] is True


def test_scheduled_generate_collects_analyzes_and_dedups_across_runs(auto_client, monkeypatch):
    client, engine, tmp_path = auto_client
    counter = {'calls': 0}
    monkeypatch.setattr(model_provider, 'complete', _fake_complete_factory(counter, title='实现定时任务调度'))

    tz_name = 'Asia/Shanghai'
    fixed_utc = datetime(2026, 11, 12, 10, 1, 0, tzinfo=timezone.utc)  # 18:01 Asia/Shanghai
    log_file = tmp_path / 'codex-today.jsonl'
    project_dir = str(tmp_path / 'repo-a')
    _write_codex_jsonl(log_file, _codex_session(fixed_utc - timedelta(hours=2), 'sess-auto-1', '增加后台定时任务调度', project_dir))

    s = client.get('/api/settings').json()
    s['codex'] = {'enabled': True, 'paths': [str(log_file)]}
    s['auto_import_analysis'] = False
    s['auto_generate'] = {
        'enabled': True,
        'mode': 'interval',
        'daily_times': ['18:00'],
        'interval_minutes': 60,
        'weekdays_only': False,
        'collect_before_analyze': True,
        'skip_if_unchanged': True,
        'skip_if_confirmed': True,
    }
    client.put('/api/settings', json=s).raise_for_status()

    runtime = client.app.state.runtime
    # 1. First run collects & generates pending candidates without overwriting tasks list
    res1 = auto_schedule.execute_auto_generate(engine, runtime, force=False, now_utc=fixed_utc)
    assert res1 is not None and res1['triggered'] is True and res1['status_code'] == 'success'
    assert res1['new_events'] >= 3
    assert res1['pending'] == 1
    assert counter['calls'] == 2  # filter_work + task_writer

    rep1 = client.get('/api/reports/2026-11-12').json()
    assert rep1['tasks'] == []
    assert len(rep1['conversations']['analysis']['pending_tasks']) == 1

    # 2. Second scheduled run after interval elapsed with no changes skips without LLM call
    res2 = auto_schedule.execute_auto_generate(engine, runtime, force=False, now_utc=fixed_utc + timedelta(minutes=65))
    assert res2 is not None and res2['triggered'] is False and res2['status_code'] == 'skipped'
    assert counter['calls'] == 2

    # 3. Adding a new dialog turn invalidates fingerprint and triggers re-analysis
    new_rows = _codex_session(fixed_utc - timedelta(hours=2), 'sess-auto-1', '增加后台定时任务调度', project_dir) + _codex_session(
        fixed_utc + timedelta(minutes=70), 'sess-auto-2', '新增设置页面开关', project_dir
    )
    _write_codex_jsonl(log_file, new_rows)
    res3 = auto_schedule.execute_auto_generate(engine, runtime, force=False, now_utc=fixed_utc + timedelta(minutes=130))
    assert res3 is not None and res3['triggered'] is True and res3['status_code'] == 'success'
    assert res3['new_events'] >= 3
    assert counter['calls'] == 4

    # 4. Run-now with force=True runs even when unchanged, and updates status
    status_before = client.get('/api/auto-generate/status').json()
    assert status_before['last_status'] == 'success'
    assert status_before['llm_enabled'] is True


def test_auto_import_preserves_manual_and_confirmed_and_multiday(auto_client, monkeypatch):
    client, engine, tmp_path = auto_client
    counter = {'calls': 0}
    monkeypatch.setattr(model_provider, 'complete', _fake_complete_factory(counter, title='定时自动导入工作项'))

    now_local = auto_schedule.local_now('Asia/Shanghai')
    today_day = now_local.date()
    prev_day = today_day - timedelta(days=1)
    anchor_day = today_day
    today_utc = now_local.astimezone(timezone.utc)

    # Create a manual work item on prev_day and bind prev_day..today_day
    manual_id = client.post(
        '/api/tasks',
        json={'date': str(prev_day), 'title': '已有人工补录事项', 'project': '客户项目', 'confirmed_minutes': 90, 'is_todo': False},
    ).json()['id']
    client.post(
        '/api/multiday/bind',
        json={'start_date': str(prev_day), 'end_date': str(today_day), 'anchor_date': str(anchor_day), 'target_minutes': 960},
    ).raise_for_status()

    log_file = tmp_path / 'codex-multiday.jsonl'
    _write_codex_jsonl(
        log_file,
        _codex_session(today_utc - timedelta(hours=1), 'sess-md-1', '处理跨天工作汇总', str(tmp_path / 'repo-md')),
    )

    sync_triggers = []
    runtime = client.app.state.runtime
    runtime.on_tasks_changed = lambda: sync_triggers.append('triggered')

    s = client.get('/api/settings').json()
    s['codex'] = {'enabled': True, 'paths': [str(log_file)]}
    s['auto_import_analysis'] = True
    s['auto_generate'] = {
        'enabled': True,
        'mode': 'daily_times',
        'daily_times': [now_local.strftime('%H:%M')],
        'interval_minutes': 60,
        'weekdays_only': False,
        'collect_before_analyze': True,
        'skip_if_unchanged': True,
        'skip_if_confirmed': True,
    }
    client.put('/api/settings', json=s).raise_for_status()

    run_res = client.post('/api/auto-generate/run-now', json={})
    run_res.raise_for_status()
    payload = run_res.json()
    assert payload['triggered'] is True
    assert payload['auto_imported'] is True
    assert payload['created'] == 1
    assert len(sync_triggers) == 1

    rep = client.get(f'/api/reports/{today_day}').json()
    ids = {t['id'] for t in rep['tasks']}
    assert manual_id in ids
    assert any('定时自动导入工作项' in t['title'] for t in rep['tasks'])

    # Confirm the day and verify skip_if_confirmed skips next run without invoking LLM
    need_remain = rep['target_minutes'] - rep['total_minutes']
    auto_task = next(t for t in rep['tasks'] if t['origin'] == 'auto')
    client.patch(f"/api/tasks/{auto_task['id']}", json={'confirmed_minutes': need_remain}).raise_for_status()
    client.post(f'/api/reports/{today_day}/confirm', json={}).raise_for_status()

    calls_before = counter['calls']
    skip_res = auto_schedule.execute_auto_generate(engine, runtime, force=False)
    assert skip_res is not None
    assert skip_res['triggered'] is False
    assert '已确认' in skip_res['reason']
    assert counter['calls'] == calls_before


def test_lock_contention_model_error_and_api_guards(auto_client, monkeypatch):
    client, engine, tmp_path = auto_client
    now_utc = datetime.now(timezone.utc)
    today_day = auto_schedule.local_now('Asia/Shanghai', now_utc).date()

    # 1. Settings validation HTTP 400 when daily_times empty while enabled
    s = client.get('/api/settings').json()
    s['auto_generate'] = {
        'enabled': True,
        'mode': 'daily_times',
        'daily_times': [],
        'interval_minutes': 60,
        'weekdays_only': False,
        'collect_before_analyze': True,
        'skip_if_unchanged': True,
        'skip_if_confirmed': True,
    }
    bad = client.put('/api/settings', json=s)
    assert bad.status_code == 400 or bad.status_code == 422

    # 2. llm_enabled=False blocks run-now cleanly
    s['llm_enabled'] = False
    s['auto_generate']['enabled'] = False
    client.put('/api/settings', json=s).raise_for_status()
    no_llm = client.post('/api/auto-generate/run-now', json={})
    assert no_llm.status_code == 400

    # 3. Stale DB 'running' status is normalized to 'interrupted'
    auto_schedule.write_state(engine, {'last_status': 'running', 'last_reason': '执行中'})
    st_interrupted = client.get('/api/auto-generate/status').json()
    assert st_interrupted['running'] is False
    assert st_interrupted['last_status'] == 'interrupted'

    # 4. Active lock causes HTTP 409 on run-now and quiet None on scheduled_generate
    log_file = tmp_path / 'codex-err.jsonl'
    _write_codex_jsonl(log_file, _codex_session(now_utc - timedelta(hours=1), 'sess-err', '测试异常恢复', str(tmp_path)))
    s['llm_enabled'] = True
    s['codex'] = {'enabled': True, 'paths': [str(log_file)]}
    s['auto_generate'] = {
        'enabled': True,
        'mode': 'interval',
        'daily_times': ['18:00'],
        'interval_minutes': 60,
        'weekdays_only': False,
        'collect_before_analyze': True,
        'skip_if_unchanged': True,
        'skip_if_confirmed': True,
    }
    client.put('/api/settings', json=s).raise_for_status()

    runtime = client.app.state.runtime
    lock = runtime.analysis_locks.setdefault(today_day, Lock())
    assert lock.acquire(blocking=False)
    try:
        assert auto_schedule.scheduled_generate(engine, runtime) is None
        assert client.get('/api/auto-generate/status').json()['running'] is True
        assert client.post('/api/auto-generate/run-now', json={}).status_code == 409
    finally:
        lock.release()

    # 5. Model failure preserves state cleanly and records last_status='error'
    def boom(*args, **kwargs):
        raise model_provider.ModelError('模拟模型网关超时')

    monkeypatch.setattr(model_provider, 'complete', boom)
    res_err = auto_schedule.scheduled_generate(engine, runtime)
    assert res_err is not None and res_err['status_code'] == 'error'
    st_after = client.get('/api/auto-generate/status').json()
    assert st_after['last_status'] == 'error'
    assert '模拟模型网关超时' in st_after['last_error']
    assert client.post('/api/auto-generate/run-now', json={}).status_code == 400


def test_daily_slot_dedup_across_restart_and_all_excluded_skip(auto_client, monkeypatch):
    client, engine, tmp_path = auto_client
    counter = {'calls': 0}
    monkeypatch.setattr(model_provider, 'complete', _fake_complete_factory(counter, title='定时时段防重测试'))

    tz = ZoneInfo('Asia/Shanghai')
    slot_utc = datetime(2026, 11, 13, 18, 1, 0, tzinfo=tz).astimezone(timezone.utc)
    log_file = tmp_path / 'codex-slot.jsonl'
    _write_codex_jsonl(log_file, _codex_session(slot_utc - timedelta(hours=1), 'sess-slot-1', '检查每日时段幂等', str(tmp_path)))

    s = client.get('/api/settings').json()
    s['codex'] = {'enabled': True, 'paths': [str(log_file)]}
    s['auto_generate'] = {
        'enabled': True,
        'mode': 'daily_times',
        'daily_times': ['18:00'],
        'interval_minutes': 60,
        'weekdays_only': False,
        'collect_before_analyze': True,
        'skip_if_unchanged': False,
        'skip_if_confirmed': True,
    }
    client.put('/api/settings', json=s).raise_for_status()

    runtime1 = client.app.state.runtime
    res1 = auto_schedule.execute_auto_generate(engine, runtime1, force=False, now_utc=slot_utc)
    assert res1 is not None and res1['status_code'] == 'success'
    assert counter['calls'] == 2

    # Simulate app restart with a brand new AnalysisRuntime at 18:02 inside same 180s window
    runtime2 = auto_schedule.AnalysisRuntime()
    res_dup = auto_schedule.execute_auto_generate(engine, runtime2, force=False, now_utc=slot_utc + timedelta(seconds=60))
    assert res_dup is None
    assert counter['calls'] == 2

    # Excluding the only session causes skip with descriptive message
    client.post(
        '/api/conversations/exclude-session',
        json={'source': 'codex', 'session_id': 'sess-slot-1', 'reason': 'manual'},
    ).raise_for_status()
    res_excl = auto_schedule.execute_auto_generate(engine, runtime2, force=True, respect_unchanged=False, now_utc=slot_utc + timedelta(seconds=90))
    assert res_excl is not None and res_excl['status_code'] == 'skipped'
    assert '排除' in res_excl['reason']
    assert counter['calls'] == 2
