//! 系统托盘（对应指导文档任务 1）。
//!
//! 托盘图标：左键单击切换主窗口显示/隐藏；右键显示上下文菜单：
//! ```text
//! ├─ 显示主窗口
//! ├─ 隐藏到托盘
//! ├─ ─────────
//! ├─ 打开日志目录
//! ├─ 检查更新        ← 有更新时文字变为「检查更新（有更新可用）」
//! ├─ ─────────
//! └─ 退出 TTS MultiModel
//! ```
//! 悬停提示：`TTS MultiModel - 运行中` / `TTS MultiModel - 正在处理`。
//!
//! 本模块统一使用默认运行时 `Wry`（二进制 crate 不需要泛型 runtime），
//! `AppHandle` 即 `AppHandle<Wry>`，与其它模块签名一致。

use std::sync::{Arc, Mutex};

use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager};

/// 菜单项 ID
const ID_SHOW: &str = "tray_show";
const ID_HIDE: &str = "tray_hide";
const ID_LOGS: &str = "tray_logs";
const ID_UPDATE: &str = "tray_update";
const ID_QUIT: &str = "tray_quit";

const TOOLTIP_IDLE: &str = "TTS MultiModel - 运行中";
const TOOLTIP_BUSY: &str = "TTS MultiModel - 正在处理";
const MENU_UPDATE_IDLE: &str = "检查更新";
const MENU_UPDATE_AVAILABLE: &str = "检查更新（有更新可用）";

/// 托盘句柄集合，供跨模块更新文字/提示
#[derive(Default)]
pub struct TrayState {
    pub tray: Mutex<Option<TrayIcon>>,
    pub update_item: Mutex<Option<MenuItem<tauri::Wry>>>,
    /// 退出意图（关闭按钮走隐藏，仅托盘「退出」置真后允许真正退出）
    pub quitting: Arc<Mutex<bool>>,
}

/// 构建托盘图标与菜单，注册到 [`TrayState`]。
pub fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, ID_SHOW, "显示主窗口", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, ID_HIDE, "隐藏到托盘", true, None::<&str>)?;
    let sep1 = PredefinedMenuItem::separator(app)?;
    let logs = MenuItem::with_id(app, ID_LOGS, "打开日志目录", true, None::<&str>)?;
    let update = MenuItem::with_id(app, ID_UPDATE, MENU_UPDATE_IDLE, true, None::<&str>)?;
    let sep2 = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, ID_QUIT, "退出 TTS MultiModel", true, None::<&str>)?;

    let menu = Menu::with_items(app, &[&show, &hide, &sep1, &logs, &update, &sep2, &quit])?;

    let mut builder = TrayIconBuilder::with_id("tts-multimodel-tray")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .tooltip(TOOLTIP_IDLE)
        .on_menu_event(handle_menu)
        .on_tray_icon_event(handle_tray_event);

    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    let tray = builder.build(app)?;

    let state = app.state::<TrayState>();
    *state.tray.lock().unwrap() = Some(tray);
    *state.update_item.lock().unwrap() = Some(update);
    log::info!("系统托盘已创建（Windows 上图标可能被收纳，可拖到任务栏显示）");
    Ok(())
}

/// 左键单击：切换窗口显示/隐藏。仅在按钮抬起时响应，避免 Down+Up 双触发。
fn handle_tray_event(tray: &TrayIcon, event: TrayIconEvent) {
    if let TrayIconEvent::Click {
        button: MouseButton::Left,
        button_state: MouseButtonState::Up,
        ..
    } = event
    {
        crate::window::toggle_visibility(tray.app_handle());
    }
}

/// 菜单点击分发
fn handle_menu(app: &AppHandle, event: MenuEvent) {
    match event.id.as_ref() {
        ID_SHOW => crate::window::show_and_focus(app),
        ID_HIDE => crate::window::hide_to_tray(app),
        ID_LOGS => open_logs(app),
        ID_UPDATE => trigger_manual_check(app),
        ID_QUIT => request_quit(app),
        other => log::debug!("未处理的托盘菜单项: {other}"),
    }
}

/// 打开日志目录（不存在则先创建）
fn open_logs(app: &AppHandle) {
    let log_dir = app.state::<Arc<crate::AppPaths>>().log_dir.clone();
    if let Err(e) = std::fs::create_dir_all(&log_dir) {
        log::warn!("创建日志目录失败: {e}");
    }
    if let Err(e) = open::that(&log_dir) {
        log::warn!("打开日志目录失败 {}: {e}", log_dir.display());
    }
}

/// 托盘「检查更新」：走命令式检查，结果事件由前端桥接弹窗
fn trigger_manual_check(app: &AppHandle) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        // 先唤起主窗口：检查结果弹窗必须对用户可见（窗口隐藏到托盘时也弹出）
        crate::window::show_and_focus(&handle);
        match crate::updater::check_update(handle.clone(), false).await {
            Ok(Some(_)) => {
                // 有更新：让前端打开对话框
                let _ = handle.emit("open-update-dialog", ());
            }
            Ok(None) => {
                let _ = handle.emit("update-not-available", ());
            }
            Err(e) => {
                let _ = handle.emit("update-check-failed", serde_json::json!({ "message": e }));
            }
        }
    });
}

/// 托盘「退出」：置退出意图 → 存窗口状态 → app.exit
fn request_quit(app: &AppHandle) {
    *app.state::<TrayState>().quitting.lock().unwrap() = true;
    crate::window::save_state(app);
    app.exit(0);
}

/// 更新托盘悬停提示（忙/闲）
pub fn set_tray_busy(app: &AppHandle, busy: bool) {
    if let Some(tray) = app.state::<TrayState>().tray.lock().unwrap().as_ref() {
        let _ = tray.set_tooltip(Some(if busy { TOOLTIP_BUSY } else { TOOLTIP_IDLE }));
    }
}

/// 更新托盘「检查更新」菜单项文字（是否有更新可用）
pub fn set_update_badge(app: &AppHandle, available: bool) {
    if let Some(item) = app.state::<TrayState>().update_item.lock().unwrap().as_ref() {
        let _ = item.set_text(if available { MENU_UPDATE_AVAILABLE } else { MENU_UPDATE_IDLE });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn menu_id_constants_unique() {
        let ids = [ID_SHOW, ID_HIDE, ID_LOGS, ID_UPDATE, ID_QUIT];
        let mut sorted = ids.to_vec();
        sorted.sort();
        sorted.dedup();
        assert_eq!(ids.len(), sorted.len());
    }

    #[test]
    fn tray_state_default_not_quitting() {
        let s = TrayState::default();
        assert!(!*s.quitting.lock().unwrap());
        assert!(s.tray.lock().unwrap().is_none());
        assert!(s.update_item.lock().unwrap().is_none());
    }
}
