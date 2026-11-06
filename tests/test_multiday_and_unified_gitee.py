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
