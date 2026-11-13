from __future__ import annotations

import os
import sys
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from awt import cli, db
from awt.app import create_app


def test_tauri_origins_allowed_for_runtime_and_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("AWT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AWT_RUNTIME_MODE", "desktop-tauri")
    monkeypatch.delenv("AWT_PORTABLE", raising=False)
    engine = db.make_engine(tmp_path / "desktop.db")
    try:
        with TestClient(create_app(engine)) as client:
            info = client.get("/api/runtime-info", headers={"Origin": "http://tauri.localhost"})
            assert info.status_code == 200
            assert info.json() == {
                "service": "ai-work-tracker",
                "mode": "desktop-tauri",
                "api_version": "1.0",
                "desktop_ready": True,
                "portable": False,
            }
            assert info.headers.get("access-control-allow-origin") == "http://tauri.localhost"

            for allowed_origin in ("http://tauri.localhost", "https://tauri.localhost", "tauri://localhost"):
                res = client.post(
                    "/api/tasks",
                    json={
                        "date": "2026-11-12",
                        "title": f"【桌面端】测试来自 {allowed_origin}",
                        "details": "",
                        "project": "桌面端",
                        "category": "任务",
                        "status": "delivered",
                        "confirmed_minutes": 30,
                        "time_locked": True,
                    },
                    headers={"Origin": allowed_origin, "sec-fetch-site": "cross-site"},
                )
                assert res.status_code == 200, res.text
                assert res.headers.get("access-control-allow-origin") == allowed_origin

            blocked = client.post(
                "/api/tasks",
                json={"date": "2026-11-12", "title": "blocked"},
                headers={"Origin": "https://evil.example", "sec-fetch-site": "cross-site"},
            )
            assert blocked.status_code == 403
    finally:
        engine.dispose()


def test_data_dir_frozen_fallback_and_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("AWT_DATA_DIR", raising=False)
    if os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert db.data_dir() == tmp_path / "AIWorkTracker"
    else:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert db.data_dir() == tmp_path / "AIWorkTracker"

    custom = tmp_path / "CustomData"
    monkeypatch.setenv("AWT_DATA_DIR", str(custom))
    assert db.data_dir() == custom


def test_prepare_sidecar_stdio_redirects_none_streams(tmp_path, monkeypatch):
    monkeypatch.setenv("AWT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    handle = cli._prepare_sidecar_stdio()
    try:
        assert handle is not None
        assert sys.stdout is handle
        assert sys.stderr is handle
        print("sidecar stdio test")
        handle.flush()
        log_file = tmp_path / "logs" / "sidecar.log"
        assert log_file.is_file()
        assert "sidecar stdio test" in log_file.read_text(encoding="utf-8")
    finally:
        if handle is not None:
            handle.close()


def test_prepare_sidecar_stdio_logs_to_file_when_forced(tmp_path, monkeypatch):
    monkeypatch.setenv("AWT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AWT_LOG_TO_FILE", "1")
    orig_out, orig_err = sys.stdout, sys.stderr
    handle = cli._prepare_sidecar_stdio()
    try:
        assert handle is not None
        assert sys.stdout is handle
        assert sys.stderr is handle
        print("forced sidecar log")
        handle.flush()
        log_file = tmp_path / "logs" / "sidecar.log"
        assert log_file.is_file()
        assert "forced sidecar log" in log_file.read_text(encoding="utf-8")
    finally:
        sys.stdout = orig_out
        sys.stderr = orig_err
        if handle is not None:
            handle.close()


def test_stdin_watchdog_sets_should_exit_on_pipe_eof(monkeypatch):
    rfd, wfd = os.pipe()
    done_event = threading.Event()
    server = SimpleNamespace(should_exit=False)

    class _FakeStdin:
        def fileno(self):
            return rfd

    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    try:
        thread = cli._start_stdin_watchdog(server, done_event, grace_seconds=2.0)
        time.sleep(0.05)
        assert server.should_exit is False
        os.close(wfd)
        wfd = -1
        deadline = time.monotonic() + 2.0
        while not server.should_exit and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.should_exit is True
        done_event.set()
        thread.join(timeout=2.0)
        assert not thread.is_alive()
    finally:
        done_event.set()
        if wfd >= 0:
            os.close(wfd)
        try:
            os.close(rfd)
        except OSError:
            pass
