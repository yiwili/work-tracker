"""Tests for cloud storage of historical work items.

Verifies:
1. Preview of待导入数量 and source distribution.
2. Resumable batched upload with cursor tracking.
3. Safe repeated upload (idempotency, unchanged detection).
4. Missing date handling (None / NULLS LAST).
5. Cross-account isolation.
6. Cloud pagination, filtering (date, project, source, search).
7. FastAPI client endpoints.
"""
from datetime import datetime, timezone
import sys
from pathlib import Path
import httpx
import pytest
from sqlalchemy import select, func
from starlette.testclient import TestClient

from awt import cloud_sync, db, history_upload, gitee
from awt.app import create_app
from awt.cloud_client import CloudClient

sys.path.insert(0, str(Path(__file__).parent))
from fake_cloud import FakeCloudServer


@pytest.fixture(autouse=True)
def isolate_awt_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "awt_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AWT_DATA_DIR", str(data_dir))
    return data_dir


@pytest.fixture
def fake_cloud():
    server = FakeCloudServer()
    # Add a second account for cross-account testing
    server.accounts['user2@example.com'] = {
        'id': 'acc-2',
        'email': 'user2@example.com',
        'password': 'password123',
        'display_name': 'Second User',
        'change_seq': 0,
        'min_retained_seq': 0,
        'unified_publish_enabled': False,
    }
    transport = httpx.MockTransport(server.handle)
    return server, transport


@pytest.fixture
def make_desktop(tmp_path, fake_cloud):
    _, transport = fake_cloud

    def _factory(name: str):
        db_path = tmp_path / f"{name}.db"
        engine = db.make_engine(db_path)
        db.migrate(engine)

        client = CloudClient(
            base_url="https://cloud.awt.local",
            transport=transport,
        )
        return engine, client

    return _factory


def insert_sample_history(engine):
    items = [
        {
            'id': 'hist_001',
            'title': '修复登录页面样式漂移',
            'details': '修复移动端输入框在输入法弹出时的溢出问题',
            'project': 'frontend-app',
            'work_date': '2026-10-01',
            'source': 'gitee',
            'source_id': 'I10001',
            'url': 'https://gitee.com/org/repo/issues/I10001',
            'imported_at': '2026-10-02T10:00:00Z',
        },
        {
            'id': 'hist_002',
            'title': '重构用户权限验证中间件',
            'details': '统一使用 JWT + RBAC 权限校验',
            'project': 'backend-api',
            'work_date': '2026-10-05',
            'source': 'file',
            'source_id': '',
            'url': '',
            'imported_at': '2026-10-06T11:00:00Z',
        },
        {
            'id': 'hist_003',
            'title': '数据库连接池高并发调优',
            'details': '增加连接池最大空闲数量并优化回收超时',
            'project': 'backend-api',
            'work_date': '',  # missing date empty string
            'source': 'file',
            'source_id': '',
            'url': '',
            'imported_at': '2026-10-07T12:00:00Z',
        },
        {
            'id': 'hist_004',
            'title': '自动化部署脚本编写',
            'details': '编写 Docker 镜像构建与发布脚本',
            'project': 'infra-ops',
            'work_date': None,  # missing date None
            'source': 'gitee',
            'source_id': 'I10004',
            'url': 'https://gitee.com/org/repo/issues/I10004',
            'imported_at': '2026-10-08T13:00:00Z',
        },
        {
            'id': 'hist_005',
            'title': '系统监控告警配置',
            'details': '配置 Prometheus 告警规则与钉钉机器人通知',
            'project': 'infra-ops',
            'work_date': '2026-10-15',
            'source': 'custom',
            'source_id': 'C99',
            'url': '',
            'imported_at': '2026-10-16T14:00:00Z',
        },
    ]
    with engine.begin() as conn:
        for it in items:
            conn.execute(db.history.insert().values(**it))
    return items


def test_history_preview(make_desktop, fake_cloud):
    engine, client = make_desktop("preview_desk")
    insert_sample_history(engine)

    # 1. Preview without cloud binding
    preview = history_upload.history_preview(engine, client=None)
    local = preview['local']
    assert local['total_records'] == 5
    assert local['missing_date_count'] == 2
    assert local['earliest_date'] == '2026-10-01'
    assert local['latest_date'] == '2026-10-15'
    assert local['sources'] == {'gitee': 2, 'file': 2, 'custom': 1}
    assert preview['cursor']['completed'] is False
    assert preview['cursor']['uploaded_count'] == 0
    assert preview['cloud'] is None

    # 2. Preview with bound client (cloud currently has 0 records)
    cloud_sync.bind(engine, "https://cloud.awt.local", "user@example.com", "password123", "Desk", client=client)
    client.token = "wts_session1"
    fake_cloud[0].sessions["wts_session1"] = {'account_id': 'acc-1', 'device_id': 'dev1'}

    preview_bound = history_upload.history_preview(engine, client=client)
    assert preview_bound['cloud']['total_records'] == 0
    assert preview_bound['cloud']['missing_date_count'] == 0


def test_history_upload_batch_and_resume(make_desktop, fake_cloud):
    engine, client = make_desktop("upload_desk")
    insert_sample_history(engine)

    cloud_sync.bind(engine, "https://cloud.awt.local", "user@example.com", "password123", "Desk", client=client)
    client.token = "wts_session1"
    fake_cloud[0].sessions["wts_session1"] = {'account_id': 'acc-1', 'device_id': 'dev1'}

    # Batch 1 (size=2): hist_001, hist_002
    step1 = history_upload.upload_history_batch(engine, client, batch_size=2)
    assert step1['completed'] is False
    assert step1['items_in_batch'] == 2
    assert step1['uploaded_count'] == 2
    assert step1['batch_result']['inserted'] == 2

    # Check cursor in SQLite sync_meta
    with engine.connect() as conn:
        cursor = history_upload.get_upload_cursor(conn)
        assert cursor['last_id'] == 'hist_002'
        assert cursor['uploaded_count'] == 2
        assert cursor['completed'] is False

    # Simulate resume by running next batch (size=2): hist_003, hist_004
    step2 = history_upload.upload_history_batch(engine, client, batch_size=2)
    assert step2['completed'] is False
    assert step2['items_in_batch'] == 2
    assert step2['uploaded_count'] == 4
    assert step2['batch_result']['inserted'] == 2

    with engine.connect() as conn:
        cursor = history_upload.get_upload_cursor(conn)
        assert cursor['last_id'] == 'hist_004'
        assert cursor['uploaded_count'] == 4
        assert cursor['completed'] is False

    # Batch 3: hist_005 (remaining 1 item)
    step3 = history_upload.upload_history_batch(engine, client, batch_size=2)
    assert step3['completed'] is True
    assert step3['items_in_batch'] == 1
    assert step3['uploaded_count'] == 5
    assert step3['batch_result']['inserted'] == 1

    with engine.connect() as conn:
        cursor = history_upload.get_upload_cursor(conn)
        assert cursor['last_id'] == 'hist_005'
        assert cursor['uploaded_count'] == 5
        assert cursor['completed'] is True

    # Cloud summary check
    summary = client.cloud_history_summary()
    assert summary['total_records'] == 5
    assert summary['missing_date_count'] == 2
    assert summary['earliest_date'] == '2026-10-01'
    assert summary['latest_date'] == '2026-10-15'
    assert summary['sources'] == {'gitee': 2, 'file': 2, 'custom': 1}


def test_history_upload_idempotency_and_repeat(make_desktop, fake_cloud):
    engine, client = make_desktop("idempotent_desk")
    insert_sample_history(engine)

    cloud_sync.bind(engine, "https://cloud.awt.local", "user@example.com", "password123", "Desk", client=client)
    client.token = "wts_session1"
    fake_cloud[0].sessions["wts_session1"] = {'account_id': 'acc-1', 'device_id': 'dev1'}

    # Full upload
    res1 = history_upload.upload_all_history(engine, client, batch_size=2)
    assert res1['completed'] is True
    assert res1['inserted'] == 5
    assert res1['unchanged'] == 0

    # Repeat full upload with force_restart=True (should detect all unchanged)
    res2 = history_upload.upload_all_history(engine, client, batch_size=2, force_restart=True)
    assert res2['completed'] is True
    assert res2['inserted'] == 0
    assert res2['unchanged'] == 5

    # Cloud summary remains 5
    summary = client.cloud_history_summary()
    assert summary['total_records'] == 5


def test_history_cross_account_isolation(make_desktop, fake_cloud):
    engine1, client1 = make_desktop("desk1")
    engine2, client2 = make_desktop("desk2")

    insert_sample_history(engine1)

    # Bind desk1 to acc-1
    cloud_sync.bind(engine1, "https://cloud.awt.local", "user@example.com", "password123", "Desk 1", client=client1)
    client1.token = "wts_token1"
    fake_cloud[0].sessions["wts_token1"] = {'account_id': 'acc-1', 'device_id': 'dev1'}

    # Bind desk2 to acc-2
    cloud_sync.bind(engine2, "https://cloud.awt.local", "user2@example.com", "password123", "Desk 2", client=client2)
    client2.token = "wts_token2"
    fake_cloud[0].sessions["wts_token2"] = {'account_id': 'acc-2', 'device_id': 'dev2'}

    # Desk 1 uploads items
    history_upload.upload_all_history(engine1, client1, batch_size=10)

    # Acc 2 queries history -> must be completely isolated (0 records)
    summary2 = client2.cloud_history_summary()
    assert summary2['total_records'] == 0
    list2 = client2.list_cloud_history()
    assert list2['total'] == 0
    assert list2['items'] == []

    # Insert a single distinct record for Desk 2 and upload
    with engine2.begin() as conn:
        conn.execute(db.history.insert().values(
            id='hist_user2_001',
            title='User 2 专属历史任务',
            details='详情',
            project='user2-project',
            work_date='2026-11-01',
            source='file',
            source_id='',
            url='',
            imported_at='2026-11-01T00:00:00Z',
        ))
    history_upload.upload_all_history(engine2, client2, batch_size=10)

    # Verify acc-2 sees only 1 record
    summary2_after = client2.cloud_history_summary()
    assert summary2_after['total_records'] == 1

    # Verify acc-1 still sees only its 5 records
    summary1 = client1.cloud_history_summary()
    assert summary1['total_records'] == 5


def test_history_cloud_pagination_and_filters(make_desktop, fake_cloud):
    engine, client = make_desktop("filter_desk")
    insert_sample_history(engine)

    cloud_sync.bind(engine, "https://cloud.awt.local", "user@example.com", "password123", "Desk", client=client)
    client.token = "wts_session1"
    fake_cloud[0].sessions["wts_session1"] = {'account_id': 'acc-1', 'device_id': 'dev1'}

    history_upload.upload_all_history(engine, client, batch_size=10)

    # 1. Default list: sorted work_date DESC NULLS LAST
    res = client.list_cloud_history(page=1, limit=10)
    assert res['total'] == 5
    items = res['items']
    # Items with dates come first in descending order: 2026-10-15, 2026-10-05, 2026-10-01
    assert items[0]['work_date'] == '2026-10-15'
    assert items[1]['work_date'] == '2026-10-05'
    assert items[2]['work_date'] == '2026-10-01'
    # Items without date come last
    assert items[3]['work_date'] is None or items[3]['work_date'] == ''
    assert items[4]['work_date'] is None or items[4]['work_date'] == ''

    # 2. Filter has_date=True
    res_dated = client.list_cloud_history(has_date=True)
    assert res_dated['total'] == 3

    # 3. Filter has_date=False
    res_undated = client.list_cloud_history(has_date=False)
    assert res_undated['total'] == 2

    # 4. Filter date range: 2026-10-02 to 2026-10-10
    res_range = client.list_cloud_history(date_from='2026-10-02', date_to='2026-10-10')
    assert res_range['total'] == 1
    assert res_range['items'][0]['id'] == 'hist_002'

    # 5. Filter project
    res_proj = client.list_cloud_history(project='backend-api')
    assert res_proj['total'] == 2

    # 6. Filter source
    res_src = client.list_cloud_history(source='gitee')
    assert res_src['total'] == 2

    # 7. Keyword search q='连接池'
    res_q = client.list_cloud_history(q='连接池')
    assert res_q['total'] == 1
    assert res_q['items'][0]['id'] == 'hist_003'


def test_desktop_fastapi_endpoints(tmp_path, fake_cloud, monkeypatch):
    server, transport = fake_cloud
    db_path = tmp_path / "app_test.db"
    engine = db.make_engine(db_path)
    db.migrate(engine)
    insert_sample_history(engine)

    # Bind desktop
    client = CloudClient(base_url="https://cloud.awt.local", transport=transport)
    cloud_sync.bind(engine, "https://cloud.awt.local", "user@example.com", "password123", "Desk App", client=client)
    token = "wts_app_token"
    server.sessions[token] = {'account_id': 'acc-1', 'device_id': 'app-dev'}
    monkeypatch.setattr(gitee, "token", lambda service='': token if service == 'cloud-session' else '')

    # Patch CloudClient in app to use our transport
    orig_init = CloudClient.__init__

    def mock_init(self, base_url, token='', timeout=15.0, transport=None):
        orig_init(self, base_url, token=token, timeout=timeout, transport=transport or fake_cloud[1])

    monkeypatch.setattr(CloudClient, "__init__", mock_init)

    app = create_app(engine)
    api_client = TestClient(app)

    # 1. GET /api/history/cloud/preview
    r = api_client.get("/api/history/cloud/preview")
    assert r.status_code == 200
    p = r.json()
    assert p['local']['total_records'] == 5
    assert p['local']['missing_date_count'] == 2

    # 2. POST /api/history/cloud/upload
    r = api_client.post("/api/history/cloud/upload", json={'batch_size': 3, 'all_batches': True})
    assert r.status_code == 200
    up = r.json()
    assert up['completed'] is True
    assert up['inserted'] == 5

    # 3. GET /api/history/cloud/summary
    r = api_client.get("/api/history/cloud/summary")
    assert r.status_code == 200
    s = r.json()
    assert s['total_records'] == 5

    # 4. GET /api/history/cloud
    r = api_client.get("/api/history/cloud", params={'q': 'JWT'})
    assert r.status_code == 200
    lst = r.json()
    assert lst['total'] == 1
    assert lst['items'][0]['id'] == 'hist_002'
