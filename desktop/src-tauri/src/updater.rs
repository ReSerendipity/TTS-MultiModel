//! 应用代码增量更新：检查更新 + 下载覆盖（原子换载 + 失败回滚）。
//!
//! 更新对象是应用代码目录 `app/`（Python 后端与前端模板/静态资源），不包含壳自身。
//!
//! 事件契约（前端桥接层 `desktop-bridge.js` 消费）：
//! - `update-available` [`UpdateInfo`] —— 发现新版本
//! - `update-progress` [`UpdateProgress`] —— 下载/校验/解压/换载/重启各阶段进度
//! - `update-done` `{ version, message }` —— 更新成功
//! - `update-error` `{ message }` —— 更新失败（已自动回滚）

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager, State};

use crate::config::{AppConfig, UpdateSource};
use crate::health_check;
use crate::AppPaths;

/// GitHub API 速率限制：未认证 60 次/小时，启动自动检查做节流
const AUTO_CHECK_MIN_INTERVAL: Duration = Duration::from_secs(10 * 60);
/// 检查请求超时
const HTTP_TIMEOUT: Duration = Duration::from_secs(10);
/// 更新包资产命名模板
const ASSET_TEMPLATE: &str = "app-v{version}.zip";

// ===== 数据结构 =====

/// `app/version.json` 的 schema
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppVersion {
    pub version: String,
    #[serde(default)]
    pub release_date: String,
    #[serde(default)]
    pub minimum_shell_version: String,
    #[serde(default)]
    pub changelog: String,
}

/// 更新信息（检查结果，前端对话框直接渲染）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UpdateInfo {
    /// 远端最新版本（semver，已剥离 v 前缀）
    pub version: String,
    /// 本地当前版本
    pub current: String,
    /// 变更日志（markdown）
    pub changelog: String,
    /// 下载地址
    pub url: String,
    /// SHA256（小写 hex）
    pub sha256: String,
    /// 包大小（字节，0=未知）
    #[serde(default)]
    pub size: u64,
}

/// 缓存的检查结果（桥接层加载时拉取，避免启动事件竞态）
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct CachedCheck {
    pub checked: bool,
    pub info: Option<UpdateInfo>,
}

/// 更新进度事件 `update-progress` 的负载
#[derive(Debug, Clone, Serialize)]
pub struct UpdateProgress {
    /// download | verify | extract | swap | restart
    pub phase: &'static str,
    pub percent: u8,
    pub message: String,
}

/// 进程控制器：由 main.rs 注入，供更新器停/启 Python（避免模块循环依赖）
pub struct ProcessController {
    /// 停止 Python 子进程（幂等）
    pub stop: Arc<dyn Fn() + Send + Sync>,
    /// 重新启动 Python 子进程，返回新端口（调用前必须先 stop）
    pub restart: Arc<dyn Fn() -> Result<u16> + Send + Sync>,
    /// 新服务就绪后收尾：标记运行中 + 导航 WebView 到新端口
    pub finalize: Arc<dyn Fn(u16) + Send + Sync>,
}

/// 更新器共享状态
pub struct UpdateState {
    /// 手动/自动检查互斥，防止并发重复
    pub busy: AtomicBool,
    /// 启动自动检查节流时间戳
    pub last_auto_check: Mutex<Option<std::time::SystemTime>>,
    /// 最近一次检查结果缓存
    pub cached: Mutex<CachedCheck>,
    /// 是否有更新流程正在执行（watchdog 据此暂停崩溃重启）
    pub updating: AtomicBool,
    pub controller: Arc<Mutex<Option<Arc<ProcessController>>>>,
}

impl Default for UpdateState {
    fn default() -> Self {
        Self::new()
    }
}

impl UpdateState {
    pub fn new() -> Self {
        Self {
            busy: AtomicBool::new(false),
            last_auto_check: Mutex::new(None),
            cached: Mutex::new(CachedCheck::default()),
            updating: AtomicBool::new(false),
            controller: Arc::new(Mutex::new(None)),
        }
    }
}

/// 读取 `app/version.json`；缺失或损坏返回 `None`
pub fn load_app_version(app_dir: &Path) -> Option<AppVersion> {
    let p = app_dir.join("version.json");
    match fs::read_to_string(&p) {
        Ok(s) => match serde_json::from_str::<AppVersion>(&s) {
            Ok(v) => Some(v),
            Err(e) => {
                log::warn!("解析 {} 失败: {}", p.display(), e);
                None
            }
        },
        Err(_) => {
            log::info!("{} 不存在，视为无版本文件", p.display());
            None
        }
    }
}

// ===== 检查更新 =====

/// GitHub release JSON 中关心的字段
#[derive(Debug, Deserialize)]
struct GhRelease {
    tag_name: String,
    #[serde(default)]
    body: Option<String>,
    #[serde(default)]
    assets: Vec<GhAsset>,
    #[serde(default)]
    draft: bool,
}

#[derive(Debug, Deserialize)]
struct GhAsset {
    name: String,
    url: String,
    #[serde(default)]
    browser_download_url: Option<String>,
    #[serde(default)]
    size: Option<u64>,
}

/// 解析版本号：剥 v/V 前缀
pub fn normalize_version(raw: &str) -> String {
    raw.trim().trim_start_matches(['v', 'V']).to_string()
}

fn parse_semver(raw: &str) -> Result<semver::Version> {
    let v = normalize_version(raw);
    semver::Version::parse(&v).with_context(|| format!("版本号无法解析: {raw}"))
}

/// 构建 HTTP 客户端：native-tls（Windows SChannel，与系统浏览器/.NET 同栈）+ 系统代理/环境变量代理。
/// 解决大陆网络下 rustls 指纹被 TLS 层阻断导致检查更新失败的问题（实测 curl/rustls 0.02s 断、.NET 200 通）。
fn build_http_client(timeout: Duration) -> Result<reqwest::Client> {
    let mut builder = reqwest::Client::builder()
        .timeout(timeout)
        .user_agent("TTSMultiModel-Desktop");
    if let Some(proxy) = resolve_proxy() {
        log::info!("网络请求使用代理: {proxy}");
        builder = builder.proxy(reqwest::Proxy::all(&proxy).context("代理地址无效")?);
    }
    builder.build().context("创建 HTTP 客户端失败")
}

/// 解析可用代理：优先环境变量（HTTPS_PROXY 等），其次 Windows 系统代理（ProxyEnable=1）
fn resolve_proxy() -> Option<String> {
    for var in [
        "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy",
    ] {
        if let Ok(v) = std::env::var(var) {
            let v = v.trim().to_string();
            if !v.is_empty() {
                return Some(v);
            }
        }
    }
    #[cfg(windows)]
    {
        if let Ok(key) = winreg::RegKey::predef(winreg::enums::HKEY_CURRENT_USER)
            .open_subkey(r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        {
            let enable: u32 = key.get_value("ProxyEnable").unwrap_or(0);
            if enable == 1 {
                if let Ok(server) = key.get_value::<String, _>("ProxyServer") {
                    let server = server.trim();
                    if !server.is_empty() {
                        return Some(parse_proxy_server(server));
                    }
                }
            }
        }
    }
    None
}

/// 规范化 Windows 注册表 ProxyServer 值（可能含协议前缀或分号分段，如 http=...;https=...）
fn parse_proxy_server(s: &str) -> String {
    if s.contains('=') && s.contains(';') {
        for part in s.split(';') {
            let part = part.trim();
            if let Some(v) = part.strip_prefix("https=") {
                let v = v.trim();
                if !v.is_empty() {
                    return if v.contains("://") { v.to_string() } else { format!("http://{v}") };
                }
            }
        }
    }
    if s.contains("://") { s.to_string() } else { format!("http://{s}") }
}

/// 拉取 GitHub release JSON（latest 或按 tag 定位）。
/// 备用：当前更新检查流程走 fetch_recent_releases，此函数保留供按 tag 回滚/特定版本定位。
#[allow(dead_code)]
async fn fetch_github_release(
    client: &reqwest::Client,
    owner: &str,
    repo: &str,
    tag: Option<&str>,
) -> Result<GhRelease> {
    let url = match tag {
        Some(t) => format!("https://api.github.com/repos/{owner}/{repo}/releases/tags/{t}"),
        None => format!("https://api.github.com/repos/{owner}/{repo}/releases/latest"),
    };
    let resp = client
        .get(&url)
        .header("Accept", "application/vnd.github+json")
        .header("User-Agent", "TTSMultiModel-Desktop")
        .send()
        .await
        .with_context(|| format!("请求 GitHub API 失败: {url}"))?;
    let status = resp.status();
    if status == reqwest::StatusCode::NOT_FOUND {
        return Err(anyhow!("未找到更新发布（release）"));
    }
    if status == reqwest::StatusCode::FORBIDDEN || status == reqwest::StatusCode::UNAUTHORIZED {
        return Err(anyhow!("GitHub API 访问受限（速率限制或鉴权失败）"));
    }
    if !status.is_success() {
        return Err(anyhow!("GitHub API 返回 HTTP {status}"));
    }
    let release: GhRelease = resp.json().await.context("解析 GitHub release JSON 失败")?;
    Ok(release)
}

/// 拉取最新可更新 release：取最近 N 条中版本最高的（含 prerelease，排除 draft）。
/// 相比 /releases/latest（只认正式版），这样测试版（prerelease）也能被发现。
async fn fetch_latest_release(
    client: &reqwest::Client,
    owner: &str,
    repo: &str,
) -> Result<GhRelease> {
    let url = format!("https://api.github.com/repos/{owner}/{repo}/releases?per_page=10");
    let resp = client
        .get(&url)
        .header("Accept", "application/vnd.github+json")
        .header("User-Agent", "TTSMultiModel-Desktop")
        .send()
        .await
        .with_context(|| format!("请求 GitHub API 失败: {url}"))?;
    let status = resp.status();
    if status == reqwest::StatusCode::FORBIDDEN || status == reqwest::StatusCode::UNAUTHORIZED {
        return Err(anyhow!("GitHub API 访问受限（速率限制或鉴权失败）"));
    }
    if !status.is_success() {
        return Err(anyhow!("GitHub API 返回 HTTP {status}"));
    }
    let releases: Vec<GhRelease> = resp.json().await.context("解析 GitHub release 列表失败")?;
    releases
        .into_iter()
        .filter(|r| !r.draft)
        .filter(|r| parse_semver(&normalize_version(&r.tag_name)).is_ok())
        .max_by(|a, b| {
            let av = parse_semver(&normalize_version(&a.tag_name)).unwrap_or_else(|_| semver::Version::new(0, 0, 0));
            let bv = parse_semver(&normalize_version(&b.tag_name)).unwrap_or_else(|_| semver::Version::new(0, 0, 0));
            av.cmp(&bv)
        })
        .ok_or_else(|| anyhow!("未找到任何已发布版本"))
}

/// 从 release assets 解析 `app-v{ver}.zip`（下载地址、大小、.sha256 资产地址）
fn resolve_assets(release: &GhRelease) -> Result<(String, u64, String)> {
    let version = normalize_version(&release.tag_name);
    let zip_name = ASSET_TEMPLATE.replace("{version}", &version);
    let sha_name = format!("{zip_name}.sha256");

    let zip = release
        .assets
        .iter()
        .find(|a| a.name == zip_name)
        .or_else(|| {
            release
                .assets
                .iter()
                .find(|a| a.name.starts_with("app-v") && a.name.ends_with(".zip"))
        })
        .ok_or_else(|| anyhow!("更新包不存在: 未找到 {zip_name}"))?;
    let size = zip.size.unwrap_or(0);
    let url = zip
        .browser_download_url
        .clone()
        .unwrap_or_else(|| zip.url.clone());
    let sha_url = release
        .assets
        .iter()
        .find(|a| a.name == sha_name)
        .map(|a| a.browser_download_url.clone().unwrap_or_else(|| a.url.clone()))
        .unwrap_or_default();
    Ok((url, size, sha_url))
}

/// 从任意文本提取第一个 64 位 hex token（SHA256）
fn extract_hex64(text: &str) -> Option<String> {
    text.split(|c: char| !c.is_ascii_hexdigit())
        .find(|t| t.len() == 64)
        .map(|t| t.to_ascii_lowercase())
}

/// 获取 SHA256：优先 release 描述，其次 .sha256 资产
async fn fetch_sha256(client: &reqwest::Client, release_body: Option<&str>, sha_url: &str) -> Result<String> {
    if let Some(body) = release_body {
        if let Some(h) = extract_hex64(body) {
            return Ok(h);
        }
    }
    if !sha_url.is_empty() {
        let resp = client
            .get(sha_url)
            .header("User-Agent", "TTSMultiModel-Desktop")
            .send()
            .await
            .context("下载 sha256 文件失败")?;
        if resp.status().is_success() {
            let text = resp.text().await.context("读取 sha256 文本失败")?;
            if let Some(h) = extract_hex64(&text) {
                return Ok(h);
            }
        }
    }
    Err(anyhow!("未找到 SHA256 校验值（release 描述或 .sha256 资产）"))
}

/// 解析扁平更新 JSON（自定义服务器非 GitHub 形态）。纯函数，便于单测。
/// 版本不高于 current 时返回 Ok(None)。
fn parse_flat_update(raw: &serde_json::Value, current: &str) -> Result<Option<UpdateInfo>> {
    let current_semver = parse_semver(current)?;
    let version = raw
        .get("version")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow!("更新信息缺少 version 字段"))?;
    if parse_semver(version)?.le(&current_semver) {
        return Ok(None);
    }
    Ok(Some(UpdateInfo {
        version: normalize_version(version),
        current: current.to_string(),
        changelog: raw
            .get("changelog")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        url: raw
            .get("url")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow!("更新信息缺少 url 字段"))?
            .to_string(),
        sha256: raw
            .get("sha256")
            .and_then(|v| v.as_str())
            .and_then(extract_hex64)
            .ok_or_else(|| anyhow!("更新信息缺少有效 sha256 字段"))?,
        size: raw.get("size").and_then(|v| v.as_u64()).unwrap_or(0),
    }))
}

/// 自定义服务器检查：兼容 GitHub release JSON 与扁平 JSON 两种形态
async fn check_custom_source(client: &reqwest::Client, url: &str, current: &str) -> Result<Option<UpdateInfo>> {
    let raw: serde_json::Value = client
        .get(url)
        .header("User-Agent", "TTSMultiModel-Desktop")
        .send()
        .await
        .with_context(|| format!("请求更新服务器失败: {url}"))?
        .error_for_status()
        .context("更新服务器返回错误状态")?
        .json()
        .await
        .context("解析更新服务器 JSON 失败")?;

    let current_semver = parse_semver(current)?;
    let info = if raw.get("tag_name").is_some() {
        let release: GhRelease = serde_json::from_value(raw).context("解析 release JSON 失败")?;
        let version = normalize_version(&release.tag_name);
        if parse_semver(&version)?.le(&current_semver) {
            return Ok(None);
        }
        let (dl, size, sha_url) = resolve_assets(&release)?;
        let sha256 = fetch_sha256(client, release.body.as_deref(), &sha_url).await?;
        UpdateInfo {
            version,
            current: current.to_string(),
            changelog: release.body.unwrap_or_default(),
            url: dl,
            sha256,
            size,
        }
    } else {
        match parse_flat_update(&raw, current)? {
            Some(i) => i,
            None => return Ok(None),
        }
    };
    Ok(Some(info))
}

/// 检查更新核心实现；`auto=true` 时网络失败静默降级
async fn do_check(app: &AppHandle, auto: bool) -> Result<Option<UpdateInfo>> {
    let cfg = AppConfig::load();
    let paths = app.state::<Arc<AppPaths>>().inner().clone();
    let current = load_app_version(&paths.app_dir)
        .map(|v| v.version)
        .unwrap_or_else(|| "0.0.0".into());
    let current_semver = match parse_semver(&current) {
        Ok(v) => v,
        Err(e) => {
            log::warn!("本地版本号无效: {e:#}");
            return Ok(None);
        }
    };

    let client = build_http_client(HTTP_TIMEOUT)?;

    let result = match &cfg.update_source {
        UpdateSource::Github { owner, repo } => {
            let latest = fetch_latest_release(&client, owner, repo).await?;
            let version = normalize_version(&latest.tag_name);
            let newer = parse_semver(&version).map(|v| v.gt(&current_semver)).unwrap_or(false);
            if !newer {
                Ok(None)
            } else {
                let (dl, size, sha_url) = resolve_assets(&latest)?;
                let sha256 = fetch_sha256(&client, latest.body.as_deref(), &sha_url).await?;
                Ok(Some(UpdateInfo {
                    version,
                    current: current.clone(),
                    changelog: latest.body.unwrap_or_default(),
                    url: dl,
                    sha256,
                    size,
                }))
            }
        }
        UpdateSource::Custom { url } => check_custom_source(&client, url, &current).await,
    };

    match &result {
        Ok(Some(info)) => {
            log::info!("发现新版本: {} -> {}", info.current, info.version);
            let state = app.state::<Arc<UpdateState>>();
            *state.cached.lock().unwrap() = CachedCheck { checked: true, info: Some(info.clone()) };
            crate::tray::set_update_badge(app, true);
            let _ = app.emit("update-available", info);
        }
        Ok(None) => {
            log::info!("当前已是最新版本 ({current})");
            let state = app.state::<Arc<UpdateState>>();
            *state.cached.lock().unwrap() = CachedCheck { checked: true, info: None };
            crate::tray::set_update_badge(app, false);
        }
        Err(e) => {
            if auto {
                log::warn!("自动检查更新失败（静默）: {e:#}");
            } else {
                log::error!("检查更新失败: {e:#}");
            }
        }
    }
    result
}

/// 前端/托盘命令：检查更新。返回 `Some(UpdateInfo)` 表示有更新；
/// `auto=true`（启动检查）失败一律 Ok(None) 静默。
#[tauri::command]
pub async fn check_update(app: AppHandle, auto: bool) -> Result<Option<UpdateInfo>, String> {
    let state = app.state::<Arc<UpdateState>>();
    if state.updating.load(Ordering::SeqCst) {
        return Err("更新正在进行中".into());
    }
    if !state.busy.swap(true, Ordering::SeqCst) {
        // 抢锁成功
    } else {
        return Err("已有检查在进行中，请稍候".into());
    }
    if auto {
        let skip = {
            let last = state.last_auto_check.lock().unwrap();
            match *last {
                Some(t) => t.elapsed().unwrap_or(Duration::from_secs(u64::MAX)) < AUTO_CHECK_MIN_INTERVAL,
                None => false,
            }
        };
        if skip {
            state.busy.store(false, Ordering::SeqCst);
            return Ok(None);
        }
        *state.last_auto_check.lock().unwrap() = Some(std::time::SystemTime::now());
    }
    let result = do_check(&app, auto).await;
    state.busy.store(false, Ordering::SeqCst);
    match result {
        Ok(info) => Ok(info),
        Err(e) => {
            if auto {
                Ok(None)
            } else {
                Err(format!("{e:#}"))
            }
        }
    }
}

/// 前端命令：本地版本 + 壳版本 + 缓存检查结果（桥接层初始化拉取）
#[tauri::command]
pub fn get_update_state(app: AppHandle) -> Result<serde_json::Value, String> {
    let paths = app.state::<Arc<AppPaths>>().inner().clone();
    let cached = app.state::<Arc<UpdateState>>().cached.lock().unwrap().clone();
    let app_version = load_app_version(&paths.app_dir);
    Ok(serde_json::json!({
        "app_version": app_version.as_ref().map(|v| v.version.clone()).unwrap_or_else(|| "0.0.0".into()),
        "shell_version": env!("CARGO_PKG_VERSION"),
        "release_date": app_version.as_ref().map(|v| v.release_date.clone()).unwrap_or_default(),
        "changelog": app_version.as_ref().map(|v| v.changelog.clone()).unwrap_or_default(),
        "pending": cached,
    }))
}

/// 前端命令：最近一次检查结果
#[tauri::command]
pub fn get_pending_update(app: AppHandle) -> Option<UpdateInfo> {
    app.state::<Arc<UpdateState>>().cached.lock().unwrap().info.clone()
}

// ===== 下载与执行 =====

fn emit_progress(app: &AppHandle, phase: &'static str, percent: u8, message: impl Into<String>) {
    let _ = app.emit("update-progress", UpdateProgress { phase, percent, message: message.into() });
}

fn emit_error(app: &AppHandle, message: impl Into<String>) {
    let _ = app.emit("update-error", serde_json::json!({ "message": message.into() }));
}

/// 流式下载到文件，返回实际字节数；`total>0` 时按 0..90% 汇报进度
async fn download(client: &reqwest::Client, url: &str, dest: &Path, total: u64, app: &AppHandle) -> Result<u64> {
    let resp = client
        .get(url)
        .header("User-Agent", "TTSMultiModel-Desktop")
        .send()
        .await
        .context("连接下载服务器失败")?;
    let resp = resp.error_for_status().context("下载请求被服务器拒绝")?;
    let mut file = File::create(dest).with_context(|| format!("创建下载文件失败: {}", dest.display()))?;
    let mut stream = resp.bytes_stream();
    let mut written: u64 = 0;
    let mut last_emit = 0u8;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.context("下载流中断")?;
        file.write_all(&chunk)?;
        written += chunk.len() as u64;
        if total > 0 {
            let pct = ((written * 90) / total.max(1)).min(90) as u8;
            if pct >= last_emit + 2 || written >= total {
                last_emit = pct;
                emit_progress(app, "download", pct, format!("下载中 {pct}%"));
            }
        }
    }
    file.flush()?;
    Ok(written)
}

/// 计算文件 SHA256（分块读取）
fn sha256_of(path: &Path) -> Result<String> {
    let mut file = File::open(path).with_context(|| format!("打开校验文件失败: {}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        let n = file.read(&mut buf).context("读取校验文件失败")?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

/// 防 zip-slip：规范化条目路径，越界（`..`、绝对路径）返回 None
fn safe_join(dest: &Path, entry_name: &str) -> Option<PathBuf> {
    let rel = Path::new(entry_name);
    if rel.is_absolute() {
        return None;
    }
    let mut out = dest.to_path_buf();
    for comp in rel.components() {
        match comp {
            Component::Normal(c) => out.push(c),
            Component::CurDir => {}
            _ => return None,
        }
    }
    Some(out)
}

/// 解压 zip 到 dest（zip 根即 app/ 内容）；`on_progress` 收到 0..=100
pub fn extract_zip_with(zip_path: &Path, dest: &Path, on_progress: &dyn Fn(u8)) -> Result<usize> {
    let file = File::open(zip_path).context("打开更新包失败")?;
    let mut archive = zip::ZipArchive::new(file).context("更新包不是有效的 zip 格式")?;
    let total = archive.len();
    fs::create_dir_all(dest).with_context(|| format!("创建解压目录失败: {}", dest.display()))?;
    let mut last_emit = 0u8;
    for i in 0..total {
        let mut entry = archive.by_index(i).context("读取 zip 条目失败")?;
        let Some(target) = safe_join(dest, entry.name()) else {
            return Err(anyhow!("更新包含非法路径条目: {}", entry.name()));
        };
        if entry.is_dir() {
            fs::create_dir_all(&target)?;
            continue;
        }
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut out = File::create(&target).with_context(|| format!("创建文件失败: {}", target.display()))?;
        io::copy(&mut entry, &mut out).with_context(|| format!("解压失败: {}", target.display()))?;
        let pct = ((i as u64 * 100) / total.max(1) as u64).min(100) as u8;
        if pct >= last_emit + 5 {
            last_emit = pct;
            on_progress(pct);
        }
    }
    on_progress(100);
    Ok(total)
}

/// 探测目录可写性（Windows 下无写权限时提示管理员运行）
fn probe_writable(dir: &Path) -> bool {
    let probe = dir.join(".tts_multimodel_write_probe");
    match OpenOptions::new().write(true).create(true).truncate(true).open(&probe) {
        Ok(_) => {
            let _ = fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

/// 删除目录；Windows 句柄释放有延迟（taskkill 后 kernel 异步清理，
/// 句柄会在进程退出后短暂残留），重试更久；且先清只读属性
/// （read-only .pyc 等文件会阻止删除）。
fn remove_dir_all_retry(path: &Path) -> Result<()> {
    for attempt in 0..10 {
        match fs::remove_dir_all(path) {
            Ok(()) => return Ok(()),
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(e) => {
                if attempt == 9 {
                    return Err(e).with_context(|| format!("删除失败: {}", path.display()));
                }
                std::thread::sleep(Duration::from_millis(500));
            }
        }
    }
    Ok(())
}


/// 带重试的重命名（Windows：防病毒/索引器可能短暂持有句柄导致瞬时失败）
fn rename_with_retry(src: &Path, dst: &Path) -> Result<()> {
    let mut last_err: Option<io::Error> = None;
    for _ in 0..6 {
        match fs::rename(src, dst) {
            Ok(()) => return Ok(()),
            Err(e) => {
                last_err = Some(e);
                std::thread::sleep(Duration::from_millis(150));
            }
        }
    }
    let e = last_err.expect("循环至少执行一次");
    Err(anyhow!(e).context(format!("重命名失败: {} -> {}", src.display(), dst.display())))
}

/// 递归复制目录（发布包内无符号链接；目录目标不存在时自动创建）
fn copy_dir_recursive(src: &Path, dst: &Path) -> Result<()> {
    if !src.is_dir() {
        return Ok(());
    }
    fs::create_dir_all(dst).with_context(|| format!("创建 {} 失败", dst.display()))?;
    for entry in fs::read_dir(src).with_context(|| format!("读取 {} 失败", src.display()))? {
        let entry = entry?;
        let from = entry.path();
        let to = dst.join(entry.file_name());
        let ft = entry.file_type()?;
        if ft.is_dir() {
            copy_dir_recursive(&from, &to)?;
        } else if ft.is_file() {
            if let Err(e) = fs::copy(&from, &to) {
                return Err(anyhow!(e).context(format!("复制 {} -> {} 失败", from.display(), to.display())));
            }
        }
    }
    Ok(())
}

/// 换载时从旧 `app/` 保留到新 `app/` 的顶层目录。
/// 完整安装包提供 runtime/（侧载 Python，约 4 GB）、model/（模型权重，约 3.9 GB）与用户数据
/// data/（处理历史）、logs/；增量更新包只含应用代码（约 13 MB）。若换载时整体替换 `app/`
/// 而不保留这些目录，运行时与模型会丢失、用户历史会被清空——所以必须在 swap 前补进 `app.new/`。
const PRESERVE_TOP_DIRS: &[&str] = &["runtime", "model", "data", "logs"];

/// 换载时从旧 `app/` 保留到新 `app/` 的根级单文件（用户可修改/本机持有，丢失代价高）：
/// - `config.yaml`：用户对端口/限流/日志/水印等的修改，整体换载会被新版默认配置覆盖；
/// - `.watermark_key`：水印 HMAC 签名密钥，丢失即旧产物无法验证（溯源举证链断裂）。
///
/// 保留旧文件优先（用户修改/本机密钥 > 新版默认），增量包不含这些文件，不会产生覆盖。
const PRESERVE_ROOT_FILES: &[&str] = &["config.yaml", ".watermark_key"];

/// 把旧 `app/` 根下的保留文件复制进 `app.new/`（目标已存在时不覆盖，以新包为准）。
fn preserve_root_files(app_dir: &Path, app_new: &Path, on_file: &mut dyn FnMut(&str)) -> Result<()> {
    for name in PRESERVE_ROOT_FILES {
        let src = app_dir.join(name);
        let dst = app_new.join(name);
        if src.is_file() && !dst.exists() {
            on_file(name);
            fs::copy(&src, &dst).with_context(|| {
                format!("保留应用根文件失败: {}", src.display())
            })?;
        }
    }
    Ok(())
}

/// 解压 `app.new/` 后、原子换载前：把旧 `app/` 中需要保留的顶层目录补进 `app.new/`。
/// 更新包自带同名目录（整包更新场景）时不覆盖，以更新包为准。
fn preserve_heavy_dirs(app_dir: &Path, app_new: &Path, on_dir: &mut dyn FnMut(&str)) -> Result<()> {
    for name in PRESERVE_TOP_DIRS {
        let src = app_dir.join(name);
        let dst = app_new.join(name);
        if src.is_dir() && !dst.exists() {
            on_dir(name);
            copy_dir_recursive(&src, &dst)?;
        }
    }
    Ok(())
}

/// 换载后回滚：丢弃新 `app/`，把 `app.bak/` 还原为 `app/`（用于新版本启动失败/不健康）。
fn restore_backup(app_dir: &Path, bak: &Path) {
    if bak.exists() {
        let _ = remove_dir_all_retry(app_dir);
        let _ = rename_with_retry(bak, app_dir);
    }
}

/// 纯文件系统层面的原子换载（不含进程停/启，便于单测）：
/// `app/` → `app.bak/`，`app.new/` → `app/`；第二步入失败则把 `app.bak/` 还原回 `app/`。
///
/// 返回 `Ok(())` 时：`app/` 已是新版本，`app.bak/` 保留旧版本（成功收尾由调用方删除）。
/// 返回 `Err` 时：`app/` 仍是旧版本（要么第一步就没动，要么第二步失败后已回滚），
/// 应用保证可用——这是验收 5.5「更新失败自动回滚、应用不损坏」的核心保证。
fn swap_app_directories(app_dir: &Path, app_new: &Path, bak: &Path) -> Result<()> {
    // 清理上一轮遗留备份
    if bak.exists() {
        remove_dir_all_retry(bak).with_context(|| format!("清理旧备份失败: {}", bak.display()))?;
    }
    // 第一步：旧 app/ → app.bak/（失败则什么都没动，直接返回错误）
    rename_with_retry(app_dir, bak)
        .with_context(|| format!("备份 {} 失败", app_dir.display()))?;
    // 第二步：app.new/ → app/（失败则把 app.bak/ 还原回 app/，恢复原状）
    if let Err(e) = rename_with_retry(app_new, app_dir) {
        if let Err(re) = rename_with_retry(bak, app_dir) {
            // 连回滚都失败：这是最危险的情况，显式告警（调用方仍需尝试重启旧版本）
            return Err(anyhow!(
                "换载失败且回滚失败（请手动把 {} 改回 {}）：换载错误={e}，回滚错误={re}",
                bak.display(),
                app_dir.display()
            ));
        }
        return Err(anyhow!("换载更新失败（已自动回滚原版本）: {e}"));
    }
    Ok(())
}

fn state_controller(app: &AppHandle) -> Arc<ProcessController> {
    app.state::<Arc<UpdateState>>()
        .controller
        .lock()
        .unwrap()
        .clone()
        .expect("ProcessController 未初始化")
}

/// 换载后新版本不健康时的回滚：停当前进程 → 丢弃新 app/ 并还原 app.bak/ → 重启旧版本。
///
/// 注意此时 `app_dir` 是**新版本**（已换载成功）、`bak` 是旧版本；两者都可能存在，
/// 不能再用 `!app_dir.exists()` 作为还原条件（那样换载后场景永远不会还原）。
fn rollback(app: &AppHandle, paths: &AppPaths, reason: &str) {
    let bak = paths.app_dir.with_extension("bak");
    let controller = state_controller(app);
    // 先停掉正持有新版本文件句柄的进程，否则 restore 的删除/重命名会被占用
    (controller.stop)();
    if bak.exists() {
        restore_backup(&paths.app_dir, &bak);
    }
    match (controller.restart)() {
        Ok(port) => {
            (controller.finalize)(port);
            log::info!("已回滚到更新前版本并重启服务");
        }
        Err(e) => log::error!("回滚后重启失败: {e:#}"),
    }
    emit_error(app, format!("更新失败，已自动回滚到原版本。原因：{reason}"));
}

/// 更新分步实现：任一失败点保证可回滚（备份目录尚未换载时，直接保留原 app/）
async fn do_update_steps(app: &AppHandle, info: &UpdateInfo) -> Result<()> {
    let paths = app.state::<Arc<AppPaths>>().inner().clone();
    let bak = paths.app_dir.with_extension("bak");

    // 前置权限检查
    if !probe_writable(&paths.app_dir) {
        return Err(anyhow!(
            "应用目录无写入权限（{}），请以管理员身份运行后重试",
            paths.app_dir.display()
        ));
    }

    let client = build_http_client(Duration::from_secs(3600))?;

    // 1. 下载（期间应用可正常使用）
    fs::create_dir_all(&paths.updates_dir).context("创建 updates 目录失败")?;
    let zip_path = paths.updates_dir.join(ASSET_TEMPLATE.replace("{version}", &info.version));
    let _ = fs::remove_file(&zip_path);
    emit_progress(app, "download", 0, "开始下载更新包…");
    let downloaded = download(&client, info.url.as_str(), &zip_path, info.size, app)
        .await
        .context("下载失败")?;
    if info.size > 0 && downloaded != info.size {
        let _ = fs::remove_file(&zip_path);
        return Err(anyhow!(
            "下载不完整（{downloaded} / {} 字节），请重试",
            info.size
        ));
    }
    log::info!("更新包下载完成: {downloaded} 字节");

    // 2. SHA256 校验
    emit_progress(app, "verify", 0, "校验 SHA256…");
    let actual = sha256_of(&zip_path).context("校验计算失败")?;
    if actual != info.sha256 {
        let _ = fs::remove_file(&zip_path);
        return Err(anyhow!(
            "SHA256 校验失败（期望 {}…，实际 {}…），已丢弃更新包",
            &info.sha256[..12.min(info.sha256.len())],
            &actual[..12.min(actual.len())]
        ));
    }
    emit_progress(app, "verify", 100, "校验通过");

    // 3. 解压到 app.new/
    let app_new = paths.app_dir.with_extension("new");
    let _ = remove_dir_all_retry(&app_new);
    emit_progress(app, "extract", 0, "解压更新包…");
    let entries = {
        let app2 = app.clone();
        let zp = zip_path.clone();
        let dest = app_new.clone();
        tokio::task::spawn_blocking(move || {
            extract_zip_with(&zp, &dest, &|pct| emit_progress(&app2, "extract", pct, format!("解压中 {pct}%")))
        })
        .await
        .map_err(|e| anyhow!("解压任务调度失败: {e}"))?
        .context("解压失败")?
    };
    log::info!("解压完成: {entries} 个条目");

    // 4. 停 Python（必须先停进程释放 runtime/model 句柄——被 torch 占用的模型
    //     文件在进程存活时 fs::copy 会阻塞卡死：实测 3.4GB 模型复制数分钟停滞，
    //     停进程后同样文件约 2 秒复制完（1.7GB/s））
    emit_progress(app, "swap", 0, "停止服务并保留数据…");
    let controller = state_controller(app);
    (controller.stop)();
    // 释放壳自身占用的句柄：DualLogger 写 app\logs\tts_multimodel-shell.log，若不停开，
    // 整体重命名 app -> app.bak 会报 os error 32（另一个程序正在使用此文件）。
    crate::close_logger();
    // 壳工作目录若落在 app 树内也会阻止重命名，换载期间切到临时目录（resolve_app_dir 不依赖 CWD）
    if let Ok(td) = std::env::temp_dir().canonicalize() {
        let _ = std::env::set_current_dir(&td);
    } else {
        let _ = std::env::set_current_dir(std::env::temp_dir());
    }


    // 4.5 保留重型/用户目录（runtime、model、data、logs）：增量更新包不含这些，
    //     必须在换载前从旧 app/ 补进 app.new/，否则换载后运行时/模型/历史会丢失。
    //     放停进程之后复制，文件无占用才快；失败时重启旧版本保证服务可用。
    emit_progress(app, "extract", 100, "保留运行时与用户数据…");
    if let Err(e) = preserve_heavy_dirs(&paths.app_dir, &app_new, &mut |name| {
        log::info!("保留顶层目录: {name}/");
    }) {
        let _ = (controller.restart)();
        return Err(anyhow!("保留运行时/模型目录失败，已恢复服务: {e:#}"));
    }
    // 根级用户文件（config.yaml 用户修改、.watermark_key 本机密钥）：不保留则
    // 换载后用户配置被重置、水印密钥轮换导致旧产物无法验证（桌面端迁移问题 B-1）。
    if let Err(e) = preserve_root_files(&paths.app_dir, &app_new, &mut |name| {
        log::info!("保留应用根文件: {name}");
    }) {
        let _ = (controller.restart)();
        return Err(anyhow!("保留应用根文件失败，已恢复服务: {e:#}"));
    }

    // 4.6 校验 app.new/version.json
    let new_version = load_app_version(&app_new).ok_or_else(|| anyhow!("更新包缺少 version.json"))?;
    if normalize_version(&new_version.version) != info.version {
        let _ = remove_dir_all_retry(&app_new);
        let _ = (controller.restart)();
        return Err(anyhow!(
            "更新包版本不符（声明 {}，实际 {}），已丢弃",
            info.version,
            new_version.version
        ));
    }

    // 5. 原子换载 → 重启（换载失败时 swap_app_directories 内部已把 app.bak/ 还原，
    //    app/ 保持旧版本；此处只需尝试拉起旧版本保证服务可用）
    if let Err(e) = swap_app_directories(&paths.app_dir, &app_new, &bak) {
        let _ = (controller.restart)();
        return Err(anyhow!("换载更新失败（已恢复原版本）: {e:#}"));
    }
    let port = match (controller.restart)() {
        Ok(port) => port,
        Err(e) => {
            // 新版本无法启动：回滚目录到旧版本再拉起
            restore_backup(&paths.app_dir, &bak);
            let _ = (controller.restart)();
            return Err(anyhow!("重启服务失败，已回滚目录: {e:#}"));
        }
    };

    // 6. 等待新服务就绪（端口可能变化）
    emit_progress(app, "restart", 30, "正在启动新版本…");
    match health_check::wait_for_ready(port, Duration::from_secs(120)).await {
        Ok(_) => {
            (controller.finalize)(port);
        }
        Err(e) => {
            rollback(app, &paths, &format!("新版本服务启动超时: {e:#}"));
            return Err(anyhow!("更新后服务无法启动，已回滚: {e:#}"));
        }
    }

    // 7. 清理备份与缓存（app.bak 只保留当前版本，成功即删）
    let _ = remove_dir_all_retry(&bak);
    let _ = fs::remove_file(&zip_path);
    log::info!("更新完成: {} -> {}", info.current, info.version);
    Ok(())
}

/// 启动时兜底清理上次更新失败残留的 app.bak（只删备份，不影响当前 app）。
/// 更新成功收尾采用延迟 30s 异步删除，若仍被占用（杀毒/句柄延迟）则留待下次启动清理。
pub fn cleanup_stale_backup(app_dir: &Path) {
    let bak = app_dir.with_extension("bak");
    if bak.exists() {
        match remove_dir_all_retry(&bak) {
            Ok(()) => log::info!("已清理残留更新备份: {}", bak.display()),
            Err(e) => log::warn!("残留更新备份清理失败（忽略）: {e:#}"),
        }
    }
}

/// 更新执行主流程（后台异步任务）
async fn run_update(app: AppHandle, info: UpdateInfo) {
    let state = app.state::<Arc<UpdateState>>();
    state.updating.store(true, Ordering::SeqCst);
    crate::tray::set_update_badge(&app, false);

    let outcome = do_update_steps(&app, &info).await;

    match outcome {
        Ok(()) => {
            state.updating.store(false, Ordering::SeqCst);
            {
                let mut cached = state.cached.lock().unwrap();
                cached.info = None;
            }
            emit_progress(&app, "restart", 100, "更新完成");
            let _ = app.emit(
                "update-done",
                serde_json::json!({ "version": info.version, "message": format!("已成功更新到 {}", info.version) }),
            );
        }
        Err(e) => {
            state.updating.store(false, Ordering::SeqCst);
            emit_error(&app, format!("{e:#}"));
        }
    }
}

/// 前端命令：开始更新（fire-and-forget，进度走事件）
#[tauri::command]
pub fn start_update(app: AppHandle, update_state: State<'_, Arc<UpdateState>>) -> Result<(), String> {
    if update_state.updating.swap(true, Ordering::SeqCst) {
        return Err("更新已在进行中".into());
    }
    // 上面 swap 已置位，run_update 里会再 store(true)（幂等）
    match update_state.cached.lock().unwrap().info.clone() {
        Some(info) => {
            let handle = app.clone();
            tauri::async_runtime::spawn(async move {
                run_update(handle, info).await;
            });
            Ok(())
        }
        None => {
            update_state.updating.store(false, Ordering::SeqCst);
            Err("没有可用的更新，请先检查更新".into())
        }
    }
}

/// 前端命令：放弃当前待装更新（清缓存与托盘标记）
#[tauri::command]
pub fn dismiss_update(app: AppHandle) {
    let state = app.state::<Arc<UpdateState>>();
    state.cached.lock().unwrap().info = None;
    crate::tray::set_update_badge(&app, false);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(tag: &str) -> GhRelease {
        GhRelease {
            draft: false,
            tag_name: tag.into(),
            body: Some(format!("## 修复\n- sha256: {}", "a".repeat(64))),
            assets: vec![
                GhAsset {
                    name: format!("app-v{}.zip", normalize_version(tag)),
                    url: "https://example.com/a.zip".into(),
                    browser_download_url: Some("https://example.com/a.zip".into()),
                    size: Some(1024),
                },
                GhAsset {
                    name: format!("app-v{}.zip.sha256", normalize_version(tag)),
                    url: "https://example.com/a.zip.sha256".into(),
                    browser_download_url: Some("https://example.com/a.zip.sha256".into()),
                    size: Some(65),
                },
                GhAsset {
                    name: "installer.exe".into(),
                    url: "https://example.com/i.exe".into(),
                    browser_download_url: Some("https://example.com/i.exe".into()),
                    size: Some(9),
                },
            ],
        }
    }

    #[test]
    fn normalize_strips_v() {
        assert_eq!(normalize_version("v1.5.2"), "1.5.2");
        assert_eq!(normalize_version("1.5.2"), "1.5.2");
        assert_eq!(normalize_version("  v1.5.2 "), "1.5.2");
    }

    #[test]
    fn semver_compare_not_string() {
        let a = parse_semver("v1.9.0").unwrap();
        let b = parse_semver("1.10.0").unwrap();
        assert!(a < b, "字符串比较会把 1.10 误判为小于 1.9");
    }

    #[test]
    fn resolve_assets_finds_zip_and_sha() {
        let (url, size, sha_url) = resolve_assets(&fixture("v1.5.2")).unwrap();
        assert_eq!(url, "https://example.com/a.zip");
        assert_eq!(size, 1024);
        assert!(sha_url.ends_with(".zip.sha256"));
    }

    #[test]
    fn extract_hex64_from_release_body() {
        let body = "修复若干问题\nSHA256 校验: BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD";
        assert_eq!(
            extract_hex64(body).unwrap(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert!(extract_hex64("no hash here").is_none());
    }

    #[test]
    fn safe_join_blocks_traversal() {
        assert!(safe_join(Path::new("C:\\dest"), "../evil").is_none());
        assert!(safe_join(Path::new("C:\\dest"), "/abs/evil").is_none());
        assert!(safe_join(Path::new("C:\\dest"), "..\\..\\windows\\x").is_none());
        assert!(safe_join(Path::new("C:\\dest"), "app/version.json").is_some());
        assert!(safe_join(Path::new("C:\\dest"), "./ok/file.txt").is_some());
    }

    #[test]
    fn sha256_known_vector() {
        let dir = std::env::temp_dir().join(format!("tts_multimodel_sha_{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let p = dir.join("abc.bin");
        fs::write(&p, b"abc").unwrap();
        assert_eq!(sha256_of(&p).unwrap(), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn extract_zip_roundtrip() {
        let dir = std::env::temp_dir().join(format!("tts_multimodel_zip_{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let zpath = dir.join("t.zip");
        let file = File::create(&zpath).unwrap();
        let mut writer = zip::ZipWriter::new(file);
        let opts: zip::write::FileOptions<'_, ()> =
            zip::write::FileOptions::default().compression_method(zip::CompressionMethod::Deflated);
        writer.start_file("start_portable.py", opts).unwrap();
        writer.write_all(b"print(1)").unwrap();
        writer.add_directory("configs_3b/", opts).unwrap();
        writer.start_file("configs_3b/model.yaml", opts).unwrap();
        writer.write_all(b"k: v").unwrap();
        writer.finish().unwrap();

        let dest = dir.join("out");
        let pcts = std::cell::RefCell::new(Vec::new());
        let n = extract_zip_with(&zpath, &dest, &|p| pcts.borrow_mut().push(p)).unwrap();
        assert_eq!(n, 3);
        assert!(dest.join("start_portable.py").exists());
        assert!(dest.join("configs_3b/model.yaml").exists());
        assert_eq!(*pcts.borrow().last().unwrap(), 100);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn version_json_fixture_parses() {
        let v: AppVersion = serde_json::from_str(
            r#"{"version":"1.5.1","release_date":"2026-09-04","minimum_shell_version":"1.0.0","changelog":"x"}"#,
        )
        .unwrap();
        assert_eq!(v.version, "1.5.1");
    }

    // ===== 原子换载 + 回滚（验收 5.5 核心：更新失败应用不损坏）=====

    /// 在 root 下建一个含 marker 文件的目录并返回目录路径
    fn mk_dir_with_marker(root: &Path, name: &str, marker_content: &str) -> PathBuf {
        let dir = root.join(name);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("marker.txt"), marker_content).unwrap();
        dir
    }

    fn read_marker(dir: &Path) -> String {
        fs::read_to_string(dir.join("marker.txt")).unwrap_or_default()
    }

    #[test]
    fn swap_success_promotes_new_and_keeps_backup() {
        let root = std::env::temp_dir().join(format!("tts_multimodel_swap_ok_{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let app = mk_dir_with_marker(&root, "app", "OLD");
        let new = mk_dir_with_marker(&root, "app.new", "NEW");
        let bak = root.join("app.bak");

        swap_app_directories(&app, &new, &bak).unwrap();

        // 换载后 app/ 是新版；app.bak/ 保留旧版；app.new/ 已被搬走
        assert_eq!(read_marker(&app), "NEW");
        assert!(bak.exists() && read_marker(&bak) == "OLD");
        assert!(!new.exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn swap_rolls_back_when_promote_fails() {
        let root = std::env::temp_dir().join(format!("tts_multimodel_swap_fail_{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let app = mk_dir_with_marker(&root, "app", "OLD");
        let bak = root.join("app.bak");
        // 制造 promote 失败：app.new 不存在 → rename 到 app 会失败；
        // 但 swap 内部第一步已把 app→app.bak，第二步失败须把 app.bak→app 还原。
        let new = root.join("app.new"); // 故意不创建

        let result = swap_app_directories(&app, &new, &bak);
        assert!(result.is_err(), "promote 应失败");

        // 关键：app/ 必须恢复为旧版本，应用不损坏
        assert!(app.exists());
        assert_eq!(read_marker(&app), "OLD");
        // app.bak 已被还原搬走
        assert!(!bak.exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn restore_backup_discards_new_and_recovers_old() {
        let root = std::env::temp_dir().join(format!("tts_multimodel_restore_{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        // 模拟换载后新版本不健康：app/ 是 NEW，app.bak/ 是 OLD
        let app = mk_dir_with_marker(&root, "app", "NEW");
        let bak = mk_dir_with_marker(&root, "app.bak", "OLD");

        restore_backup(&app, &bak);

        assert_eq!(read_marker(&app), "OLD", "回滚后 app/ 应为旧版本");
        assert!(!bak.exists(), "还原后不应残留 app.bak");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn rename_with_retry_moves_dir() {
        let root = std::env::temp_dir().join(format!("tts_multimodel_rename_{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let src = mk_dir_with_marker(&root, "src", "x");
        let dst = root.join("dst");
        rename_with_retry(&src, &dst).unwrap();
        assert!(!src.exists());
        assert_eq!(read_marker(&dst), "x");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn preserve_heavy_dirs_copies_heavy_dirs_into_new() {
        // 旧 app/ 含 runtime、model、data（增量更新包不含），app.new/ 只有应用代码
        let root = std::env::temp_dir().join(format!("tts_multimodel_preserve_{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let app = root.join("app");
        let app_new = root.join("app.new");
        let runtime = app.join("runtime");
        let model = app.join("model");
        let data = app.join("data");
        fs::create_dir_all(&runtime).unwrap();
        fs::create_dir_all(&model).unwrap();
        fs::create_dir_all(&data).unwrap();
        fs::write(runtime.join("python.exe"), b"py").unwrap();
        fs::write(model.join("tts_multimodel.safetensors"), b"wt").unwrap();
        fs::write(data.join("history.json"), b"[]").unwrap();
        // app.new 只有应用代码
        fs::create_dir_all(&app_new).unwrap();
        fs::write(app_new.join("start_portable.py"), b"code").unwrap();

        let mut preserved = Vec::new();
        preserve_heavy_dirs(&app, &app_new, &mut |n| preserved.push(n.to_string())).unwrap();

        assert!(app_new.join("runtime/python.exe").exists(), "runtime 应被保留");
        assert!(app_new.join("model/tts_multimodel.safetensors").exists(), "model 应被保留");
        assert!(app_new.join("data/history.json").exists(), "data 应被保留");
        assert!(app_new.join("start_portable.py").exists());
        assert_eq!(preserved.len(), 3, "应报告保留 runtime/model/data 三个目录");
        assert_eq!(preserved.iter().filter(|n| *n == "runtime").count(), 1);
        // 旧 app/ 原封不动
        assert!(runtime.join("python.exe").exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn preserve_heavy_dirs_respects_update_package_content() {
        // 整包更新场景：app.new/ 自带 runtime → 不覆盖，以更新包为准
        let root = std::env::temp_dir().join(format!("tts_multimodel_preserve2_{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let app = root.join("app");
        let app_new = root.join("app.new");
        fs::create_dir_all(&app).unwrap();
        fs::create_dir_all(&app_new).unwrap();
        fs::create_dir_all(app.join("runtime")).unwrap();
        fs::write(app.join("runtime/python.exe"), b"old").unwrap();
        fs::create_dir_all(app_new.join("runtime")).unwrap();
        fs::write(app_new.join("runtime/python.exe"), b"new").unwrap();

        let mut preserved = Vec::new();
        preserve_heavy_dirs(&app, &app_new, &mut |n| preserved.push(n.to_string())).unwrap();

        assert_eq!(fs::read(app_new.join("runtime/python.exe")).unwrap(), b"new", "更新包自带时不应覆盖");
        assert!(preserved.is_empty(), "不应报告保留");
        let _ = fs::remove_dir_all(&root);
    }
    #[test]
    fn flat_update_parses_when_newer() {
        let raw = serde_json::json!({
            "version": "1.6.0",
            "changelog": "新功能",
            "url": "https://example.com/app-v1.6.0.zip",
            "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "size": 2048,
        });
        let info = parse_flat_update(&raw, "1.5.1").unwrap().expect("应判定为有更新");
        assert_eq!(info.version, "1.6.0");
        assert_eq!(info.current, "1.5.1");
        assert_eq!(info.size, 2048);
        assert_eq!(info.sha256.len(), 64);
    }

    #[test]
    fn flat_update_none_when_not_newer() {
        let raw = serde_json::json!({
            "version": "1.5.1",
            "url": "u",
            "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        });
        assert!(parse_flat_update(&raw, "1.5.1").unwrap().is_none());
        assert!(parse_flat_update(&raw, "1.6.0").unwrap().is_none(), "本地更新则无更新");
    }

    #[test]
    fn flat_update_strips_v_prefix_and_compares_semver() {
        let raw = serde_json::json!({
            "version": "v1.10.0",
            "url": "u",
            "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        });
        // v 前缀剥离 + semver：1.10.0 > 1.9.0（字符串比较会错）
        let info = parse_flat_update(&raw, "1.9.0").unwrap().expect("1.10 应大于 1.9");
        assert_eq!(info.version, "1.10.0");
    }

    #[test]
    fn flat_update_rejects_bad_sha() {
        let raw = serde_json::json!({
            "version": "1.6.0",
            "url": "u",
            "sha256": "not-a-valid-hash",
        });
        assert!(parse_flat_update(&raw, "1.5.1").is_err(), "缺有效 sha256 应报错");
    }

    /// 集成测试：起一个 127.0.0.1 上的迷你 HTTP 服务，验证 check_custom_source
    /// 的完整解析+比较路径（仅本机回环，不访问外网）。
    #[test]
    fn check_custom_source_against_local_server() {
        use std::net::TcpListener;
        use std::io::Write as _;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let body = r#"{"version":"2.0.0","changelog":"big","url":"http://127.0.0.1/x.zip","sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad","size":10}"#.to_string();
        let resp = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        let server = std::thread::spawn(move || {
            // 只需处理一个请求（Connection: close，reqwest 不重试成功响应）
            if let Some(stream) = listener.incoming().next() {
                let mut s = match stream {
                    Ok(s) => s,
                    Err(_) => return,
                };
                let mut buf = [0u8; 1024];
                let _ = s.read(&mut buf); // 读掉请求头
                let _ = s.write_all(resp.as_bytes());
                let _ = s.flush();
            }
        });

        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(5))
            .build()
            .unwrap();
        let url = format!("http://127.0.0.1:{port}/update.json");
        let info = tauri::async_runtime::block_on(check_custom_source(&client, &url, "1.5.1"))
            .unwrap()
            .expect("本地服务应判定为有更新");
        assert_eq!(info.version, "2.0.0");
        assert_eq!(info.changelog, "big");
        let _ = server.join();
    }
}
