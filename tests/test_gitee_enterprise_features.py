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

    # Pull issues into local cache first; preview must not query live gitee.com issues
    c.get(f'/api/gitee/enterprise/issues?date={day}&source=remote').raise_for_status()
    monkeypatch.setattr(
        ge,
        '_paged',
        lambda tool, args: (
            CATALOG if tool == 'list_programs'
            else [{'id': 9, 'title': '任务', 'category': 'task'}] if tool == 'list_issue_types'
            else pytest.fail(f'preview must not call remote {tool}') if tool == 'list_enterprise_issues'
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


def test_preview_matches_cloud_server_without_gitee_live_query(ent_client, monkeypatch):
    from datetime import date as Date
    from pathlib import Path
    import sys
    import httpx
    from awt import cloud_sync
    from awt.cloud_client import CloudClient
    from awt.models import GiteeConfig, GiteePreviewInput

    sys.path.insert(0, str(Path(__file__).parent))
    from fake_cloud import FakeCloudServer

    c, engine = ent_client
    day = '2026-03-21'
    t1 = c.post(
        '/api/tasks',
        json={
            'date': day,
            'title': '【上海工程】服务端历史同步去重事项',
            'details': '通过服务端历史记录匹配',
            'confirmed_minutes': 60,
            'project': '上海工程技术大学档案系统',
        },
    ).json()['id']
    t2 = c.post(
        '/api/tasks',
        json={
            'date': day,
            'title': '【上海工程】多端协同发布已登记事项',
            'details': '通过服务端发布队列匹配',
            'confirmed_minutes': 90,
            'project': '上海工程技术大学档案系统',
        },
    ).json()['id']

    server = FakeCloudServer()
    transport = httpx.MockTransport(server.handle)
    cloud_client = CloudClient(base_url='https://cloud.awt.local', transport=transport)
    cloud_sync.bind(engine, 'https://cloud.awt.local', 'user@example.com', 'password123', 'TestDev', client=cloud_client)
    cloud_client.import_history_batch(
        0,
        1,
        [
            {
                'id': 'hist-777',
                'title': '【上海工程】服务端历史同步去重事项',
                'details': '来自其他设备同步的历史记录',
                'project': '上海工程技术大学档案系统',
                'work_date': day,
                'source': 'gitee_enterprise',
                'source_id': '777',
                'url': 'https://gitee.com/ent/issue/777',
            }
        ],
    )
    batch_res = cloud_client.register_publish_batch(
        idempotency_key='other-device-batch',
        executor='device',
        items=[
            {
                'work_item_id': 'other-task',
                'target_scope': 'ent:7',
                'operation': 'create',
                'target': {'enterprise_id': 7, 'program_id': 11},
                'payload': {
                    'title': '【上海工程】多端协同发布已登记事项',
                    'description': f'工作日期：{day}\n预估工时：1.5h',
                    'duration': 90,
                    'minutes': 90,
                },
            }
        ],
    )
    req_id = batch_res['items'][0]['request_id']
    cloud_client.claim_publish_request(req_id)
    cloud_client.report_publish_result(req_id, outcome='succeeded', remote_id='888', url='https://gitee.com/ent/issue/888')

    monkeypatch.setattr(
        ge,
        '_paged',
        lambda tool, args: (
            CATALOG if tool == 'list_programs'
            else pytest.fail(f'preview must not call remote {tool}')
        ),
    )

    prev = ge.preview(engine, GiteePreviewInput(date=Date(2026, 3, 21), task_ids=[t1, t2]), cloud_client=cloud_client)
    by_tid = {it['task_id']: it for it in prev['items']}
    assert by_tid[t1]['matched_issue']['issue_id'] == 777
    assert by_tid[t2]['matched_issue']['issue_id'] == 888


def test_sync_gitee_filters_by_git_global_email(ent_client, monkeypatch):
    from awt import gitee
    from awt.models import GiteeConfig

    c, _ = ent_client
    seen_issue_args = []
    monkeypatch.setattr(ge.git_tools, 'global_author', lambda: {'name': 'Me', 'email': 'me@example.com', 'source': 'global'})

    member_rows = [
        {'user_id': 101, 'name': 'Me', 'username': 'me_user', 'email': 'me@example.com'},
        {'user_id': 202, 'name': 'Coworker', 'username': 'other_user', 'email': 'coworker@example.com'},
    ]
    issue_rows = [
        {
            'id': 1001,
            'ident': 'ME-1',
            'title': '我的企业待办工作项',
            'description': '工作日期：2026-03-22',
            'state': 'open',
            'program_id': 11,
            'assignee': {'id': 101, 'email': 'me@example.com'},
            'plan_started_at': '2026-03-22T00:00:00',
        },
        {
            'id': 1002,
            'ident': 'OT-2',
            'title': '同事的工作项不应拉取',
            'description': '工作日期：2026-03-22',
            'state': 'open',
            'program_id': 11,
            'assignee': {'id': 202, 'email': 'coworker@example.com'},
            'plan_started_at': '2026-03-22T00:00:00',
        },
        {
            'id': 1003,
            'ident': 'ME-3',
            'title': '我创建但未指派的工作项',
            'description': '工作日期：2026-03-22',
            'state': 'open',
            'program_id': 11,
            'author': {'id': 101, 'email': 'me@example.com'},
            'plan_started_at': '2026-03-22T00:00:00',
        },
        {
            'id': 1004,
            'ident': 'OT-4',
            'title': '同事创建的工作项不应拉取',
            'description': '工作日期：2026-03-22',
            'state': 'open',
            'program_id': 11,
            'author': {'id': 202, 'email': 'coworker@example.com'},
            'plan_started_at': '2026-03-22T00:00:00',
        },
    ]

    def fake_paged(tool, args):
        if tool == 'list_enterprise_members':
            return member_rows
        if tool == 'list_enterprise_issues':
            seen_issue_args.append(dict(args))
            return issue_rows
        return []

    monkeypatch.setattr(ge, '_paged', fake_paged)
    all_mine = ge.fetch_all_issues(7)
    open_mine = ge.unfinished_issues(7)
    assert [i['id'] for i in all_mine] == ['1001', '1003']
    assert [i['id'] for i in open_mine] == ['1001', '1003']
    assert len(seen_issue_args) == 2
    assert all(a.get('assignee_id') == '101' for a in seen_issue_args)

    # Also verify Gitee V5 fetch and fetch_unfinished filter by git global email
    import httpx
    original_client = httpx.Client
    monkeypatch.setattr(gitee, 'token', lambda service='gitee': 'v5-token')
    monkeypatch.setattr(gitee.git_tools, 'global_author', lambda: {'name': 'Me', 'email': 'me@example.com', 'source': 'global'})
    v5_queries = []

    def v5_handler(request):
        if request.url.path.endswith('/user'):
            return httpx.Response(200, json={'login': 'me_user', 'email': 'me@example.com'})
        v5_queries.append(dict(request.url.params))
        return httpx.Response(200, json=[
            {
                'id': 1,
                'title': '我的V5工作项',
                'created_at': '2026-03-22T08:00:00Z',
                'repository': {'full_name': 'org/repo'},
                'assignee': {'login': 'me_user', 'email': 'me@example.com'},
            },
            {
                'id': 2,
                'title': '同事的V5工作项',
                'created_at': '2026-03-22T09:00:00Z',
                'repository': {'full_name': 'org/repo'},
                'assignee': {'login': 'other_user', 'email': 'coworker@example.com'},
            },
            {
                'id': 3,
                'title': '同事创建的未指派V5工作项',
                'created_at': '2026-03-22T10:00:00Z',
                'repository': {'full_name': 'org/repo'},
                'user': {'login': 'other_user', 'email': 'coworker@example.com'},
            },
        ])

    monkeypatch.setattr(gitee.httpx, 'Client', lambda **kw: original_client(transport=httpx.MockTransport(v5_handler), **kw))
    fetched, _ = gitee.fetch(GiteeConfig())
    assert [r['id'] for r in fetched] == [1]
    unfinished = gitee.fetch_unfinished(GiteeConfig())
    assert [r['id'] for r in unfinished] == ['1']
    assert v5_queries[1]['filter'] == 'assigned'
