//! 系统通知：前端事件 `show-notification` → Windows Toast。
//!
//! 规则（对应指导文档任务 3）：
//! - 应用窗口在前台（聚焦且未最小化）时**不发**通知，避免打扰；
//! - 声音受 `AppConfig.notification_sound` 控制；
//! - Windows 平台点击 Toast 会激活本应用进程（系统默认行为）。注意：当前
//!   **未实现** `notification-clicked` 前端兜底，若窗口处于隐藏（托盘）状态，
//!   点击通知仅激活进程，窗口不会自动唤回（已知限制，见 AI-2 交接文档 §6）。

use serde::Deserialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_notification::NotificationExt;

use crate::config::AppConfig;

/// `show-notification` 事件负载（前端 emit）
#[derive(Debug, Clone, Deserialize)]
pub struct ShowNotification {
    pub title: String,
    #[serde(default)]
    pub body: String,
}

/// 当前应用是否处于前台（任一可见且聚焦的窗口）
fn is_foreground(app: &AppHandle) -> bool {
    app.webview_windows().values().any(|w| {
        w.is_focused().unwrap_or(false) && w.is_visible().unwrap_or(false) && !w.is_minimized().unwrap_or(false)
    })
}

/// 处理前端通知请求（在 `setup` 中通过 `app.listen` 注册）
pub fn handle_show_notification(app: &AppHandle, payload: ShowNotification) {
    if is_foreground(app) {
        log::debug!("应用在前台，抑制通知: {}", payload.title);
        return;
    }
    let cfg = AppConfig::load();
    let mut builder = app
        .notification()
        .builder()
        .title(&payload.title)
        .body(&payload.body);
    if cfg.notification_sound {
        builder = builder.sound("Notification.Default");
    }
    if let Err(e) = builder.show() {
        log::warn!("发送系统通知失败: {e}");
    }
}

/// 前端命令：直接发一条通知（如更新完成提示），同样受前台抑制规则约束
#[tauri::command]
pub fn show_notification(app: AppHandle, title: String, body: String) -> Result<(), String> {
    handle_show_notification(&app, ShowNotification { title, body });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn payload_deserialize_optional_body() {
        let p: ShowNotification = serde_json::from_str(r#"{"title":"修复完成"}"#).unwrap();
        assert_eq!(p.title, "修复完成");
        assert_eq!(p.body, "");
    }
}
