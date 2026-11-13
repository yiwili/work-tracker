from pathlib import Path
import json
import os
import sys

from fastapi.testclient import TestClient

from awt import app as app_module, db, portable


def test_resolve_portable_data_dir_markers(tmp_path):
    assert db.resolve_portable_data_dir(tmp_path) is None

    flag_dir = tmp_path / "with-flag"
    flag_dir.mkdir()
    (flag_dir / "portable.flag").write_text("1", encoding="utf-8")
    assert db.resolve_portable_data_dir(flag_dir) == flag_dir / "data"

    plain_dir = tmp_path / "with-plain"
    plain_dir.mkdir()
    (plain_dir / "portable").write_text("", encoding="utf-8")
    assert db.resolve_portable_data_dir(plain_dir) == plain_dir / "data"

    data_dir = tmp_path / "with-data"
    (data_dir / "data").mkdir(parents=True)
    assert db.resolve_portable_data_dir(data_dir) == data_dir / "data"


def test_frozen_data_dir_prefers_env_then_portable_then_localappdata(tmp_path, monkeypatch):
    fake_exe_dir = tmp_path / "app"
    fake_exe_dir.mkdir()
    (fake_exe_dir / "portable.flag").write_text("1", encoding="utf-8")
    fake_exe = fake_exe_dir / "ai-work-tracker.exe"
    fake_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.delenv("AWT_DATA_DIR", raising=False)
    monkeypatch.delenv("AWT_PORTABLE", raising=False)

    assert db.data_dir() == fake_exe_dir / "data"
    assert db.is_portable_mode() is True

    # Removing marker falls back to non-portable user data directory
    (fake_exe_dir / "portable.flag").unlink()
    assert db.is_portable_mode() is False
    assert db.data_dir() == db._default_frozen_data_dir()

    # Explicit AWT_DATA_DIR always takes precedence over portable marker
    (fake_exe_dir / "portable.flag").write_text("1", encoding="utf-8")
    explicit = tmp_path / "custom-data"
    monkeypatch.setenv("AWT_DATA_DIR", str(explicit))
    assert db.data_dir() == explicit


def test_resolve_frontend_dist_and_runtime_info_portable(tmp_path, monkeypatch):
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<!doctype html><html><title>拾时</title></html>", encoding="utf-8")

    monkeypatch.setenv("AWT_FRONTEND_DIST", str(dist_dir))
    monkeypatch.setenv("AWT_PORTABLE", "1")
    monkeypatch.setenv("AWT_RUNTIME_MODE", "portable-win")

    assert app_module.resolve_frontend_dist() == dist_dir

    engine = db.make_engine(tmp_path / "work.db")
    db.migrate(engine)
    client = TestClient(app_module.create_app(engine))

    resp = client.get("/api/runtime-info")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "portable-win"
    assert resp.json()["portable"] is True

    page = client.get("/")
    assert page.status_code == 200
    assert "拾时" in page.text


def test_portable_check_existing_instance_and_browser_lookup(tmp_path, monkeypatch):
    runtime_file = tmp_path / "portable-runtime.json"
    runtime_file.write_text(json.dumps({"pid": os.getpid(), "port": 55555}), encoding="utf-8")

    monkeypatch.setattr(portable, "_is_pid_running", lambda pid: pid == os.getpid())
    monkeypatch.setattr(portable, "_probe_runtime_ready", lambda port, timeout=0.6: port == 55555)
    assert portable.check_existing_instance(tmp_path) == "http://127.0.0.1:55555"

    monkeypatch.setattr(portable, "_probe_runtime_ready", lambda port, timeout=0.6: False)
    assert portable.check_existing_instance(tmp_path) is None

    port = portable.pick_port(0)
    assert 1 <= port <= 65535

    if os.name == "nt":
        browser = portable.find_app_browser()
        # Windows hosts normally ship Edge; when absent in a minimal container this is None
        if browser is not None:
            kind, exe = browser
            assert kind in ("edge", "chrome")
            assert Path(exe).is_file()
