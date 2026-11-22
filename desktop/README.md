# TTSMultiModel Desktop（Tauri v2 桌面壳）

TTS MultiModel 语音合成工具的桌面壳：Tauri v2 外壳 + 侧载 Python 运行时 + WebView2，
将原有的「浏览器 + 本地 FastAPI」形态封装为原生桌面应用（托盘、通知、拖拽、
窗口状态记忆、完整安装包更新）。

## 目录结构

```
desktop/
├── src/
│   ├── index.html            # 启动画面（Python 未就绪时展示）
│   └── desktop-bridge.js     # 前端桥接：托盘/通知/拖拽/更新对话框/标题联动（浏览器模式自动降级）
└── src-tauri/
    ├── Cargo.toml            # Rust 依赖
    ├── tauri.conf.json       # 应用配置（productName/identifier）
    ├── capabilities/main.json# IPC 权限：core+notification+自研 updater 命令，remote 放行 127.0.0.1
    └── src/
        ├── main.rs           # 入口：单实例/插件注册/命令/窗口事件/watchdog/退出兜底
        ├── python_process.rs # Python 子进程生命周期（启动/健康检查/重启/进程树终止）
        ├── port_manager.rs   # 随机空闲端口分配
        ├── health_check.rs   # /api/system/health 就绪轮询
        ├── tray.rs           # 系统托盘（左键切换/右键菜单/忙闲提示/更新徽标）
        ├── window.rs         # 窗口状态记忆/外链拦截/标题联动/关闭到托盘/桥接注入
        ├── notification.rs   # Windows Toast（前台抑制、声音开关）
        ├── drag_drop.rs      # 文件拖拽白名单 + read_dragged_file 命令
        ├── config.rs         # %APPDATA%/TTSMultiModel/{config.json,window_state.json} 原子读写
        └── updater.rs        # 完整包更新：GitHub 检查/semver/下载/SHA256/静默安装
```

## 开发环境要求

- Rust stable（当前使用 1.98.0）+ MSVC toolchain（`rustup component add clippy`）
- Node.js 22+（`desktop/package.json` 依赖 `@tauri-apps/cli`）
- Windows 10/11 自带 WebView2 Runtime（精简版 LTSC 需手动安装）
- Python 3.12 项目环境：开发模式复用项目根 `.venv`（含 CUDA torch）

## 运行

```powershell
# 开发模式（自动用项目根 .venv 起 Python 后端，WebView 加载 http://127.0.0.1:随机端口）
cd desktop
npm run tauri dev

# 或直接运行已编译的 debug 二进制（效果同上）
cd desktop/src-tauri
.\target\debug\tts-multimodel-desktop.exe
```

配置解析顺序（`resolve_runtime_dir` / `resolve_app_dir`）：
1. 打包后：exe 旁 `app/`（应用代码）+ `app/runtime/`（Python 运行时）
2. 开发模式：项目根（`scripts/start_portable.py` 所在）+ 项目根 `.venv/Scripts`

## 测试与静态检查

```powershell
cd desktop/src-tauri
cargo test          # 36 项单测（更新器/窗口/托盘/拖拽/配置/端口）
cargo clippy --all-targets -- -D warnings   # 零警告
cargo build         # 产出 target/debug/tts-multimodel-desktop.exe
```

## 用户配置（%APPDATA%/TTSMultiModel/）

| 文件 | 说明 |
|---|---|
| `config.json` | `update_source` / `auto_check_update` / `notification_sound` / `close_to_tray`（UTF-8 含 BOM 亦可解析） |
| `window_state.json` | 窗口大小/位置/最大化状态（DPI 归一 + 位置合法性校验） |
| `updates/` | 更新包下载缓存（不污染应用树） |

## 更新契约（单一路线：完整安装包）

- 官网下载与应用内「检查更新」拿到**同一个文件**：`TTSMultiModel-Setup-v{version}.exe`
  （由 `.github/workflows/desktop-installer.yml` 在 `v*` tag push 时产出并挂到 Release）
- 历史版本曾并存一条代码增量路线（`app-v{ver}.zip` + 原子换载），已移除：
  维护两条产线/两类资产得不偿失，且 Tauri v2 官方 updater 在 Windows 上同样复用完整 NSIS 安装包
- 执行序列（`updater.rs`）：扫 Release 资产 → 下载完整 exe → SHA256 校验 →
  以 `/S` 静默启动 NSIS → 安装器自带 `KillRunning` 杀掉壳与 Python 后覆盖安装 → 用户手动重开
- 用户数据不丢：安装段只覆盖同名文件、不删整个 `$INSTDIR`；`runtime/` `model/` `data/`
  `logs/` 保留（只有卸载段才 `RMDir /r "$INSTDIR"`）
- 与 Tauri 官方 updater 的差异：官方传 `/S /UPDATE /ARGS` 并让安装器自动重启应用；
  本项目 NSIS 是自写的（不认 `/UPDATE`），故只用 `/S`，装完提示用户手动重启
- 旧式 `app.bak/` 原子换载/自动回滚随增量路线一并退役

## 已知问题与限制

- 通知点击聚焦依赖 Windows Toast 系统激活行为；窗口隐藏（托盘）时点击通知
  不会自动唤回窗口，需托盘手动操作（`notification.rs` 注释已如实说明）。
- 托盘「检查更新」在启动页阶段结果仅托盘标记，对话框等页面就绪后手动检查再弹。
- 拖拽多文件落**文件夹**才切批量；散文件取首个进单文件流程。
- GitHub 未认证 API 限速 60/h；自动检查有 10 分钟节流。
- 更新只走**自研 `updater.rs` + 完整安装包**一条路线；曾注册但从未调用的 `tauri-plugin-updater`、以及旧的代码增量 zip 产线均已移除，避免两套资产/两队维护。
- WebView2 缺失（LTSC）时启动失败，AI-3 安装包需带引导（Tauri 默认 bootstrapper）。

## 交接与后续

- 6 阶段迁移计划见 `docs/tauri-migration/AI-1-基础壳构建指导.md`
- 阶段二交付与 AI-3 交接见 `docs/tauri-migration/AI-2-交付与交接说明.md`
- 打包发布与测试见 `docs/tauri-migration/AI-3-打包发布与测试指导.md`
