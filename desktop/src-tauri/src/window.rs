//! 窗口管理增强（对应指导文档任务 2）：
//! - 窗口状态记忆：关闭/移动/缩放时记录大小、位置、最大化，下次启动恢复；
//!   存储于 `%APPDATA%/TTSMultiModel/window_state.json`（见 [`crate::config::WindowState`]）。
//! - 外部链接：非本机地址不在 WebView 内跳转，改用系统默认浏览器打开
//!   （`on_navigation` 拦截 + `on_new_window` 拦截 target=_blank / window.open）。
//! - 桥接脚本注入：`initialization_script` 对壳页与后端页统一注入 desktop-bridge.js，
//!   页面内所有 Tauri 能力（通知/拖拽/更新/标题）由桥接层提供。
//! - 窗口标题动态更新：前端任务状态 → [`set_window_busy`] 命令 → 标题切换。
//! - 关闭按钮（X）→ 最小化到托盘：`minimize + set_skip_taskbar(true)` 等效隐藏，
//!   且系统 Toast 点击/进程激活时能自动还原（`hide()` 做不到）。

use tauri::{AppHandle, Manager, WebviewWindow, WebviewWindowBuilder};

use crate::config::WindowState;

/// 主窗口标签
pub const MAIN_LABEL: &str = "main";
/// 空闲标题
pub const IDLE_TITLE: &str = "TTS MultiModel 语音合成";
/// 处理中标题
pub const BUSY_TITLE: &str = "TTS MultiModel - 处理中...";
/// 前端桥接脚本（编译期内嵌，运行时以 initialization_script 注入）
pub const BRIDGE_JS: &str = include_str!("../../src/desktop-bridge.js");

/// 判断 URL 是否属于应用自身（允许在 WebView 内导航）。
/// 放行：`tauri:`/`about:`/`ipc:`/`data:` 内部协议、`127.0.0.1`/`localhost`（本机后端）。
pub fn is_internal_url(url: &tauri::Url) -> bool {
    match url.scheme() {
        "tauri" | "about" | "ipc" | "data" => true,
        "http" | "https" => matches!(url.host_str(), Some("127.0.0.1") | Some("localhost") | Some("tauri.localhost") | None),
        _ => false,
    }
}

/// 用系统默认浏览器打开外部链接
pub fn open_external(url: &str) {
    if let Err(e) = open::that(url) {
        log::warn!("打开外部链接失败 {url}: {e}");
    }
}

/// 恢复存档的窗口几何到 builder（缺档/异常尺寸回退 1280x800 居中）。
/// 位置合法性由 [`position_sane`] 兜底（显示器拔掉后窗口可能跑到屏幕外 → 居中重建）。
fn apply_saved_state<'a>(builder: WebviewWindowBuilder<'a, tauri::Wry, AppHandle>) -> WebviewWindowBuilder<'a, tauri::Wry, AppHandle> {
    match WindowState::load() {
        Some(s) if position_sane(&s) => {
            let mut b = builder
                .inner_size(s.width as f64, s.height as f64)
                .position(s.x as f64, s.y as f64);
            if s.maximized {
                b = b.maximized(true);
            }
            b
        }
        _ => builder.inner_size(1280.0, 800.0).center(),
    }
}

/// 尺寸底线校验（异常存档回退默认）
pub fn position_sane(s: &WindowState) -> bool {
    s.width >= 800 && s.height >= 600 && s.width <= 16384 && s.height <= 16384
}

/// 读取当前窗口几何并落盘（Moved/Resized 去抖后、隐藏前、退出前调用）。
///
/// getter（inner_size / outer_position）返回**物理像素**，而 builder 的
/// inner_size / position 接受**逻辑像素**；这里统一除以 scale_factor 存逻辑值，
/// 否则高 DPI 下每次重启窗口会按缩放系数逐次放大。
pub fn save_state(app: &AppHandle) {
    let Some(win) = app.get_webview_window(MAIN_LABEL) else {
        return;
    };
    let scale = win.scale_factor().unwrap_or(1.0).max(0.5);
    let (pw, ph) = win
        .inner_size()
        .map(|s| (s.width, s.height))
        .unwrap_or((1280, 800));
    let (px, py) = win
        .outer_position()
        .map(|p| (p.x, p.y))
        .unwrap_or((100, 100));
    let state = WindowState {
        width: (pw as f64 / scale).round().max(1.0) as u32,
        height: (ph as f64 / scale).round().max(1.0) as u32,
        x: (px as f64 / scale).round() as i32,
        y: (py as f64 / scale).round() as i32,
        maximized: win.is_maximized().unwrap_or(false),
        visible: true,
    };
    if let Err(e) = state.save() {
        log::warn!("保存窗口状态失败: {e}");
    }
}

/// 隐藏到托盘（不退出）：最小化 + 移出任务栏。先存状态再隐藏。
pub fn hide_to_tray(app: &AppHandle) {
    save_state(app);
    if let Some(win) = app.get_webview_window(MAIN_LABEL) {
        let _ = win.set_skip_taskbar(true);
        let _ = win.minimize();
    }
}

/// 显示并聚焦主窗口（托盘左键 / 二次启动 / Toast 点击兜底）
pub fn show_and_focus(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(MAIN_LABEL) {
        let _ = win.set_skip_taskbar(false);
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

/// 切换显示/隐藏（托盘左键单击）
pub fn toggle_visibility(app: &AppHandle) {
    let Some(win) = app.get_webview_window(MAIN_LABEL) else {
        return;
    };
    let visible = win.is_visible().unwrap_or(true);
    let minimized = win.is_minimized().unwrap_or(false);
    if visible && !minimized {
        hide_to_tray(app);
    } else {
        show_and_focus(app);
    }
}

/// 更新窗口标题（busy=true 显示处理中）
pub fn set_busy_title(app: &AppHandle, busy: bool) {
    if let Some(win) = app.get_webview_window(MAIN_LABEL) {
        let _ = win.set_title(if busy { BUSY_TITLE } else { IDLE_TITLE });
    }
}

/// 构建主窗口：恢复状态 + 外部链接拦截 + 桥接脚本注入。
/// 初始 visible(false)，几何就绪后 show 避免状态恢复闪烁。
pub fn build_main_window(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    let win = apply_saved_state(
        WebviewWindowBuilder::new(
            app,
            MAIN_LABEL,
            tauri::WebviewUrl::App("index.html".into()),
        )
        .title(IDLE_TITLE)
        .min_inner_size(1024.0, 768.0)
        .resizable(true)
        // 无边框：标题栏由前端自绘（拖拽区 + 窗口控制按钮），正式产品不露系统窗口
        .decorations(false)
        .visible(false)
        .initialization_script(BRIDGE_JS)
        .on_navigation(|url| {
            if is_internal_url(url) {
                true
            } else {
                open_external(url.as_str());
                false
            }
        })
        .on_new_window(|url, _features| {
            // target=_blank / window.open 一律系统浏览器
            open_external(url.as_str());
            tauri::webview::NewWindowResponse::Deny
        }),
    )
    .build()?;
    let _ = win.show();
    Ok(win)
}

/// 前端命令：设置标题忙闲状态（任务开始/结束由桥接层调用）
#[tauri::command]
pub fn set_window_busy(app: AppHandle, busy: bool) {
    set_busy_title(&app, busy);
    crate::tray::set_tray_busy(&app, busy);
}

/// 前端命令：显示并聚焦主窗口（Toast 点击兜底）
#[tauri::command]
pub fn show_main_window(app: AppHandle) {
    show_and_focus(&app);
}

/// 前端命令：手动保存窗口状态
#[tauri::command]
pub fn save_window_state(app: AppHandle) {
    save_state(&app);
}

/// 前端命令：最小化窗口
#[tauri::command]
pub fn window_minimize(app: AppHandle) {
    if let Some(win) = app.get_webview_window(MAIN_LABEL) {
        let _ = win.minimize();
    }
}

/// 前端命令：最大化/还原切换
#[tauri::command]
pub fn window_maximize_toggle(app: AppHandle) {
    if let Some(win) = app.get_webview_window(MAIN_LABEL) {
        if win.is_maximized().unwrap_or(false) {
            let _ = win.unmaximize();
        } else {
            let _ = win.maximize();
        }
    }
}

/// 前端命令：关闭按钮（与系统 X 一致：保存状态并最小化到托盘，不退出进程）
#[tauri::command]
pub fn window_close(app: AppHandle) {
    hide_to_tray(&app);
}

/// 前端命令：全屏/退出全屏切换，返回切换后的全屏状态（供前端更新标题栏样式）
#[tauri::command]
pub fn window_toggle_fullscreen(app: AppHandle) -> bool {
    let Some(win) = app.get_webview_window(MAIN_LABEL) else {
        return false;
    };
    let fs = win.is_fullscreen().unwrap_or(false);
    let _ = win.set_fullscreen(!fs);
    !fs
}

/// 前端命令：查询当前全屏状态
#[tauri::command]
pub fn window_is_fullscreen(app: AppHandle) -> bool {
    app.get_webview_window(MAIN_LABEL)
        .and_then(|w| w.is_fullscreen().ok())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn internal_urls_allowed() {
        assert!(is_internal_url(&tauri::Url::parse("tauri://localhost/index.html").unwrap()));
        assert!(is_internal_url(&tauri::Url::parse("http://127.0.0.1:7870/").unwrap()));
        assert!(is_internal_url(&tauri::Url::parse("http://localhost:5173/").unwrap()));
        assert!(is_internal_url(&tauri::Url::parse("about:blank").unwrap()));
        assert!(is_internal_url(&tauri::Url::parse("http://tauri.localhost/index.html").unwrap()));
    }

    #[test]
    fn external_urls_blocked() {
        assert!(!is_internal_url(&tauri::Url::parse("https://www.baidu.com/").unwrap()));
        assert!(!is_internal_url(&tauri::Url::parse("https://github.com/x").unwrap()));
        assert!(!is_internal_url(&tauri::Url::parse("http://8.8.8.8/").unwrap()));
    }

    #[test]
    fn title_constants() {
        assert_eq!(IDLE_TITLE, "TTS MultiModel 语音合成");
        assert_eq!(BUSY_TITLE, "TTS MultiModel - 处理中...");
    }

    #[test]
    fn position_sanity_checks_bounds() {
        let ok = WindowState { width: 1280, height: 800, x: 0, y: 0, maximized: false, visible: true };
        let tiny = WindowState { width: 100, height: 80, x: 0, y: 0, maximized: false, visible: true };
        let huge = WindowState { width: 999999, height: 800, x: 0, y: 0, maximized: false, visible: true };
        assert!(position_sane(&ok));
        assert!(!position_sane(&tiny));
        assert!(!position_sane(&huge));
    }
}
