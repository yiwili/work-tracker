from __future__ import annotations

from fastapi.testclient import TestClient

from awt import db
from awt.app import create_app


def test_theme_api_list_switch_and_customize(tmp_path, monkeypatch):
    monkeypatch.setenv("AWT_DATA_DIR", str(tmp_path))
    engine = db.make_engine(tmp_path / "themes.db")
    with TestClient(create_app(engine)) as client:
        res = client.get("/api/themes")
        assert res.status_code == 200
        body = res.json()
        assert body["active_theme"] == "apple-hig"
        assert body["default_theme"] == "apple-hig"
        theme_ids = [t["id"] for t in body["themes"]]
        assert "apple-hig" in theme_ids
        assert "apple-dark" in theme_ids
        assert "classic-preserved" in theme_ids

        # Switch active theme
        switch_res = client.put("/api/themes/active", json={"theme_id": "apple-dark"})
        assert switch_res.status_code == 200
        assert switch_res.json()["active_theme"] == "apple-dark"

        # Customize variables via decoupled API interface
        custom_res = client.put(
            "/api/themes/apple-dark",
            json={
                "badge": "深色 · 定制",
                "variables": {"--apple-blue": "#38bdf8"},
            },
        )
        assert custom_res.status_code == 200
        assert custom_res.json()["badge"] == "深色 · 定制"
        assert custom_res.json()["variables"]["--apple-blue"] == "#38bdf8"

        # Register a standalone custom theme via interface
        new_res = client.put(
            "/api/themes/nord-dark",
            json={
                "name": "Nord 极夜",
                "badge": "插件主题",
                "description": "通过主题接口动态挂载的独立配色",
                "base_mode": "dark",
                "variables": {"--apple-bg": "#2e3440"},
            },
        )
        assert new_res.status_code == 200
        assert new_res.json()["id"] == "nord-dark"

        # Default light theme cannot be deleted
        del_default = client.delete("/api/themes/apple-hig")
        assert del_default.status_code == 400

        # Custom theme can be deleted
        del_custom = client.delete("/api/themes/nord-dark")
        assert del_custom.status_code == 200
        remaining_ids = [t["id"] for t in del_custom.json()["themes"]]
        assert "nord-dark" not in remaining_ids
    engine.dispose()


def test_report_review_items_specify_date_location_and_indices(tmp_path, monkeypatch):
    monkeypatch.setenv("AWT_DATA_DIR", str(tmp_path))
    engine = db.make_engine(tmp_path / "review_items.db")
    with TestClient(create_app(engine)) as client:
        t1 = client.post(
            "/api/tasks",
            json={
                "date": "2026-11-10",
                "title": "【2026档案系统】优化登录页面表单校验与错误提示逻辑",
                "details": "",
                "project": "2026档案系统",
                "category": "任务",
                "status": "delivered",
                "confirmed_minutes": 60,
                "time_locked": True,
            },
        ).json()["id"]
        t2 = client.post(
            "/api/tasks",
            json={
                "date": "2026-11-10",
                "title": "【2026档案系统】优化登录页面表单校验与错误提示样式",
                "details": "",
                "project": "2026档案系统",
                "category": "任务",
                "status": "delivered",
                "confirmed_minutes": 60,
                "time_locked": True,
            },
        ).json()["id"]

        rep = client.get("/api/reports/2026-11-10").json()
        assert len(rep["warnings"]) >= 1
        assert "2026-11-10" in rep["warnings"][0]
        assert "今日工作清单" in rep["warnings"][0]
        assert "第 #1 条" in rep["warnings"][0]
        assert "第 #2 条" in rep["warnings"][0]

        assert len(rep["review_items"]) >= 1
        item = rep["review_items"][0]
        assert item["location"] == "今日工作清单"
        assert item["date"] == "2026-11-10"
        assert item["project"] == "2026档案系统"
        assert item["indices"] == [1, 2]
        assert set(item["ids"]) == {t1, t2}
        assert item["ids"] == [t["id"] for t in rep["tasks"]]
    engine.dispose()
