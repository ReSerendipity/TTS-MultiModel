use std::net::TcpListener;
use anyhow::{Result, anyhow};

/// 应用标准端口。
/// 固定端口让 localStorage 的 origin（scheme+host+port）跨启动稳定：
/// 动态端口每次重启都会变，引导完成标记 / 表单草稿 / 偏好设置全部丢失
/// （2026-11-22 演示实测：引导卡反复弹出、表单跨页丢失的根因）。
pub const STANDARD_PORT: u16 = 7869;

/// 查找一个空闲的 TCP 端口
/// 优先使用标准端口 7869（被占用——如开发实例并行——再回退动态端口）。
pub fn find_free_port() -> Result<u16> {
    if let Ok(listener) = TcpListener::bind(("127.0.0.1", STANDARD_PORT)) {
        let port = listener.local_addr()?.port();
        drop(listener);
        std::thread::sleep(std::time::Duration::from_millis(50));
        return Ok(port);
    }
    for _ in 0..5 {
        match TcpListener::bind("127.0.0.1:0") {
            Ok(listener) => {
                let port = listener.local_addr()?.port();
                drop(listener);
                // 短暂等待确保端口真正释放
                std::thread::sleep(std::time::Duration::from_millis(50));
                return Ok(port);
            }
            Err(_) => continue,
        }
    }
    Err(anyhow!("无法找到空闲端口"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_find_free_port() {
        let port = find_free_port().unwrap();
        assert!(port > 0);
        assert!(port < 65535);
    }
}
