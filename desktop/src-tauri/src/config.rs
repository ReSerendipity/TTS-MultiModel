//! 配置管理：应用共享目录（`%APPDATA%/TTSMultiModel`）、壳配置（更新设置/通知设置）
//! 与窗口状态（大小/位置/最大化）的持久化读写。
//!
//! 文件布局：
//! - `<appdata>/config.json` —— [`AppConfig`]
//! - `<appdata>/window_state.json` —— [`WindowState`]
//!
//! 所有写入都走“先写临时文件再 rename”的原子替换，避免进程中途退出留下半截 JSON。

use std::fs;
use std::io;
use std::path::PathBuf;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

/// 应用数据目录名（位于 `%APPDATA%` 下）
pub const APP_DATA_DIR_NAME: &str = "TTSMultiModel";

/// 返回 `%APPDATA%/TTSMultiModel`，必要时创建。配置缺失 `APPDATA` 时回退到 home 目录。
pub fn app_data_dir() -> Result<PathBuf> {
    let base = dirs::config_dir()
        .or_else(|| dirs::home_dir().map(|h| h.join("AppData").join("Roaming")))
        .context("无法解析 APPDATA 配置目录")?;
    let dir = base.join(APP_DATA_DIR_NAME);
    fs::create_dir_all(&dir).with_context(|| format!("创建配置目录失败: {}", dir.display()))?;
    Ok(dir)
}

/// 更新来源配置：GitHub 仓库或自定义服务器。
///
/// 自定义服务器需提供一个 JSON 端点，字段与 [`crate::updater::UpdateInfo`] 对齐：
/// `{"version":"1.5.2","changelog":"...","url":"...","sha256":"...","size":123}`，
/// 也可以直接返回 GitHub release JSON（自动提取 assets 中的 `app-v{ver}.zip` 与 `.sha256`）。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum UpdateSource {
    Github { owner: String, repo: String },
    Custom { url: String },
}

impl Default for UpdateSource {
    fn default() -> Self {
        // 默认指向本仓库（与 origin 一致）
        UpdateSource::Github {
            owner: "ReSerendipity".into(),
            repo: "TTS_MultiModel".into(),
        }
    }
}

/// 壳级配置（`config.json`）。所有字段都有默认值，文件缺失/损坏时使用 [`AppConfig::default`]。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct AppConfig {
    /// 更新来源
    pub update_source: UpdateSource,
    /// 启动时是否自动检查更新
    pub auto_check_update: bool,
    /// 系统通知是否带声音
    pub notification_sound: bool,
    /// 关闭窗口时最小化到托盘而不是退出
    pub close_to_tray: bool,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            update_source: UpdateSource::default(),
            auto_check_update: true,
            notification_sound: true,
            close_to_tray: true,
        }
    }
}

fn config_path() -> Result<PathBuf> {
    Ok(app_data_dir()?.join("config.json"))
}

/// 去除 UTF-8 BOM（记事本「另存为 UTF-8」默认带 BOM，serde_json 无法解析）。
fn strip_bom(s: &str) -> &str {
    s.strip_prefix('\u{feff}').unwrap_or(s)
}

impl AppConfig {
    /// 读取配置；文件不存在或解析失败时返回默认值（并记录日志）。
    pub fn load() -> Self {
        match config_path() {
            Ok(p) => match fs::read_to_string(&p) {
                Ok(s) => serde_json::from_str(strip_bom(&s)).unwrap_or_else(|e| {
                    log::warn!("解析 {} 失败，使用默认配置: {}", p.display(), e);
                    Self::default()
                }),
                Err(e) if e.kind() == io::ErrorKind::NotFound => Self::default(),
                Err(e) => {
                    log::warn!("读取 {} 失败，使用默认配置: {}", p.display(), e);
                    Self::default()
                }
            },
            Err(e) => {
                log::warn!("无法定位配置目录，使用默认配置: {}", e);
                Self::default()
            }
        }
    }

    /// 原子写回配置。
    pub fn save(&self) -> Result<()> {
        let p = config_path()?;
        write_atomic(&p, serde_json::to_string_pretty(self)?.as_bytes())
            .with_context(|| format!("保存配置失败: {}", p.display()))
    }
}

/// 前端命令：读取当前壳配置（更新/通知/关窗行为等）
#[tauri::command]
pub fn get_app_config() -> AppConfig {
    AppConfig::load()
}

/// 前端命令：开关系统通知声音（对应指导文档任务 3「通知有声音，可在设置中关闭」）。
/// 更新对话框与设置页都可通过本命令持久化偏好。
#[tauri::command]
pub fn set_notification_sound(enabled: bool) -> Result<(), String> {
    let mut cfg = AppConfig::load();
    cfg.notification_sound = enabled;
    cfg.save().map_err(|e| format!("{e:#}"))
}

/// 前端命令：开关「关闭窗口最小化到托盘」
#[tauri::command]
pub fn set_close_to_tray(enabled: bool) -> Result<(), String> {
    let mut cfg = AppConfig::load();
    cfg.close_to_tray = enabled;
    cfg.save().map_err(|e| format!("{e:#}"))
}

/// 窗口状态（`window_state.json`）。物理像素 + 双坐标，兼顾多显示器。
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct WindowState {
    pub width: u32,
    pub height: u32,
    pub x: i32,
    pub y: i32,
    pub maximized: bool,
    pub visible: bool,
}

fn window_state_path() -> Result<PathBuf> {
    Ok(app_data_dir()?.join("window_state.json"))
}

impl WindowState {
    /// 读取窗口状态；不存在或损坏返回 `None`。
    pub fn load() -> Option<Self> {
        let p = window_state_path().ok()?;
        let s = fs::read_to_string(&p).ok()?;
        match serde_json::from_str::<WindowState>(strip_bom(&s)) {
            Ok(w) => Some(w),
            Err(e) => {
                log::warn!("解析 {} 失败: {}", p.display(), e);
                None
            }
        }
    }

    /// 原子写回窗口状态。
    pub fn save(&self) -> Result<()> {
        let p = window_state_path()?;
        write_atomic(&p, serde_json::to_string_pretty(self)?.as_bytes())
            .with_context(|| format!("保存窗口状态失败: {}", p.display()))
    }
}

/// 先写同目录 `.tmp` 再 rename，保证目标文件要么是旧内容要么是新内容。
fn write_atomic(path: &PathBuf, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, bytes)?;
    fs::rename(&tmp, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_roundtrip() {
        let cfg = AppConfig::default();
        let json = serde_json::to_string(&cfg).unwrap();
        let back: AppConfig = serde_json::from_str(&json).unwrap();
        assert!(back.auto_check_update);
        assert!(back.close_to_tray);
        assert!(back.notification_sound);
        match back.update_source {
            UpdateSource::Github { owner, repo } => {
                assert_eq!(owner, "ReSerendipity");
                assert_eq!(repo, "TTS_MultiModel");
            }
            UpdateSource::Custom { .. } => panic!("default should be github"),
        }
    }

    #[test]
    fn missing_fields_use_default() {
        // 手工编辑的旧配置缺字段时不应炸
        let back: AppConfig = serde_json::from_str(r#"{"notification_sound":false}"#).unwrap();
        assert!(!back.notification_sound);
        assert!(back.auto_check_update);
    }

    #[test]
    fn window_state_roundtrip() {
        let ws = WindowState { width: 1280, height: 800, x: 10, y: 20, maximized: true, visible: true };
        let json = serde_json::to_string(&ws).unwrap();
        let back: WindowState = serde_json::from_str(&json).unwrap();
        assert_eq!(back.width, 1280);
        assert!(back.maximized);
    }

    #[test]
    fn strip_bom_handles_utf8_bom() {
        // 记事本「另存为 UTF-8」会带 BOM，serde_json 直接解析会失败；strip_bom 后必须成功
        let raw = "\u{feff}{\"close_to_tray\":false}";
        assert_eq!(strip_bom(raw), "{\"close_to_tray\":false}");
        let cfg: AppConfig = serde_json::from_str(strip_bom(raw)).unwrap();
        assert!(!cfg.close_to_tray);
        // 无 BOM 时应原样返回
        assert_eq!(strip_bom("{\"a\":1}"), "{\"a\":1}");
    }

    #[test]
    fn write_atomic_creates_file() {
        let dir = std::env::temp_dir().join(format!("tts_multimodel_cfg_test_{}", std::process::id()));
        let path = dir.join("config.json");
        write_atomic(&path, b"{}".as_slice()).unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "{}");
        let _ = fs::remove_dir_all(&dir);
    }
}
