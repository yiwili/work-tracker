from datetime import date
import sys
from pathlib import Path
import httpx
import pytest
from sqlalchemy import select

from awt import cloud_sync, db, service
from awt.cloud_client import CloudClient
from awt.models import Settings, TaskInput, TaskPatch, GiteeProjectRule

sys.path.insert(0, str(Path(__file__).parent))
from fake_cloud import FakeCloudServer


@pytest.fixture
def fake_cloud():
    server = FakeCloudServer()
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


def test_two_way_sync_and_three_way_merge(make_desktop, fake_cloud):
    engA, clientA = make_desktop("desktopA")
    engB, clientB = make_desktop("desktopB")

    # 1. Bind both desktops
    cloud_sync.bind(engA, "https://cloud.awt.local", "user@example.com", "password123", "Desktop A", client=clientA)
    clientA.token = "wts_sessionA"
    fake_cloud[0].sessions["wts_sessionA"] = {'account_id': 'acc-1', 'device_id': 'devA'}

    cloud_sync.bind(engB, "https://cloud.awt.local", "user@example.com", "password123", "Desktop B", client=clientB)
    clientB.token = "wts_sessionB"
    fake_cloud[0].sessions["wts_sessionB"] = {'account_id': 'acc-1', 'device_id': 'devB'}

    # 2. Desktop A creates a task locally
    with engA.begin() as conn:
        task_id = service.save_task(conn, TaskInput(
            date=date(2026, 11, 6),
            title="Initial Title",
            confirmed_minutes=60,
        ))

    # A syncs
    resA = cloud_sync.sync_once(engA, clientA)
    assert resA['pushed'] == 1
    assert resA['applied'] == 1

    # B syncs -> receives task
    resB = cloud_sync.sync_once(engB, clientB)
    assert resB['pulled'] == 1

    with engB.connect() as conn:
        taskB = service.get_task(conn, task_id)
        assert taskB['title'] == "Initial Title"
        worklogB = conn.execute(select(db.worklogs).where(db.worklogs.c.task_id == task_id)).mappings().one()
        assert worklogB['confirmed_minutes'] == 60

    # 3. A edits title, B edits minutes (orthogonal edits)
    with engA.begin() as conn:
        service.save_task(conn, TaskInput(
            date=date(2026, 11, 6),
            title="Title Changed by A",
            confirmed_minutes=60,
        ), task_id=task_id)

    with engB.begin() as conn:
        service.save_task(conn, TaskInput(
            date=date(2026, 11, 6),
            title="Initial Title",
            confirmed_minutes=120,
        ), task_id=task_id)

    # Both sync
    cloud_sync.sync_once(engA, clientA)
    cloud_sync.sync_once(engB, clientB)
    cloud_sync.sync_once(engA, clientA)

    # Verify converged state on both
    with engA.connect() as conn:
        tA = service.get_task(conn, task_id)
        wA = conn.execute(select(db.worklogs).where(db.worklogs.c.task_id == task_id)).mappings().one()
        assert tA['title'] == "Title Changed by A"
        assert wA['confirmed_minutes'] == 120

    with engB.connect() as conn:
        tB = service.get_task(conn, task_id)
        wB = conn.execute(select(db.worklogs).where(db.worklogs.c.task_id == task_id)).mappings().one()
        assert tB['title'] == "Title Changed by A"
        assert wB['confirmed_minutes'] == 120


def test_conflict_detection_and_resolution(make_desktop, fake_cloud):
    engA, clientA = make_desktop("desktopA")
    engB, clientB = make_desktop("desktopB")

    cloud_sync.bind(engA, "https://cloud.awt.local", "user@example.com", "password123", "Desktop A", client=clientA)
    clientA.token = "wts_sessionA"
    fake_cloud[0].sessions["wts_sessionA"] = {'account_id': 'acc-1', 'device_id': 'devA'}

    cloud_sync.bind(engB, "https://cloud.awt.local", "user@example.com", "password123", "Desktop B", client=clientB)
    clientB.token = "wts_sessionB"
    fake_cloud[0].sessions["wts_sessionB"] = {'account_id': 'acc-1', 'device_id': 'devB'}

    with engA.begin() as conn:
        task_id = service.save_task(conn, TaskInput(
            date=date(2026, 11, 6),
            title="Conflict Task",
            confirmed_minutes=60,
        ))

    cloud_sync.sync_once(engA, clientA)
    cloud_sync.sync_once(engB, clientB)

    # Both modify the same field (confirmed_minutes)
    with engA.begin() as conn:
        service.save_task(conn, TaskInput(
            date=date(2026, 11, 6),
            title="Conflict Task",
            confirmed_minutes=90,
        ), task_id=task_id)

    with engB.begin() as conn:
        service.save_task(conn, TaskInput(
            date=date(2026, 11, 6),
            title="Conflict Task",
            confirmed_minutes=150,
        ), task_id=task_id)

    # A syncs first
    cloud_sync.sync_once(engA, clientA)

    # B syncs second -> conflict!
    resB = cloud_sync.sync_once(engB, clientB)
    assert resB['conflicts'] == 1

    with engB.connect() as conn:
        conflicts = cloud_sync.list_conflicts(conn)
        assert len(conflicts) == 1
        conf_id = conflicts[0]['id']
        assert 'confirmed_minutes' in conflicts[0]['fields']

    # Resolve conflict: choose remote (90m)
    cloud_sync.resolve_conflict(engB, conf_id, {'confirmed_minutes': 'remote'})

    with engB.connect() as conn:
        conflicts = cloud_sync.list_conflicts(conn)
        assert len(conflicts) == 0
        wB = conn.execute(select(db.worklogs).where(db.worklogs.c.task_id == task_id)).mappings().one()
        assert wB['confirmed_minutes'] == 90


def test_delete_vs_edit_conflict(make_desktop, fake_cloud):
    engA, clientA = make_desktop("desktopA")
    engB, clientB = make_desktop("desktopB")

    cloud_sync.bind(engA, "https://cloud.awt.local", "user@example.com", "password123", "Desktop A", client=clientA)
    clientA.token = "wts_sessionA"
    fake_cloud[0].sessions["wts_sessionA"] = {'account_id': 'acc-1', 'device_id': 'devA'}

    cloud_sync.bind(engB, "https://cloud.awt.local", "user@example.com", "password123", "Desktop B", client=clientB)
    clientB.token = "wts_sessionB"
    fake_cloud[0].sessions["wts_sessionB"] = {'account_id': 'acc-1', 'device_id': 'devB'}

    with engA.begin() as conn:
        task_id = service.save_task(conn, TaskInput(
            date=date(2026, 11, 6),
            title="To be deleted",
            confirmed_minutes=60,
        ))

    cloud_sync.sync_once(engA, clientA)
    cloud_sync.sync_once(engB, clientB)

    # B edits title
    with engB.begin() as conn:
        service.save_task(conn, TaskInput(
            date=date(2026, 11, 6),
            title="Important Edit",
            confirmed_minutes=60,
        ), task_id=task_id)
    cloud_sync.sync_once(engB, clientB)

    # A deletes it based on old version
    with engA.begin() as conn:
        service.delete_task(conn, task_id)

    # A syncs -> delete vs edit conflict
    resA = cloud_sync.sync_once(engA, clientA)
    assert resA['conflicts'] == 1


def test_snapshot_recovery_on_cursor_expired(make_desktop, fake_cloud):
    server, transport = fake_cloud
    eng, client = make_desktop("desktopA")

    cloud_sync.bind(eng, "https://cloud.awt.local", "user@example.com", "password123", "Desktop A", client=client)
    client.token = "wts_sessionA"
    server.sessions["wts_sessionA"] = {'account_id': 'acc-1', 'device_id': 'devA'}

    with eng.begin() as conn:
        tid = service.save_task(conn, TaskInput(
            date=date(2026, 11, 6),
            title="Task 1",
            confirmed_minutes=30,
        ))

    cloud_sync.sync_once(eng, client)

    # Simulate cursor expiration
    server.cursor_expired = True

    # Next sync recovers via snapshot
    res = cloud_sync.sync_once(eng, client)
    assert res['status'] == 'ok'

    with eng.connect() as conn:
        cur = cloud_sync.get_meta(conn, 'cursor')
        assert cur == server.accounts['user@example.com']['change_seq']


def test_settings_and_rules_sync(make_desktop, fake_cloud):
    engA, clientA = make_desktop("desktopA")
    engB, clientB = make_desktop("desktopB")

    cloud_sync.bind(engA, "https://cloud.awt.local", "user@example.com", "password123", "Desktop A", client=clientA)
    clientA.token = "wts_sessionA"
    fake_cloud[0].sessions["wts_sessionA"] = {'account_id': 'acc-1', 'device_id': 'devA'}

    cloud_sync.bind(engB, "https://cloud.awt.local", "user@example.com", "password123", "Desktop B", client=clientB)
    clientB.token = "wts_sessionB"
    fake_cloud[0].sessions["wts_sessionB"] = {'account_id': 'acc-1', 'device_id': 'devB'}

    # A modifies settings & adds a Gitee project rule
    with engA.begin() as conn:
        s = service.get_settings(conn)
        s.target_minutes = 540
        s.title_examples = ["示例标题1", "示例标题2"]
        s.gitee_enterprise.rules = [
            GiteeProjectRule(pattern="上海工程", mode="prefix", program_id=11)
        ]
        service.save_settings(conn, s)

    cloud_sync.sync_once(engA, clientA)
    cloud_sync.sync_once(engB, clientB)

    with engB.connect() as conn:
        sB = service.get_settings(conn)
        assert sB.target_minutes == 540
        assert sB.title_examples == ["示例标题1", "示例标题2"]
        assert len(sB.gitee_enterprise.rules) == 1
        assert sB.gitee_enterprise.rules[0].pattern == "上海工程"


def test_bind_migration_flow(make_desktop, fake_cloud):
    eng, client = make_desktop("desktopA")

    cloud_sync.bind(eng, "https://cloud.awt.local", "user@example.com", "password123", "Desktop A", client=client)
    client.token = "wts_sessionA"
    fake_cloud[0].sessions["wts_sessionA"] = {'account_id': 'acc-1', 'device_id': 'devA'}

    with eng.begin() as conn:
        service.save_task(conn, TaskInput(date=date(2026, 11, 6), title="Task 1", confirmed_minutes=60))
        service.save_task(conn, TaskInput(date=date(2026, 11, 6), title="Task 2", confirmed_minutes=60))

    preview = cloud_sync.migration_preview(eng, client=client)
    assert preview['local']['work_items'] == 2

    res = cloud_sync.migrate(eng, client=client)
    assert res['unified_publish_enabled'] is True

    with eng.connect() as conn:
        assert cloud_sync.get_meta(conn, 'unified_publish_enabled') is True


def test_cloud_coordinated_publish(make_desktop, fake_cloud, monkeypatch):
    server, transport = fake_cloud
    eng, client = make_desktop("desktopA")

    cloud_sync.bind(eng, "https://cloud.awt.local", "user@example.com", "password123", "Desktop A", client=client)
    client.token = "wts_sessionA"
    server.sessions["wts_sessionA"] = {'account_id': 'acc-1', 'device_id': 'devA'}

    with eng.begin() as conn:
        cloud_sync.set_meta(conn, 'unified_publish_enabled', True)
        s = service.get_settings(conn)
        s.gitee_enterprise.enterprise_id = 12345
        service.save_settings(conn, s)
        task_id = service.save_task(conn, TaskInput(date=date(2026, 11, 6), title="Cloud Task", confirmed_minutes=60))

    monkeypatch.setattr("awt.gitee_enterprise.programs", lambda ent_id: [{'id': 101, 'name': 'Test Program', 'ident': 'PROG', 'status': 0}])
    monkeypatch.setattr("awt.gitee_enterprise.issue_types", lambda ent_id, prog_id: [{'id': 1, 'name': '任务'}])
    monkeypatch.setattr("awt.gitee_enterprise.token", lambda: "fake-gitee-token")
    monkeypatch.setattr("awt.gitee_enterprise._tool", lambda tool, args, **kwargs: {'id': 99999, 'issue_url': 'https://gitee.com/ent/issue/99999'})

    from awt import gitee_enterprise
    from awt.models import GiteePreviewInput, GiteePublishInput, GiteeExportTarget

    prev = gitee_enterprise.preview(eng, GiteePreviewInput(date=date(2026, 11, 6)), cloud_client=client)
    assert prev['batch_id'] in server.batches

    pub_res = gitee_enterprise.publish(eng, GiteePublishInput(
        batch_id=prev['batch_id'],
        confirmed=True,
        targets=[GiteeExportTarget(task_id=task_id, program_id=101, issue_type_id=1)],
    ), cloud_client=client)

    assert pub_res['created'] == 1
    matched_req = next((r for r in server.requests.values() if r.get('work_item_id') == task_id), None)
    assert matched_req is not None
    assert matched_req['status'] == 'succeeded'

