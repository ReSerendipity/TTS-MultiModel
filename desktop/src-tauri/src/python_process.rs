use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use anyhow::{Result, anyhow};
use serde::Serialize;
use tauri::{AppHandle, Emitter};
#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::port_manager::find_free_port;

/// 从 HTTP 响应头里取 CSRF token：优先响应头 `X-CSRF-Token`（家族部分仓由中间件直接下发），
/// 回退解析 `Set-Cookie: csrf_token=...`（部分仓的 CSRF 中间件只种 cookie，不发头）。
fn extract_csrf_token(headers: &str) -> Option<String> {
    let mut cookie_token: Option<String> = None;
    for line in headers.lines() {
        let lower = line.to_ascii_lowercase();
        if let Some(rest) = lower.strip_prefix("x-csrf-token:") {
            let idx = line.len() - rest.len();
            let value = line[idx..].trim();
            if !value.is_empty() {
                return Some(value.to_string());
            }
        }
        if lower.starts_with("set-cookie:") && lower.contains("csrf_token=") {
            // 形如：Set-Cookie: csrf_token=<value>; Path=/; SameSite=Strict
            let after = &line[line.find("csrf_token=")? + "csrf_token=".len()..];
            let value = after.split(';').next().unwrap_or("").trim();
            if !value.is_empty() {
                cookie_token = Some(value.to_string());
            }
        }
    }
    cookie_token
}

/// 请求 Python 服务本机优雅关闭（桌面端 B-5）。
///
/// 自适应 CSRF：先 GET `/api/system/health` 取 token，再以 double-submit（cookie + 头同值）
/// POST `/api/system/shutdown`；取不到 token 时按「CSRF 关闭」直接 POST。
///
/// 为什么要自适应：家族四仓实现分叉——Image 强制带 token（CSRF 默认开）、SeedVR2/TTS/VOSR2
/// 直接裸 POST（CSRF 默认关）。任一固定写法在另一种配置下都会静默失效（403 → 退化成
/// taskkill，优雅关闭形同虚设）。统一为自适应后，四仓同一份实现即可覆盖两种配置。
fn request_graceful_shutdown(port: u16) -> bool {
    use std::io::{Read, Write};
    use std::net::TcpStream;
    use std::time::Duration;

    let Ok(addr) = format!("127.0.0.1:{port}").parse::<std::net::SocketAddr>() else {
        return false;
    };

    // 1) GET 健康检查，顺带取 CSRF token（中间件在安全方法响应上补种）
    let token = {
        let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(800)) else {
            return false;
        };
        let _ = s.set_read_timeout(Some(Duration::from_millis(1500)));
        let req = format!(
            "GET /api/system/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
        );
        if s.write_all(req.as_bytes()).is_err() {
            return false;
        }
        let mut buf = Vec::new();
        if s.read_to_end(&mut buf).is_err() {
            return false;
        }
        let head = String::from_utf8_lossy(&buf);
        let headers = head.split("\r\n\r\n").next().unwrap_or("");
        extract_csrf_token(headers)
    };

    // 2) POST 关闭端点（有 token 走 double-submit，无 token 裸 POST）
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(800)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_millis(1500)));
    let req = match token {
        Some(tok) => format!(
            "POST /api/system/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: csrf_token={tok}\r\nX-CSRF-Token: {tok}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        ),
        None => format!(
            "POST /api/system/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        ),
    };
    if s.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 128];
    let _ = s.read(&mut buf);
    true
}

/// Python 进程状态
#[derive(Debug, Clone, Serialize)]
pub enum PythonStatus {
    Stopped,
    Starting,
    Running,
    Crashed,
}

/// Python 子进程管理器
pub struct PythonProcess {
    child: Option<Child>,
    port: u16,
    runtime_dir: PathBuf,
    app_dir: PathBuf,
    log_dir: PathBuf,
    status: PythonStatus,
    restart_count: u32,
    max_restarts: u32,
}

impl PythonProcess {
    pub fn new(runtime_dir: PathBuf, app_dir: PathBuf, log_dir: PathBuf) -> Self {
        Self {
            child: None,
            port: 0,
            runtime_dir,
            app_dir,
            log_dir,
            status: PythonStatus::Stopped,
            restart_count: 0,
            max_restarts: 3,
        }
    }

    /// 启动 Python 子进程
    pub fn start(&mut self) -> Result<u16> {
        // 找空闲端口
        let port = find_free_port()?;
        self.port = port;

        // 确定 Python 可执行文件路径
        let python_exe = self.runtime_dir.join("python.exe");
        if !python_exe.exists() {
            return Err(anyhow!("Python 可执行文件不存在: {}", python_exe.display()));
        }

        // 启动脚本
        let start_script = self.app_dir.join("start_portable.py");
        if !start_script.exists() {
            return Err(anyhow!("启动脚本不存在: {}", start_script.display()));
        }

        // 确保日志目录存在
        std::fs::create_dir_all(&self.log_dir)?;
        let log_file = self.log_dir.join(format!(
            "python_{}.log",
            chrono::Local::now().format("%Y%m%d_%H%M%S")
        ));
        let log_writer = std::fs::File::create(&log_file)?;

        // 启动子进程
        let child = Command::new(&python_exe)
            .arg(&start_script)
            .arg("--port")
            .arg(port.to_string())
            .arg("--host")
            .arg("127.0.0.1")
            .current_dir(&self.app_dir)
            .stdout(Stdio::from(log_writer.try_clone()?))
            .stderr(Stdio::from(log_writer))
            .env("PYTHONNOUSERSITE", "1")
            .env("PYTHONUNBUFFERED", "1")
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .spawn()?;

        self.child = Some(child);
        self.status = PythonStatus::Starting;
        log::info!("Python 进程已启动，PID={}, 端口={}", self.child.as_ref().unwrap().id(), port);

        Ok(port)
    }

    /// 标记为运行中
    pub fn mark_running(&mut self) {
        self.status = PythonStatus::Running;
        self.restart_count = 0;
    }

    /// 检查进程是否存活
    pub fn is_alive(&mut self) -> bool {
        if let Some(child) = &mut self.child {
            match child.try_wait() {
                Ok(None) => true,
                Ok(Some(status)) => {
                    log::warn!("Python 进程已退出，状态={}", status);
                    false
                }
                Err(_) => false,
            }
        } else {
            false
        }
    }

    /// 尝试重启（受 max_restarts 限制）
    pub fn try_restart(&mut self) -> Result<bool> {
        if self.restart_count >= self.max_restarts {
            self.status = PythonStatus::Crashed;
            return Ok(false);
        }
        self.restart_count += 1;
        log::info!("正在重启 Python 进程（第 {}/{} 次）", self.restart_count, self.max_restarts);
        self.stop()?;
        self.start()?;
        Ok(true)
    }

    /// 停止 Python 进程（Windows 下杀整棵进程树：uvicorn/torch 会 spawn 子进程，
    /// 只杀直接子进程会留下孤儿持有 app/ 文件句柄，导致更新换载 rename 失败）
    pub fn stop(&mut self) -> Result<()> {
        if let Some(mut child) = self.child.take() {
            let pid = child.id();
            #[cfg(windows)]
            {
                // B-5: 先请求本机 /api/system/shutdown 优雅关闭（停队列、卸载模型、
                // 关历史库），最多等 6 秒；未退出才 taskkill /F 强杀兜底（幂等）。
                let shutdown_sent = if self.port > 0 {
                    request_graceful_shutdown(self.port)
                } else {
                    false
                };
                if shutdown_sent {
                    log::info!("已请求 Python 服务优雅关闭（port={}）", self.port);
                    for _ in 0..60 {
                        match child.try_wait() {
                            Ok(Some(_)) => break,
                            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(100)),
                            Err(_) => break,
                        }
                    }
                }
                // taskkill /T 级联终止子进程树；失败再回退 kill()
                let ok = Command::new("taskkill")
                    .args(["/PID", &pid.to_string(), "/T", "/F"])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status()
                    .map(|s| s.success())
                    .unwrap_or(false);
                if !ok {
                    let _ = child.kill();
                }
            }
            #[cfg(not(windows))]
            {
                let _ = child.kill();
            }
            let _ = child.wait();
            log::info!("Python 进程树已停止（根 PID={pid}）");
        }
        // 兜底：扫描并终止所有属于本应用的 python 进程（父进程已退出的孤儿也一并清除，
        // 否则它们会持有 app/ 目录句柄/工作目录，导致更新换载 rename app 失败 os error 32）
        #[cfg(windows)]
        {
            let app_dir = resolve_app_dir();
            let marker = format!("{0}\\start_portable.py", app_dir.display());
            // PowerShell -like 通配：反斜杠是字面字符，单引号按 PowerShell 规则双写转义
            let esc = marker.replace('\'', "''");
            let ps = format!(
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {{ $_.CommandLine -like '*{esc}*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
            );
            let _ = Command::new("powershell")
                .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &ps])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            log::info!("已扫描清除本应用残留 python 进程");
        }
        self.status = PythonStatus::Stopped;
        Ok(())
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn status(&self) -> PythonStatus {
        self.status.clone()
    }
}

/// 全局共享的 Python 进程管理器
pub struct PythonState {
    pub process: Mutex<PythonProcess>,
}

/// 启动事件负载
#[derive(Serialize, Clone)]
struct StartupStatus {
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

/// 向发送启动状态
pub fn emit_startup_status(app: &AppHandle, message: &str, error: Option<String>) {
    let _ = app.emit("startup-status", StartupStatus {
        message: message.to_string(),
        error,
    });
}

/// 解析运行时目录：优先侧载 runtime，开发模式回退项目 .venv，再退系统 Python
pub fn resolve_runtime_dir(app_dir: &Path) -> PathBuf {
    // 1. 打包后：应用目录下的 runtime/
    let bundled = app_dir.join("runtime");
    if bundled.join("python.exe").exists() {
        return bundled;
    }

    // 2. 开发模式 A：app_dir 即项目根（resolve_app_dir 的开发分支），.venv 在其下
    let dev_local = app_dir.join(".venv").join("Scripts");
    if dev_local.join("python.exe").exists() {
        return dev_local;
    }

    // 3. 开发模式 B：app_dir 为 exe_dir/app，.venv 位于上两级（历史布局）
    let dev_venv = app_dir
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.join(".venv").join("Scripts"));
    if let Some(venv) = dev_venv {
        if venv.join("python.exe").exists() {
            return venv;
        }
    }

    // 4. 回退：系统 PATH 中的 python
    PathBuf::from("python")
}

/// 纯函数：按优先级从可执行文件路径解析应用目录（**打包布局**）；
/// 无法判定（如开发态 `target/release`）返回 `None`，由调用方回退开发模式。
///
/// 优先级：
///   1. 扁平布局（installer-data 轨）：`exe_dir/start_portable.py` 存在 → 返回 `exe_dir`；
///   2. app/ 嵌套布局（便携 bundle 轨）：`exe_dir/app/start_portable.py` 存在 → 返回 `exe_dir/app`。
pub fn resolve_app_dir_from_exe(exe: &std::path::Path) -> Option<PathBuf> {
    let exe_dir = exe.parent()?;
    if exe_dir.join("start_portable.py").exists() {
        return Some(exe_dir.to_path_buf());
    }
    let bundled = exe_dir.join("app");
    if bundled.join("start_portable.py").exists() {
        return Some(bundled);
    }
    None
}

/// 解析应用代码目录
pub fn resolve_app_dir() -> PathBuf {
    // 1/2. 打包布局（先扁平后 app/ 嵌套）——见 resolve_app_dir_from_exe。
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = resolve_app_dir_from_exe(&exe) {
            return dir;
        }
    }

    // 3. 开发模式：CARGO_MANIFEST_DIR 上溯到项目根（desktop/src-tauri → 项目根）
    let dev_app = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf();
    if dev_app.join("start_portable.py").exists() {
        return dev_app;
    }

    dev_app
}

#[cfg(test)]
mod app_dir_tests {
    use super::resolve_app_dir_from_exe;
    use std::path::PathBuf;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(name);
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn flat_layout_returns_exe_dir() {
        // 扁平布局（installer-data 轨）：exe_dir 下有 start_portable.py
        let d = tmp("tts_appdir_flat");
        std::fs::write(d.join("start_portable.py"), b"# stub").unwrap();
        let got = resolve_app_dir_from_exe(&d.join("TTSMultiModel.exe"));
        assert_eq!(got, Some(d.clone()));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn nested_app_layout_returns_app_subdir() {
        // app/ 嵌套布局（便携 bundle 轨）
        let d = tmp("tts_appdir_nested");
        std::fs::create_dir_all(d.join("app")).unwrap();
        std::fs::write(d.join("app").join("start_portable.py"), b"# stub").unwrap();
        let got = resolve_app_dir_from_exe(&d.join("TTSMultiModel.exe"));
        assert_eq!(got, Some(d.join("app")));
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn bare_shell_dir_returns_none() {
        // 裸壳目录（无 payload）→ None，交给开发模式回退
        let d = tmp("tts_appdir_bare");
        assert!(resolve_app_dir_from_exe(&d.join("TTSMultiModel.exe")).is_none());
        let _ = std::fs::remove_dir_all(&d);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_token_prefers_header() {
        let headers = "HTTP/1.1 200 OK\r\nX-CSRF-Token: abc.def\r\nSet-Cookie: csrf_token=zzz; Path=/";
        assert_eq!(extract_csrf_token(headers).as_deref(), Some("abc.def"));
    }

    #[test]
    fn extract_token_falls_back_to_cookie() {
        let headers = "HTTP/1.1 200 OK\r\nset-cookie: csrf_token=nonce.sig; Path=/; SameSite=Strict\r\n";
        assert_eq!(extract_csrf_token(headers).as_deref(), Some("nonce.sig"));
    }

    #[test]
    fn extract_token_returns_none_when_absent() {
        let headers = "HTTP/1.1 200 OK\r\ncontent-type: application/json";
        assert_eq!(extract_csrf_token(headers), None);
    }

    #[test]
    fn extract_token_ignores_other_cookies() {
        let headers = "HTTP/1.1 200 OK\r\nset-cookie: theme=dark; Path=/\r\n";
        assert_eq!(extract_csrf_token(headers), None);
    }
}
