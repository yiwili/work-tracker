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
