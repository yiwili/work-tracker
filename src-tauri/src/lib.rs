use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::os::windows::ffi::OsStrExt;
#[cfg(windows)]
use std::os::windows::process::CommandExt;

use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct SidecarProcess {
    child: Child,
    stdin: Option<ChildStdin>,
    port: u16,
}

impl SidecarProcess {
    fn shutdown(&mut self) {
        // Closing the stdin pipe notifies `--watch-stdin` to perform graceful lifespan exit.
        self.stdin.take();
        let deadline = Instant::now() + Duration::from_millis(2500);
        while Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => thread::sleep(Duration::from_millis(50)),
                Err(_) => break,
            }
        }
        #[cfg(windows)]
        {
            let pid = self.child.id().to_string();
            let mut kill_cmd = Command::new("taskkill");
            kill_cmd.args(["/PID", &pid, "/T", "/F"]);
            kill_cmd.creation_flags(CREATE_NO_WINDOW);
            let _ = kill_cmd.output();
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for SidecarProcess {
    fn drop(&mut self) {
        self.shutdown();
    }
}

struct SidecarState(Mutex<Option<SidecarProcess>>);

struct DataDirState(PathBuf);

fn show_startup_error(title: &str, message: &str) {
    eprintln!("{title}: {message}");
    #[cfg(windows)]
    {
        #[link(name = "user32")]
        extern "system" {
            fn MessageBoxW(hwnd: isize, text: *const u16, caption: *const u16, u_type: u32) -> i32;
        }
        let wide_msg: Vec<u16> = std::ffi::OsStr::new(message)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        let wide_title: Vec<u16> = std::ffi::OsStr::new(title)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        const MB_OK: u32 = 0x0000_0000;
        const MB_ICONERROR: u32 = 0x0000_0010;
        unsafe {
            MessageBoxW(0, wide_msg.as_ptr(), wide_title.as_ptr(), MB_OK | MB_ICONERROR);
        }
    }
}

fn resolve_portable_data_dir_from_exe_dir(exe_dir: &Path) -> Option<PathBuf> {
    for marker in ["portable.flag", "portable"] {
        if exe_dir.join(marker).is_file() {
            return Some(exe_dir.join("data"));
        }
    }
    if exe_dir.join("data").is_dir() {
        return Some(exe_dir.join("data"));
    }
    None
}

fn resolve_data_dir(app: &AppHandle) -> (PathBuf, bool) {
    if let Some(raw) = std::env::var_os("AWT_DATA_DIR") {
        let trimmed = raw.to_string_lossy().trim().to_string();
        if !trimmed.is_empty() {
            let portable = std::env::var_os("AWT_PORTABLE")
                .map(|v| matches!(v.to_string_lossy().trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
                .unwrap_or(false);
            return (PathBuf::from(trimmed), portable);
        }
    }
    if let Ok(cur_exe) = std::env::current_exe() {
        if let Some(exe_dir) = cur_exe.parent() {
            if let Some(portable_dir) = resolve_portable_data_dir_from_exe_dir(exe_dir) {
                return (portable_dir, true);
            }
        }
    }
    if let Ok(local_data) = app.path().local_data_dir() {
        return (local_data.join("AIWorkTracker"), false);
    }
    (
        std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.join("data")))
            .unwrap_or_else(|| PathBuf::from("data")),
        true,
    )
}

fn pick_sidecar_port() -> Result<u16, String> {
    if let Ok(listener) = TcpListener::bind("127.0.0.1:8765") {
        let port = listener
            .local_addr()
            .map_err(|e| format!("读取本地监听端口失败: {e}"))?
            .port();
        drop(listener);
        return Ok(port);
    }
    let listener =
        TcpListener::bind("127.0.0.1:0").map_err(|e| format!("无法分配本机空闲端口: {e}"))?;
    let port = listener
        .local_addr()
        .map_err(|e| format!("读取本地空闲端口失败: {e}"))?
        .port();
    drop(listener);
    Ok(port)
}

fn find_sidecar_executable(app: &AppHandle) -> Result<PathBuf, String> {
    let exe_name = if cfg!(windows) {
        "awt-sidecar.exe"
    } else {
        "awt-sidecar"
    };

    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(res_dir) = app.path().resource_dir() {
        candidates.push(res_dir.join("resources").join("sidecar").join(exe_name));
        candidates.push(res_dir.join("sidecar").join(exe_name));
        candidates.push(res_dir.join(exe_name));
    }
    if let Ok(cur_exe) = std::env::current_exe() {
        if let Some(exe_dir) = cur_exe.parent() {
            candidates.push(exe_dir.join("resources").join("sidecar").join(exe_name));
            candidates.push(exe_dir.join("sidecar").join(exe_name));
            candidates.push(exe_dir.join(exe_name));
        }
    }
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    candidates.push(
        manifest_dir
            .join("resources")
            .join("sidecar")
            .join(exe_name),
    );
    candidates.push(
        manifest_dir
            .parent()
            .unwrap_or(manifest_dir)
            .join("build")
            .join("sidecar-dist")
            .join("awt-sidecar")
            .join(exe_name),
    );

    for path in &candidates {
        if path.is_file() {
            return Ok(path.clone());
        }
    }

    let searched = candidates
        .iter()
        .map(|p| p.display().to_string())
        .collect::<Vec<_>>()
        .join("\n  - ");
    Err(format!(
        "未找到桌面后台组件 ({exe_name})。已检查路径：\n  - {searched}\n请先运行一键打包脚本 build-desktop.cmd 或 .\\build-desktop.ps1。"
    ))
}

fn poll_runtime_ready(port: u16) -> bool {
    let Ok(addr) = format!("127.0.0.1:{port}").parse() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(250)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    let request = format!(
        "GET /api/runtime-info HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }
    (response.contains("HTTP/1.1 200") || response.contains("HTTP/1.0 200"))
        && response.contains("\"desktop_ready\"")
        && response.contains("true")
}

fn spawn_sidecar(app: &AppHandle, data_dir: &Path, is_portable: bool) -> Result<SidecarProcess, String> {
    std::fs::create_dir_all(data_dir)
        .map_err(|e| format!("无法创建数据目录 {}：{e}", data_dir.display()))?;

    let sidecar_exe = find_sidecar_executable(app)?;
    let port = pick_sidecar_port()?;

    let mut cmd = Command::new(&sidecar_exe);
    cmd.args(["serve", "--port", &port.to_string(), "--watch-stdin"])
        .env("AWT_RUNTIME_MODE", "desktop-tauri")
        .env("AWT_PORTABLE", if is_portable { "1" } else { "0" })
        .env("AWT_DATA_DIR", data_dir)
        .env("AWT_LOG_TO_FILE", "1")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Some(parent_dir) = sidecar_exe.parent() {
        cmd.current_dir(parent_dir);
    }
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("启动后台服务失败 ({})：{e}", sidecar_exe.display()))?;
    let stdin = child.stdin.take();

    let mut proc = SidecarProcess {
        child,
        stdin,
        port,
    };

    let deadline = Instant::now() + Duration::from_secs(25);
    while Instant::now() < deadline {
        if let Ok(Some(status)) = proc.child.try_wait() {
            let log_hint = data_dir.join("logs").join("sidecar.log");
            return Err(format!(
                "后台服务提前退出（状态码: {status}）。\n请查看日志：{}",
                log_hint.display()
            ));
        }
        if poll_runtime_ready(port) {
            return Ok(proc);
        }
        thread::sleep(Duration::from_millis(100));
    }

    let log_hint = data_dir.join("logs").join("sidecar.log");
    proc.shutdown();
    Err(format!(
        "等待本地后台服务就绪超时（端口 127.0.0.1:{port}）。\n请检查日志：{}",
        log_hint.display()
    ))
}

fn stop_sidecar(app: &AppHandle) {
    if let Ok(mut guard) = app.state::<SidecarState>().0.lock() {
        if let Some(mut proc) = guard.take() {
            proc.shutdown();
        }
    }
}

fn restore_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn open_external_url(url: &str) {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return;
    }
    #[cfg(windows)]
    {
        let mut cmd = Command::new("rundll32.exe");
        cmd.args(["url.dll,FileProtocolHandler", url]);
        cmd.creation_flags(CREATE_NO_WINDOW);
        let _ = cmd.spawn();
    }
    #[cfg(target_os = "macos")]
    {
        let _ = Command::new("open").arg(url).spawn();
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = Command::new("xdg-open").arg(url).spawn();
    }
}

fn open_data_directory(path: &Path) {
    let _ = std::fs::create_dir_all(path);
    #[cfg(windows)]
    {
        let _ = Command::new("explorer.exe").arg(path).spawn();
    }
    #[cfg(target_os = "macos")]
    {
        let _ = Command::new("open").arg(path).spawn();
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = Command::new("xdg-open").arg(path).spawn();
    }
}

fn setup_tray(app: &AppHandle, is_portable: bool) -> Result<(), Box<dyn std::error::Error>> {
    let show_item = MenuItemBuilder::with_id("show", "显示主窗口").build(app)?;
    let data_item = MenuItemBuilder::with_id("open_data", "打开数据目录").build(app)?;
    let quit_item = MenuItemBuilder::with_id("quit", "退出拾时").build(app)?;

    let menu = MenuBuilder::new(app)
        .item(&show_item)
        .item(&data_item)
        .separator()
        .item(&quit_item)
        .build()?;

    let tooltip = if is_portable {
        "拾时 · AI 工作记录（便携版）"
    } else {
        "拾时 · AI 工作记录"
    };

    let mut tray_builder = TrayIconBuilder::with_id("main-tray")
        .menu(&menu)
        .tooltip(tooltip)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => restore_main_window(app),
            "open_data" => {
                let dir = app.state::<DataDirState>().0.clone();
                open_data_directory(&dir);
            }
            "quit" => {
                stop_sidecar(app);
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                restore_main_window(tray.app_handle());
            }
        });

    if let Some(icon) = app.default_window_icon().cloned() {
        tray_builder = tray_builder.icon(icon);
    }

    tray_builder.build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(SidecarState(Mutex::new(None)))
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            restore_main_window(app);
        }))
        .setup(|app| {
            let handle = app.handle().clone();
            let (data_dir, is_portable) = resolve_data_dir(&handle);
            app.manage(DataDirState(data_dir.clone()));

            setup_tray(&handle, is_portable)?;

            let port = match spawn_sidecar(&handle, &data_dir, is_portable) {
                Ok(proc) => {
                    let port = proc.port;
                    if let Ok(mut guard) = handle.state::<SidecarState>().0.lock() {
                        *guard = Some(proc);
                    }
                    port
                }
                Err(msg) => {
                    show_startup_error("拾时启动失败", &msg);
                    return Err(msg.into());
                }
            };

            let init_script = format!(
                "window.__AWT_API_BASE__ = 'http://127.0.0.1:{port}/api'; window.__AWT_DESKTOP__ = true;"
            );

            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("拾时 · AI 工作记录")
                .inner_size(1280.0, 820.0)
                .min_inner_size(960.0, 640.0)
                .center()
                .initialization_script(&init_script)
                .on_navigation(|url| {
                    let scheme = url.scheme();
                    if scheme == "http" || scheme == "https" {
                        let host = url.host_str().unwrap_or("");
                        if host == "tauri.localhost" || host == "127.0.0.1" || host == "localhost" {
                            return true;
                        }
                        open_external_url(url.as_str());
                        return false;
                    }
                    true
                })
                .on_new_window(|url, _features| {
                    open_external_url(url.as_str());
                    NewWindowResponse::Deny
                })
                .build()?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building 拾时 desktop application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            stop_sidecar(app_handle);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::resolve_portable_data_dir_from_exe_dir;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn make_temp_dir(label: &str) -> std::path::PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("awt-rust-test-{label}-{nanos}"));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn portable_data_dir_requires_marker_or_existing_data_folder() {
        let dir = make_temp_dir("empty");
        assert_eq!(resolve_portable_data_dir_from_exe_dir(&dir), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn portable_data_dir_detects_flag_file_and_data_dir() {
        let dir_flag = make_temp_dir("flag");
        std::fs::write(dir_flag.join("portable.flag"), b"").unwrap();
        assert_eq!(
            resolve_portable_data_dir_from_exe_dir(&dir_flag),
            Some(dir_flag.join("data"))
        );
        let _ = std::fs::remove_dir_all(&dir_flag);

        let dir_data = make_temp_dir("data");
        std::fs::create_dir_all(dir_data.join("data")).unwrap();
        assert_eq!(
            resolve_portable_data_dir_from_exe_dir(&dir_data),
            Some(dir_data.join("data"))
        );
        let _ = std::fs::remove_dir_all(&dir_data);
    }
}
