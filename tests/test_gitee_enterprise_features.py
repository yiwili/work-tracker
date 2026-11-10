import pytest
from fastapi.testclient import TestClient
from awt import db, gitee_enterprise as ge
from awt.app import create_app


CATALOG = [
    {'id': 11, 'name': '上海工程技术大学档案系统', 'ident': 'SHGC', 'status': 0},
    {'id': 22, 'name': '2026档案系统', 'ident': '2026', 'status': 0},
]


@pytest.fixture
def ent_client(tmp_path, monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(ge, 'token', lambda: 'ent-test-token')
    engine = db.make_engine(tmp_path / 'ent_features.db')
    with TestClient(create_app(engine)) as c:
        c.put(
            '/api/gitee/enterprise/config',
            json={'enterprise_id': 7, 'rules': [], 'include_details': True},
        ).raise_for_status()
        yield c, engine
    engine.dispose()


def test_extract_issue_date():
    assert ge._extract_issue_date({'description': '工作日期：2026-03-20\n预估工时：2h'}) == '2026-03-20'
    assert ge._extract_issue_date({'plan_started_at': '2026-03-21T00:00:00'}) == '2026-03-21'
    assert ge._extract_issue_date({'created_at': '2026-03-22T09:30:00Z'}) == '2026-03-22'
    assert ge._extract_issue_date({}) == ''


def test_enterprise_issues_by_date_endpoint(ent_client, monkeypatch):
    c, _ = ent_client
    raw_issues = [
        {
            'id': 301,
            'ident': 'SHGC-301',
            'title': '【上海工程】电子文件归档接口联调',
            'description': '工作日期：2026-03-20\n预估工时：2h\n\n接口联调说明',
            'state': 'open',
            'program_id': 11,
            'duration': 120,
            'plan_started_at': '2026-03-20T00:00:00',
            'issue_url': 'https://gitee.com/ent/issue/301',
        },
        {
            'id': 302,
            'ident': 'SHGC-302',
            'title': '【2026】历史数据迁移脚本优化',
            'description': '工作日期：2026-03-19\n预估工时：1.5h',
            'state': 'closed',
            'program_id': 22,
            'duration': 90,
            'plan_started_at': '2026-03-19T00:00:00',
            'issue_url': 'https://gitee.com/ent/issue/302',
        },
    ]
    monkeypatch.setattr(
        ge,
        '_paged',
        lambda tool, args: (
            CATALOG if tool == 'list_programs'
            else raw_issues if tool == 'list_enterprise_issues'
            else []
        ),
    )

    r = c.get('/api/gitee/enterprise/issues?date=2026-03-20')
    r.raise_for_status()
    data = r.json()
    assert data['date'] == '2026-03-20'
    assert data['enterprise_id'] == 7
    assert data['total'] == 1
    assert len(data['items']) == 1
    item = data['items'][0]
    assert item['issue_id'] == '301'
    assert item['issue_date'] == '2026-03-20'
    assert item['program_name'] == '上海工程技术大学档案系统'
    assert item['hours'] == 2.0


def test_preview_replacement_detection_and_publish_and_verify(ent_client, monkeypatch):
    c, _ = ent_client
    day = '2026-03-20'
    t_res = c.post(
        '/api/tasks',
        json={
            'date': day,
            'title': '【上海工程】电子文件归档接口联调',
            'details': '更新归档接口参数校验与回执处理',
            'confirmed_minutes': 150,
            'project': '上海工程技术大学档案系统',
        },
    )
    t_res.raise_for_status()
    task_id = t_res.json()['id']

    raw_issues = [
        {
            'id': 301,
            'ident': 'SHGC-301',
            'title': '【上海工程】电子文件归档接口联调',
            'description': '工作日期：2026-03-20\n预估工时：2h\n\n旧描述内容',
            'state': 'open',
            'program_id': 11,
            'duration': 120,
            'plan_started_at': '2026-03-20T00:00:00',
            'issue_url': 'https://gitee.com/ent/issue/301',
        }
    ]
    monkeypatch.setattr(
        ge,
        '_paged',
        lambda tool, args: (
            CATALOG if tool == 'list_programs'
            else [{'id': 9, 'title': '任务', 'category': 'task'}] if tool == 'list_issue_types'
            else raw_issues if tool == 'list_enterprise_issues'
            else []
        ),
    )

    p_res = c.post('/api/gitee/enterprise/preview', json={'date': day, 'task_ids': [task_id]})
    p_res.raise_for_status()
    preview_data = p_res.json()

    assert len(preview_data['remote_day_issues']) == 1
    assert preview_data['remote_day_issues'][0]['issue_id'] == '301'
    p_item = preview_data['items'][0]
    assert p_item['matched_issue'] is not None
    assert p_item['matched_issue']['issue_id'] == 301
    assert p_item['matched_issue']['will_replace'] is True
    assert p_item['matched_issue']['existing_minutes'] == 120
    assert '旧描述内容' in p_item['matched_issue']['existing_description']

    update_calls = []

    def mock_tool(tool, args, credential=None, uncertain=False):
        if tool == 'update_enterprise_issue':
            update_calls.append(args)
            return {'id': int(args['issue_id']), 'issue_url': 'https://gitee.com/ent/issue/301'}
        if tool == 'get_enterprise_issue_detail':
            return {
                'id': 301,
                'title': '【上海工程】电子文件归档接口联调',
                'state': 'open',
                'duration': 150,
                'issue_url': 'https://gitee.com/ent/issue/301',
            }
        raise AssertionError(f'Unexpected tool {tool}')

    monkeypatch.setattr(ge, '_tool', mock_tool)

    pub_res = c.post(
        '/api/gitee/enterprise/publish',
        json={
            'batch_id': preview_data['batch_id'],
            'confirmed': True,
            'targets': [
                {
                    'task_id': task_id,
                    'program_id': 11,
                    'issue_type_id': 9,
                    'existing_issue_id': 301,
                    'replace_existing': True,
                }
            ],
        },
    )
    pub_res.raise_for_status()
    pub_data = pub_res.json()
    assert pub_data['created'] == 1
    assert pub_data['replaced'] == 1
    assert pub_data['items'][0]['action'] == 'replaced'
    assert pub_data['items'][0]['verified'] is True
    assert len(update_calls) == 1
    assert update_calls[0]['issue_id'] == '301'
    assert update_calls[0]['duration'] == 150

    ver_res = c.get(f'/api/gitee/enterprise/verify?date={day}')
    ver_res.raise_for_status()
    ver_data = ver_res.json()
    assert ver_data['date'] == day
    assert ver_data['enterprise_id'] == 7
    assert len(ver_data['items']) == 1
    assert ver_data['items'][0]['verified'] is True
    assert ver_data['items'][0]['remote_duration'] == 150
    assert ver_data['items'][0]['remote_title'] == '【上海工程】电子文件归档接口联调'


def test_enterprise_issues_local_history_manual_sync_and_retain(ent_client, monkeypatch):
    c, _ = ent_client

    # 1. Local browse works immediately with zero remote calls even before syncing
    monkeypatch.setattr(
        ge,
        '_paged',
        lambda *a, **kw: pytest.fail('local browse must never hit remote Gitee MCP'),
    )
    local_empty = c.get('/api/gitee/enterprise/issues?date=2026-11-10&source=local')
    local_empty.raise_for_status()
    assert local_empty.json()['total'] == 0
    assert local_empty.json()['source'] == 'local'

    # 2. Manual remote sync fetches issues and caches them locally in history + integration cache
    raw_issues = [
        {
            'id': 401,
            'ident': 'SHGC-401',
            'title': '【上海工程】数据库归档校验优化',
            'description': '工作日期：2026-11-10\n预估工时：2.5h\n\n核对归档索引完整性',
            'state': 'progressing',
            'program_id': 11,
            'duration': 150,
            'plan_started_at': '2026-11-10T00:00:00',
            'issue_url': 'https://gitee.com/ent/issue/401',
        },
        {
            'id': 402,
            'ident': 'SHGC-402',
            'title': '【2026】历史凭证导出修复',
            'description': '工作日期：2026-11-09\n预估工时：1h',
            'state': 'closed',
            'program_id': 22,
            'duration': 60,
            'plan_started_at': '2026-11-09T00:00:00',
            'issue_url': 'https://gitee.com/ent/issue/402',
        },
    ]
    monkeypatch.setattr(
        ge,
        '_paged',
        lambda tool, args: (
            CATALOG if tool == 'list_programs'
            else raw_issues if tool == 'list_enterprise_issues'
            else []
        ),
    )

    remote_sync = c.get('/api/gitee/enterprise/issues?date=2026-11-10&source=remote')
    remote_sync.raise_for_status()
    sync_data = remote_sync.json()
    assert sync_data['total'] == 1
    assert sync_data['local_total'] == 2
    assert sync_data['synced_at']

    # 3. Subsequent local browse on different dates & 'all' hits zero remote calls and returns cached records
    monkeypatch.setattr(
        ge,
        '_paged',
        lambda *a, **kw: pytest.fail('local browse must use local persistence'),
    )
    local_all = c.get('/api/gitee/enterprise/issues?date=all&source=local').json()
    assert local_all['total'] == 2
    local_prev = c.get('/api/gitee/enterprise/issues?date=2026-11-09&source=local').json()
    assert local_prev['total'] == 1
    assert local_prev['items'][0]['issue_id'] == '402'

    # 4. Retain issue into local daily work_tasks (dual-end server + local retention)
    retain_res = c.post(
        '/api/gitee/enterprise/issues/retain',
        json={'date': '2026-11-10', 'issue_ids': ['401']},
    )
    retain_res.raise_for_status()
    assert retain_res.json()['created_count'] == 1

    # Verify it now appears in local daily report with preserved hours & link
    rep = c.get('/api/reports/2026-11-10').json()
    assert len(rep['tasks']) == 1
    assert rep['tasks'][0]['title'] == '【上海工程】数据库归档校验优化'
    assert rep['tasks'][0]['confirmed_minutes'] == 150
    assert rep['tasks'][0]['origin'] == 'gitee'

    # Local browse now marks #401 as linked to local report
    local_today = c.get('/api/gitee/enterprise/issues?date=2026-11-10&source=local').json()
    assert local_today['items'][0]['linked_task_id'] == rep['tasks'][0]['id']

    # Re-retaining does not duplicate the task
    retain_again = c.post(
        '/api/gitee/enterprise/issues/retain',
        json={'date': '2026-11-10', 'issue_ids': ['401']},
    ).json()
    assert retain_again['created_count'] == 0
    assert retain_again['already_linked_count'] == 1
