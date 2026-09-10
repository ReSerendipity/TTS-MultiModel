// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod config;
mod drag_drop;
mod health_check;
mod notification;
mod port_manager;
mod python_process;
mod tray;
mod updater;
mod window;

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use tauri::{
    App, AppHandle, Emitter, Listener, Manager, RunEvent, WebviewEvent, WindowEvent,
};

use python_process::{
    emit_startup_status, resolve_app_dir, resolve_runtime_dir, PythonProcess, PythonState,
};

/// 应用关键目录集合（更新器、托盘、拖拽等模块共享）
pub struct AppPaths {
    /// 应用代码根（app/ 或开发模式项目根）
    pub app_dir: PathBuf,
    /// Python 运行时目录
    pub runtime_dir: PathBuf,
    /// 日志目录
    pub log_dir: PathBuf,
    /// 更新包下载缓存目录
    pub updates_dir: PathBuf,
}

/// 主窗口导航到后端应用地址
fn navigate_to_app(app: &AppHandle, port: u16) {
    if let Some(win) = app.get_webview_window(window::MAIN_LABEL) {
        let url = format!("http://127.0.0.1:{}/", port);
        match url.parse() {
            Ok(url) => {
                let _ = win.navigate(url);
            }
            Err(e) => log::error!("解析导航地址失败 {url}: {e}"),
        }
    }
}

/// 启动 Python 并加载 WebView（成功时导航主窗口到后端地址）
async fn start_python_and_load(app: &AppHandle, state: &Arc<PythonState>) {
    emit_startup_status(app, "正在启动 Python 运行时...", None);

    let port = {
        let mut proc = state.process.lock().unwrap();
        match proc.start() {
            Ok(port) => port,
            Err(e) => {
                log::error!("启动 Python 失败: {}", e);
                emit_startup_status(
                    app,
                    "启动失败",
                    Some(format!(
                        "Python 启动失败: {}\n请检查 runtime/python.exe 是否存在",
                        e
                    )),
                );
                return;
            }
        }
    };

    emit_startup_status(app, "正在加载模型，请稍候...", None);

    // 等待健康检查（先释放锁再 await，避免 MutexGuard 跨 await）
    let wait_result = health_check::wait_for_ready(port, Duration::from_secs(60)).await;

    match wait_result {
        Ok(integrity) => {
            {
                let mut proc = state.process.lock().unwrap();
                proc.mark_running();
            }
            log::info!("Python 服务就绪，端口={}", port);
            navigate_to_app(app, port);

            // B-2: 完整性自检失败时启动页告警 + 系统通知（篡改不再静默）
            if let Some(st) = integrity {
                if st.failed > 0 {
                    let files = st.failed_files.join(", ");
                    log::error!("完整性自检失败 {} 项: {}", st.failed, files);
                    emit_startup_status(
                        app,
                        "安全告警",
                        Some(format!(
                            "核心模块完整性自检失败（{} 项）：{}\n可能已被篡改，请检查安装目录。",
                            st.failed, files
                        )),
                    );
                    crate::notification::handle_show_notification(
                        app,
                        crate::notification::ShowNotification {
                            title: "TTS MultiModel 安全告警".to_string(),
                            body: format!(
                                "核心模块完整性自检失败 {} 项，文件可能已被篡改。",
                                st.failed
                            ),
                        },
                    );
                }
            }
        }
        Err(e) => {
            log::error!("Python 启动超时: {}", e);
            emit_startup_status(
                app,
                "启动超时",
                Some(format!("服务启动超时: {}\n请查看日志了解详情", e)),
            );
        }
    }
}

/// 后台监控线程：检测 Python 崩溃并自动重启（更新进行中暂停重启）
fn start_watchdog(app: AppHandle, state: Arc<PythonState>) {
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(2)).await;

            // 更新流程正在停/启进程，跳过本轮监控避免误判崩溃
            if app
                .state::<Arc<updater::UpdateState>>()
                .updating
                .load(Ordering::SeqCst)
            {
                continue;
            }

            let needs_restart = {
                let mut proc = state.process.lock().unwrap();
                matches!(proc.status(), python_process::PythonStatus::Running)
                    && !proc.is_alive()
            };

            if needs_restart {
                log::warn!("检测到 Python 进程崩溃，尝试重启");
                emit_startup_status(&app, "检测到进程崩溃，正在重启...", None);

                let restarted = {
                    let mut proc = state.process.lock().unwrap();
                    proc.try_restart().unwrap_or(false)
                };

                if restarted {
                    let new_port = {
                        let proc = state.process.lock().unwrap();
                        proc.port()
                    };
                    let ready =
                        health_check::wait_for_ready(new_port, Duration::from_secs(30)).await.is_ok();

                    if ready {
                        {
                            let mut proc = state.process.lock().unwrap();
                            proc.mark_running();
                        }
                        navigate_to_app(&app, new_port);
                        emit_startup_status(&app, "已恢复", None);
                    }
                } else {
                    emit_startup_status(
                        &app,
                        "重启失败",
                        Some("Python 进程多次崩溃，已停止自动重启。请查看日志后手动重试。".to_string()),
                    );
                }
            }
        }
    });
}

/// 停 → 启 Python（供更新器 ProcessController 使用），返回新端口
fn restart_python(app: &AppHandle) -> anyhow::Result<u16> {
    let state = app.state::<Arc<PythonState>>();
    let port = {
        let mut proc = state.process.lock().unwrap();
        proc.stop()?;
        proc.start()?
    };
    log::info!("Python 已重启，新端口={port}");
    Ok(port)
}

// ===== Tauri 命令 =====

/// 重试启动命令（前端按钮调用）
#[tauri::command]
async fn retry_start(app: AppHandle, state: tauri::State<'_, Arc<PythonState>>) -> Result<(), String> {
    {
        let mut proc = state.process.lock().unwrap();
        proc.stop().map_err(|e| e.to_string())?;
    }
    let app_clone = app.clone();
    let state_clone = state.inner().clone();
    tauri::async_runtime::spawn(async move {
        start_python_and_load(&app_clone, &state_clone).await;
    });
    Ok(())
}

/// 更新完成后请求应用重启服务（走统一重启链路）
#[tauri::command]
async fn restart_service(app: AppHandle) -> Result<(), String> {
    let state = app.state::<Arc<PythonState>>().inner().clone();
    tauri::async_runtime::spawn(async move {
        start_python_and_load(&app, &state).await;
    });
    Ok(())
}

/// 命令式检查更新（托盘「检查更新」与前端桥接共用），返回结果供调用方决定是否弹窗
#[tauri::command]
async fn trigger_check_update(app: AppHandle) -> Result<Option<updater::UpdateInfo>, String> {
    updater::check_update(app, false).await
}

/// 打开更新对话框（主窗桥接层监听此事件弹框；桥接在启动页同样已注入，
/// 后端未就绪时也能展示更新进度，因此无需独立窗口）。
#[tauri::command]
fn open_update_dialog(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window(window::MAIN_LABEL) {
        let _ = win.emit("open-update-dialog", ());
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
    Ok(())
}

fn setup(app: &mut App) -> Result<(), Box<dyn std::error::Error>> {
    let app_handle = app.handle().clone();

    // 解析目录
    let app_dir = resolve_app_dir();
    updater::cleanup_stale_backup(&app_dir);
    let runtime_dir = resolve_runtime_dir(&app_dir);
    let log_dir = app_dir.join("logs");
    // 更新包下载缓存目录：放 APPDATA，避免污染应用树/仓库
    let updates_dir = config::app_data_dir()
        .map(|d| d.join("updates"))
        .unwrap_or_else(|_| app_dir.join("updates"));

    log::info!("应用目录: {}", app_dir.display());
    log::info!("运行时目录: {}", runtime_dir.display());
    log::info!("日志目录: {}", log_dir.display());

    app.manage(Arc::new(AppPaths {
        app_dir: app_dir.clone(),
        runtime_dir: runtime_dir.clone(),
        log_dir: log_dir.clone(),
        updates_dir,
    }));

    // 全局状态
    let python_state = Arc::new(PythonState {
        process: std::sync::Mutex::new(PythonProcess::new(runtime_dir, app_dir, log_dir)),
    });
    app.manage(python_state.clone());
    app.manage(Arc::new(updater::UpdateState::new()));
    app.manage(tray::TrayState::default());
    app.manage(drag_drop::DraggedFiles::default());

    // 更新器进程控制器
    {
        let a = app_handle.clone();
        let controller = Arc::new(updater::ProcessController {
            stop: Arc::new({
                let a = a.clone();
                move || {
                    let state = a.state::<Arc<PythonState>>();
                    let mut proc = state.process.lock().unwrap();
                    let _ = proc.stop();
                }
            }),
            restart: Arc::new({
                let a = a.clone();
                move || restart_python(&a)
            }),
            finalize: Arc::new({
                let a = a.clone();
                move |port: u16| {
                    let state = a.state::<Arc<PythonState>>();
                    {
                        let mut proc = state.process.lock().unwrap();
                        proc.mark_running();
                    }
                    navigate_to_app(&a, port);
                    emit_startup_status(&a, "服务已就绪", None);
                }
            }),
        });
        *app_handle
            .state::<Arc<updater::UpdateState>>()
            .controller
            .lock()
            .unwrap() = Some(controller);
    }

    // 主窗口（含状态恢复、外部链接拦截、桥接脚本注入）
    window::build_main_window(&app_handle)?;
    // 首启落一份窗口状态存档，保证 window_state.json 始终存在可编辑
    window::save_state(&app_handle);

    // 系统托盘
    tray::build_tray(&app_handle)?;

    // 前端 → Rust 通知事件
    {
        let handle = app_handle.clone();
        app_handle.listen("show-notification", move |event| {
            if let Ok(payload) =
                serde_json::from_str::<notification::ShowNotification>(event.payload())
            {
                notification::handle_show_notification(&handle, payload);
            }
        });
    }

    // 启动 Python
    {
        let app_clone = app_handle.clone();
        let state_clone = python_state.clone();
        tauri::async_runtime::spawn(async move {
            start_python_and_load(&app_clone, &state_clone).await;
        });
    }
    // 崩溃监控
    start_watchdog(app_handle.clone(), python_state);

    // 启动时自动检查更新（配置开关 + 节流 + 静默失败）
    {
        let handle = app_handle.clone();
        tauri::async_runtime::spawn(async move {
            let cfg = config::AppConfig::load();
            if cfg.auto_check_update {
                tokio::time::sleep(Duration::from_secs(5)).await;
                let _ = updater::check_update(handle, true).await;
            }
        });
    }

    Ok(())
}

/// 窗口 Moved/Resized 保存去抖（Rust 侧最小间隔 1.5s）
struct WindowSaveDebounce(AtomicU64);

impl WindowSaveDebounce {
    fn should_save(&self) -> bool {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0);
        let last = self.0.load(Ordering::Relaxed);
        if now.saturating_sub(last) >= 1500 {
            self.0.store(now, Ordering::Relaxed);
            true
        } else {
            false
        }
    }
}

/// 简易双写日志：stdout + logs\tts_multimodel-shell.log（Rust 侧诊断可见，解决之前日志被吞的问题）
/// 壳日志大小上限与备份份数（B-9：tts_multimodel-shell.log 无限增长修复）
const SHELL_LOG_MAX_BYTES: u64 = 5 * 1024 * 1024;
const SHELL_LOG_BACKUPS: u32 = 3;

/// 滚动 shell 日志：tts_multimodel-shell.log -> .1 -> .2 -> .3，删除最旧
fn rotate_shell_log(path: &std::path::Path) {
    // 删除最旧备份
    let oldest = path.with_extension(format!("log.{}", SHELL_LOG_BACKUPS));
    let _ = std::fs::remove_file(&oldest);
    // 倒序移位
    for i in (1..SHELL_LOG_BACKUPS).rev() {
        let from = path.with_extension(format!("log.{}", i));
        let to = path.with_extension(format!("log.{}", i + 1));
        if from.exists() {
            let _ = std::fs::rename(&from, &to);
        }
    }
    // 当前文件 -> .1
    let _ = std::fs::rename(path, path.with_extension("log.1"));
}

struct DualLogger {
    file: std::sync::Mutex<Option<std::fs::File>>,
    path: std::sync::Mutex<Option<std::path::PathBuf>>,
}

impl log::Log for DualLogger {
    fn enabled(&self, _metadata: &log::Metadata) -> bool {
        true
    }
    fn log(&self, record: &log::Record) {
        use std::io::Write;
        let line = format!(
            "[{}] [{}] {}",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
            record.level(),
            record.args()
        );
        let mut out = std::io::stdout();
        let _ = writeln!(out, "{line}");
        if let Ok(mut guard) = self.file.lock() {
            if let Some(f) = guard.as_mut() {
                // B-9: 超过大小阈值时滚动（关旧 -> 移位 -> 重开），防止 shell 日志无限增长
                if let Ok(meta) = f.metadata() {
                    if meta.len() > SHELL_LOG_MAX_BYTES {
                        let rotate_path = self.path.lock().ok().and_then(|p| p.clone());
                        if let Some(p) = rotate_path {
                            let _ = f.flush();
                            *guard = None;
                            rotate_shell_log(&p);
                            if let Ok(nf) = std::fs::OpenOptions::new()
                                .create(true)
                                .append(true)
                                .open(&p)
                            {
                                *guard = Some(nf);
                            }
                        }
                    }
                }
                if let Some(f) = guard.as_mut() {
                    let _ = writeln!(f, "{line}");
                }
            }
        }
    }
    fn flush(&self) {
        use std::io::Write;
        if let Ok(mut guard) = self.file.lock() {
            if let Some(f) = guard.as_mut() {
                let _ = f.flush();
            }
        }
    }
}

/// 全局日志器引用（供换载前关闭文件句柄：app 目录整体重命名时不能握有 app\logs 内的句柄）
static LOGGER: OnceLock<Arc<DualLogger>> = OnceLock::new();

/// 关闭日志文件句柄（更新换载前调用；关闭后日志只写 stdout，换载完成后由重启逻辑重新打开）
pub fn close_logger() {
    if let Some(l) = LOGGER.get() {
        if let Ok(mut guard) = l.file.lock() {
            *guard = None;
        }
    }
}

/// 初始化日志：日志文件落在 logs\tts_multimodel-shell.log，RUST_LOG 可覆盖级别（debug/trace/warn/error）
fn init_logging() {
    let file = resolve_app_dir().join("logs").join("tts_multimodel-shell.log");
    if let Some(parent) = file.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&file)
        .ok();
    let logger = Arc::new(DualLogger {
        file: std::sync::Mutex::new(f),
        path: std::sync::Mutex::new(Some(file)),
    });
    let _ = LOGGER.set(logger.clone());
    if let Err(e) = log::set_boxed_logger(Box::new(logger)) {
        eprintln!("日志初始化失败: {e}");
    }
    let level = std::env::var("RUST_LOG")
        .unwrap_or_else(|_| "info".to_string())
        .to_lowercase();
    log::set_max_level(match level.as_str() {
        "debug" => log::LevelFilter::Debug,
        "trace" => log::LevelFilter::Trace,
        "warn" => log::LevelFilter::Warn,
        "error" => log::LevelFilter::Error,
        _ => log::LevelFilter::Info,
    });
}

fn main() {
    // 初始化日志（RUST_LOG 可覆盖）
    init_logging();

    tauri::Builder::default()
        // 单实例：二次启动聚焦现有窗口而不是开新实例
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            window::show_and_focus(app);
        }))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            retry_start,
            restart_service,
            trigger_check_update,
            open_update_dialog,
            updater::check_update,
            updater::start_update,
            updater::get_update_state,
            updater::get_pending_update,
            updater::dismiss_update,
            notification::show_notification,
            window::set_window_busy,
            window::show_main_window,
            window::save_window_state,
            window::window_minimize,
            window::window_maximize_toggle,
            window::window_close,
            window::window_toggle_fullscreen,
            window::window_is_fullscreen,
            drag_drop::read_dragged_file,
            config::get_app_config,
            config::set_notification_sound,
            config::set_close_to_tray,
        ])
        .on_window_event({
            let debounce = Arc::new(WindowSaveDebounce(AtomicU64::new(0)));
            move |win, event| {
                let app = win.app_handle();
                match event {
                    WindowEvent::CloseRequested { api, .. } => {
                        if win.label() == window::MAIN_LABEL {
                            let quitting = *app.state::<tray::TrayState>().quitting.lock().unwrap();
                            if quitting {
                                window::save_state(app);
                            } else if config::AppConfig::load().close_to_tray {
                                // X 关闭 → 隐藏到托盘；真正退出走托盘「退出」
                                api.prevent_close();
                                window::hide_to_tray(app);
                            } else {
                                // 用户关闭了「最小化到托盘」偏好 → 允许退出
                                *app.state::<tray::TrayState>().quitting.lock().unwrap() = true;
                                window::save_state(app);
                            }
                        }
                    }
                    WindowEvent::Moved(_) | WindowEvent::Resized(_) => {
                        if win.label() == window::MAIN_LABEL && debounce.should_save() {
                            window::save_state(app);
                        }
                    }
                    WindowEvent::Focused(true)
                        if win.label() == window::MAIN_LABEL =>
                    {
                        // Toast/托盘激活回前台时恢复任务栏可见
                        let _ = win.set_skip_taskbar(false);
                    }
                    _ => {}
                }
            }
        })
        .on_webview_event(|webview, event| {
            // 拖放进入应用时登记文件白名单（read_dragged_file 权限边界）
            if let WebviewEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) = event {
                webview.state::<drag_drop::DraggedFiles>().allow_paths(paths.clone());
            }
        })
        .setup(setup)
        .build(tauri::generate_context!())
        .expect("启动 Tauri 应用失败")
        .run(|app, event| {
            match event {
                RunEvent::Exit => {
                    // 退出兜底：停止 Python 子进程，避免孤儿进程
                    let state = app.state::<Arc<PythonState>>();
                    let mut proc = state.process.lock().unwrap();
                    let _ = proc.stop();
                }
                RunEvent::ExitRequested { api, code, .. } => {
                    let quitting = *app.state::<tray::TrayState>().quitting.lock().unwrap();
                    // 用户显式退出（托盘/快捷键）或有退出码 → 放行；否则隐藏态保活
                    if !quitting && code.is_none() {
                        api.prevent_exit();
                    }
                }
                _ => {}
            }
        });
}
