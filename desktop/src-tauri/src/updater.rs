//! 桌面更新（单一路线）：官网下载与「检查更新」拿到的是同一个完整安装包。
//!
//! 与旧「代码增量 + 原子换载」路线的区别：
//! - 资产：`TTSMultiModel-Setup-v{version}.exe`（不是 app-v*.zip）
//! - 安装：下载 + SHA256 校验后静默运行 NSIS `/S`；安装器自带 KillRunning
//!   宏会杀掉运行中的壳与 Python 子进程，再覆盖安装（runtime/model/data 保留）
//! - 因此壳无法在安装后发事件——用户需手动重开应用；旧式的自动重启/回滚不适用
//!
//! 事件契约（前端桥接层 `desktop-bridge.js` 消费）：
//! - `update-available` [`UpdateInfo`] —— 发现新版本
//! - `update-progress` [`UpdateProgress`] —— 下载/校验/启动安装器各阶段进度
//! - `update-installing` `{ version, message }` —— 安装器即将接管，应用会被关闭
//! - `update-error` `{ message }` —— 下载/校验/启动失败

use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter, Manager, State};

use crate::config::{AppConfig, UpdateSource};
use crate::AppPaths;

/// GitHub API 速率限制：未认证 60 次/小时，启动自动检查做节流
const AUTO_CHECK_MIN_INTERVAL: Duration = Duration::from_secs(10 * 60);
/// 检查请求超时
const HTTP_TIMEOUT: Duration = Duration::from_secs(10);
/// 更新包资产命名模板（单一路线：官网与检查更新下同一个完整安装包）。
/// NSIS 安装器自带 KillRunning 宏，能杀掉运行中的壳再覆盖安装，无需原子换载。
const ASSET_TEMPLATE: &str = "TTSMultiModel-Setup-v{version}.exe";

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
// 完整包路线下不再由更新器调用；main.rs 仍构造并 manage，保留供后续 shell 重启能力复用。
#[allow(dead_code)]
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
    let exe_name = ASSET_TEMPLATE.replace("{version}", &version);
    let sha_name = format!("{exe_name}.sha256");

    // 只认精确匹配的安装包资产；不再回退到任意 app-v*.zip（单一路线）。
    let exe = release
        .assets
        .iter()
        .find(|a| a.name == exe_name)
        .ok_or_else(|| anyhow!("更新包不存在: 未找到 {exe_name}"))?;
    let size = exe.size.unwrap_or(0);
    let url = exe
        .browser_download_url
        .clone()
        .unwrap_or_else(|| exe.url.clone());
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


/// 执行更新：下载完整安装包 → SHA256 校验 → 静默启动 NSIS 安装器。
///
/// NSIS 自带 KillRunning 宏会杀掉运行中的壳与 Python 子进程再覆盖安装，
/// 因此本函数在安装器启动后可能不再返回；所有前端事件必须在 spawn 前发完。
async fn do_update_steps(app: &AppHandle, info: &UpdateInfo) -> Result<()> {
    let paths = app.state::<Arc<AppPaths>>().inner().clone();
    let client = build_http_client(Duration::from_secs(3600))?;

    fs::create_dir_all(&paths.updates_dir).context("创建 updates 目录失败")?;
    let exe_name = ASSET_TEMPLATE.replace("{version}", &info.version);
    let exe_path = paths.updates_dir.join(&exe_name);
    let _ = fs::remove_file(&exe_path);
    emit_progress(app, "download", 0, "开始下载安装包…");
    let downloaded = download(&client, info.url.as_str(), &exe_path, info.size, app)
        .await
        .context("下载失败")?;
    if info.size > 0 && downloaded != info.size {
        let _ = fs::remove_file(&exe_path);
        return Err(anyhow!(
            "下载不完整（{downloaded} / {} 字节），请重试",
            info.size
        ));
    }
    log::info!("安装包下载完成: {downloaded} 字节");

    emit_progress(app, "verify", 0, "校验 SHA256…");
    let actual = sha256_of(&exe_path).context("校验计算失败")?;
    if actual != info.sha256 {
        let _ = fs::remove_file(&exe_path);
        return Err(anyhow!(
            "SHA256 校验失败（期望 {}…，实际 {}…），已丢弃安装包",
            &info.sha256[..12.min(info.sha256.len())],
            &actual[..12.min(actual.len())]
        ));
    }
    emit_progress(app, "verify", 100, "校验通过");

    emit_progress(app, "install", 0, "正在启动安装程序，应用即将关闭…");
    let _ = app.emit(
        "update-installing",
        serde_json::json!({
            "version": info.version,
            "message": "安装程序即将接管，应用会关闭；安装完成后请重新启动 TTS MultiModel。"
        }),
    );

    launch_installer(&exe_path)?;
    log::info!("安装器已启动: {} -> {}", info.current, info.version);
    Ok(())
}

/// 以独立进程组启动 NSIS 静默安装器：壳被安装器杀掉后它必须继续存活。
#[cfg(windows)]
fn launch_installer(exe: &Path) -> Result<()> {
    use std::os::windows::process::CommandExt;
    const DETACHED_PROCESS: u32 = 0x0000_0008;
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
    let mut cmd = std::process::Command::new(exe);
    cmd.arg("/S")
        .creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        .spawn()
        .with_context(|| format!("启动安装器失败: {}", exe.display()))?;
    Ok(())
}

#[cfg(not(windows))]
fn launch_installer(exe: &Path) -> Result<()> {
    Err(anyhow!("完整安装包更新仅支持 Windows: {}", exe.display()))
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
                    name: format!("TTSMultiModel-Setup-v{}.exe", normalize_version(tag)),
                    url: "https://example.com/setup.exe".into(),
                    browser_download_url: Some("https://example.com/setup.exe".into()),
                    size: Some(1024),
                },
                GhAsset {
                    name: format!("TTSMultiModel-Setup-v{}.exe.sha256", normalize_version(tag)),
                    url: "https://example.com/setup.exe.sha256".into(),
                    browser_download_url: Some("https://example.com/setup.exe.sha256".into()),
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
    fn resolve_assets_rejects_legacy_increment_zip() {
        // 单一路线：只有完整安装包算更新资产，旧的 app-v*.zip 不再被接受
        let mut release = fixture("v1.5.2");
        release.assets.retain(|a| !a.name.starts_with("TTSMultiModel-Setup"));
        assert!(resolve_assets(&release).is_err(), "只剩增量 zip 时应报更新包不存在");
    }

    #[test]
    fn resolve_assets_finds_installer_and_sha() {
        let (url, size, sha_url) = resolve_assets(&fixture("v1.5.2")).unwrap();
        assert_eq!(url, "https://example.com/setup.exe");
        assert_eq!(size, 1024);
        assert!(sha_url.ends_with(".exe.sha256"));
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
    fn sha256_known_vector() {
        let dir = std::env::temp_dir().join(format!("tts_multimodel_sha_{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let p = dir.join("abc.bin");
        fs::write(&p, b"abc").unwrap();
        assert_eq!(sha256_of(&p).unwrap(), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
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
