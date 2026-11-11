import json
from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from awt import db, model_provider, service, gitee, gitee_enterprise as ge
from awt.app import create_app
from awt.models import CONVERSATION_SOURCES, AllocationInput


@pytest.fixture
def test_client(tmp_path, monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR', str(tmp_path / 'data'))
    engine = db.make_engine(tmp_path / 'multiday.db')
    with TestClient(create_app(engine)) as client:
        s = client.get('/api/settings').json()
        s.update(llm_enabled=True, llm_model='test', analysis_sources=['codex'], auto_import_analysis=True)
        for key in CONVERSATION_SOURCES:
            s[key]['paths'] = []
        client.put('/api/settings', json=s).raise_for_status()
        yield client, engine
    engine.dispose()


def test_multiday_analysis_and_target_hours(test_client, monkeypatch):
    client, engine = test_client

    # Insert events on day 1 (2026-11-04) and day 2 (2026-11-05)
    with engine.begin() as conn:
        conn.execute(insert(db.events).values(
            id='e-day1', source='codex', session_id='s1',
            occurred_at='2026-11-04T08:30:00+08:00',
            kind='user_prompt', summary='优化系统多天报表功能',
            cwd='', project='archive', raw_ref='ref1', metadata={}
        ))
        conn.execute(insert(db.events).values(
            id='e-day2', source='codex', session_id='s2',
            occurred_at='2026-11-05T09:15:00+08:00',
            kind='user_prompt', summary='完善Gitee企业版同步接口',
            cwd='', project='archive', raw_ref='ref2', metadata={}
        ))

    captured = []
    def fake_complete(settings, system, text, **kwargs):
        payload = json.loads(text)
        if 'required_refs' in payload:
            return json.dumps({'work_refs': payload['required_refs'], 'excluded': [], 'uncertain_refs': []})
        captured.append(payload)
        return json.dumps({
            'summary': '完成了多天报表功能与Gitee企业版同步接口。',
            'tasks': [
                {'title': '优化系统多天报表功能', 'details': '分析两天的活动', 'project': 'archive', 'category': '开发', 'refs': ['e1']},
                {'title': '完善Gitee企业版同步接口', 'details': '统一企业令牌', 'project': 'archive', 'category': '开发', 'refs': ['e2']},
            ]
        }, ensure_ascii=False)

    monkeypatch.setattr(model_provider, 'complete', fake_complete)

    # Analyze 2 days (4号和5号，共16小时=960分钟) into target day 2026-11-05
    res = client.post('/api/analyze?date=2026-11-05&start_date=2026-11-04&end_date=2026-11-05&target_minutes=960', json={})
    res.raise_for_status()
    data = res.json()
    assert data['created'] == 2

    # Check report for target day 2026-11-05
    rep = client.get('/api/reports/2026-11-05').json()
    assert rep['target_minutes'] == 960  # 16 hours
    assert len(rep['tasks']) == 2
    assert all(t['date'] == '2026-11-05' for t in rep['tasks'])

    # Test updating target minutes via PUT /api/reports/{day}/target
    put_res = client.put('/api/reports/2026-11-05/target', json={'target_minutes': 1080})
    put_res.raise_for_status()
    assert put_res.json()['target_minutes'] == 1080

    # Put it back to 960
    client.put('/api/reports/2026-11-05/target', json={'target_minutes': 960}).raise_for_status()


def test_multiday_time_allocation_16_hours(test_client, monkeypatch):
    client, engine = test_client
    day = '2026-11-05'

    # Set report target to 16h (960 min)
    client.put(f'/api/reports/{day}/target', json={'target_minutes': 960}).raise_for_status()

    # Add 2 tasks to day
    t1 = client.post('/api/tasks', json={'date': day, 'title': '任务一', 'details': '描述一', 'project': 'prj', 'category': '开发'}).json()['id']
    t2 = client.post('/api/tasks', json={'date': day, 'title': '任务二', 'details': '描述二', 'project': 'prj', 'category': '开发'}).json()['id']

    # Mock time estimator
    def fake_time_estimator(settings, system, text, **kwargs):
        return json.dumps({
            'items': [
                {'id': t1, 'eligible': True, 'weight': 5, 'estimated_minutes': 480, 'reason': '按8小时估算'},
                {'id': t2, 'eligible': True, 'weight': 5, 'estimated_minutes': 480, 'reason': '按8小时估算'}
            ],
            'note': '总计16小时平分'
        })

    monkeypatch.setattr(model_provider, 'complete', fake_time_estimator)

    alloc_res = client.post(f'/api/reports/{day}/estimate-time', json={'target_minutes': 960})
    alloc_res.raise_for_status()
    alloc_data = alloc_res.json()
    assert alloc_data['allocated'] == 2

    # Check report status
    rep = client.get(f'/api/reports/{day}').json()
    assert rep['target_minutes'] == 960


def test_unified_gitee_token_and_history_sync(test_client, monkeypatch):
    client, engine = test_client

    # Save token via enterprise endpoint
    monkeypatch.setattr(ge, 'token', lambda: 'test-enterprise-token')
    monkeypatch.setattr(gitee, 'token', lambda service='gitee': 'test-enterprise-token')

    # Configure enterprise id
    client.put('/api/gitee/enterprise/config', json={'enterprise_id': 88, 'rules': []}).raise_for_status()

    # Mock enterprise issues fetch
    mock_issues = [
        {'id': 101, 'title': '企业工作项A', 'description': 'descA', 'program_name': '项目A', 'created_at': '2026-10-20T10:00:00+08:00', 'issue_url': 'https://gitee.com/ent/issue/101'},
        {'id': 102, 'title': '企业工作项B', 'description': 'descB', 'program_name': '项目B', 'created_at': '2026-10-21T10:00:00+08:00', 'issue_url': 'https://gitee.com/ent/issue/102'}
    ]
    monkeypatch.setattr(ge, '_paged', lambda tool, args: {'list_enterprise_issues': mock_issues}.get(tool, []))

    # Sync gitee history
    sync_res = client.post('/api/gitee/sync', json={})
    sync_res.raise_for_status()
    sync_data = sync_res.json()
    assert sync_data['records'] == 2
    assert '#88' in sync_data['file']


def test_multiday_collect_and_conversations_overview_and_from_events(test_client, monkeypatch, tmp_path):
    client, engine = test_client

    repo_dir = tmp_path / 'sample_repo'
    repo_dir.mkdir()
    s = client.get('/api/settings').json()
    s['git_roots'] = [str(repo_dir)]
    s['git_author'] = {'name': 'Dev User', 'email': 'dev@example.com'}
    client.put('/api/settings', json=s).raise_for_status()

    monkeypatch.setattr(
        service,
        'resolve_project',
        lambda cwd, settings, cache=None: (
            'sample_repo',
            {
                'id': 'repo-sample',
                'root': str(repo_dir),
                'project': 'sample_repo',
                'remote': '',
                'branch': 'main',
                'current_changes': '',
                'observed_at': '2026-11-05T10:00:00+00:00',
            },
        ),
    )

    collected_ranges = []

    def fake_collect_commits(repo, day, settings, warnings=None, end_day=None):
        collected_ranges.append((str(day), str(end_day)))
        return [
            {
                'id': 'commit-day3',
                'repo_id': repo['id'],
                'sha': 'a' * 40,
                'authored_at': '2026-11-03T06:00:00+00:00',
                'committed_at': '2026-11-03T06:00:00+00:00',
                'subject': 'feat: 11月3日提交的功能',
                'author_name': 'Dev User',
                'author_email': 'dev@example.com',
                'changes': '1\t0\ta.py',
            },
            {
                'id': 'commit-day5',
                'repo_id': repo['id'],
                'sha': 'b' * 40,
                'authored_at': '2026-11-05T07:00:00+00:00',
                'committed_at': '2026-11-05T07:00:00+00:00',
                'subject': 'fix: 11月5日修复的问题',
                'author_name': 'Dev User',
                'author_email': 'dev@example.com',
                'changes': '2\t1\tb.py',
            },
        ]

    monkeypatch.setattr(service, 'collect_commits', fake_collect_commits)

    # Also insert a conversation event on 2026-11-03
    with engine.begin() as conn:
        conn.execute(insert(db.events).values(
            id='chat-day3', source='codex', session_id='s-3',
            occurred_at='2026-11-03T03:00:00+00:00',
            kind='user_prompt', summary='排查11月3日的问题',
            cwd=str(repo_dir), project='sample_repo', raw_ref='ref3', metadata={}
        ))

    # 1. Multi-day collect across 2026-11-03 ~ 2026-11-05
    col_res = client.post('/api/collect?date=2026-11-05&start_date=2026-11-03&end_date=2026-11-05', json={})
    col_res.raise_for_status()
    col_data = col_res.json()
    assert col_data['start_date'] == '2026-11-03'
    assert col_data['end_date'] == '2026-11-05'
    assert col_data['git_events'] == 2
    assert collected_ranges == [('2026-11-03', '2026-11-05')]

    # 2. Multi-day conversations overview returns daily_counts for each day in the range
    conv_res = client.get('/api/conversations?date=2026-11-05&start_date=2026-11-03&end_date=2026-11-05')
    conv_res.raise_for_status()
    conv = conv_res.json()
    assert conv['date'] == '2026-11-05'
    assert conv['start_date'] == '2026-11-03'
    assert conv['end_date'] == '2026-11-05'
    assert conv['user_messages'] == 1
    daily_map = {d['date']: d for d in conv['daily_counts']}
    assert set(daily_map) == {'2026-11-03', '2026-11-04', '2026-11-05'}
    assert daily_map['2026-11-03']['user_messages'] == 1
    assert daily_map['2026-11-03']['git_events'] == 1
    assert daily_map['2026-11-04']['user_messages'] == 0
    assert daily_map['2026-11-04']['git_events'] == 0
    assert daily_map['2026-11-05']['git_events'] == 1

    # 3. Adopting a multi-day Git commit from 2026-11-03 into 2026-11-05 tasks
    ev_res = client.get('/api/events?date=2026-11-05&start_date=2026-11-03&end_date=2026-11-05').json()
    git_evs = [e for e in ev_res if e['source'] == 'git']
    assert len(git_evs) == 2

    adopt_res = client.post('/api/tasks/from-events', json={
        'date': '2026-11-05',
        'start_date': '2026-11-03',
        'end_date': '2026-11-05',
        'event_ids': [git_evs[0]['id'], git_evs[1]['id']],
    })
    adopt_res.raise_for_status()
    assert adopt_res.json()['created'] == 2


def test_multiday_binding_persistence_unified_view_rerun_and_unbind(test_client, monkeypatch):
    client, engine = test_client
    db.migrate(engine)  # Verify migration idempotency

    # Create manual entries on 2026-11-04 and 2026-11-05, plus an unrelated day 2026-11-03
    m_d1 = client.post('/api/tasks', json={
        'date': '2026-11-04',
        'title': '【项目A】第一天人工补录事项',
        'details': 'D1 manual',
        'project': 'archive',
        'category': '任务',
        'status': 'delivered',
        'is_todo': False,
        'confirmed_minutes': 60,
    }).json()['id']
    m_d2 = client.post('/api/tasks', json={
        'date': '2026-11-05',
        'title': '【项目A】第二天人工补录事项',
        'details': 'D2 manual',
        'project': 'archive',
        'category': '任务',
        'status': 'delivered',
        'is_todo': False,
        'confirmed_minutes': 60,
    }).json()['id']

    # Create an earlier todo and complete it on today (2026-11-05)
    todo_id = client.post('/api/tasks', json={
        'date': '2026-11-02',
        'title': '【项目A】遗留待办收尾',
        'details': 'todo details',
        'project': 'archive',
        'category': '任务',
        'status': 'requested',
        'is_todo': True,
        'confirmed_minutes': 0,
    }).json()['id']
    client.patch(f'/api/tasks/{todo_id}', json={'status': 'delivered', 'date': '2026-11-05'}).raise_for_status()

    # Create uncompleted todo on an earlier day (must stay in /api/todos, not leak into 11-04..11-05 report)
    pending_todo_id = client.post('/api/tasks', json={
        'date': '2026-11-02',
        'title': '【项目A】未完成待办',
        'details': '',
        'project': 'archive',
        'category': '任务',
        'status': 'requested',
        'is_todo': True,
        'confirmed_minutes': 0,
    }).json()['id']
    todos_list = client.get('/api/todos').json()
    todo_ids_in_dock = {t['id'] for t in todos_list}
    assert pending_todo_id in todo_ids_in_dock
    assert m_d1 not in todo_ids_in_dock
    assert m_d2 not in todo_ids_in_dock
    assert todo_id not in todo_ids_in_dock

    with engine.begin() as conn:
        conn.execute(insert(db.events).values(
            id='bind-e1', source='codex', session_id='sb1',
            occurred_at='2026-11-04T09:00:00+08:00',
            kind='user_prompt', summary='Day1 核心功能开发',
            cwd='', project='archive', raw_ref='r1', metadata={}
        ))
        conn.execute(insert(db.events).values(
            id='bind-e2', source='codex', session_id='sb2',
            occurred_at='2026-11-05T10:00:00+08:00',
            kind='user_prompt', summary='Day2 联调与验证',
            cwd='', project='archive', raw_ref='r2', metadata={}
        ))

    seen_payloads = []
    call_round = {'n': 0}

    def fake_complete(settings, system, text, **kwargs):
        payload = json.loads(text)
        if 'required_refs' in payload:
            return json.dumps({'work_refs': payload['required_refs'], 'excluded': [], 'uncertain_refs': []})
        seen_payloads.append(payload)
        call_round['n'] += 1
        suffix = f"（第{call_round['n']}轮）"
        return json.dumps({
            'summary': '两日协同工作分析结果',
            'tasks': [
                {'title': f'完成跨天核心模块联调{suffix}', 'details': '涵盖两天记录', 'project': 'archive', 'refs': ['e1', 'e2']},
            ],
        }, ensure_ascii=False)

    monkeypatch.setattr(model_provider, 'complete', fake_complete)

    # 1. Analyze 2026-11-04 ~ 2026-11-05 from 2026-11-05 -> automatically persists binding
    res1 = client.post('/api/analyze?date=2026-11-05&start_date=2026-11-04&end_date=2026-11-05&target_minutes=960', json={})
    res1.raise_for_status()
    data1 = res1.json()
    assert data1['binding']['active'] is True
    assert data1['binding']['start_date'] == '2026-11-04'
    assert data1['binding']['end_date'] == '2026-11-05'
    assert data1['binding']['anchor_date'] == '2026-11-05'

    # 2. Switching to either bound day shows identical combined tasks (D1 manual, D2 manual, completed todo, AI task)
    rep_d1 = client.get('/api/reports/2026-11-04').json()
    rep_d2 = client.get('/api/reports/2026-11-05').json()
    assert rep_d1['target_minutes'] == 960
    assert rep_d2['target_minutes'] == 960
    assert rep_d1['binding']['id'] == rep_d2['binding']['id']
    assert [t['id'] for t in rep_d1['tasks']] == [t['id'] for t in rep_d2['tasks']]
    ids_d1 = {t['id'] for t in rep_d1['tasks']}
    assert m_d1 in ids_d1
    assert m_d2 in ids_d1
    assert todo_id in ids_d1
    assert len(rep_d1['tasks']) == 4

    bind_status = client.get('/api/multiday/binding?date=2026-11-04').json()
    assert bind_status['bound'] is True

    # 3. Re-running AI analysis on Day 1 without explicit range uses the bound 2-day range
    res2 = client.post('/api/analyze?date=2026-11-04', json={})
    res2.raise_for_status()
    assert len(seen_payloads) == 2
    assert '2026-11-04 至 2026-11-05' in seen_payloads[-1]['date']
    rep_d1_after = client.get('/api/reports/2026-11-04').json()
    rep_d2_after = client.get('/api/reports/2026-11-05').json()
    assert [t['id'] for t in rep_d1_after['tasks']] == [t['id'] for t in rep_d2_after['tasks']]
    assert any('第2轮' in t['title'] for t in rep_d1_after['tasks'])
    assert m_d1 in {t['id'] for t in rep_d1_after['tasks']}
    assert m_d2 in {t['id'] for t in rep_d1_after['tasks']}
    assert todo_id in {t['id'] for t in rep_d1_after['tasks']}

    # 4. Changing analysis range while bound is blocked until manual unbind
    bad_range = client.post('/api/analyze?date=2026-11-04&start_date=2026-11-03&end_date=2026-11-04', json={})
    assert bad_range.status_code == 400
    assert '手动解绑' in bad_range.json()['detail']

    # 5. Cross-day merge is permitted while days are bound
    merge_res = client.post('/api/tasks/merge', json={'ids': [m_d1, m_d2]})
    merge_res.raise_for_status()
    rep_merged = client.get('/api/reports/2026-11-04').json()
    assert m_d2 not in {t['id'] for t in rep_merged['tasks']}
    assert any(t['id'] == m_d1 and t['confirmed_minutes'] == 120 for t in rep_merged['tasks'])

    # 6. Manual unbind restores independent day views and default target minutes
    unbind_res = client.post('/api/multiday/unbind', json={'date': '2026-11-04'})
    unbind_res.raise_for_status()
    assert unbind_res.json()['unbound'] is True
    assert client.get('/api/multiday/binding?date=2026-11-04').json()['bound'] is False

    rep_d1_unbound = client.get('/api/reports/2026-11-04').json()
    rep_d2_unbound = client.get('/api/reports/2026-11-05').json()
    assert rep_d1_unbound['binding'] is None
    assert rep_d1_unbound['target_minutes'] == 480
    assert rep_d2_unbound['target_minutes'] == 480
    assert {t['id'] for t in rep_d1_unbound['tasks']} == {m_d1}
    assert todo_id in {t['id'] for t in rep_d2_unbound['tasks']}
    assert m_d1 not in {t['id'] for t in rep_d2_unbound['tasks']}

    # 7. Explicit manual bind endpoint works after unbind
    bind_res = client.post('/api/multiday/bind', json={
        'start_date': '2026-11-03',
        'end_date': '2026-11-04',
        'anchor_date': '2026-11-04',
        'target_minutes': 900,
    })
    bind_res.raise_for_status()
    assert bind_res.json()['bound'] is True
    assert client.get('/api/reports/2026-11-03').json()['target_minutes'] == 900


