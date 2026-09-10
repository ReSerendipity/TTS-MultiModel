use std::net::TcpListener;
use anyhow::{Result, anyhow};

/// 查找一个空闲的 TCP 端口
pub fn find_free_port() -> Result<u16> {
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
