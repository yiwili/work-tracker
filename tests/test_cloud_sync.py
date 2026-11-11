from datetime import date
import sys
from pathlib import Path
import httpx
import pytest
from sqlalchemy import select, and_

from awt import cloud_sync, db, service, gitee
from awt.cloud_client import CloudClient
from awt.models import Settings, TaskInput, TaskPatch, GiteeProjectRule

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

    pub_res = gitee_enterprise.publish(eng, GiteePublishInput(
        batch_id=prev['batch_id'],
        confirmed=True,
        targets=[GiteeExportTarget(task_id=task_id, program_id=101, issue_type_id=1)],
    ), cloud_client=client)

    assert prev['batch_id'] in server.batches
    assert pub_res['created'] == 1
    matched_req = next((r for r in server.requests.values() if r.get('work_item_id') == task_id), None)
    assert matched_req is not None
    assert matched_req['status'] == 'succeeded'



def test_push_does_not_advance_pull_cursor_skipping_remote_changes(make_desktop, fake_cloud):
    server, _ = fake_cloud
    engA, clientA = make_desktop("desktopA")
    engB, clientB = make_desktop("desktopB")

    cloud_sync.bind(engA, "https://cloud.awt.local", "user@example.com", "password123", "Desktop A", client=clientA)
    clientA.token = "wts_sessionA"
    server.sessions["wts_sessionA"] = {'account_id': 'acc-1', 'device_id': 'devA'}

    cloud_sync.bind(engB, "https://cloud.awt.local", "user@example.com", "password123", "Desktop B", client=clientB)
    clientB.token = "wts_sessionB"
    server.sessions["wts_sessionB"] = {'account_id': 'acc-1', 'device_id': 'devB'}

    # 1. Desktop A creates task Alpha and syncs
    with engA.begin() as conn:
        task_a = service.save_task(conn, TaskInput(date=date(2026, 11, 6), title="Task Alpha", confirmed_minutes=60))
    resA = cloud_sync.sync_once(engA, clientA)
    assert resA['applied'] == 1

    # 2. Desktop B creates task Beta locally BEFORE pulling task Alpha
    with engB.begin() as conn:
        task_b = service.save_task(conn, TaskInput(date=date(2026, 11, 6), title="Task Beta", confirmed_minutes=45))

    # 3. Desktop B syncs. Push sends Beta; Pull cursor must NOT be advanced by push so it still pulls Alpha!
    resB = cloud_sync.sync_once(engB, clientB)
    assert resB['applied'] == 1
    assert resB['pulled'] >= 1

    # Verify Desktop B now has BOTH task Alpha and task Beta!
    with engB.connect() as conn:
        task_ids = set(conn.execute(select(db.tasks.c.id)).scalars())
        assert task_a in task_ids
        assert task_b in task_ids


def test_concurrent_edit_during_in_flight_push_preserves_newer_edit(make_desktop, fake_cloud):
    server, _ = fake_cloud
    eng, client = make_desktop("desktopA")

    cloud_sync.bind(eng, "https://cloud.awt.local", "user@example.com", "password123", "Desktop A", client=client)
    client.token = "wts_sessionA"
    server.sessions["wts_sessionA"] = {'account_id': 'acc-1', 'device_id': 'devA'}

    with eng.begin() as conn:
        task_id = service.save_task(conn, TaskInput(date=date(2026, 11, 6), title="Concurrent Edit Task", confirmed_minutes=90))

    # Hook client.push to simulate the user modifying the task from 90 to 120 while push is in flight
    orig_push = client.push
    def hooked_push(ops):
        with eng.begin() as conn:
            existing = service.get_task(conn, task_id)
            existing['confirmed_minutes'] = 120
            existing['time_locked'] = True
            service.save_task(conn, TaskInput.model_validate(existing), task_id)
        return orig_push(ops)

    client.push = hooked_push

    res = cloud_sync.sync_once(eng, client)
    assert res['applied'] == 1

    # Local value must remain 120 (not overwritten back to 90 by the push response!)
    with eng.connect() as conn:
        row = conn.execute(select(db.worklogs.c.confirmed_minutes).where(db.worklogs.c.task_id == task_id)).scalar()
        assert row == 120

        # sync_dirty must still have a dirty entry for this task
        dirty = conn.execute(select(db.sync_dirty.c.seq).where(and_(
            db.sync_dirty.c.entity_type == 'work_item',
            db.sync_dirty.c.entity_id == task_id,
        ))).scalar()
        assert dirty is not None

    # Restore orig_push and run second sync: 120 gets pushed to server
    client.push = orig_push
    res2 = cloud_sync.sync_once(eng, client)
    assert res2['applied'] == 1

    # Server entity now has confirmed_minutes = 120
    ent = server.entities.get(('acc-1', 'work_item', task_id))
    assert ent is not None
    assert ent['fields']['confirmed_minutes'] == 120


def test_publish_aborts_with_zero_gitee_calls_on_offline_401_or_claim_conflict(make_desktop, fake_cloud, monkeypatch):
    server, _ = fake_cloud
    eng, client = make_desktop("desktopA")

    cloud_sync.bind(eng, "https://cloud.awt.local", "user@example.com", "password123", "Desktop A", client=client)
    client.token = "wts_sessionA"
    server.sessions["wts_sessionA"] = {'account_id': 'acc-1', 'device_id': 'devA'}

    with eng.begin() as conn:
        cloud_sync.set_meta(conn, 'unified_publish_enabled', True)
        s = service.get_settings(conn)
        s.gitee_enterprise.enterprise_id = 88888
        service.save_settings(conn, s)
        task_id = service.save_task(conn, TaskInput(date=date(2026, 11, 6), title="Guarded Task", confirmed_minutes=60))

    gitee_calls = []
    monkeypatch.setattr("awt.gitee_enterprise.programs", lambda ent_id: [{'id': 101, 'name': 'Prog', 'ident': 'P', 'status': 0}])
    monkeypatch.setattr("awt.gitee_enterprise.issue_types", lambda ent_id, prog_id: [{'id': 1, 'name': '任务'}])
    monkeypatch.setattr("awt.gitee_enterprise.token", lambda: "fake-gitee-token")
    def fake_tool(tool, args, **kwargs):
        gitee_calls.append(tool)
        return {'id': 12345, 'issue_url': 'https://gitee.com/ent/issue/12345'}
    monkeypatch.setattr("awt.gitee_enterprise._tool", fake_tool)

    from awt import gitee_enterprise
    from awt.models import GiteePreviewInput, GiteePublishInput, GiteeExportTarget
    from awt.cloud_client import CloudUnavailable, CloudError

    prev = gitee_enterprise.preview(eng, GiteePreviewInput(date=date(2026, 11, 6)), cloud_client=client)

    # 1. Simulate Offline / CloudUnavailable -> 0 Gitee calls
    def offline_batch(*args, **kwargs):
        raise CloudUnavailable("network down")
    monkeypatch.setattr(client, "register_publish_batch", offline_batch)

    with pytest.raises(gitee_enterprise.EnterpriseError):
        gitee_enterprise.publish(eng, GiteePublishInput(
            batch_id=prev['batch_id'],
            confirmed=True,
            targets=[GiteeExportTarget(task_id=task_id, program_id=101, issue_type_id=1)],
        ), cloud_client=client)
    assert len(gitee_calls) == 0

    # 2. Simulate 401 Unauthorized -> 0 Gitee calls
    def auth_fail_batch(*args, **kwargs):
        raise CloudError(401, "unauthorized", "session expired")
    monkeypatch.setattr(client, "register_publish_batch", auth_fail_batch)

    with pytest.raises(gitee_enterprise.EnterpriseError):
        gitee_enterprise.publish(eng, GiteePublishInput(
            batch_id=prev['batch_id'],
            confirmed=True,
            targets=[GiteeExportTarget(task_id=task_id, program_id=101, issue_type_id=1)],
        ), cloud_client=client)
    assert len(gitee_calls) == 0

    # 3. Simulate claim conflict (e.g. 409 claimed by cloud or another device) -> 0 Gitee calls
    monkeypatch.undo()
    monkeypatch.setattr("awt.gitee_enterprise.programs", lambda ent_id: [{'id': 101, 'name': 'Prog', 'ident': 'P', 'status': 0}])
    monkeypatch.setattr("awt.gitee_enterprise.issue_types", lambda ent_id, prog_id: [{'id': 1, 'name': '任务'}])
    monkeypatch.setattr("awt.gitee_enterprise.token", lambda: "fake-gitee-token")
    monkeypatch.setattr("awt.gitee_enterprise._tool", fake_tool)

    def conflict_claim(req_id):
        raise CloudError(409, "already_claimed", "claimed by another device")
    monkeypatch.setattr(client, "claim_publish_request", conflict_claim)

    pub_res = gitee_enterprise.publish(eng, GiteePublishInput(
        batch_id=prev['batch_id'],
        confirmed=True,
        targets=[GiteeExportTarget(task_id=task_id, program_id=101, issue_type_id=1)],
    ), cloud_client=client)

    assert len(gitee_calls) == 0
    assert pub_res['failed'] == 1


def test_session_token_never_falls_back_to_gitee_access_token(monkeypatch):
    monkeypatch.setenv("GITEE_ACCESS_TOKEN", "sensitive-gitee-token")
    monkeypatch.setenv("GITEE_ENTERPRISE_TOKEN", "sensitive-ent-token")

    # Cloud session token must return empty string when no session file exists
    assert gitee.token(service='cloud-session') == ""

    # Gitee token still returns the configured gitee token
    assert gitee.token(service='gitee') == "sensitive-gitee-token"
    assert gitee.token(service='gitee-enterprise') == "sensitive-ent-token"


def test_cloud_client_enforces_https_for_non_loopback():
    with pytest.raises(ValueError, match="必须使用 HTTPS 协议"):
        CloudClient(base_url="http://remote-server.com")

    # Local loopbacks are allowed with http
    c1 = CloudClient(base_url="http://127.0.0.1:8765")
    assert c1.base_url == "http://127.0.0.1:8765"

    c2 = CloudClient(base_url="http://localhost:8765")
    assert c2.base_url == "http://localhost:8765"

    # HTTPS is allowed
    c3 = CloudClient(base_url="https://remote-server.com")
    assert c3.base_url == "https://remote-server.com"


def test_is_todo_flag_syncs_across_devices(make_desktop, fake_cloud):
    engA, clientA = make_desktop("desktopA")
    engB, clientB = make_desktop("desktopB")

    cloud_sync.bind(engA, "https://cloud.awt.local", "user@example.com", "password123", "Desktop A", client=clientA)
    clientA.token = "wts_sessionA"
    fake_cloud[0].sessions["wts_sessionA"] = {'account_id': 'acc-1', 'device_id': 'devA'}

    cloud_sync.bind(engB, "https://cloud.awt.local", "user@example.com", "password123", "Desktop B", client=clientB)
    clientB.token = "wts_sessionB"
    fake_cloud[0].sessions["wts_sessionB"] = {'account_id': 'acc-1', 'device_id': 'devB'}

    with engA.begin() as conn:
        manual_id = service.save_task(conn, TaskInput(date=date(2026, 11, 6), title="Manual Entry", status="requested", is_todo=False))
        todo_id = service.save_task(conn, TaskInput(date=date(2026, 11, 6), title="Real Todo", status="requested", is_todo=True))

    cloud_sync.sync_once(engA, clientA)
    cloud_sync.sync_once(engB, clientB)

    with engB.connect() as conn:
        todos_b = service.list_todos(conn)
        assert [t['id'] for t in todos_b] == [todo_id]
        tasks_b = service.list_tasks(conn, date(2026, 11, 6))
        by_id = {t['id']: t for t in tasks_b}
        assert by_id[manual_id]['is_todo'] is False
        assert by_id[todo_id]['is_todo'] is True
