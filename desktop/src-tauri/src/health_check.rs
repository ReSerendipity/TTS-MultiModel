use std::time::Duration;
use anyhow::{Result, anyhow};
use serde::Deserialize;

/// 健康检查响应中的完整性自检状态（桌面端 B-2：据此展示篡改告警）。
/// checked / manifest_signed 为 /api/system/health 响应契约字段，
/// 当前仅 ailed/ailed_files 被消费，余保留供托盘/警告增强使用。
#[derive(Debug, Clone, Default, Deserialize)]
#[allow(dead_code)]
pub struct IntegrityStatus {
    #[serde(default)]
    pub checked: bool,
    #[serde(default)]
    pub failed: i64,
    #[serde(default)]
    pub failed_files: Vec<String>,
    #[serde(default)]
    pub manifest_signed: bool,
}

/// 检查指定端口的 HTTP 服务是否就绪；就绪时返回完整性自检状态（可能为 None）。
pub async fn check_health(port: u16) -> Result<Option<IntegrityStatus>> {
    let url = format!("http://127.0.0.1:{}/api/system/health", port);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()?;
    let resp = client.get(&url).send().await?;
    if !resp.status().is_success() {
        return Err(anyhow!("健康检查失败: HTTP {}", resp.status()));
    }
    let body: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| anyhow!("解析健康检查响应失败: {e}"))?;
    let integrity = body
        .get("security")
        .and_then(|s| s.get("integrity"))
        .and_then(|v| serde_json::from_value::<IntegrityStatus>(v.clone()).ok());
    Ok(integrity)
}

/// 等待服务就绪，超时返回错误；就绪时返回完整性自检状态（旧版后端可能为 None）。
pub async fn wait_for_ready(port: u16, timeout: Duration) -> Result<Option<IntegrityStatus>> {
    let start = std::time::Instant::now();
    let mut last_error = String::new();

    while start.elapsed() < timeout {
        match check_health(port).await {
            Ok(integrity) => return Ok(integrity),
            Err(e) => {
                last_error = e.to_string();
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
        }
    }

    Err(anyhow!("服务启动超时（{}秒）: {}", timeout.as_secs(), last_error))
}
