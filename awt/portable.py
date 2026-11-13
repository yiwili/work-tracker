"""Standalone Windows portable launcher for AI Work Tracker (pure-green edition)."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import db
from .cli import _prepare_sidecar_stdio, _start_stdin_watchdog

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resolve_icon_path() -> Path | None:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "icons" / "icon.ico")
    exe_dir = _exe_dir()
    candidates.append(exe_dir / "icons" / "icon.ico")
    candidates.append(Path(__file__).resolve().parent.parent / "src-tauri" / "icons" / "icon.ico")
    for path in candidates:
        if path.is_file():
            return path
    return None


def pick_port(preferred: int = 8765) -> int:
    for candidate in (preferred, 0):
        if candidate <= 0 and candidate != 0:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return int(sock.getsockname()[1])
            except OSError:
                if candidate == 0:
                    raise
    raise OSError("无法分配本机空闲端口")


def _probe_runtime_ready(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            req = (
                f"GET /api/runtime-info HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("ascii")
            sock.sendall(req)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        head, _, body = raw.partition("\r\n\r\n")
        status_line = head.splitlines()[0] if head else ""
        if " 200 " not in status_line:
            return False
        payload = json.loads(body)
        return payload.get("service") == "ai-work-tracker" and bool(payload.get("desktop_ready"))
    except Exception:
        return False


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def check_existing_instance(data_dir: Path) -> str | None:
    runtime_file = data_dir / "portable-runtime.json"
    if not runtime_file.is_file():
        return None
    try:
        info = json.loads(runtime_file.read_text(encoding="utf-8"))
        pid = int(info.get("pid") or 0)
        port = int(info.get("port") or 0)
    except (OSError, ValueError, TypeError):
        return None
    if pid <= 0 or not (1 <= port <= 65535):
        return None
    if not _is_pid_running(pid):
        return None
    if not _probe_runtime_ready(port, timeout=0.6):
        return None
    return f"http://127.0.0.1:{port}"


def _write_runtime_file(data_dir: Path, port: int) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    runtime_file = data_dir / "portable-runtime.json"
    payload = {
        "pid": os.getpid(),
        "port": port,
        "url": f"http://127.0.0.1:{port}",
    }
    tmp = runtime_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(runtime_file)
    return runtime_file


def _remove_runtime_file(data_dir: Path) -> None:
    runtime_file = data_dir / "portable-runtime.json"
    if not runtime_file.is_file():
        return
    try:
        info = json.loads(runtime_file.read_text(encoding="utf-8"))
        if int(info.get("pid") or 0) == os.getpid():
            runtime_file.unlink(missing_ok=True)
    except Exception:
        runtime_file.unlink(missing_ok=True)


def find_app_browser() -> tuple[str, Path] | None:
    if os.name == "nt":
        candidates: list[tuple[str, Path]] = []
        pf86 = os.environ.get("PROGRAMFILES(X86)", "")
        pf = os.environ.get("PROGRAMFILES", "")
        local = os.environ.get("LOCALAPPDATA", "")
        for root in (pf86, pf, local):
            if root:
                candidates.append(("edge", Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
        edge_which = shutil.which("msedge")
        if edge_which:
            candidates.append(("edge", Path(edge_which)))
        for root in (pf, pf86, local):
            if root:
                candidates.append(("chrome", Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"))
        chrome_which = shutil.which("chrome")
        if chrome_which:
            candidates.append(("chrome", Path(chrome_which)))
        for kind, path in candidates:
            if path.is_file():
                return kind, path
    return None


def open_app_window(url: str, data_dir: Path) -> None:
    browser = find_app_browser()
    if browser is not None:
        _kind, exe = browser
        profile_dir = data_dir / "browser-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(exe),
            f"--app={url}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return
        except OSError:
            pass
    webbrowser.open(url)


def open_data_directory(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", str(data_dir)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(data_dir)])
    else:
        subprocess.Popen(["xdg-open", str(data_dir)])


def _show_error(title: str, message: str) -> None:
    print(f"{title}: {message}", file=sys.stderr)
    if os.name == "nt":
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW(None, str(message), str(title), 0x00000000 | 0x00000010)


class Win32Tray:
    """Minimal pure-ctypes Windows system tray icon + context menu."""

    WM_NULL = 0x0000
    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    WM_QUIT = 0x0012
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205
    WM_CONTEXTMENU = 0x007B
    WM_TRAYICON = 0x8001

    NIM_ADD = 0x00000000
    NIM_DELETE = 0x00000002
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004

    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    LR_DEFAULTSIZE = 0x0040
    IDI_APPLICATION = 32512

    MF_STRING = 0x0000
    MF_SEPARATOR = 0x0800
    TPM_RIGHTALIGN = 0x0008
    TPM_BOTTOMALIGN = 0x0020
    TPM_NONOTIFY = 0x0080
    TPM_RETURNCMD = 0x0100

    ID_OPEN = 1001
    ID_DATA = 1002
    ID_EXIT = 1003

    def __init__(self, tooltip: str, icon_path: Path | None, on_open, on_open_data, on_exit):
        from ctypes import wintypes

        self.tooltip = tooltip[:127]
        self.icon_path = icon_path
        self.on_open = on_open
        self.on_open_data = on_open_data
        self.on_exit = on_exit
        self.hwnd = None
        self.hicon = None
        self._nid = None
        self._class_atom = None
        self._class_name = None

        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        LRESULT = ctypes.c_ssize_t
        WPARAM = ctypes.c_size_t
        LPARAM = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)
        self._WNDPROC = WNDPROC

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uTimeoutOrVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_ubyte * 16),
                ("hBalloonIcon", wintypes.HICON),
            ]

        self._WNDCLASSEXW = WNDCLASSEXW
        self._NOTIFYICONDATAW = NOTIFYICONDATAW
        self._wintypes = wintypes

        self.user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        self.user32.DefWindowProcW.restype = LRESULT
        self.user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        self.user32.RegisterClassExW.restype = wintypes.ATOM
        self.user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        self.user32.UnregisterClassW.restype = wintypes.BOOL
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.DestroyWindow.restype = wintypes.BOOL
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self.user32.GetMessageW.restype = ctypes.c_int
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.TranslateMessage.restype = wintypes.BOOL
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.restype = LRESULT
        self.user32.PostQuitMessage.argtypes = [ctypes.c_int]
        self.user32.PostQuitMessage.restype = None
        self.user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self.user32.LoadImageW.restype = wintypes.HANDLE
        self.user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        self.user32.LoadIconW.restype = wintypes.HICON
        self.user32.DestroyIcon.argtypes = [wintypes.HICON]
        self.user32.DestroyIcon.restype = wintypes.BOOL
        self.user32.CreatePopupMenu.argtypes = []
        self.user32.CreatePopupMenu.restype = wintypes.HMENU
        self.user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        self.user32.AppendMenuW.restype = wintypes.BOOL
        self.user32.DestroyMenu.argtypes = [wintypes.HMENU]
        self.user32.DestroyMenu.restype = wintypes.BOOL
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.SetForegroundWindow.restype = wintypes.BOOL
        self.user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        self.user32.RegisterWindowMessageW.restype = wintypes.UINT
        self.shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        self.shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        self.user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.LPVOID,
        ]
        self.user32.TrackPopupMenu.restype = wintypes.BOOL
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

        self._wm_taskbar_created = self.user32.RegisterWindowMessageW("TaskbarCreated")
        self._wndproc_ref = WNDPROC(self._wndproc)

    def _load_icon(self):
        if self.icon_path and self.icon_path.is_file():
            handle = self.user32.LoadImageW(
                None,
                str(self.icon_path),
                self.IMAGE_ICON,
                0,
                0,
                self.LR_LOADFROMFILE | self.LR_DEFAULTSIZE,
            )
            if handle:
                return handle
        return self.user32.LoadIconW(None, ctypes.cast(ctypes.c_void_p(self.IDI_APPLICATION), self._wintypes.LPCWSTR))

    def _add_notify_icon(self) -> None:
        nid = self._NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(self._NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP
        nid.uCallbackMessage = self.WM_TRAYICON
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip
        self._nid = nid
        self.shell32.Shell_NotifyIconW(self.NIM_ADD, ctypes.byref(nid))

    def _delete_notify_icon(self) -> None:
        if self._nid is not None:
            self.shell32.Shell_NotifyIconW(self.NIM_DELETE, ctypes.byref(self._nid))
            self._nid = None

    def _show_menu(self) -> None:
        menu = self.user32.CreatePopupMenu()
        if not menu:
            return
        try:
            self.user32.AppendMenuW(menu, self.MF_STRING, self.ID_OPEN, "打开主界面")
            self.user32.AppendMenuW(menu, self.MF_STRING, self.ID_DATA, "打开数据目录")
            self.user32.AppendMenuW(menu, self.MF_SEPARATOR, 0, None)
            self.user32.AppendMenuW(menu, self.MF_STRING, self.ID_EXIT, "退出拾时")
            pt = self._wintypes.POINT()
            self.user32.GetCursorPos(ctypes.byref(pt))
            self.user32.SetForegroundWindow(self.hwnd)
            cmd = self.user32.TrackPopupMenu(
                menu,
                self.TPM_RIGHTALIGN | self.TPM_BOTTOMALIGN | self.TPM_NONOTIFY | self.TPM_RETURNCMD,
                pt.x,
                pt.y,
                0,
                self.hwnd,
                None,
            )
            self.user32.PostMessageW(self.hwnd, self.WM_NULL, 0, 0)
            if cmd == self.ID_OPEN:
                self.on_open()
            elif cmd == self.ID_DATA:
                self.on_open_data()
            elif cmd == self.ID_EXIT:
                self.on_exit()
                self.user32.DestroyWindow(self.hwnd)
        finally:
            self.user32.DestroyMenu(menu)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == self._wm_taskbar_created and self._wm_taskbar_created != 0:
            self._add_notify_icon()
            return 0
        if msg == self.WM_TRAYICON:
            event = lparam & 0xFFFF
            if event in (self.WM_LBUTTONUP, self.WM_LBUTTONDBLCLK):
                self.on_open()
            elif event in (self.WM_RBUTTONUP, self.WM_CONTEXTMENU):
                self._show_menu()
            return 0
        if msg == self.WM_CLOSE:
            self.user32.DestroyWindow(hwnd)
            return 0
        if msg == self.WM_DESTROY:
            self._delete_notify_icon()
            self.user32.PostQuitMessage(0)
            return 0
        return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def create(self) -> None:
        hinst = self.kernel32.GetModuleHandleW(None)
        class_name = f"AWT_Portable_Tray_{os.getpid()}"
        wc = self._WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(self._WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = class_name
        atom = self.user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            raise OSError(f"RegisterClassExW failed: {ctypes.get_last_error()}")
        self._class_atom = atom
        self._class_name = class_name
        self.hwnd = self.user32.CreateWindowExW(
            0,
            class_name,
            "拾时便携版",
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinst,
            None,
        )
        if not self.hwnd:
            raise OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}")
        self.hicon = self._load_icon()
        self._add_notify_icon()

    def run(self, stop_event: threading.Event | None = None) -> None:
        self.create()
        msg = self._wintypes.MSG()
        while True:
            if stop_event is not None and stop_event.is_set():
                self.user32.PostMessageW(self.hwnd, self.WM_CLOSE, 0, 0)
            ret = self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            self.user32.TranslateMessage(ctypes.byref(msg))
            self.user32.DispatchMessageW(ctypes.byref(msg))
        self.cleanup()

    def stop(self) -> None:
        if self.hwnd:
            self.user32.PostMessageW(self.hwnd, self.WM_CLOSE, 0, 0)

    def cleanup(self) -> None:
        self._delete_notify_icon()
        if self.hwnd and self.user32.IsWindow(self.hwnd):
            self.user32.DestroyWindow(self.hwnd)
        self.hwnd = None
        if self.hicon and self.icon_path and self.icon_path.is_file():
            self.user32.DestroyIcon(self.hicon)
        self.hicon = None
        if self._class_name:
            hinst = self.kernel32.GetModuleHandleW(None)
            self.user32.UnregisterClassW(self._class_name, hinst)
            self._class_name = None
            self._class_atom = None


def run_portable(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="拾时 · 纯绿色便携版启动器")
    parser.add_argument("--port", type=int, default=8765, help="首选本地服务端口，被占用时自动分配空闲端口")
    parser.add_argument("--no-browser", action="store_true", help="仅启动服务，不自动打开界面窗口")
    parser.add_argument("--no-tray", action="store_true", help="无托盘模式（适合命令行或自动化冒烟检查）")
    parser.add_argument("--watch-stdin", action="store_true", help="在标准输入关闭后自动退出服务")
    args = parser.parse_args(argv)

    os.environ.setdefault("AWT_RUNTIME_MODE", "portable-win")
    os.environ.setdefault("AWT_LOG_TO_FILE", "1")

    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    data_dir = db.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_browser:
        existing_url = check_existing_instance(data_dir)
        if existing_url:
            open_app_window(existing_url, data_dir)
            return 0

    log_stream = _prepare_sidecar_stdio("portable.log")

    import uvicorn
    from .app import create_app, resolve_frontend_dist

    if resolve_frontend_dist() is None:
        msg = "未找到内置前端资源（frontend/dist），无法启动便携版界面。"
        if not args.no_tray:
            _show_error("拾时便携版启动失败", msg)
        else:
            print(msg, file=sys.stderr)
        return 1

    port = pick_port(args.port)
    use_colors = bool(sys.stderr is not None and hasattr(sys.stderr, "isatty") and sys.stderr.isatty())
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, use_colors=use_colors)
    server = uvicorn.Server(config)
    done_event = threading.Event()
    server_errors: list[BaseException] = []

    def _serve():
        try:
            server.run()
        except BaseException as exc:  # pragma: no cover - defensive runtime capture
            server_errors.append(exc)
        finally:
            done_event.set()

    server_thread = threading.Thread(target=_serve, daemon=True, name="awt-portable-uvicorn")
    server_thread.start()

    if args.watch_stdin and sys.stdin is not None:
        _start_stdin_watchdog(server, done_event)

    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        if done_event.is_set():
            err_detail = f"\n{server_errors[0]}" if server_errors else ""
            msg = f"本地服务未能成功启动，请查看日志：{data_dir / 'logs' / 'portable.log'}{err_detail}"
            if not args.no_tray:
                _show_error("拾时便携版启动失败", msg)
            else:
                print(msg, file=sys.stderr)
            return 1
        if _probe_runtime_ready(port, timeout=0.4):
            break
        time.sleep(0.1)
    else:
        server.should_exit = True
        msg = f"等待本地服务就绪超时（127.0.0.1:{port}），请检查日志：{data_dir / 'logs' / 'portable.log'}"
        if not args.no_tray:
            _show_error("拾时便携版启动失败", msg)
        else:
            print(msg, file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{port}"
    _write_runtime_file(data_dir, port)

    if not args.no_browser:
        open_app_window(url, data_dir)

    try:
        if os.name == "nt" and not args.no_tray:
            tray = Win32Tray(
                tooltip=f"拾时 · 绿色便携版 ({url})",
                icon_path=resolve_icon_path(),
                on_open=lambda: open_app_window(url, data_dir),
                on_open_data=lambda: open_data_directory(data_dir),
                on_exit=lambda: setattr(server, "should_exit", True),
            )
            tray.run()
        else:
            server_thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        server_thread.join(timeout=5.0)
        _remove_runtime_file(data_dir)
        if log_stream is not None:
            try:
                log_stream.flush()
                log_stream.close()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(run_portable())
