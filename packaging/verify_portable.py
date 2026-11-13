"""Headless smoke check for the compiled PyInstaller pure-green portable app."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _pick_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def main(exe_arg: str) -> int:
    src_exe = Path(exe_arg).resolve()
    if not src_exe.is_file():
        raise FileNotFoundError(f"Missing portable binary: {src_exe}")
    src_dir = src_exe.parent
    if not (src_dir / "portable.flag").is_file():
        raise AssertionError(f"Missing portable.flag alongside {src_exe}")

    port = _pick_free_port()

    with tempfile.TemporaryDirectory(prefix="awt-portable-smoke-") as td:
        sandbox_dir = Path(td) / "app"
        shutil.copytree(src_dir, sandbox_dir)
        exe = sandbox_dir / src_exe.name

        env = {k: v for k, v in os.environ.items() if k not in ("AWT_DATA_DIR", "AWT_PORTABLE", "AWT_FRONTEND_DIST")}
        env["PYTHONUTF8"] = "1"

        t0 = time.monotonic()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [str(exe), "--port", str(port), "--no-browser", "--no-tray", "--watch-stdin"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(sandbox_dir),
            env=env,
            creationflags=creationflags,
        )
        try:
            info = None
            while time.monotonic() - t0 < 25:
                if proc.poll() is not None:
                    raise RuntimeError(f"Portable app exited unexpectedly with code {proc.returncode}")
                try:
                    with _DIRECT_OPENER.open(
                        f"http://127.0.0.1:{port}/api/runtime-info", timeout=0.5
                    ) as resp:
                        info = json.loads(resp.read().decode("utf-8"))
                        if info.get("desktop_ready"):
                            break
                except Exception:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Timed out waiting for portable /api/runtime-info")

            if info.get("mode") != "portable-win" or not info.get("portable"):
                raise AssertionError(f"Unexpected portable runtime info: {info}")

            with _DIRECT_OPENER.open(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                if resp.status != 200:
                    raise AssertionError(f"Expected 200 for /, got {resp.status}")
                html = resp.read().decode("utf-8", errors="replace")
            if "拾时" not in html and "root" not in html:
                raise AssertionError("Bundled frontend index.html content looks invalid")

            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/tasks",
                data=json.dumps(
                    {
                        "date": "2026-11-12",
                        "title": "【便携版】绿色目录冒烟校验",
                        "details": "",
                        "project": "便携版",
                        "category": "任务",
                        "status": "delivered",
                        "confirmed_minutes": 30,
                        "time_locked": True,
                    }
                ).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{port}",
                },
                method="POST",
            )
            with _DIRECT_OPENER.open(req, timeout=2) as resp:
                if resp.status != 200:
                    raise AssertionError(f"Expected 200 for same-origin /api/tasks, got {resp.status}")

            db_path = sandbox_dir / "data" / "work.db"
            if not db_path.is_file() or db_path.stat().st_size == 0:
                raise AssertionError(f"Expected portable SQLite DB at {db_path}")

            log_path = sandbox_dir / "data" / "logs" / "portable.log"
            if not log_path.is_file() or log_path.stat().st_size == 0:
                raise AssertionError(f"Expected portable log at {log_path}")

            assert proc.stdin is not None
            proc.stdin.close()
            rc = proc.wait(timeout=10)
            if rc != 0:
                raise AssertionError(f"Expected exit code 0 after stdin EOF, got {rc}")

            print(
                f"Portable green smoke check passed in {time.monotonic() - t0:.2f}s "
                f"(mode={info.get('mode')}, portable={info.get('portable')})"
            )
            return 0
        finally:
            if proc.poll() is None:
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-portable-exe>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
