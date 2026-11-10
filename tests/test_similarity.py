from datetime import date
import pytest
from fastapi.testclient import TestClient

from awt import db, service
from awt.app import create_app
from awt.models import Settings, TaskInput
from awt.similarity import (
    calculate_task_pair_similarity,
    compute_baseline_titles,
    compute_tasks_signature,
    detect_duplicates,
    synthesize_merge_recommendation,
    check_day_similarity,
    resolve_similarity,
    get_similarity_state,
    get_similarity_status,
)


@pytest.fixture
def test_db(tmp_path):
    engine = db.make_engine(tmp_path / "test.db")
    db.migrate(engine)
    return engine


@pytest.fixture
def client(test_db):
    app = create_app(test_db)
    return TestClient(app)


def test_compute_tasks_signature():
    tasks1 = [
        {"id": "t1", "title": "任务1", "details": "详情1", "project": "P1", "category": "开发", "origin": "manual", "confirmed_minutes": 60, "deleted": False},
        {"id": "t2", "title": "任务2", "details": "详情2", "project": "P2", "category": "测试", "origin": "auto", "confirmed_minutes": 0, "deleted": False},
    ]
    sig1 = compute_tasks_signature(tasks1)
    # Order of input should not matter
    sig2 = compute_tasks_signature(list(reversed(tasks1)))
    assert sig1 == sig2

    # Changing non-title fields (details, project, category, status, confirmed_minutes) does NOT change the signature
    tasks_non_title_mod = [
        {"id": "t1", "title": "任务1", "details": "修改后的详情1", "project": "P_NEW", "category": "运维", "status": "delivered", "origin": "manual", "confirmed_minutes": 180, "time_locked": True, "deleted": False},
        {"id": "t2", "title": "任务2", "details": "修改后的详情2", "project": "P2", "category": "测试", "status": "verified", "origin": "auto", "confirmed_minutes": 90, "deleted": False},
    ]
    assert compute_tasks_signature(tasks_non_title_mod) == sig1

    # Changing title changes the signature
    tasks_mod = [
        {"id": "t1", "title": "任务1(已修改)", "details": "详情1", "project": "P1", "category": "开发", "origin": "manual", "confirmed_minutes": 60, "deleted": False},
        {"id": "t2", "title": "任务2", "details": "详情2", "project": "P2", "category": "测试", "origin": "auto", "confirmed_minutes": 0, "deleted": False},
    ]
    assert compute_tasks_signature(tasks_mod) != sig1


def test_similarity_calculation_and_duplicates():
    manual = {"id": "m1", "title": "[AWT] 优化工时计算与分配算法", "details": "修改分配步长", "project": "AWT", "origin": "manual"}
    ai_duplicate = {"id": "a1", "title": "优化工时计算与分配算法", "details": "调整算法分配逻辑", "project": "AWT", "origin": "auto"}
    ai_unrelated = {"id": "a2", "title": "修复前端导航样式Bug", "details": "修改CSS", "project": "AWT", "origin": "auto"}

    score_dup, reason_dup = calculate_task_pair_similarity(manual, ai_duplicate)
    assert score_dup >= 0.70
    assert "高度重复" in reason_dup

    score_unrelated, _ = calculate_task_pair_similarity(manual, ai_unrelated)
    assert score_unrelated < 0.50

    # Dissimilar titles with identical details and project must NOT be flagged as duplicates
    ai_same_details_diff_title = {
        "id": "a3",
        "title": "重构数据库连接池配置参数",
        "details": "这是一段完全相同的很长很长的工作详情描述内容，用于测试仅对比标题",
        "project": "AWT",
        "origin": "auto",
    }
    manual_same_details = {
        "id": "m2",
        "title": "排查前端页面白屏渲染异常",
        "details": "这是一段完全相同的很长很长的工作详情描述内容，用于测试仅对比标题",
        "project": "AWT",
        "origin": "manual",
    }
    score_diff_title, _ = calculate_task_pair_similarity(manual_same_details, ai_same_details_diff_title)
    assert score_diff_title < 0.70

    dups = detect_duplicates([manual, manual_same_details, ai_duplicate, ai_unrelated, ai_same_details_diff_title], threshold=0.70)
    assert len(dups) == 1
    assert dups[0]["pair_id"] == "m1_a1"

    # Ignored pair is skipped
    dups_ignored = detect_duplicates([manual, ai_duplicate, ai_unrelated], threshold=0.70, dismissed_pairs={"m1_a1"})
    assert len(dups_ignored) == 0


def test_synthesize_merge_recommendation():
    manual = {"id": "m1", "title": "开发用户登录模块", "details": "实现JWT验证", "project": "核心系统", "category": "开发", "confirmed_minutes": 90, "origin": "manual"}
    ai = {"id": "a1", "title": "实现用户登录功能", "details": "添加登录路由与控制器", "project": "核心系统", "category": "开发", "confirmed_minutes": 0, "origin": "auto"}

    rec = synthesize_merge_recommendation(None, manual, ai, 0.85)
    assert rec["pair_id"] == "m1_a1"
    assert rec["recommended_title"] == "开发用户登录模块"
    assert "人工录入内容" in rec["recommended_details"]
    assert "AI提取内容" in rec["recommended_details"]
    assert rec["recommended_project"] == "核心系统"
    assert rec["recommended_minutes"] == 90


def test_check_day_similarity_and_resolve(test_db):
    day = "2026-11-09"
    with test_db.connect() as conn:
        service.save_settings(conn, Settings(llm_enabled=False))
        # Create a manual task and a duplicate AI task
        m_id = service.save_task(conn, TaskInput(date=day, title="优化数据库查询接口", details="手动补充的优化", project="后端服务", category="开发", confirmed_minutes=60, status="delivered"))
        a_id = service.save_task(conn, TaskInput(date=day, title="数据库查询接口性能优化", details="AI分析代码提交", project="后端服务", category="开发", confirmed_minutes=0, status="attempted"))
        # Force a_id to be origin='auto'
        conn.execute(db.tasks.update().where(db.tasks.c.id == a_id).values(origin="auto"))
        conn.commit()

        # Step 1: Check similarity -> should detect duplicates
        res = check_day_similarity(conn, day, force=False)
        assert res["status"] == "duplicates_found"
        assert res["has_duplicates"] is True
        assert len(res["pairs"]) == 1
        assert res["pairs"][0]["pair_id"] == f"{m_id}_{a_id}"

        # Status check
        tasks = service.list_tasks(conn, day)
        st = get_similarity_status(conn, day, tasks)
        assert st["checked"] is True
        assert st["handled"] is False
        assert st["has_duplicates"] is True

        # Step 2: Resolve by merging
        resolution = {
            "pair_id": f"{m_id}_{a_id}",
            "action": "ai_merge",
            "target_task_id": m_id,
            "title": "优化数据库查询接口（已合并）",
            "details": "合并后详情",
            "project": "后端服务",
            "category": "开发",
            "confirmed_minutes": 60,
        }
        resolve_res = resolve_similarity(conn, day, [resolution])
        assert resolve_res["success"] is True

        # Task a_id should now be deleted, m_id updated
        active_tasks = service.list_tasks(conn, day)
        assert len(active_tasks) == 1
        assert active_tasks[0]["id"] == m_id
        assert active_tasks[0]["title"] == "优化数据库查询接口（已合并）"

        # Re-check status -> should now be clean and handled
        st_after = get_similarity_status(conn, day, active_tasks)
        assert st_after["checked"] is True
        assert st_after["handled"] is True
        assert st_after["has_duplicates"] is False
        assert st_after["tasks_changed"] is False

        # check_day_similarity without force should return clean without work
        res2 = check_day_similarity(conn, day, force=False)
        assert res2["status"] == "clean"
        assert res2["has_duplicates"] is False


def test_api_similarity_and_estimate_time_enforcement(client, test_db):
    day = "2026-11-09"
    with test_db.connect() as conn:
        service.save_settings(conn, Settings(llm_enabled=False, target_minutes=120))
        m_id = service.save_task(conn, TaskInput(date=day, title="订单退款流程开发", details="退款接口", project="商城", category="开发", confirmed_minutes=60, status="delivered"))
        a_id = service.save_task(conn, TaskInput(date=day, title="订单退款流程开发", details="退款业务逻辑", project="商城", category="开发", confirmed_minutes=0, status="attempted"))
        conn.execute(db.tasks.update().where(db.tasks.c.id == a_id).values(origin="auto"))
        conn.commit()

    # 1. Calling estimate-time should be blocked by 409 because of unhandled duplicates
    est_resp = client.post(f"/api/reports/{day}/estimate-time", json={})
    assert est_resp.status_code == 409
    assert "高度重复" in est_resp.json()["detail"]

    # 2. Manual similarity check API
    chk_resp = client.post(f"/api/reports/{day}/check-similarity", json={"force": True})
    assert chk_resp.status_code == 200
    data = chk_resp.json()
    assert data["status"] == "duplicates_found"
    assert len(data["pairs"]) == 1

    # 3. Resolve similarity via API
    resolve_resp = client.post(f"/api/reports/{day}/resolve-similarity", json={
        "resolutions": [
            {
                "pair_id": f"{m_id}_{a_id}",
                "action": "ai_merge",
                "target_task_id": m_id,
                "title": "订单退款流程开发（合并）",
                "details": "合并后的详情",
                "project": "商城",
                "category": "开发",
                "confirmed_minutes": 60,
            }
        ]
    })
    assert resolve_resp.status_code == 200

    # 4. Now calling estimate-time should succeed (or not fail on similarity check)
    est_resp2 = client.post(f"/api/reports/{day}/estimate-time", json={})
    assert est_resp2.status_code in (200, 400) # 400 if minutes apportionment logic notes something, but not 409 similarity blocked!
    if est_resp2.status_code == 409:
        assert "高度重复" not in est_resp2.json().get("detail", "")


def test_task_modification_triggers_recheck(test_db):
    day = "2026-11-09"
    with test_db.connect() as conn:
        service.save_settings(conn, Settings(llm_enabled=False))
        m_id = service.save_task(conn, TaskInput(date=day, title="编写单元测试", details="测试代码", project="测试项目", category="测试", confirmed_minutes=30, status="delivered"))
        a_id = service.save_task(conn, TaskInput(date=day, title="编写单元测试用例", details="测试自动化", project="测试项目", category="测试", confirmed_minutes=0, status="attempted"))
        conn.execute(db.tasks.update().where(db.tasks.c.id == a_id).values(origin="auto"))
        conn.commit()

        # Step 1: Resolve duplicate with ignore
        resolve_similarity(conn, day, [{"pair_id": f"{m_id}_{a_id}", "action": "ignore"}])
        st1 = get_similarity_status(conn, day, service.list_tasks(conn, day))
        assert st1["handled"] is True
        assert st1["tasks_changed"] is False
        assert st1["titles_modified"] is False

        # Step 2a: User modifies ONLY details, confirmed_minutes, or status (not title) -> should NOT trigger recheck!
        service.save_task(conn, TaskInput(date=day, title="编写单元测试", details="修改了详情和工时但没改标题", project="测试项目2", category="开发", confirmed_minutes=90, status="verified"), task_id=m_id)
        conn.commit()
        st_no_title_change = get_similarity_status(conn, day, service.list_tasks(conn, day))
        assert st_no_title_change["tasks_changed"] is False
        assert st_no_title_change["titles_modified"] is False

        # Step 2b: User modifies the title!
        service.save_task(conn, TaskInput(date=day, title="编写单元测试（已更新）", details="新增更多测试", project="测试项目", category="测试", confirmed_minutes=45, status="delivered"), task_id=m_id)
        conn.commit()

        # Step 3: Check status -> tasks_changed and titles_modified must be True!
        st2 = get_similarity_status(conn, day, service.list_tasks(conn, day))
        assert st2["tasks_changed"] is True
        assert st2["titles_modified"] is True


def test_ai_analysis_import_auto_checks_similarity(test_db):
    from types import SimpleNamespace
    from awt.daily_analysis import import_pending

    day = "2026-11-10"
    with test_db.connect() as conn:
        service.save_settings(conn, Settings(llm_enabled=False))
        # Create a manual task first
        m_id = service.save_task(
            conn,
            TaskInput(
                date=day,
                title="优化报表导出接口性能",
                details="人工记录",
                project="AWT",
                category="开发",
                confirmed_minutes=60,
                status="delivered",
            ),
        )
        # Insert an event and pending analysis snapshot
        conn.execute(
            db.events.insert().values(
                id="ev1",
                source="codex",
                session_id="s1",
                occurred_at="2026-11-10T09:00:00+08:00",
                kind="user_prompt",
                summary="优化报表导出接口性能",
                cwd="/tmp",
                project="AWT",
                raw_ref="r1",
                metadata={},
            )
        )
        service.upsert(
            conn,
            db.analyses,
            {
                "date": day,
                "snapshot": {
                    "pending_tasks": [
                        {
                            "fingerprint": "daily-v2:fp1",
                            "title": "优化报表导出接口性能",
                            "details": "AI分析记录",
                            "project": "AWT",
                            "category": "开发",
                            "status": "changed",
                            "evidence_level": "已有行为证据",
                            "evidence_ids": ["ev1"],
                        }
                    ]
                },
            },
            "date",
        )
        res = import_pending(conn, day, [SimpleNamespace(fingerprint="daily-v2:fp1", title=None, details=None, project=None, category=None, status=None)])
        conn.commit()

        assert "similarity_check" in res
        sim_check = res["similarity_check"]
        assert sim_check["has_duplicates"] is True
        assert len(sim_check["pairs"]) == 1

        state = get_similarity_state(conn, day)
        assert state is not None
        assert "baseline_titles" in state
        assert state["titles_modified"] is False
        assert state["baseline_titles"][m_id] == "优化报表导出接口性能"


def test_keep_manual_and_keep_ai_actions(test_db):
    day = "2026-11-09"
    with test_db.connect() as conn:
        service.save_settings(conn, Settings(llm_enabled=False))
        m1 = service.save_task(conn, TaskInput(date=day, title="部署后端服务A", details="人工部署步骤", project="部署", category="运维", confirmed_minutes=45, status="delivered"))
        a1 = service.save_task(conn, TaskInput(date=day, title="部署后端服务A", details="自动记录部署脚本", project="部署", category="运维", confirmed_minutes=0, status="attempted"))
        conn.execute(db.tasks.update().where(db.tasks.c.id == a1).values(origin="auto"))

        m2 = service.save_task(conn, TaskInput(date=day, title="部署前端服务B", details="人工部署前端", project="部署", category="运维", confirmed_minutes=60, status="delivered"))
        a2 = service.save_task(conn, TaskInput(date=day, title="部署前端服务B", details="自动构建前端", project="部署", category="运维", confirmed_minutes=0, status="attempted"))
        conn.execute(db.tasks.update().where(db.tasks.c.id == a2).values(origin="auto"))
        conn.commit()

        # Pair 1: keep_manual -> a1 deleted, m1 kept
        # Pair 2: keep_ai -> m2 deleted, a2 kept with 60 confirmed minutes
        resolve_similarity(conn, day, [
            {"pair_id": f"{m1}_{a1}", "action": "keep_manual"},
            {"pair_id": f"{m2}_{a2}", "action": "keep_ai"},
        ])

        active = service.list_tasks(conn, day)
        active_ids = {t["id"]: t for t in active}

        assert m1 in active_ids
        assert a1 not in active_ids

        assert m2 not in active_ids
        assert a2 in active_ids
        assert active_ids[a2]["confirmed_minutes"] == 60


def test_report_contains_similarity_status(client, test_db):
    day = "2026-11-09"
    with test_db.connect() as conn:
        service.save_settings(conn, Settings(llm_enabled=False))
        service.save_task(conn, TaskInput(date=day, title="任务A", details="", project="P", category="开发", confirmed_minutes=0, status="delivered"))
        conn.commit()

    resp = client.get(f"/api/reports/{day}")
    assert resp.status_code == 200
    report = resp.json()
    assert "similarity_status" in report
    sim = report["similarity_status"]
    assert "checked" in sim
    assert "tasks_changed" in sim
    assert "handled" in sim
