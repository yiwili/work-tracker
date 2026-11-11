import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, insert, select

from awt import db, model_provider
from awt.app import create_app
from awt.models import CONVERSATION_SOURCES


def scope_all_work():
    return json.dumps({'work_refs': [], 'excluded': [], 'uncertain_refs': []})


def wrap_complete(task_fn):
    def fake(settings, system, text, **kwargs):
        payload = json.loads(text)
        if 'required_refs' in payload:
            return json.dumps({'work_refs': payload['required_refs'], 'excluded': [], 'uncertain_refs': []})
        return task_fn(settings, system, text, **kwargs)
    return fake


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR', str(tmp_path / 'data'))
    engine = db.make_engine(tmp_path / 'exclusions.db')
    with TestClient(create_app(engine)) as client:
        s = client.get('/api/settings').json()
        s.update(
            llm_enabled=True,
            llm_model='test-model',
            analysis_sources=['codex'],
            auto_import_analysis=True,
        )
        for key in CONVERSATION_SOURCES:
            s[key]['paths'] = []
        client.put('/api/settings', json=s).raise_for_status()
        yield client, engine
    engine.dispose()


def _insert_event(conn, *, eid, source='codex', session_id='s1', occurred_at='2026-11-10T02:00:00+00:00', kind='user_prompt', summary='处理接口问题'):
    conn.execute(
        insert(db.events).values(
            id=eid,
            source=source,
            session_id=session_id,
            occurred_at=occurred_at,
            kind=kind,
            summary=summary,
            cwd='',
            project='archive',
            raw_ref=f'ref:{eid}',
            metadata={},
        )
    )


def _insert_task(conn, *, tid, day='2026-11-10', title='任务', origin='auto', fingerprint=None, edited=False, confirmed_minutes=0, evidence_ids=()):
    fp = fingerprint or f'daily-v2:{tid}'
    conn.execute(
        insert(db.tasks).values(
            id=tid,
            date=day,
            title=title,
            details='详情',
            project='archive',
            category='任务',
            status='delivered',
            origin=origin,
            confidence='测试',
            evidence_level='已有行为证据',
            fingerprint=fp,
            edited=edited,
            deleted=False,
            is_todo=False,
        )
    )
    conn.execute(
        insert(db.worklogs).values(
            task_id=tid,
            confirmed_minutes=confirmed_minutes,
            suggested_minutes=None,
            is_confirmed=False,
            time_locked=confirmed_minutes > 0,
            time_source='manual',
            time_reason='',
        )
    )
    for eid in evidence_ids:
        conn.execute(insert(db.evidence).values(task_id=tid, event_id=eid))


def test_clear_ai_preserves_manual_gitee_git_adopt_and_pushed_links(app_client):
    client, engine = app_client
    day = '2026-11-10'

    with engine.begin() as conn:
        _insert_event(conn, eid='e-clear-1', session_id='s1', occurred_at='2026-11-10T01:00:00+00:00', kind='user_prompt', summary='需要清理的 AI 对话')
        _insert_event(conn, eid='e-clear-1a', session_id='s1', occurred_at='2026-11-10T01:01:00+00:00', kind='assistant_message', summary='AI 回复')
        _insert_event(conn, eid='e-shared', session_id='s2', occurred_at='2026-11-10T02:00:00+00:00', kind='user_prompt', summary='共享给已推送项的对话')
        _insert_event(conn, eid='e-git-1', source='git', session_id='repo1', occurred_at='2026-11-10T03:00:00+00:00', kind='git_commit', summary='fix: adopt commit')

        # Should be cleared (edited=True does not block bulk clear; deleted+edited=False prevents recovery popup)
        _insert_task(conn, tid='ai-clear-edited', title='AI 普通项（已微调）', origin='auto', edited=True, confirmed_minutes=60, evidence_ids=('e-clear-1', 'e-clear-1a'))
        # Should be cleared (failed Gitee push with no issue_id) and shares e-shared with protected created task
        _insert_task(conn, tid='ai-clear-failed-link', title='AI 失败推送项', origin='auto', evidence_ids=('e-clear-1', 'e-shared'))
        # Should be preserved (created Gitee link)
        _insert_task(conn, tid='ai-prot-created', title='AI 已推 Gitee', origin='auto', evidence_ids=('e-shared',))
        # Should be preserved (pending Gitee link)
        _insert_task(conn, tid='ai-prot-pending', title='AI 推送中', origin='auto')
        # Should be preserved (git-adopt)
        _insert_task(conn, tid='ai-prot-git', title='Git 提交采纳项', origin='auto', fingerprint='git-adopt:e-git-1', edited=True, evidence_ids=('e-git-1',))
        # Should be preserved (manual & gitee)
        _insert_task(conn, tid='manual-1', title='人工补录项', origin='manual', fingerprint='manual-fp-1', edited=True, confirmed_minutes=90)
        _insert_task(conn, tid='gitee-1', title='Gitee 同步项', origin='gitee', fingerprint='gitee-fp-1', edited=True)

        conn.execute(insert(db.gitee_links).values(id='link-created', enterprise_id=1, task_id='ai-prot-created', program_id=1, status='created', issue_id='8801', url='https://e.gitee.com/issues/8801', error='', updated_at='2026-11-10T04:00:00+00:00'))
        conn.execute(insert(db.gitee_links).values(id='link-pending', enterprise_id=1, task_id='ai-prot-pending', program_id=1, status='pending', issue_id='', url='', error='', updated_at='2026-11-10T04:00:00+00:00'))
        conn.execute(insert(db.gitee_links).values(id='link-failed', enterprise_id=1, task_id='ai-clear-failed-link', program_id=1, status='failed', issue_id='', url='', error='timeout', updated_at='2026-11-10T04:00:00+00:00'))

    before_rep = client.get(f'/api/reports/{day}').json()
    assert before_rep['clearable_ai_count'] == 2
    assert before_rep['protected_ai_count'] == 3

    res = client.post(f'/api/reports/{day}/clear-ai', json={})
    res.raise_for_status()
    data = res.json()
    assert data['cleared_tasks'] == 2
    assert data['retained_protected_ai'] == 3
    assert data['newly_excluded_events'] == 2

    after_rep = client.get(f'/api/reports/{day}').json()
    remaining_ids = {t['id'] for t in after_rep['tasks']}
    assert remaining_ids == {'ai-prot-created', 'ai-prot-pending', 'ai-prot-git', 'manual-1', 'gitee-1'}
    assert after_rep['clearable_ai_count'] == 0
    assert after_rep['protected_ai_count'] == 3
    task_map = {t['id']: t for t in after_rep['tasks']}
    assert task_map['ai-prot-created']['gitee_linked'] is True
    assert task_map['manual-1']['gitee_linked'] is False

    with engine.connect() as conn:
        cleared_rows = conn.execute(select(db.tasks).where(db.tasks.c.id.in_(['ai-clear-edited', 'ai-clear-failed-link']))).mappings().all()
        assert all(r['deleted'] is True and r['edited'] is False for r in cleared_rows)
        excl_rows = conn.execute(select(db.analysis_exclusions)).mappings().all()
        excl_targets = {r['target_key'] for r in excl_rows}
        assert excl_targets == {'e-clear-1', 'e-clear-1a'}
        assert 'e-shared' not in excl_targets
        assert 'e-git-1' not in excl_targets


def test_cleared_conversations_do_not_enter_next_analysis_or_recovery_popup(app_client, monkeypatch):
    client, engine = app_client
    day = '2026-11-10'

    with engine.begin() as conn:
        _insert_event(conn, eid='ev1', session_id='s1', occurred_at='2026-11-10T01:00:00+00:00', kind='user_prompt', summary='修复导出报错')
        _insert_event(conn, eid='ev2', session_id='s1', occurred_at='2026-11-10T01:01:00+00:00', kind='tool_call', summary='pytest')
        _insert_event(conn, eid='ev3', session_id='s1', occurred_at='2026-11-10T01:02:00+00:00', kind='assistant_message', summary='已修复')

    calls = 0

    def fake_complete(settings, system, text, **kwargs):
        nonlocal calls
        calls += 1
        payload = json.loads(text)
        refs = [r['ref'] for r in payload['conversations']]
        return json.dumps({
            'summary': '完成修复',
            'tasks': [{'title': '修复导出报错', 'details': '定位并修复', 'project': 'archive', 'category': '任务', 'refs': refs}],
        }, ensure_ascii=False)

    monkeypatch.setattr(model_provider, 'complete', wrap_complete(fake_complete))
    analyzed = client.post(f'/api/analyze?date={day}', json={})
    analyzed.raise_for_status()
    assert analyzed.json()['created'] == 1
    assert calls == 1

    cleared = client.post(f'/api/reports/{day}/clear-ai', json={})
    cleared.raise_for_status()
    assert cleared.json()['cleared_tasks'] == 1
    assert cleared.json()['excluded_events'] == 3

    ov = client.get(f'/api/conversations?date={day}').json()
    assert ov['user_messages'] == 0
    assert ov['assistant_messages'] == 0
    assert ov['session_count'] == 0
    assert ov['total_session_count'] == 1
    assert ov['excluded_session_count'] == 1
    assert ov['total_user_messages'] == 1
    assert ov['excluded_event_count'] == 3
    assert ov['analysis'] is None
    assert ov['stale'] is False
    assert ov['sessions'][0]['exclusion_state'] == 'full'

    calls_before = calls
    reanalyzed = client.post(f'/api/analyze?date={day}', json={})
    reanalyzed.raise_for_status()
    rdata = reanalyzed.json()
    assert calls == calls_before
    assert rdata['created'] == 0 and rdata['pending'] == 0
    assert '已全部排除' in rdata['warnings'][0]


def test_clear_pending_candidates_when_auto_import_disabled(app_client, monkeypatch):
    client, engine = app_client
    day = '2026-11-10'
    s = client.get('/api/settings').json()
    s['auto_import_analysis'] = False
    client.put('/api/settings', json=s).raise_for_status()

    with engine.begin() as conn:
        _insert_event(conn, eid='pend-1', session_id='s-pend', occurred_at='2026-11-10T01:00:00+00:00', kind='user_prompt', summary='待导入对话')

    monkeypatch.setattr(
        model_provider,
        'complete',
        wrap_complete(lambda *a, **k: json.dumps({
            'summary': '候选任务',
            'tasks': [{'title': '候选工作项', 'details': '待确认', 'project': 'archive', 'category': '任务', 'refs': ['e1']}],
        }, ensure_ascii=False)),
    )
    a_res = client.post(f'/api/analyze?date={day}', json={}).json()
    assert a_res['pending'] == 1

    c_res = client.post(f'/api/reports/{day}/clear-ai', json={}).json()
    assert c_res['cleared_tasks'] == 0
    assert c_res['cleared_pending'] == 1
    assert c_res['newly_excluded_events'] == 1

    ov = client.get(f'/api/conversations?date={day}').json()
    assert ov['analysis'] is None
    assert ov['excluded_session_count'] == 1


def test_event_level_exclusion_allows_new_turns_in_same_session_and_skips_orphans(app_client, monkeypatch):
    client, engine = app_client
    day = '2026-11-10'

    with engine.begin() as conn:
        _insert_event(conn, eid='t1-p', session_id='s-same', occurred_at='2026-11-10T01:00:00+00:00', kind='user_prompt', summary='第一轮旧需求')
        _insert_event(conn, eid='t1-r', session_id='s-same', occurred_at='2026-11-10T01:01:00+00:00', kind='assistant_message', summary='第一轮回复')

    monkeypatch.setattr(
        model_provider,
        'complete',
        wrap_complete(lambda *a, **k: json.dumps({
            'summary': '第一轮',
            'tasks': [{'title': '完成第一轮需求', 'details': 'old', 'project': 'archive', 'category': '任务', 'refs': ['e1']}],
        }, ensure_ascii=False)),
    )
    client.post(f'/api/analyze?date={day}', json={}).raise_for_status()
    client.post(f'/api/reports/{day}/clear-ai', json={}).raise_for_status()

    # Append an orphan assistant message from Turn 1 plus a brand-new Turn 2 in the SAME session
    with engine.begin() as conn:
        _insert_event(conn, eid='t1-orphan', session_id='s-same', occurred_at='2026-11-10T01:02:00+00:00', kind='assistant_message', summary='第一轮补充回复')
        _insert_event(conn, eid='t2-p', session_id='s-same', occurred_at='2026-11-10T04:00:00+00:00', kind='user_prompt', summary='第二轮新需求')
        _insert_event(conn, eid='t2-r', session_id='s-same', occurred_at='2026-11-10T04:01:00+00:00', kind='assistant_message', summary='第二轮已解决')

    ov = client.get(f'/api/conversations?date={day}').json()
    assert ov['session_count'] == 1
    assert ov['user_messages'] == 1
    assert ov['total_user_messages'] == 2
    assert ov['partial_excluded_session_count'] == 1
    assert ov['excluded_session_count'] == 0
    assert ov['sessions'][0]['exclusion_state'] == 'partial'

    captured_texts = []

    def capture_turn2(settings, system, text, **kwargs):
        payload = json.loads(text)
        captured_texts.append([c['text'] for c in payload['conversations']])
        return json.dumps({
            'summary': '第二轮',
            'tasks': [{'title': '完成第二轮新需求', 'details': 'new', 'project': 'archive', 'category': '任务', 'refs': [c['ref'] for c in payload['conversations']]}],
        }, ensure_ascii=False)

    monkeypatch.setattr(model_provider, 'complete', wrap_complete(capture_turn2))
    a2 = client.post(f'/api/analyze?date={day}', json={})
    a2.raise_for_status()
    assert a2.json()['created'] == 1
    assert captured_texts == [['第二轮新需求', '第二轮已解决']]


def test_session_exclude_and_restore_controls(app_client, monkeypatch):
    client, engine = app_client
    day = '2026-11-10'

    with engine.begin() as conn:
        _insert_event(conn, eid='s1-p', session_id='sess-1', occurred_at='2026-11-10T01:00:00+00:00', kind='user_prompt', summary='会话一内容')
        _insert_event(conn, eid='s2-p', session_id='sess-2', occurred_at='2026-11-10T02:00:00+00:00', kind='user_prompt', summary='会话二内容')

    bad_git = client.post('/api/conversations/exclude-session', json={'source': 'git', 'session_id': 'repo-1'})
    assert bad_git.status_code == 400

    ex = client.post('/api/conversations/exclude-session', json={'source': 'codex', 'session_id': 'sess-1'})
    ex.raise_for_status()
    assert ex.json()['excluded'] is True

    ov = client.get(f'/api/conversations?date={day}').json()
    assert ov['session_count'] == 1
    assert ov['excluded_session_count'] == 1
    sess_map = {s['id']: s for s in ov['sessions']}
    assert sess_map['sess-1']['session_excluded'] is True
    assert sess_map['sess-1']['exclusion_state'] == 'full'
    assert sess_map['sess-2']['exclusion_state'] == 'none'

    seen_prompts = []

    def capture_only_s2(settings, system, text, **kwargs):
        payload = json.loads(text)
        seen_prompts.append([c['text'] for c in payload['conversations']])
        return json.dumps({
            'summary': '仅会话二',
            'tasks': [{'title': '完成会话二任务', 'details': 's2', 'project': 'archive', 'category': '任务', 'refs': [c['ref'] for c in payload['conversations']]}],
        }, ensure_ascii=False)

    monkeypatch.setattr(model_provider, 'complete', wrap_complete(capture_only_s2))
    client.post(f'/api/analyze?date={day}', json={}).raise_for_status()
    assert seen_prompts == [['会话二内容']]

    # Also clear AI (excluding sess-2's event), then restore BOTH sessions
    client.post(f'/api/reports/{day}/clear-ai', json={}).raise_for_status()
    assert client.post('/api/conversations/restore-session', json={'source': 'codex', 'session_id': 'sess-1'}).json()['removed_rules'] >= 1
    assert client.post('/api/conversations/restore-session', json={'source': 'codex', 'session_id': 'sess-2'}).json()['removed_rules'] >= 1

    ov_restored = client.get(f'/api/conversations?date={day}').json()
    assert ov_restored['session_count'] == 2
    assert ov_restored['excluded_session_count'] == 0
    assert all(s['exclusion_state'] == 'none' for s in ov_restored['sessions'])

    # Re-analyze must generate both without recovery popup ("previously_deleted" must not trigger)
    seen_after_restore = []

    def capture_restored(settings, system, text, **kwargs):
        payload = json.loads(text)
        seen_after_restore.append([c['text'] for c in payload['conversations']])
        return json.dumps({
            'summary': '恢复后重建',
            'tasks': [{'title': '恢复后的完整工作项', 'details': 'both', 'project': 'archive', 'category': '任务', 'refs': [c['ref'] for c in payload['conversations']]}],
        }, ensure_ascii=False)

    monkeypatch.setattr(model_provider, 'complete', wrap_complete(capture_restored))
    resumed = client.post(f'/api/analyze?date={day}', json={})
    resumed.raise_for_status()
    assert resumed.json()['created'] == 1
    assert resumed.json()['pending_tasks'] == []
    assert seen_after_restore == [['会话一内容', '会话二内容']]


def test_multiday_binding_clear_ai_and_migration_0011(app_client, monkeypatch, tmp_path):
    client, engine = app_client
    client.post('/api/multiday/bind', json={'start_date': '2026-11-08', 'end_date': '2026-11-09', 'anchor_date': '2026-11-09'}).raise_for_status()

    with engine.begin() as conn:
        _insert_event(conn, eid='md-1', session_id='s-md1', occurred_at='2026-11-08T03:00:00+00:00', kind='user_prompt', summary='11月8日工作')
        _insert_event(conn, eid='md-2', session_id='s-md2', occurred_at='2026-11-09T03:00:00+00:00', kind='user_prompt', summary='11月9日工作')

    client.post('/api/tasks', json={'date': '2026-11-08', 'title': '跨天人工补录', 'confirmed_minutes': 60}).raise_for_status()

    monkeypatch.setattr(
        model_provider,
        'complete',
        wrap_complete(lambda settings, system, text, **k: json.dumps({
            'summary': '多天归纳',
            'tasks': [{'title': '跨天 AI 工作项', 'details': 'md', 'project': 'archive', 'category': '任务', 'refs': [c['ref'] for c in json.loads(text)['conversations']]}],
        }, ensure_ascii=False)),
    )
    client.post('/api/analyze?date=2026-11-09', json={}).raise_for_status()
    assert len(client.get('/api/reports/2026-11-09').json()['tasks']) == 2

    cleared = client.post('/api/reports/2026-11-09/clear-ai', json={}).json()
    assert cleared['cleared_tasks'] == 1
    assert cleared['newly_excluded_events'] == 2

    rep = client.get('/api/reports/2026-11-09').json()
    assert [t['title'] for t in rep['tasks']] == ['跨天人工补录']
    assert rep['binding']['active'] is True
    assert rep['conversations']['excluded_session_count'] == 2

    # Verify Alembic migration cleanly upgrades a pre-0011 database from 0010_multiday_bindings -> 0011_analysis_exclusions
    mig_engine = db.make_engine(tmp_path / 'mig_check.db')
    cfg = Config()
    cfg.set_main_option('script_location', str(Path(__file__).resolve().parent.parent / 'awt' / 'migrations'))
    with mig_engine.begin() as conn:
        cfg.attributes['connection'] = conn
        command.upgrade(cfg, '0010_multiday_bindings')
        conn.exec_driver_sql('DROP TABLE IF EXISTS analysis_exclusions')
        assert 'analysis_exclusions' not in inspect(conn).get_table_names()
        command.upgrade(cfg, '0011_analysis_exclusions')
        assert 'analysis_exclusions' in inspect(conn).get_table_names()
        idxs = {i['name'] for i in inspect(conn).get_indexes('analysis_exclusions')}
        assert 'uq_analysis_exclusions_scope_target' in idxs
    mig_engine.dispose()


def test_clear_ai_conflict_when_day_busy(app_client, monkeypatch):
    import threading
    client, engine = app_client
    day = '2026-11-10'
    with engine.begin() as conn:
        _insert_event(conn, eid='busy-e1', session_id='s-busy', kind='user_prompt', summary='忙线测试')
        _insert_task(conn, tid='busy-t1', title='AI 忙线项', origin='auto', evidence_ids=('busy-e1',))

    entered = threading.Event()
    release = threading.Event()

    def slow_complete(settings, system, text, **kwargs):
        entered.set()
        release.wait(timeout=5)
        return scope_all_work()

    monkeypatch.setattr(model_provider, 'complete', slow_complete)

    def bg_analyze():
        client.post(f'/api/analyze?date={day}', json={})

    worker = threading.Thread(target=bg_analyze)
    worker.start()
    try:
        assert entered.wait(timeout=3)
        res = client.post(f'/api/reports/{day}/clear-ai', json={})
        assert res.status_code == 409
    finally:
        release.set()
        worker.join(timeout=5)

    assert len(client.get(f'/api/tasks?date={day}').json()) == 1
