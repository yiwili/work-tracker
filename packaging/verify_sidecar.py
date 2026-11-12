"""Headless smoke check for the compiled PyInstaller awt-sidecar binary."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def main(exe_arg: str) -> int:
    exe = Path(exe_arg).resolve()
    if not exe.is_file():
        raise FileNotFoundError(f"Missing sidecar binary: {exe}")

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    with tempfile.TemporaryDirectory() as td:
        env = {
            **os.environ,
            "AWT_DATA_DIR": td,
            "AWT_RUNTIME_MODE": "desktop-tauri",
            "AWT_LOG_TO_FILE": "1",
            "PYTHONUTF8": "1",
        }
        t0 = time.monotonic()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [str(exe), "serve", "--port", str(port), "--watch-stdin"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(exe.parent),
            env=env,
            creationflags=creationflags,
        )
        try:
            info = None
            while time.monotonic() - t0 < 25:
                if proc.poll() is not None:
                    raise RuntimeError(f"Sidecar exited unexpectedly with code {proc.returncode}")
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/runtime-info", timeout=0.5
                    ) as resp:
                        info = json.loads(resp.read().decode("utf-8"))
                        if info.get("desktop_ready"):
                            break
                except Exception:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Timed out waiting for sidecar /api/runtime-info")

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/agents", timeout=2) as resp:
                agents = json.loads(resp.read().decode("utf-8"))
            if "task_writer" not in agents:
                raise AssertionError("Bundled prompts/task_writer missing in sidecar")

            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/tasks",
                data=json.dumps(
                    {
                        "date": "2026-11-12",
                        "title": "【桌面端】sidecar 打包冒烟校验",
                        "details": "",
                        "project": "桌面端",
                        "category": "任务",
                        "status": "delivered",
                        "confirmed_minutes": 30,
                        "time_locked": True,
                    }
                ).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Origin": "http://tauri.localhost",
                    "sec-fetch-site": "cross-site",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status != 200:
                    raise AssertionError(f"Expected 200 for http://tauri.localhost, got {resp.status}")

            bad = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/tasks",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://evil.example",
                    "sec-fetch-site": "cross-site",
                },
                method="POST",
            )
            try:
                urllib.request.urlopen(bad, timeout=2)
                raise AssertionError("Expected 403 for untrusted origin")
            except urllib.error.HTTPError as exc:
                if exc.code != 403:
                    raise AssertionError(f"Expected 403 for evil origin, got {exc.code}") from exc

            assert proc.stdin is not None
            proc.stdin.close()
            rc = proc.wait(timeout=10)
            if rc != 0:
                raise AssertionError(f"Expected exit code 0 after stdin EOF, got {rc}")

            log_file = Path(td) / "logs" / "sidecar.log"
            if not log_file.is_file() or log_file.stat().st_size == 0:
                raise AssertionError(f"Sidecar log file not written at {log_file}")

            print(f"Sidecar smoke check passed in {time.monotonic() - t0:.2f}s (mode={info.get('mode')})")
            return 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python packaging/verify_sidecar.py <path/to/awt-sidecar.exe>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
