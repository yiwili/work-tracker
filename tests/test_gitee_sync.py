"""Tests for Gitee unfinished work items sync, deduplication and enrichment."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from awt import db, service, gitee, gitee_enterprise as ge, gitee_sync
from awt.app import create_app


@pytest.fixture
def sync_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AWT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ge, "token", lambda: "ent-token-123")
    monkeypatch.setattr(gitee, "token", lambda service="gitee": "v5-token-456" if service == "gitee" else "")

    engine = db.make_engine(tmp_path / "sync.db")
    with TestClient(create_app(engine)) as c:
        c.put(
            "/api/gitee/enterprise/config",
            json={"enterprise_id": 10, "rules": [], "include_details": True},
        ).raise_for_status()
        yield c, engine
    engine.dispose()


def test_dedup_config_get_and_put(sync_client):
    c, _ = sync_client
    r = c.get("/api/gitee/dedup/config")
    r.raise_for_status()
    cfg = r.json()
    assert cfg["enabled"] is True
    assert cfg["dedup_on_pull"] is True
    assert cfg["dedup_on_publish"] is True
    assert cfg["threshold_high"] == 0.80
    assert cfg["threshold_low"] == 0.40
    assert cfg["date_weight"] == 0.20

    # Update config
    r = c.put(
        "/api/gitee/dedup/config",
        json={
            "enabled": True,
            "dedup_on_pull": True,
            "dedup_on_publish": True,
            "threshold_high": 0.85,
            "threshold_low": 0.35,
            "date_weight": 0.15,
        },
    )
    r.raise_for_status()
    updated = r.json()
    assert updated["threshold_high"] == 0.85
    assert updated["threshold_low"] == 0.35
    assert updated["date_weight"] == 0.15
    assert updated["dedup_on_publish"] is True


def test_sync_auto_match_high_confidence_prioritizes_cloud_and_preserves_local_hours(
    sync_client, monkeypatch
):
    c, engine = sync_client
    day = "2026-10-25"

    # Create local task with 90 confirmed minutes and details
    r = c.post(
        "/api/tasks",
        json={
            "date": day,
            "title": "【上海工程】修复附件管理全文提取问题",
            "details": "本地分析出的详细工作内容与排查过程",
            "confirmed_minutes": 90,
            "project": "上海工程",
        },
    )
    r.raise_for_status()
    local_task_id = r.json()["id"]

    # Mock remote unfinished issue with high confidence (>= 0.80)
    remote_issues = [
        {
            "id": "2001",
            "issue_id": "2001",
            "ident": "SHGC-2001",
            "title": "【上海工程】排查并修复附件管理全文提取问题",
            "description": "云端描述：排查附件提取失败",
            "state": "open",
            "program_id": 11,
            "program": "上海工程技术大学档案系统",
            "date": day,
            "url": "https://gitee.com/ent/issue/2001",
            "source": "gitee_enterprise",
        }
    ]
    monkeypatch.setattr(ge, "unfinished_issues", lambda eid, pid=None: remote_issues)
    monkeypatch.setattr(gitee, "fetch_unfinished", lambda cfg=None: [])

    # Call sync endpoint
    r = c.post(f"/api/reports/{day}/sync-gitee", json={})
    r.raise_for_status()
    res = r.json()

    assert len(res["auto_matched"]) == 1
    matched = res["auto_matched"][0]
    assert matched["task_id"] == local_task_id
    assert matched["confidence"] >= 0.80
    assert matched["confirmed_minutes"] == 90
    assert matched["title"] == "【上海工程】排查并修复附件管理全文提取问题"

    # Verify task in database: "以云端为主，补上本地设置的工时以及相关内容"
    report = c.get(f"/api/reports/{day}").json()
    tasks = report["tasks"]
    assert len(tasks) == 1
    t = tasks[0]
    assert t["id"] == local_task_id
    assert t["title"] == "【上海工程】排查并修复附件管理全文提取问题"  # Cloud title prioritized
    assert t["confirmed_minutes"] == 90  # Local minutes preserved
    assert t["details"] == "本地分析出的详细工作内容与排查过程"  # Local details preserved
    assert t["origin"] == "gitee"

    # Verify gitee_links record was created
    with engine.connect() as conn:
        link = conn.execute(
            select(db.gitee_links).where(db.gitee_links.c.task_id == local_task_id)
        ).mappings().first()
        assert link is not None
        assert link["issue_id"] == "2001"
        assert link["status"] == "created"


def test_sync_low_confidence_discarded(sync_client, monkeypatch):
    c, _ = sync_client
    day = "2026-10-25"

    # Local task in Project A
    c.post(
        "/api/tasks",
        json={
            "date": day,
            "title": "【上海工程】修复东方通部署死锁",
            "details": "排查并解决死锁",
            "confirmed_minutes": 60,
            "project": "上海工程",
        },
    ).raise_for_status()

    # Remote issue in completely different project B with completely different title
    remote_issues = [
        {
            "id": "9001",
            "issue_id": "9001",
            "ident": "NB-9001",
            "title": "【宁波大学】新增人脸识别摄像头配置",
            "description": "人脸配置",
            "state": "open",
            "program_id": 22,
            "program": "宁波大学",
            "date": "2026-09-01",
            "url": "https://gitee.com/ent/issue/9001",
            "source": "gitee_enterprise",
        }
    ]
    monkeypatch.setattr(ge, "unfinished_issues", lambda eid, pid=None: remote_issues)
    monkeypatch.setattr(gitee, "fetch_unfinished", lambda cfg=None: [])

    r = c.post(f"/api/reports/{day}/sync-gitee", json={})
    r.raise_for_status()
    res = r.json()

    assert len(res["auto_matched"]) == 0
    assert len(res["ambiguous"]) == 0
    assert res["discarded_count"] == 1

    # Local task remained untouched
    report = c.get(f"/api/reports/{day}").json()
    assert report["tasks"][0]["title"] == "【上海工程】修复东方通部署死锁"
    assert report["tasks"][0]["confirmed_minutes"] == 60


def test_sync_ambiguous_and_user_confirmation(sync_client, monkeypatch):
    c, _ = sync_client
    day = "2026-10-25"

    # Local task
    r = c.post(
        "/api/tasks",
        json={
            "date": day,
            "title": "【上海工程】修复附件管理全文提取问题",
            "details": "本地分析出的详细工作内容与排查过程",
            "confirmed_minutes": 75,
            "project": "上海工程",
        },
    )
    r.raise_for_status()
    local_task_id = r.json()["id"]

    # Remote issue with ambiguous similarity (~0.70)
    remote_issues = [
        {
            "id": "3001",
            "issue_id": "3001",
            "ident": "SHGC-3001",
            "title": "【上海工程】附件管理全文提取异常排查与修复",
            "description": "附件提取异常处理",
            "state": "open",
            "program_id": 11,
            "program": "上海工程",
            "date": day,
            "url": "https://gitee.com/ent/issue/3001",
            "source": "gitee_enterprise",
        }
    ]
    monkeypatch.setattr(ge, "unfinished_issues", lambda eid, pid=None: remote_issues)
    monkeypatch.setattr(gitee, "fetch_unfinished", lambda cfg=None: [])

    # Sync
    r = c.post(f"/api/reports/{day}/sync-gitee", json={})
    r.raise_for_status()
    res = r.json()

    # Should land in ambiguous range (between 0.40 and 0.80)
    assert len(res["ambiguous"]) == 1
    amb = res["ambiguous"][0]
    assert amb["id"] == "3001"
    assert 0.40 <= amb["confidence"] < 0.80
    assert amb["candidates"][0]["local_task_id"] == local_task_id

    # User decides to confirm matching
    decisions = [
        {
            "remote_issue_id": "3001",
            "action": "match",
            "target_task_id": local_task_id,
        }
    ]
    r = c.post(
        f"/api/reports/{day}/confirm-gitee-match",
        json={"date": day, "decisions": decisions},
    )
    r.raise_for_status()
    confirm_res = r.json()
    assert len(confirm_res["applied"]) == 1
    assert confirm_res["applied"][0]["action"] == "matched"
    assert confirm_res["applied"][0]["task_id"] == local_task_id

    # Verify task updated with cloud title and preserved local minutes
    report = c.get(f"/api/reports/{day}").json()
    t = report["tasks"][0]
    assert t["title"] == "【上海工程】附件管理全文提取异常排查与修复"
    assert t["confirmed_minutes"] == 75
    assert t["details"] == "本地分析出的详细工作内容与排查过程"


def test_publish_with_dedup_and_existing_issue_updates_cloud(sync_client, monkeypatch):
    c, _ = sync_client
    day = "2026-10-25"

    # Enable dedup on publish
    c.put(
        "/api/gitee/dedup/config",
        json={
            "enabled": True,
            "dedup_on_pull": True,
            "dedup_on_publish": True,
            "threshold_high": 0.80,
            "threshold_low": 0.40,
            "date_weight": 0.20,
        },
    ).raise_for_status()

    # Local task
    r = c.post(
        "/api/tasks",
        json={
            "date": day,
            "title": "【上海工程】修复附件管理全文提取问题",
            "details": "修复并验证附件全文提取功能",
            "confirmed_minutes": 100,
            "project": "上海工程",
        },
    )
    r.raise_for_status()
    task_id = r.json()["id"]

    # Remote unfinished issue
    remote_issues = [
        {
            "id": 5001,
            "title": "【上海工程】修复附件管理全文提取问题",
            "description": "云端已有未完成工作项",
            "state": "open",
            "program_id": 11,
            "program_name": "上海工程技术大学档案系统",
            "plan_started_at": day + "T00:00:00",
            "issue_url": "https://gitee.com/ent/issue/5001",
        }
    ]
    monkeypatch.setattr(ge, "_paged", lambda tool, args: (
        [{'id': 11, 'name': '上海工程技术大学档案系统', 'ident': 'SHGC', 'status': 0}] if tool == 'list_programs' else
        [{'id': 9, 'title': '任务', 'category': 'task'}] if tool == 'list_issue_types' else
        remote_issues if tool == 'list_enterprise_issues' else []
    ))

    # Sync remote issues into local cache first; preview must use local cache and skip live gitee.com queries
    c.get(f"/api/gitee/enterprise/issues?date={day}&source=remote").raise_for_status()
    monkeypatch.setattr(ge, "_paged", lambda tool, args: (
        [{'id': 11, 'name': '上海工程技术大学档案系统', 'ident': 'SHGC', 'status': 0}] if tool == 'list_programs' else
        [{'id': 9, 'title': '任务', 'category': 'task'}] if tool == 'list_issue_types' else
        pytest.fail(f"preview must not call remote {tool}") if tool == 'list_enterprise_issues' else []
    ))

    # Preview should identify matched_issue from local cache without calling list_enterprise_issues
    r = c.post("/api/gitee/enterprise/preview", json={"date": day, "task_ids": [task_id]})
    r.raise_for_status()
    p = r.json()
    assert len(p["items"]) == 1
    item = p["items"][0]
    assert item["matched_issue"] is not None
    assert item["matched_issue"]["issue_id"] == 5001
    assert item["matched_issue"]["confidence"] >= 0.80

    # Publish targeting this existing issue
    updated_calls = []
    created_calls = []

    def mock_tool(tool, args, credential=None, uncertain=False):
        if tool == "update_enterprise_issue":
            updated_calls.append(args)
            return {"id": args["issue_id"], "issue_url": f"https://gitee.com/ent/issue/{args['issue_id']}"}
        elif tool == "create_enterprise_issue":
            created_calls.append(args)
            return {"id": 9999, "issue_url": "https://gitee.com/ent/issue/9999"}
        return {}

    monkeypatch.setattr(ge, "_tool", mock_tool)

    publish_payload = {
        "batch_id": p["batch_id"],
        "confirmed": True,
        "targets": [
            {
                "task_id": task_id,
                "program_id": 11,
                "issue_type_id": 9,
                "existing_issue_id": 5001,
                "replace_existing": True,
            }
        ],
    }
    r = c.post("/api/gitee/enterprise/publish", json=publish_payload)
    r.raise_for_status()
    pub_res = r.json()

    assert pub_res["created"] == 1
    # Verify update was called, NOT create!
    assert len(updated_calls) == 1
    assert len(created_calls) == 0
    assert updated_calls[0]["issue_id"] == "5001"
    assert updated_calls[0]["duration"] == 100
    assert updated_calls[0]["description"] == ""

    # Explicit force_create must create a new Gitee item instead of silent skip
    preview2 = c.post("/api/gitee/enterprise/preview", json={"date": day, "task_ids": [task_id]})
    preview2.raise_for_status()
    p2 = preview2.json()
    assert p2["items"][0]["matched_issue"] is not None
    assert p2["items"][0]["matched_issue"]["issue_id"] == 5001
    pub_force = c.post(
        "/api/gitee/enterprise/publish",
        json={
            "batch_id": p2["batch_id"],
            "confirmed": True,
            "targets": [
                {
                    "task_id": task_id,
                    "program_id": 11,
                    "issue_type_id": 9,
                    "existing_issue_id": None,
                    "replace_existing": False,
                    "force_create": True,
                }
            ],
        },
    )
    pub_force.raise_for_status()
    assert pub_force.json()["created"] == 1
    assert len(created_calls) == 1


def test_sync_gitee_unfinished_ignores_local_ai_tasks(sync_client, monkeypatch):
    c, engine = sync_client
    day = "2026-10-25"

    r_auto = c.post(
        "/api/tasks",
        json={
            "date": day,
            "title": "【上海工程】修复附件管理全文提取问题",
            "details": "本地 AI 生成的工作内容",
            "confirmed_minutes": 90,
            "project": "上海工程技术大学档案系统",
        },
    )
    r_auto.raise_for_status()
    auto_id = r_auto.json()["id"]
    with engine.begin() as conn:
        from sqlalchemy import update
        conn.execute(update(db.tasks).where(db.tasks.c.id == auto_id).values(origin="auto"))

    remote_issues = [
        {
            "id": "6001",
            "issue_id": "6001",
            "ident": "SHGC-6001",
            "title": "【上海工程】修复附件管理全文提取问题",
            "description": "云端未完成历史事项",
            "state": "open",
            "program_id": 11,
            "program": "上海工程技术大学档案系统",
            "date": "2026-10-10",
            "url": "https://gitee.com/ent/issue/6001",
            "source": "gitee_enterprise",
        }
    ]
    monkeypatch.setattr(ge, "unfinished_issues", lambda eid, pid=None: remote_issues)
    monkeypatch.setattr(gitee, "fetch_unfinished", lambda cfg=None: [])

    res = c.post(f"/api/reports/{day}/sync-gitee", json={}).json()
    assert res["auto_matched"] == []
    assert res["ambiguous"] == []

    # Direct confirmation targeting an auto task must also leave it unchanged
    confirm_res = c.post(
        f"/api/reports/{day}/confirm-gitee-match",
        json={
            "date": day,
            "decisions": [{"remote_issue_id": "6001", "action": "match", "target_task_id": auto_id}],
        },
    ).json()
    assert confirm_res["applied"] == []

    tasks = c.get(f"/api/reports/{day}").json()["tasks"]
    assert tasks[0]["origin"] == "auto"
    assert tasks[0]["title"] == "【上海工程】修复附件管理全文提取问题"

    # Unfinished issues cached from earlier dates must not hijack preview of today's AI task,
    # and preview must not query live list_enterprise_issues.
    with engine.begin() as conn:
        ge._save_local_enterprise_issues(
            conn,
            10,
            [
                {
                    "id": 6001,
                    "title": "【上海工程】修复附件管理全文提取问题",
                    "state": "open",
                    "program_id": 11,
                    "plan_started_at": "2026-10-10T00:00:00",
                    "issue_url": "https://gitee.com/ent/issue/6001",
                }
            ],
        )
    monkeypatch.setattr(
        ge,
        "_paged",
        lambda tool, args: (
            [{"id": 11, "name": "上海工程技术大学档案系统", "ident": "SHGC", "status": 0}] if tool == "list_programs"
            else pytest.fail(f"preview must not call remote {tool}") if tool == "list_enterprise_issues"
            else []
        ),
    )
    prev = c.post("/api/gitee/enterprise/preview", json={"date": day, "task_ids": [auto_id]}).json()
    assert prev["items"][0]["matched_issue"] is None
