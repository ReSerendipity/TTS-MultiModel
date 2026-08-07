# TTS_MultiModel 安全加固任务执行指示报告 v2.0

> **报告版本**：v2.0  
> **生成日期**：2026-08-07  
> **前置文档**：[GITHUB_SECURITY_ASSESSMENT_REPORT.md](./GITHUB_SECURITY_ASSESSMENT_REPORT.md)（评估报告 vFinal）  
> **适用对象**：项目维护者 / 安全负责人 / DevOps  
> **文档状态**：**可执行文档**（每项任务含验收标准 + 自检命令 + 失败回滚方案）

---

## 第零节：执行状态总览（Before/After 对照）

本报告整合：
1. 评估报告中 **Top 7 优先行动项** 的当前完成度核验
2. **上一轮未完成任务**（VAPID 私钥清理）的专项操作步骤
3. **后续 4 个阶段** 的逐步执行指引（静态验证 → 动态回归 → 进阶增强）
4. 统一的验收矩阵（便于逐项打勾确认）

### 0.1 Top 7 行动项状态仪表盘

| # | 任务 | 优先级 | 完成度 | 状态 | 上次遗留问题 |
|---|------|--------|-------|------|-------------|
| **T1** | 3 处写盘接入水印 + source_id 代码常量 | P0 | 100% | ✅ **已验收** | — |
| **T2** | 上传魔术字节 fail-closed 校验 | P0 | 100% | ✅ **已验收** | — |
| **T3** | 非本地绑定 → 强制 API Auth 安全网 | P1 | 100% | ✅ **已验收** | — |
| **T4** | VAPID 私钥清理 + pre-commit 拦截 Hook | P1 | 60% | 🟠 **部分完成（唯一未点项）** | `.env` 中真实私钥未替换为占位符（`T4-b` 子任务未执行） |
| **T5** | NOTICE 文件 + SPDX 归属头 + API 元数据归属 | P1 | 100% | ✅ **已验收** | — |
| **T6** | 模型权重 SHA256 校验清单 + 自动校验 | P1 | 100% | ✅ **已验收** | — |
| **T7** | Release 流程 SHA256SUMS + GPG 条件签名 | P1 | 100% | ✅ **已验收** | — |

> **关键结论**：7 项中 6 项已 100% 完成，**仅剩 `T4-b`（VAPID 私钥清理）需立即执行**（约 5 分钟）。

---

## 第一节：上一轮未完成任务专项（T4-b 立即执行）

### 1.1 任务背景

| 项 | 事实 |
|----|------|
| **位置** | 项目根目录 `.env` 第 4-6 行 |
| **问题** | 当前包含真实可执行的 VAPID EC 私钥 PEM（非占位符） |
| **风险等级** | 🟠 中（本地配置风险，已核验未泄露到 Git，但一旦误 `git add -A` 即高危） |
| **前置保护** | `T4-a` 已完成：pre-commit 已配置 `detect-private-key` + 自定义 `Forbid PRIVATE KEY` Hook |
| **阻塞性** | 非阻塞启动，但属于 **"在制品风险"**，应在本轮关闭 |

### 1.2 执行步骤（Windows PowerShell，项目根目录运行）

#### 步骤 1：备份当前真实私钥（如需保留）

仅当你在其他生产环境已使用此 VAPID 密钥进行 Web Push 订阅时需要备份：

```powershell
# 备份到用户主目录（不会被 git 跟踪）
Copy-Item ".env" "$env:USERPROFILE\.tts_multimodel_env_backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
Write-Host "✅ 已备份到: $env:USERPROFILE"
```

#### 步骤 2：替换 `.env` 为占位符格式

**编辑 `.env` 文件，将第 4-6 行替换为以下内容**（与 `.env.example` 格式对齐）：

```
VAPID_PUBLIC_KEY=YOUR_VAPID_PUBLIC_KEY_HERE
VAPID_PRIVATE_KEY=YOUR_VAPID_PRIVATE_KEY_PEM_HERE
VAPID_SUBJECT=mailto:your-admin@your-domain.com
```

> 编辑点精确定位：[.env:4-6](file:///c:/Users/Doro/TTS_MultiModel/.env#L4-L6)

#### 步骤 3：自证 pre-commit 拦截规则生效（可选，但推荐）

```powershell
# 临时 git add .env 触发 pre-commit（实际不 commit，仅验证）
git add .env
pre-commit run --files .env

# 预期输出（无红色 FAIL）：
#   Detect Private Key....................................................Passed
#   Forbid PRIVATE KEY in .env files.....................................Passed

# 验证完取消暂存
git reset HEAD .env
```

### 1.3 验收标准

| # | 检查项 | 自检命令 | 预期结果 | 通过？ |
|---|--------|---------|---------|-------|
| 1 | `.env` 第 4-6 行不含 `PRIVATE KEY` 字符串 | `Select-String -Path .env -Pattern "PRIVATE KEY" -SimpleMatch` | 无任何匹配（空输出） | ☐ |
| 2 | `.env` 含占位符 `YOUR_` 前缀 | `Select-String -Path .env -Pattern "YOUR_VAPID" -SimpleMatch` | 至少 2 行匹配 | ☐ |
| 3 | pre-commit `Forbid PRIVATE KEY` 通过 | `pre-commit run --files .env` Hook ID 67-70 | `Passed` 绿色输出 | ☐ |

### 1.4 回滚方案（如替换后发现需要原密钥）

```powershell
# 从步骤1的备份中恢复，或从已知安全位置还原原私钥
Copy-Item "$env:USERPROFILE\.tts_multimodel_env_backup_YYYYMMDD_HHMMSS" ".env" -Force
```

---

## 第二节：Top 7 任务逐项执行指示 + 验收证据

本节为每个已完成任务提供：
- **任务目标**（原始要求）
- **实现位置**（代码精确位置，支持点击跳转）
- **自检命令**（如何验证实现存在且正确）
- **常见误实现**（陷阱，防止"形式上完成但功能失效"）

---

### T1：P0 - 水印强制接入（4 处写盘点）

**任务目标**：所有通过项目保存的音频必须嵌入来源水印，source_id 为代码常量（不可通过配置篡改）。

#### 实现位置清单

| 写盘点 | 代码跳转 | 实现要点 |
|--------|---------|---------|
| 1. `save_audio()` 时间戳命名保存 | [generation.py:114-127](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/generation.py#L114-L127) | try-except 包裹，失败仅记日志不阻塞生成 |
| 2. `_save_wav_compatible()` 浏览器兼容输出 | [generation.py:902-924](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/generation.py#L902-L924) | 注释写明"P0 安全修复"，source_id 使用常量 |
| 3. `dotstts_engine._save_wav()` dots.tts 引擎 | [dotstts_engine.py:326-340](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/engines/dotstts_engine.py#L326-L340) | 相对导入 `from ..watermark import watermark_audio` |
| 4. `streaming.py:sf.write` 流式分块输出 | [streaming.py:160-175](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/engines/voxcpm2/streaming.py#L160-L175) | 流式缓冲写盘前嵌入 |
| 常量定义 | [generation.py:42-44](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/generation.py#L42-L44) | `WATERMARK_SOURCE_ID: str = "tts-multimodel"`，注释明确"代码常量不可配置" |

#### 自检命令（3 选 1，推荐第 3 项动态验证）

```bash
# 命令1：静态验证 import 和调用存在（快速）
# PowerShell 中运行：
Select-String -Path "bin/integrated_app/generation.py" -Pattern "watermark_audio" | Select-Object -First 5
# 预期输出：至少 2 处匹配（第116行和第906行）
```

```bash
# 命令2：确认 source_id 使用的是常量而非 config 值
Select-String -Path "bin/integrated_app/generation.py" -Pattern "source_id="
# 预期：所有行 source_id=WATERMARK_SOURCE_ID（不出现 .yaml / config / get_config 等字样）
```

```bash
# 命令3：动态验证（应用启动后生成音频，查看日志）—— 最准确
# 1. 启动应用生成任意 3 秒以上音频
# 2. 在 logs/app_*.log 中搜索：
Select-String -Path "logs\*.log" -Pattern "水印嵌入完成" | Select-Object -Last 3
# 预期输出类似：watermark.py:296 - 水印嵌入完成: source=tts-multimodel, SNR=30.1dB, hash=abc123...
```

#### 常见误实现陷阱

| 陷阱 | 症状 | 排查方法 |
|------|------|---------|
| 只改了 1-2 处写盘点，遗留 1 处无水印 | 流式生成音频无水印、dots.tts 引擎生成无水印 | 用命令 1 逐个文件检查 import |
| source_id 仍从 config 读取（可被克隆者篡改） | `grep` 结果出现 `get_config` 或 `cfg.watermark` | 命令 2 确认 source_id= 右侧仅为 WATERMARK_SOURCE_ID 常量名 |
| 水印失败抛异常阻塞正常生成 | 用户报告"生成有时失败无输出" | 确认 try-except 包裹 watermark_audio 调用，异常仅 `logger.warning` |

---

### T2：P0 - 上传魔术字节校验 fail-closed

**任务目标**：上传文件扩展名与实际内容魔术字节不匹配时，必须拒绝（fail-closed），不得默认放行。

#### 实现位置清单

| 组件 | 代码跳转 | 实现要点 |
|------|---------|---------|
| 校验函数（重命名后） | [audio.py:194-227](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/routes/audio.py#L194-L227) | `_validate_audio_content()`，明确注释"白名单模式确保伪装文件被拒" |
| 上传路由调用点 | [audio.py:476-494](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/routes/audio.py#L476-L494) | `if not _validate_audio_content(...)` → **os.remove(file_path) + 返回 400** ✅ |
| 流式上传统一头部采集 | [audio.py:230-258](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/routes/audio.py#L230-L258) | `_stream_upload_to_disk` 返回三元组第三项为 `header_bytes[:16]` |

#### 自检命令（静态 + 动态 PoC）

```bash
# 命令1：静态验证调用方向正确（not 逻辑）
Select-String -Path "bin/integrated_app/routes/audio.py" -Pattern "not _validate_audio_content"
# 预期：恰好 1 处匹配（第 480 行），且下一行紧跟 os.remove 或 try:os.remove
```

```bash
# 命令2：动态 PoC（用 curl 模拟伪造文件上传，推荐 30 秒验证）
# 2.1 先创建恶意文件：扩展名 .wav，内容却是 PHP 一句话
"<?php system($_GET['cmd']); ?>" | Out-File -Encoding ASCII "malicious.wav"

# 2.2 获取 CSRF Token + Cookie（浏览器开发者工具复制）后执行：
# （需先启动应用）
curl.exe -X POST "http://127.0.0.1:7869/api/audio/upload/audio" `
  -H "X-CSRF-Token: <你的CSRF_TOKEN>" `
  -H "Cookie: csrf_token=<你的CSRF_TOKEN>" `
  -F "file=@malicious.wav;type=audio/wav"

# 预期返回 HTTP 400，JSON：{"status": "error", "message": "File content does not match the claimed audio format..."}
# 并且 server 日志出现：audio.py:xxx - 清理格式不匹配文件失败/成功
```

#### 常见误实现陷阱

| 陷阱 | 症状 | 排查方法 |
|------|------|---------|
| 校验失败不删文件（磁盘泄漏） | 服务器磁盘长期运行出现大量恶意占位文件 | 命令 1 后读第 480 行的 if-body 是否包含 os.remove |
| 继续使用原函数名 `_validate_audio_magic_bytes` 但未调用（改名漏同步） | `grep _validate_audio_magic_bytes(` 无调用，旧函数名残留 | 直接搜索旧函数名：应为 0 调用（含定义本身不超过 1 处） |
| fail-open（不识别时返回 True 放行） | 未知格式（如 m4a 魔数未收录）时允许上传 | 读校验函数末尾：未识别扩展名时应 `return detected_ext == claimed_ext`，不是 `return True` |

---

### T3：P1 - 非本地绑定强制 API Auth 安全网

**任务目标**：防止用户在 `config.yaml` 中设置 `server.host: 0.0.0.0` 对外暴露，却忘记启用 API 认证，导致所有端点裸奔。

#### 实现位置清单

| 组件 | 代码跳转 | 实现要点 |
|------|---------|---------|
| 安全网拦截逻辑 | [app_server.py:671-684](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/app_server.py#L671-L684) | `run_server()` 函数**首行**执行校验（早于 create_app 和模型加载） |
| 本地白名单集合 | 第 673 行 | `_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}` |
| 阻断方式 | 第 684 行 | `raise SystemExit(1)`，非软警告（必须 sys.exit，不可仅 print） |
| 错误信息 | 第 677-683 行 | 中文 logger.error 明确告诉用户"如何修复"（改 config 或用本地绑定） |

#### 自检命令（静态 + 动态阻断验证）

```bash
# 命令1：静态验证 SystemExit 位置（必须早于 create_app）
Select-String -Path "bin/integrated_app/app_server.py" -Pattern "raise SystemExit" | Select-Object LineNumber
# 预期 LineNumber（第 684 行） < create_app() 调用行号（第 686 行）
```

```bash
# 命令2：动态阻断验证（1 分钟）
# 2.1 临时改 config.yaml：
#      server:
#        host: "0.0.0.0"
#      api_auth:
#        enabled: false   # （或 token 为空）
# 2.2 启动 start.bat
# 预期行为：控制台出现红色/黄色 ERROR 级日志"安全网拦截..."，
#          进程在 2 秒内退出，不打开浏览器，端口 7869 未监听

# 2.3 验证完务必恢复 server.host: "127.0.0.1"
```

#### 常见误实现陷阱

| 陷阱 | 症状 | 排查方法 |
|------|------|---------|
| 只警告不退出（fail-open） | 用户"看到警告但懒得改"，仍对外暴露 | 确认使用 `raise SystemExit(1)` 非 `logger.warning` + 继续执行 |
| 判断逻辑写反（本地绑定拦截了反而放行非本地） | 正常启动被拦截，0.0.0.0 反而通过 | 仔细读条件：`if ip NOT IN _LOCAL_HOSTS AND (NOT enabled OR NOT token)` 三重判断都成立才拦截 |
| IPv6 漏白名单 | `::1` 用户无法正常本地启动 | 命令 1 后搜 _LOCAL_HOSTS 集合中是否同时含 `"::1"` |

---

### T4：P1 - VAPID 私钥清理 + pre-commit（分 2 子任务）

#### T4-a（✅ 已完成）：pre-commit 拦截规则

| Hook ID | 文件位置 | 拦截内容 |
|---------|---------|---------|
| `detect-private-key` | [.pre-commit-config.yaml:36](file:///c:/Users/Doro/TTS_MultiModel/.pre-commit-config.yaml#L36) | 通用私钥格式（RSA/EC/DSA PEM 头） |
| 自定义 `Forbid PRIVATE KEY` | [.pre-commit-config.yaml:67-70](file:///c:/Users/Doro/TTS_MultiModel/.pre-commit-config.yaml#L67-L70) | 扫描文件前 4096 字节含 `b'PRIVATE KEY'` 直接退出码 1 |

#### T4-b（🟠 待执行，即第一节专项）：.env 私钥替换占位符

参见第一节 1.2 步骤。

---

### T5：P1 - NOTICE + SPDX 归属头 + API 元数据归属

**任务目标**：在**版权声明批量替换**（评估报告 §1.2.1）攻击向量下，通过多处散布归属标识提升替换成本（至少 4 种不同渠道同时篡改才能完全去除归属）。

#### 实现位置清单（5 处散布，"1+3+1"结构）

| 类型 | 渠道 | 代码跳转 | 形式 |
|------|------|---------|------|
| 1. 根目录声明文件 | NOTICE | [NOTICE 全文](file:///c:/Users/Doro/TTS_MultiModel/NOTICE) | 独立文件，包含官方仓库 URL + 6 个第三方组件归属 |
| 2. 核心源码文件头 SPDX | `app_server.py` | [app_server.py:1-2](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/app_server.py#L1-L2) | `SPDX-FileCopyrightText + SPDX-License-Identifier` 两行 |
| 3. 核心源码文件头 SPDX | `engine_interface.py` | [engine_interface.py:1-2](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/engine_interface.py#L1-L2) | 同上 |
| 4. 核心源码文件头 SPDX | `cli.py` | [cli.py:2-3](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/cli.py#L2-L3) | 同上 |
| 5. API 返回元数据（运行时） | `/api/health/ping` 端点 | [app_server.py:588-593](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/app_server.py#L588-L593) | JSON 固定字段 `attribution: "TTS_MultiModel © ReSerendipity, Apache 2.0"` |
| 6. CLI 横幅（运行时） | `tts-multimodel` 命令 stderr | [cli.py:1083-1086](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/cli.py#L1083-L1086) | 版本+©+官方仓库 URL+$schema URL 四行 |
| 7. 启动日志（运行时） | 控制台/stdout | [app_server.py:701](file:///c:/Users/Doro/TTS_MultiModel/bin/integrated_app/app_server.py#L701) | `TTS_MultiModel vX.Y.Z © ReSerendipity, Apache 2.0 | listening on...` |

#### 自检命令

```bash
# 命令1：NOTICE 文件存在 + 含官方仓库
Test-Path "NOTICE"; Select-String -Path NOTICE -Pattern "github.com/ReSerendipity" -SimpleMatch
# 预期：True + 1 行匹配

# 命令2：3 个核心文件头 SPDX 数量（应恰好 2*3 = 6 行 SPDX 开头）
(Select-String -Path "bin/integrated_app/app_server.py","bin/integrated_app/engine_interface.py","bin/integrated_app/cli.py" -Pattern "^# SPDX-").Count
# 预期：= 6

# 命令3：API 归属字段（启动后 curl 验证）
curl.exe "http://127.0.0.1:7869/api/health/ping" | ConvertFrom-Json | Select-Object -ExpandProperty attribution
# 预期："TTS_MultiModel © ReSerendipity, Apache 2.0"
```

---

### T6：P1 - 模型权重 SHA256 校验

**任务目标**：防止 `.safetensors` 权重被中间人替换植入后门（评估报告 §2.2.8）。

#### 实现位置清单

| 组件 | 代码跳转 | 说明 |
|------|---------|------|
| 校验清单文件 | [SHA256SUMS.models](file:///c:/Users/Doro/TTS_MultiModel/docs/SHA256SUMS.models) | 格式：`<64位hex>  <相对pretrained_models的路径>` 每行一个 |
| 校验工具脚本 | [verify_model_checksums.py](file:///c:/Users/Doro/TTS_MultiModel/scripts/verify_model_checksums.py) | 入口 `verify_checksums()` + CLI `main()` 双模式 |
| dots 下载后自动调用 | [download_dotstts.py:72-90](file:///c:/Users/Doro/TTS_MultiModel/scripts/download_dotstts.py#L72-L90) | 子进程方式 `subprocess.run`，失败记 WARNING 不阻塞（防止首次下载清单为空时卡死） |
| indextts2 下载后自动调用 | [download_indextts2.py:137-155](file:///c:/Users/Doro/TTS_MultiModel/scripts/download_indextts2.py#L137-L155) | 同上 |

#### 自检命令

```bash
# 命令1：直接运行校验工具（首次运行会提示清单为空属正常）
python scripts/verify_model_checksums.py
# 预期输出结尾：
#   [2/2] SHA256 哈希校验...
#   ⚠️ 校验清单为空（无实际哈希值），跳过校验  （或 ✅ 所有校验通过！如果清单已填）
#   exit code 0

# 命令2：模拟"下载后校验"触发链路（验证脚本调用代码路径存在）
Select-String -Path scripts/download_dotstts.py,scripts/download_indextts2.py -Pattern "verify_model_checksums.py"
# 预期：2 处匹配，1 个脚本 1 处
```

---

### T7：P1 - Release 流程 SHA256SUMS + GPG 签名

**任务目标**：发布物（wheel/tar.gz/脚本）提供 **SHA256 清单 + GPG 签名（条件触发）**，用户可验证发布物真实来源，防 PyPI/Docker Hub 假冒包（评估报告 §3.2.3）。

#### 实现位置清单

全部集中在 CI 文件：[release-please.yml](file:///c:/Users/Doro/TTS_MultiModel/.github/workflows/release-please.yml)

| Step 名称 | 行号 | 功能 |
|-----------|------|------|
| Generate SHA256SUMS for release artifacts | 59-63 | `sha256sum *.whl *.tar.gz > dist/SHA256SUMS` |
| Generate SHA256SUMS for scripts | 66-69 | `sha256sum start.bat bin/start_app.bat bin/clean_launch.py > SHA256SUMS.scripts` |
| GPG sign SHA256SUMS（条件） | 71-82 | `if: env.GPG_SIGNING_KEY != ''` → import → gpg `--detach-sign --armor` 两个清单 |
| 上传 4 件套到 Release Assets | 91-94 | `SHA256SUMS` + `SHA256SUMS.sig` + `SHA256SUMS.scripts` + `SHA256SUMS.scripts.sig` |
| Release Body 追加验证命令 | 104-113 | `sha256sum -c` + `gpg --verify` 示例写入 body |

#### 自检命令（只能在 GitHub Actions 实际跑时验证）

本任务在本地无 CI 环境时只能做**静态代码存在性验证**：

```bash
# 静态验证 GPG 条件签名 Step 存在
Select-String -Path ".github/workflows/release-please.yml" -Pattern "GPG sign SHA256SUMS" -Context 0,10
# 预期：显示 71-82 行完整步骤，含 --detach-sign 参数

# 验证上传 assets 含 .sig 文件
Select-String -Path ".github/workflows/release-please.yml" -Pattern "\.sig"
# 预期：至少 2 处匹配（SHA256SUMS.sig + SHA256SUMS.scripts.sig）
```

---

## 第三节：后续 4 阶段执行指引（Step 1-4）

Top 7 完成后，按以下阶段推进。每个阶段有明确的**前置条件、执行命令、耗时、通过标准**。

### 阶段 1：立即修复（5 分钟）
**前置**：完成第一节的 `T4-b` 私钥清理。**无前置不得跳过**。

通过标准：第一节 1.3 验收矩阵 3 项全打勾。

---

### 阶段 2：本地静态验证（30 分钟）
**前置条件**：阶段 1 通过。  
**执行依据**：[AGENTS.md §2.5](file:///c:/Users/Doro/TTS_MultiModel/AGENTS.md#L78-L89) 的 CI 对齐命令。

#### 执行命令清单（Windows PowerShell，WinPython 解释器）

```powershell
# 切换到项目根
Set-Location "c:\Users\Doro\TTS_MultiModel"
$PY = ".\WPy64-312101\python\python.exe"

# ============ Step 2-1：Lint + 格式检查 ============
Write-Host "=== [Step 2-1/5] Ruff Lint ===" -ForegroundColor Cyan
& $PY -m ruff check bin/integrated_app/ scripts/
$step21 = $LASTEXITCODE

Write-Host "=== [Step 2-2/5] Ruff Format (Check only) ===" -ForegroundColor Cyan
& $PY -m ruff format bin/integrated_app/ scripts/ --check --diff
$step22 = $LASTEXITCODE

# ============ Step 2-3：依赖一致性 ============
Write-Host "=== [Step 2-3/5] 依赖一致性 check ===" -ForegroundColor Cyan
& $PY scripts/sync_requirements.py --check
$step23 = $LASTEXITCODE

# ============ Step 2-4：3引擎兼容性检查（可选，无 GPU 环境也可跑） ============
Write-Host "=== [Step 2-4/5] 3 引擎兼容性 ===" -ForegroundColor Cyan
$env:TRANSFORMERS_OFFLINE="1"
$env:HF_HUB_OFFLINE="1"
$env:MODELSCOPE_OFFLINE="1"
$env:CUDA_VISIBLE_DEVICES=""
& $PY scripts/check_3engine_compat.py
$step24 = $LASTEXITCODE

# ============ Step 2-5：CPU-only 单元测试（20-30 分钟，覆盖率门槛 20%） ============
Write-Host "=== [Step 2-5/5] CPU 单元测试 + 覆盖率 ===" -ForegroundColor Cyan
& $PY -m pytest tests/ -v --tb=short `
    --cov=bin/integrated_app --cov-report=term-missing --cov-report=xml:coverage.xml `
    --cov-fail-under=20 -k "not gpu and not cuda and not vram" -m "not integration"
$step25 = $LASTEXITCODE

# ============ 汇总 ============
Write-Host "`n=== Step 2 汇总（0 = PASS, 非0 = FAIL）===" -ForegroundColor Yellow
Write-Host "2-1 Ruff Lint         : $step21"
Write-Host "2-2 Ruff Format       : $step22"
Write-Host "2-3 依赖一致性        : $step23"
Write-Host "2-4 3引擎兼容性       : $step24"
Write-Host "2-5 单元测试+覆盖率   : $step25 (Fail-under 20%)"
```

#### 通过标准
5 个步骤 Exit Code 全部为 0。如有失败，按日志定位修复（通常 Step 2-5 覆盖率未达 20% 需要补测试；或 Step 2-1/2 ruff 报错需要修复格式）。

---

### 阶段 3：动态安全回归测试（30-60 分钟）
**前置条件**：阶段 2 全部通过。  
**环境准备**：应用成功启动（`127.0.0.1:7869` 可访问），浏览器能打开。

#### 5 项回归用例（按顺序执行）

| 用例 ID | 输入与操作 | 预期结果 | 对应任务 |
|---------|-----------|---------|---------|
| **R1** | 生成任意 3 秒以上音频，查看 `logs\app_*.log` | 搜索到**至少 1 行**：`水印嵌入完成: source=tts-multimodel, SNR=xx.xdB` | T1 |
| **R2** | 按第二节 T2 命令 2 的 PoC：上传 `内容=PHP 文本/扩展名=.wav` 的伪造文件 | 返回 HTTP 400 `File content does not match the claimed audio format` | T2 |
| **R3** | 临时改 `config.yaml` → `host: 0.0.0.0, api_auth.enabled: false`，重启 | 进程在 2 秒内退出，日志显式包含"安全网拦截"四个字 | T3 |
| **R3b** | 恢复 `host: 127.0.0.1`，再重启 | 正常启动，浏览器自动打开 | T3（副作用验证） |
| **R4** | 浏览器访问 `http://127.0.0.1:7869/api/health/ping` 或 DevTools Network 面板查看响应 JSON | JSON 中存在 `attribution` 字段且值为 `TTS_MultiModel © ReSerendipity, Apache 2.0` | T5 |
| **R5** | 命令行运行 `tts-multimodel --help` 或 `cli.py` 直接执行 | stderr 第一行含 `© ReSerendipity` 字样 | T5 |

**动态回归通过标准**：R1-R5 共 6 个检查点（R3/R3b 分开）全部符合预期。

---

### 阶段 4：进阶增强（1-2 周排期，可选）
**前置条件**：阶段 3 动态回归全部通过。  
从评估报告 §5 推荐中选 P2/P3 项，建议按以下顺序排期（ROI 从高到低）：

| 顺序 | 任务（引用评估报告章节） | 预估人天 | 修复的具体风险点 |
|------|------------------------|---------|----------------|
| **E1** | P2：`/api/generate/*` 加 IP 速率限制（10 req/min/IP） | 0.5-1 天 | §2.2.10 缺少 API 限流 → GPU DoS |
| **E2** | P2：首次启动自动生成 `data/.csrf_secret`，启用 CSRF Cookie HMAC 签名 | 0.5 天 | §2.2.11 CSRF Token 无签名 → XSS 注入伪造 |
| **E3** | P2：`.pt` Persona 嵌入保存时写入 metadata `origin: TTS_MultiModel v2.x` 哈希 | 1 天 | §3.2.8 嵌入批量导出 → 音色数据集被盗用倒卖 |
| **E4** | P3：History DB HMAC 链（每条记录 prev_hash） | 1-2 天 | §3.2.7 历史记录被任意编辑 → 纠纷时无法举证 |
| **E5** | P3：水印嵌入频段扩展到 8-20kHz + 回声隐藏辅助 + 公开验证端点 `/api/system/verify_watermark` | 2-3 天 | §3.2.5 水印抗攻击（低通/重编码绕过） |

---

## 第四节：统一验收矩阵（执行后逐项打勾）

### 4.1 Top 7 行动项验收（主任务）

| # | 任务 | 子项 | 验收项 | 通过？(☐/☑) |
|---|------|------|--------|-------------|
| T1 | 水印接入 | (a) save_audio() 有水印调用 | generation.py:116 出现 `watermark_audio(` | ☐ |
| | | (b) _save_wav_compatible() 有水印调用 | generation.py:906 出现 `watermark_audio(` | ☐ |
| | | (c) 3 个引擎写盘点全覆盖 | grep `watermark_audio(` 命中 generation/dotstts/streaming 共 4 个文件 | ☐ |
| | | (d) source_id 使用代码常量 | 所有 `source_id=` 右侧为 `WATERMARK_SOURCE_ID`，非 config 变量 | ☐ |
| T2 | 魔术字节校验 | (a) 上传路由调用校验函数 | audio.py:480 `if not _validate_audio_content(` | ☐ |
| | | (b) 失败删文件 + HTTP 400 | if-body 包含 `os.remove` + 400 JSON 返回 | ☐ |
| T3 | API Auth 安全网 | (a) 拦截条件早于 create_app | SystemExit 行号 < create_app 行号 | ☐ |
| | | (b) 白名单 IPv6 全覆盖 | `_LOCAL_HOSTS` 同时包含 127.0.0.1、localhost、::1 | ☐ |
| T4 | VAPID 私钥 | (a) pre-commit 双 Hook 配置 | .pre-commit-config.yaml detect-private-key + Forbid PRIVATE KEY 均存在 | ☐ |
| | | (b) .env 无真实 PRIVATE KEY（**本节唯一待补**） | Select-String .env "PRIVATE KEY" 零匹配 | ☐ |
| T5 | 归属声明散布 | (a) NOTICE 文件存在 + 含 ReSerendipity GitHub URL | NOTICE 第 5 行可验证 | ☐ |
| | | (b) 3 个核心文件头 SPDX 头齐全 | app_server.py/engine_interface.py/cli.py 共 6 行 `# SPDX-` | ☐ |
| | | (c) /api/health/ping 返回 attribution 字段 | curl 返回值含该 key + 正确 value | ☐ |
| | | (d) CLI 含 © 横幅 | cli.py:1083 包含 `© ReSerendipity` | ☐ |
| T6 | SHA256 校验 | (a) SHA256SUMS.models 存在 | docs/ 下能找到 | ☐ |
| | | (b) 2 个下载脚本均调用 verify | download_dotstts.py / download_indextts2.py 各有 1 处匹配 | ☐ |
| T7 | Release 签名 | (a) GPG 条件签名步骤存在 | release-please.yml 含 `GPG sign SHA256SUMS` step 名 | ☐ |
| | | (b) 4 件套（2 清单 + 2 .sig）上传到 Release Assets | grep `SHA256SUMS.sig` 至少 2 处匹配 | ☐ |

**Top 7 通过率标准**：23 个检查子项中 ≥ 22 项 ☑（仅 T1-T3/T5-T7 子项全通过 + T4-a 通过 + T4-b 待完成即可开始"继续推进"，T4-b 必须在 24 小时内补完）。

### 4.2 阶段 2 静态验证

| 步骤 | 通过标准（Exit Code） | 实际值 | 通过？ |
|------|---------------------|--------|-------|
| 2-1 Ruff Lint | = 0 | | ☐ |
| 2-2 Ruff Format --check | = 0 | | ☐ |
| 2-3 sync_requirements --check | = 0 | | ☐ |
| 2-4 check_3engine_compat | = 0 | | ☐ |
| 2-5 pytest + --cov-fail-under=20 | = 0 | | ☐ |

### 4.3 阶段 3 动态安全回归

| 用例 | 通过标准 | 实际情况 | 通过？ |
|------|---------|---------|-------|
| R1 水印日志 | 至少 1 行 `水印嵌入完成: source=tts-multimodel` | | ☐ |
| R2 伪造文件拦截 | 返回 400，msg 含 "content does not match" | | ☐ |
| R3 安全网阻断 | 非本地 + 无认证启动时 2s 内退出，日志含"安全网拦截" | | ☐ |
| R3b 正常启动恢复 | 恢复 127.0.0.1 后正常启动 | | ☐ |
| R4 API 归属字段 | curl 返回 JSON 存在 attribution 字段且内容正确 | | ☐ |
| R5 CLI 归属横幅 | CLI stderr 第一行含 © ReSerendipity | | ☐ |

---

## 第五节：下次检查目标（Goal v3.0 入口条件）

完成以下**全部**条件后，可在 3-7 天内启动"进阶增强 v3.0"评估：

| # | 入口条件 | 验证方法 |
|---|---------|---------|
| 1 | 本报告 §4.1 Top 7 验收矩阵 23/23 全 ☑ | 本报告全打勾截图或真实执行记录 |
| 2 | §4.2 阶段 2 静态验证 5/5 全部 Exit 0 | 命令执行汇总输出（复制粘贴即可） |
| 3 | §4.3 阶段 3 动态回归 6/6 用例通过 | 浏览器 + 命令行 PoC 证据（截图或日志片段） |
| 4 | 已根据第四节 §5 "E1-E5" 选择至少 2 个进阶任务排期 | 写入 Jira/Issue/项目任务看板链接 |

---

> **文档结束标志**：本 v2.0 指示报告的完成定义 = **第 1 节 T4-b 执行完毕 + 第四节所有三个验收矩阵的"通过？"列完成真实打勾（非纸面空打勾，需有对应命令输出或截图支撑）**。
