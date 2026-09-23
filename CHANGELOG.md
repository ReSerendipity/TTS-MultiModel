# Changelog

> 说明：以下历史条目中提及的 `AGENTS.md` 为**本地维护、不随仓库分发**的资产（`.gitignore` 已忽略）；条目仅为变更发生时的历史记录，clone 读者无需在仓库中查找该文件。

## [2.2.5](https://github.com/ReSerendipity/TTS_MultiModel/compare/v2.2.4...v2.2.5) (2026-09-22)


### Bug Fixes

* **ci:** DCO 按"提交作者"豁免自动化，否则自动发版路径结构上不可用 ([d267d37](https://github.com/ReSerendipity/TTS_MultiModel/commit/d267d3751d524b38191e6786109a1b9f623833ea))
* **ci:** DCO 按提交作者豁免自动化 —— 否则自动发版路径结构上不可用 ([9eec594](https://github.com/ReSerendipity/TTS_MultiModel/commit/9eec5941a4a6f73eb395c53d8f9cb56fb3a34633))


### Documentation

* **DOD:** 记 v2.2.4 发版前这一轮的验收，并写清没覆盖的格子 ([d051529](https://github.com/ReSerendipity/TTS_MultiModel/commit/d051529978328d28993be1361f00bd2434b395b8))
* **DOD:** 记 v2.2.4 发版前这一轮验收，并写明没重跑的格子 ([c54b474](https://github.com/ReSerendipity/TTS_MultiModel/commit/c54b4749075ba5b37156581b7a7ba406a2800f22))

## [Unreleased]

### Bug Fixes

* **deploy:** k8s 清单写的镜像名与工作流真正推的不是一个：`ghcr.io/.../tts-multimodel`（连字符）从未存在，真实的是 `tts_multimodel`（下划线，`${{ github.repository }}` 整体小写、`_` 原样保留）。照着 `deploy/kubernetes/deployment.yaml` apply 的两个容器（含 init 容器）必然拉不到镜像；`docs/rollback_sop.md` 那条回滚命令除了名字还多带了个 `v` 前缀 —— 工作流的 semver 图案（`type=semver,pattern={{version}}`）产出的形状是 `2.2.0`，不带 `v`。名字与 tag 形状现在由 `tests/test_image_name_consistency.py` 从工作流推导钉住（原来那处正则把名字写死了，所以只核得出版本、核不出名字）。

## [2.2.4] - 2026-09-22

两处用户可见修复，其余是**发布与 CI 链路的收口**（这个版本存在的理由之一：v2.2.3 之后 main 上
攒了一批"门禁本身错了"的修复，需要一次可安装的锚点）。

### Bug Fixes

* **engines:** 12GB 显存档**直切引擎必 503**（#84）—— 切换路径先做显存预检再清场，于是预检永远
  看见"上一个引擎还占着"。现在先清场再预检，报错文案给可操作下一步（哪个引擎占了多少、要释放到多少）。
* **security:** persona / 历史卡片把数据拼进 `onclick="…"` 属性（#99）—— 名字里带 `'` 就能逃出
  属性上下文。改为事件闭包接线（数据进 JS 闭包，不进 HTML 属性），信息面板的**同源裸插值**一并收口。

### CI / 发布链路（不改运行时行为，但改"能不能信这条流水线"）

* **性能门禁**（issue #118）判据从 `--benchmark-compare-fail=mean:20%` 换成
  **`median` 单向 > 50%**，逐条打印对比表而不是整段重定向到 step summary；同代码在 runner 间的
  实测摆动是 **17%–80%**，而旧判据方向也不对（被它判成"回退"的五个例子**全是变快**）。
* 更深的第二层：门禁的"三级基线存储"**三条都不通** —— L1 `actions/cache` 在
  **10.23 GB / 配额 10 GB** 下两小时就被驱逐（main 存的基线，同一天 PR 侧 `Cache not found`）、
  L2 那两步的 `if startsWith(github.ref,'refs/tags/')` 在现有触发器下**永不成立**（三个已发布
  Release 的资产里确实没有基线文件）、L3 `benchmarks/baseline.json` 是 **0 条占位且无人读**。
  现在对比按 L1 → L3 退让、空占位单独 `::warning::`（不把"没数据"过成"通过"）、读取器同时认
  storage 顶层与 `{"benchmark":{…}}` 两种 JSON 形状，L3 用 main 真 run 的 artifact 填了 5 条 median。
* **release-please 的 `extra-files`** 第一次在真 release PR 上被验：`type: yaml` 不是"改一个字段"，
  而是 parse 后整份重排 —— `config.yaml` 被改成 **160 增 / 160 删、注释行 96 → 0**。已摘掉该条目
  （`config.yaml` 归手工同步），并加闸：类型只允许 `json|toml|generic`、逐条走 jsonpath 确认
  "今天确实命中"、以及拦"RP 抬了 `$.version` 却没抬同文件里的 `changelog`/`release_date`"。
* 记录一条会咬人的机制：**release PR 上看不到任何 CI 检查**（GitHub 固定行为：`GITHUB_TOKEN`
  产生的提交不再级联触发 workflow）。所以手工同步位不会红在 release PR 上，只会红在发布之后的
  main 上 —— 合 release PR 前必须本地跑版本位一致性。v2.2.4 因此走手工路径（#120 关闭）。
* **§7b 依赖复算**跑完 ①②③：候选锁与真锁**不同源**（97 vs 92 条、一致 74、16 条版本变、
  7 条 huggingface-hub 的 extras 链来历未定、2 条新增）。`transformers` 复算给 4.52.4 会让
  引擎血统闸红，`protobuf` 直接给到 7.36.2 但 gencode 兼容要到 import 期才暴露。
  **候选锁未采纳**，真锁继续手工钉；④ 的 GB 级真装仍待验收。
* 治理文件：`.gitattributes` 给 `/.gitattributes`、`/.mailmap`、`Dockerfile` 锁 LF；
  `.mailmap` 补上此前漏掉的 `TTS MultiModel Dev` 两个变体（**249 个提交**，shortlog 上原本
  多出第三个"贡献者"）；`CODEOWNERS` 指向真实路径（注意：`require_code_owner_reviews` 未开启，
  它目前**拦不住任何合并**）。

### 版本语义

按约定式提交算也是 patch（`fix:` / `docs:` / `ci:` / `chore:`，无 `feat:`）。
`.release-please-manifest.json` 随本次一起抬到 2.2.4，避免 release-please 在下一条 release PR
上把版本号再往抬一格（手工与自动两条路互踩，见 `docs/release-governance.md` §1）。

### 已知未覆盖（比 v2.2.3 多两条）

* 资产仍是 **wheel + sdist + SHA256SUMS**：不含 26 GB 便携分卷（core/torch/model）与桌面增量包，
  `Setup.exe` 的**真机安装验收**也没做过（该链路无任何 workflow 覆盖）。
* `ghcr.io/.../tts-multimodel:2.2.4` 是否真的产出，本机无从核对：匿名拉 manifest 得 403、
  `gh api packages` 得 404（token 无 `read:packages`）。镜像由 `docker-publish.yml` 在
  `v*` tag push 时按 `type=semver` 产出，且被 Trivy HIGH/CRITICAL 阻断逻辑把关。
* §7b 的 ④（复算锁真装 + 三引擎复验）未做，#89/#90/#91 三条 dependabot PR 因此仍挂着。
* 上面那条"合 release PR 前必须本地跑版本位闸"目前只是**文档 + 手工动作**，不是机器闸；
  要变机器闸需要一个能对 release 分支 head SHA 报 commit status 的作业（权限决策）。

## [2.2.3] - 2026-09-22

补丁版，只为把一处**随包分发缺口**送进可安装的产物：它修在 main 上，但晚于 v2.2.2 的 tag，
所以 v2.2.2 的 wheel 里仍然带着这个缺陷。

### Security

* **integrity:** wheel 此前**没把完整性自检三件套打进包**（`integrity_manifest.json`、其
  `Ed25519` 签名、验签公钥），而 `config.yaml` 默认 `security.integrity_selfcheck.enforce: true`，
  且"清单不存在"分支只 `logger.info("跳过自检")` 就返回 → **纯 `pip install` 的部署路径上
  P0 完整性保护一条都没执行，配置却声称它在强制运行**（Docker 与便携包另外拷了源码树，
  所以容器启动探测一直是绿的，把这个缺口遮住了）。现在三件随包走，且 enforce 开着却没清单
  → `RuntimeError` 拒绝启动并给三条出路；非强制模式保持原跳过语义。验收落在**产物**而非声明：
  包内条目 1062 → 1065，并把 wheel 解到临时目录真跑一遍正负两向。
  守卫 `tests/test_integrity_selfcheck_packaging.py` + CI 的 `Build (sdist/wheel)` 新增产物核对步骤。

### Bug Fixes

* **ci:** `release-please.yml` 的三处静默失效已修（`skip-github-pull-request: true` 而仓库从无
  release PR → 每次 main push 都 `found 0 possible releases` 后成功；5 个 v4 不认的入参被整段忽略；
  job 从未声明 `outputs:` → 挂在它下面的 `build-release`（sdist/wheel + SHA256SUMS）永远不跑）。
  修好后它当场自动开出了下一条 release PR，故补上 `release-please-config.json` 与
  `.release-please-manifest.json`，并加"什么都没发生就硬失败/写进 job 摘要"的自证。
* **version:** 版本位一致性守卫（#113 引入）补两处：漏核了**安装器内嵌**的
  `scripts/installer/version.json`（`setup.nsi` 的 `File "version.json"` 取的就是它，且没有脚本
  会重新生成它）；另撤掉一条我自己写反的断言 —— `minimum_shell_version` 是**下界**，
  强令它等于当前版本等于每次发版把上一版壳判死，改为只要求"不高于本次版本"。

### 已知未覆盖（同 v2.2.2 口径，未变）

桌面安装包链路与便携分卷仍无 workflow 覆盖；GPU 冒烟因无注册 runner 在 CI 上恒为 skipped；
锁集的全新 venv 真装复验待执行。

## [2.2.2] - 2026-09-22

<!-- 2026-09-16 那批内容原挂在「未发布」的 v2.2.2 标题下（tag 曾建后撤销）；2026-09-22 正式发出，
     并追加「发布前置回归」一节记录 09-21/22 这一批。资产范围见本节末尾的说明。 -->

### Features

* **desktop:** Tauri 桌面壳（`desktop/src-tauri`）：完整性自检签名 + enforce、水印密钥管理、启动契约（`runtime\python.exe` + `start_portable.py`，`--port` 由壳指定）（对应《桌面分发与安全加固-20260910》P1-2）
* **dist:** 便携分卷打包（core/torch/model 三组件、1900MB 7z 原生分卷、SHA256SUMS 全覆盖 + 回读校验）、NSIS 安装器（`scripts/installer`，kill 子进程 + 许可页）、增量更新（`make_shell_update.ps1` 生成 zip + `shell-update.json` 扁平契约）、发布门禁五步（`release_gate.ps1`）与完整性逐环节诊断（`diag_integrity.py`）（对应 P1-3/P2-1）
* **desktop:** 主界面窗口控制适配——无边框壳导航到后端页面后由桥接注入自绘标题栏（拖拽/双击最大化/最小化/关闭，`window_start_dragging`/`window_is_maximized`/`window_request_close` 三新命令与权限）；splash 补最大化按钮；关闭语义与系统 X 一致（尊重 `close_to_tray`）（GOTCHAS #104）

### Bug Fixes

* **dist:** 修复 requirements-lock.txt / requirements-small.txt 与 .venv 实测的版本漂移脱节（antlr4/pydantic/pydantic-core/mpmath/protobuf/transformers/tokenizers 按实测回退），解决全新 WinPython 上 `pip install -r requirements-small.txt` 连续 ResolutionImpossible（GOTCHAS #102）
* **dist:** 修复 `release_tauri.ps1` 单卷产物 bug：`$volumes` 非数组致 `manifest.json` 的 `volume_count: null`（改为 `@()` 强制数组）；`upload-list.txt` 引用不存在的 `unpack_portable_bundle.ps1`（改回实际分发的 `unpack_desktop.ps1`）
* **dist:** 补全 TTS 桌面解包器 `scripts/unpack_desktop.ps1`（此前缺失致分卷 `unpack_helper` 悬空；移植自 SeedVR2，适配根级 `runtime\`/`TTSMultiModel.exe` 布局，UTF-8 BOM）
* **dist:** NSIS 安装器正式发布形态落地——`license.txt` 升级为「中文安装须知与用户条款（合法使用承诺/隐私声明/第三方模型商用限制/免责）+ Apache-2.0 全文」；安装前告知弹窗（磁盘/离线/AI 标识义务，静默自动确认）；修复 `assemble_installer_data.ps1` 顶层文件被 `Copy-TTSMultiModelTree` 打包成同名目录的布局错误（GOTCHAS #103）+ 安装器嵌套布局防御断言；`TTSMultiModel-Setup-v2.2.2.exe` 端到端静默安装→布局断言→VoxCPM2 引擎就绪→卸载复验全过

### Security

* **dist:** 发布物剔除本机泄漏与开发遗留——`app/cert.pem`/`app/key.pem`（本机 HTTPS 私钥曾随包分发！）与 `start_ui_test.py`/`general_settings.json`/`SHA256SUMS.known-good`/`.server_port`/`tts_test/`/egg-info 全量进排除清单，`DeniedLeafNames` 门禁补 cert/key（进包即构建失败）；发布根目录剔除 CHANGELOG/SECURITY/pyproject/requirements-lock（GOTCHAS #104）
* **integrity:** 核心模块完整性自检 Ed25519 签名 + `enforce` 阻断（P0）；水印密钥从配置文件迁出为 env/`data/.watermark_key`（P0）；便携包清单重算/重签链路（`generate_integrity_manifest.py --app-dir` + EOF 尾换行规范化）、分发负向断言（无密钥/本机路径残留）、篡改模拟门禁（P1-3/P2-1）

### Bug Fixes

* **ci:** 修复 `scripts/check_config_refs.py` 配置门禁红灯——①为 `config.yaml` 的 `watermark:` 段补建 `WatermarkConfig` 模型并挂到 `AppConfig`（`watermark.py` 长期访问未建模字段）；②把此前「只声明未消费」的 `security.training_data_ttl_days` 真正接线（新增 `cleanup_expired_training_data`，随 lifespan 周期清理与关闭清理执行）
* **docker:** runtime 阶段补装 `python3.12-venv`（Ubuntu/deadsnakes 拆分包，`ensurepip` 由它提供），修复 `python3.12 -m ensurepip --upgrade` 报 `No module named ensurepip` 导致的镜像构建失败（与 builder 阶段对齐）
* **ci:** 修复 `scripts/check_engine_specs.py` 引擎规格三向门禁在 CI 结构性不可通过——权重目录 `/model/` 已列入 `.gitignore`（外部产物、运行时卷挂载），门禁却强制要求其存在；现仓库未附带权重时磁盘存在性降级为 WARN，仅当 `model/` 已存在时才 FAIL（该门禁此前被上一道 config 门禁遮蔽，从未在 CI 跑过）
* **docker:** 修复 `docker-compose.yml` 中 `deploy.resources.limits.devices` 非法字段（Compose 规范 `limits` 仅接受 `cpus`/`memory`/`pids`，GPU 属 `reservations.devices`），该字段使 Docker Smoke 的 Compose 校验失败
* **security:** 重新生成并重签 `app/integrated_app/security/integrity_manifest.json`，使核心模块完整性清单与本批代码变更一致（否则 enforce 模式启动被拒，E2E 红）
* **ci:** 修复覆盖率预算门禁在 CI 结构性假红——`tests/training/*` 的 `skipif` 依赖 `datasets/einops/argbind/soundfile`（属 `pyproject.toml [training]` extra），而 CI 只装 `-r requirements.txt` → 训练测试整体跳过 → `training/{data,packers,state}.py` 覆盖率 0%，12 个矩阵项全部 `Check coverage budget` 失败；现三个平台的依赖安装步骤补装该 extra

### Bug Fixes

* **launcher:** 便携钉装自洽修复（真实构建暴露）——全新 WinPython 3.12.10.1 上 `pip install -r requirements-small.txt` 报 ResolutionImpossible，9 项版本对齐 .venv 实测（antlr4 4.9.3 / pydantic-core 2.46.4 / mpmath 1.3.0 / tokenizers 0.21.0 + transformers 4.52.1 / huggingface-hub 0.36.2 / protobuf 3.19.6 / fsspec 2026.6.0 / uvicorn 0.52.4），移除已不引用的 tensorboardx 钉版；92 项 dry-run 全解
* **dist:** 清理 WinPython 自带脚本 shebang 本机路径残留——`Scripts/jp.py` 首行 `#!` 硬编码构建机临时路径，被门禁 ③ no-local-path-residue 拦下；build 脚本离线验证后自动重写为 `#!python.exe`
* **dist:** 发布门禁补 ⑤ 冒烟启动——便携包解包后用包内 WPy64 python 跑 `diag_integrity.py --enforce` 自检（断言 `selfcheck=True`/`VERIFY=True`），将完整性 enforce 闭环到分发产物；④ 篡改模拟前清理 `installed`/`out-bundle` 释放磁盘（峰值 116GB→58GB）

### Chore

* **dist:** 便携依赖钉装（`launcher/requirements-small.txt` 93 项 + `torch==2.13.0+cu132` 系列钉版，`sync_requirements.py --check-small` 校验）（P1-1）

### 发布前置回归（2026-09-21/22，随 v2.2.2 发出）

* **deps（P0）:** `einops` 提为**核心依赖** —— vendored VoxCPM 在模块顶层
  `from einops import rearrange`，而它原先只出现在 `training` extra 里，`funasr`/`modelscope`
  又只在 **extras** 声明它：任何按 `requirements.txt` / `[project].dependencies` 装出来的干净环境
  （镜像、便携包）都起不来默认引擎 `tts-1`。由 docker-smoke 新增的"镜像内引擎导入探针"第一次真跑抓到
  （run 35618578940）。同形状把 `addict` 补进两份钉版集；新增守卫 D5「核心声明必须有钉版」。
* **docker:** 镜像构建间歇性失败的真因是 `apt-get update` 对「某个索引没抓下来」只打 `W:` 并**返回 0**，
  两行之后才炸成 `E: Unable to locate package python3.12`。两段 RUN 都改成「PPA 索引里真查得到才继续」，
  取不到则重试 5 轮后硬停并给可操作原因。先前记成「`bash-builtins`/man-db 的确定性故障」是**误判**，
  已被探针数据与绿色 run 双重证伪（`docs/DOD.md` 相应段落已更正）。
* **security:** CSRF 密钥取不出来时**拒绝启动**（原为 warning 后以空密钥继续挂 `CSRFMiddleware`，
  等于静默关掉这道防护）；密钥改 `0o600` 落盘、既有宽权限文件收紧；CodeQL 既有告警 #1
  （明文存储密钥）走「真加固 + sink 行带理由抑制」，不整条静音。
* **engine:** IndexTTS 推理按引擎名加 `threading.RLock`。真因是**预热绕过串行队列、与用户请求并发**
  （不是先前记的「第二次引擎切换之后」），device-side assert 会毒化整个 CUDA context 使同进程后续合成全废；
  真机并发条件下字节数与串行一致（2.5 = 336,642 B / 2.0 = 239,674 B）。
* **api:** OpenAI 口 `tts-1-hd` 不再必然 500 —— 该口没有参考音频通道，改回 400 并指明该走哪个端点。
* **ci:** 修两处「永远绿的假信号」：`gpu-smoke.yml` 的 job 因缺 secret + 零注册 runner 一直 `skipped`
  而 run 顶层 success（现发 warning 并写进 step summary）；镜像内的引擎模块此前**从没被导入过**（新增探针）。
* **deps:** 依赖下界回到实测能跑的 `transformers>=4.52.1,<4.53` + `tokenizers>=0.21.0,<0.22`；
  4.52.x 的代价按 OSV 实测是 **16 个公告**（其中 8 条上游根本没有修复版本），逐条写可达性判据与理由后
  在 pip-audit / Trivy 里绑由头豁免；20 条 Dependabot 告警同样逐条 dismiss
  （台账见 `docs/SECURITY_DEPENDABOT_TRIAGE.md`）。
* **security:** CodeQL 存量分诊台账救回并按 09-21 实测刷新：open **110 → 76**、critical **1 → 0**，
  34 条差额逐条对上账（`docs/SECURITY_CODEQL_TRIAGE.md` §6）。

> **版本口径如实记一句**：本批含 **43 个 `feat:` 提交**却仍标 `2.2.2`（按 SemVer 应为 `2.3.0`）。
> 这是所有者的显式决定 —— 本段内容与 2026-09-16 那次被撤销的 v2.2.2 tag 属同一批，沿用该号。
> 另：仓库只有 1 个 Actions secret（`MANIFEST_SIGNING_KEY_B64`），`GPG_PRIVATE_KEY` 缺席 →
> `gpg-signed-release.yml` 只会 notice 跳过，资产不做分离签名；本次 Release 附源码包 + wheel +
> SHA256SUMS，**不含** 26 GB 便携分卷与增量包（那部分资产未获授权，命令见 `docs/release-governance.md` §2）。

## [2.2.1](https://github.com/ReSerendipity/TTS_MultiModel/compare/v2.2.0...v2.2.1) (2026-08-22)


### Bug Fixes

* **tests:** 修复 EngineRegistry 导入错误 ([82f6ae5](https://github.com/ReSerendipity/TTS_MultiModel/commit/82f6ae5a37a1623fcbd864e9d8faf98a1ce0f624))


### Documentation

* 顶部补齐 CI 徽章，移除底部重复徽章 ([1205d8a](https://github.com/ReSerendipity/TTS_MultiModel/commit/1205d8a76c9f68d0653a0ddee9a847ffeb8893a2))

### Chore

* **working-tree batch (uncommitted):** ruff 终检 11→0（批次内 23 文件全绿）：修复 `lora.py` F821（`LoRAMeta` 新增字段误读 `meta_dict`→`raw_meta`）、`training.py` SIM105×2、`clean_launch.py` UP009/UP015；清理 `metrics.py` 未用导入（F401）；安全/后端/容器化评估整改批次收尾，AGENTS.md 自进化同步至 v1.19（见 `docs/project/KNOWN_GOTCHAS.md` #41）

## [2.2.0](https://github.com/ReSerendipity/TTS_MultiModel/compare/v2.1.0...v2.2.0) (2026-08-21)


### Features

* add GitHub Pages online demo (pure frontend simulation) ([dad34bc](https://github.com/ReSerendipity/TTS_MultiModel/commit/dad34bcb2109be7e5936f5e58a50e01bf69f849d))
* **compliance:** add script workshop legal warning (5 locales) - prohibit synthesizing protected voices per IndexTTS DISCLAIMER ([4ae3cbd](https://github.com/ReSerendipity/TTS_MultiModel/commit/4ae3cbd1429db235701f37516240a0f8aca0e91c))
* full-feature demo v2 - 15 tabs/command palette/persona/history ([fef46c1](https://github.com/ReSerendipity/TTS_MultiModel/commit/fef46c13ff7826680dde2a5cfc3d767a18b5d1f7))
* **logging:** 完善日志机制 - 统一格式(PID/TID/模块位置/request_id) + 环境变量覆盖 + 双通道输出 ([9097178](https://github.com/ReSerendipity/TTS_MultiModel/commit/9097178276570fc9662eee3553dcb75ae0e4c54b))
* **security:** complete security hardening based on assessment report ([f623304](https://github.com/ReSerendipity/TTS_MultiModel/commit/f62330493b9732bdd17ac5387917533d00ea3969))
* **tests:** 安全测试补盲与 M1 里程碑达成（最终轮次） ([b6b5e48](https://github.com/ReSerendipity/TTS_MultiModel/commit/b6b5e4872e3f547ea8df7d7319cd4072fd106140))
* **tests:** 新增安全测试覆盖与引擎协议合规性测试 (M1 里程碑) ([4a2c010](https://github.com/ReSerendipity/TTS_MultiModel/commit/4a2c010cf46d47fc33a4207e3ba6e25311fc2433))
* **text-processing:** G2P manager, text segmenter, content safety, prompt expander ([9f005b8](https://github.com/ReSerendipity/TTS_MultiModel/commit/9f005b8ec5a8acab7c412a47ad17900fd0963421))
* update integrated app features and clean up deprecated components ([c0728cd](https://github.com/ReSerendipity/TTS_MultiModel/commit/c0728cda58111a67fe6a0902d0e55a624a661e86))
* 添加性能监控脚本与计划文档 ([91b8ddd](https://github.com/ReSerendipity/TTS_MultiModel/commit/91b8ddde5dd9bb547b7bf4d0b72c9726280ed015))
* 累计提交历史任务成果 - IndexTTS2 引擎完善/多语言/文档/学习报告 ([36a94f2](https://github.com/ReSerendipity/TTS_MultiModel/commit/36a94f29cd3040c4489670e07c22f25762109ddf))
* 跨项目借鉴改造 — 安全/配置/断点续跑/模型共享/音频水印 ([11a0e57](https://github.com/ReSerendipity/TTS_MultiModel/commit/11a0e57ea5d887e272c24563ab84f9883945686c))
* 路线图落地 — 数字水印、spec 契约层、断点续跑恢复接线、前端冒烟 ([27e2a7d](https://github.com/ReSerendipity/TTS_MultiModel/commit/27e2a7dc0d236bd3cd4b350e315ae2ad478650d1))


### Bug Fixes

* .gitignore static/ 规则过宽导致前端资源未入库（CI 页面无 CSS/JS）；锚定根目录并补提交 static 39 个文件 ([fc9c484](https://github.com/ReSerendipity/TTS_MultiModel/commit/fc9c484899839cca12a138f60b9e4895ce44699e))
* app_server 补充 __main__ 入口（python -m 启动失败根因，E2E 服务器无法启动） ([611422c](https://github.com/ReSerendipity/TTS_MultiModel/commit/611422c3c8c7817e21a071f95c0f79ba8eb1b3ff))
* **build:** 移除 license classifier（PEP 639 与 license 表达式冲突，新版 setuptools 拒绝构建） ([aca3fc7](https://github.com/ReSerendipity/TTS_MultiModel/commit/aca3fc71a1e8fa5d374d3338a75f6140cd074ccb))
* check_local.py 移除未使用 import 并通过 black ([8834c15](https://github.com/ReSerendipity/TTS_MultiModel/commit/8834c15605db4853ac611a97537c1a58237152a5))
* **ci+tests:** 修复流水线、激活 smoke marker 并更新 AGENTS.md ([89bbcdd](https://github.com/ReSerendipity/TTS_MultiModel/commit/89bbcdd023b37e70dae76afe0af4b5a3352a2716))
* **ci:** pytest 加 180s 超时保护（pytest-timeout），防止测试挂起导致 job 卡 6 小时 ([e47fe7b](https://github.com/ReSerendipity/TTS_MultiModel/commit/e47fe7b05851f64b59550fe04e2a614a6d0f9d42))
* **ci:** release-please 改为仅手动触发（避免 push 时误报失败） ([2676fe2](https://github.com/ReSerendipity/TTS_MultiModel/commit/2676fe286e8e8ec9c855a5839d132a3c82e1212a))
* **e2e:** collapse_toggle 与 theme_toggle 测试前 dismiss onboarding overlay（CI 全新环境无 localStorage 缓存） ([87769a3](https://github.com/ReSerendipity/TTS_MultiModel/commit/87769a3166104bccc29e906563b9a5ee8e253aba))
* **e2e:** test_collapse_toggle 完整版 onboarding dismiss（等 2.2s 覆盖 setTimeout boot + 二扫 DOM） ([590d257](https://github.com/ReSerendipity/TTS_MultiModel/commit/590d2579d28f0ece14247c3ed20e2a4200ff81a2))
* **e2e:** 修正失效的 /tabs/ 路由为 /?tab=，修复 wait_for_function arguments 兼容性（Playwright 新版） ([d58a03f](https://github.com/ReSerendipity/TTS_MultiModel/commit/d58a03f8982da3655c8e32735effa640ac3233d9))
* **e2e:** 停止 JS 定时器 + 禁用 CSS 动画消除动画帧差异；临时重建 Linux baseline ([fa50990](https://github.com/ReSerendipity/TTS_MultiModel/commit/fa509906b6eb55a69033fc185570a9d8750f25dc))
* **e2e:** 冻结 Math.random 消除波形随机绘制导致的截图差异 ([ce84dca](https://github.com/ReSerendipity/TTS_MultiModel/commit/ce84dca77c18b48bd5088a675d7e7f90748657ce))
* **e2e:** 引擎 tab 切换用 JS click 绕过 model-tabs 容器指针拦截（headless 布局差异） ([5903281](https://github.com/ReSerendipity/TTS_MultiModel/commit/5903281f62917af3c3a6147fe365e07ba230d056))
* **e2e:** 截图测试点击折叠分组内 tab 前先展开；E2E job 超时 15→30 分钟 ([c89f2dc](https://github.com/ReSerendipity/TTS_MultiModel/commit/c89f2dc879465aff635052810093d64ad5862c80))
* **e2e:** 拦截远程字体 + 等待 fonts.ready 消除字体加载时序差异 ([c34afc3](https://github.com/ReSerendipity/TTS_MultiModel/commit/c34afc3b6fae8cb3b3c6cb76a48e6624caffec4f))
* **e2e:** 视觉回归测试统一稳定化（onboarding dismiss + 渲染等待）+ 更新全部 baseline ([ad2132f](https://github.com/ReSerendipity/TTS_MultiModel/commit/ad2132f8042c37d9abad80e2b66e997df1c31c95))
* **e2e:** 等待 htmx 异步内容加载完成再截图（消除跨 run 加载时序差异） ([7359fc0](https://github.com/ReSerendipity/TTS_MultiModel/commit/7359fc023ad0232f668ed5f06956c656135a8c92))
* hide watermark from user-visible surfaces (logs to debug, README, demo, agreement wording) ([b1acdc5](https://github.com/ReSerendipity/TTS_MultiModel/commit/b1acdc5ad2d7b6435ddeefc31d963a64d60110e0))
* remove local-only Chinese docs from remote; add gitignore rules ([56e7efc](https://github.com/ReSerendipity/TTS_MultiModel/commit/56e7efc48ba271acd7b444556126d80a76aae581))
* resolve ruff lint & format issues to pass CI (Lint job) ([8cde27a](https://github.com/ReSerendipity/TTS_MultiModel/commit/8cde27ac738b87b555cbb928b4d1c972128b3531))
* ruff import 排序（audio_watermark 拆分 import，修复 CI lint 失败） ([435c19f](https://github.com/ReSerendipity/TTS_MultiModel/commit/435c19fa62b1807f0d5fcab4f1a5d0943697dde8))
* SSE 测试线程改 daemon 防 pytest 挂死（根因：SSE 无限流线程永不退出）；矩阵排除 tests/e2e（Playwright 由独立 workflow 跑） ([0c37acb](https://github.com/ReSerendipity/TTS_MultiModel/commit/0c37acbd9d0338805365892c14d8d82bed51ba11))
* **test:** correct whitespace and length calculation in text segmenter crossfade, fix whitespace and empty text trim in G2P manager/prompt expander, align G2P is_available with design that supported language is always available on download ([15b3f64](https://github.com/ReSerendipity/TTS_MultiModel/commit/15b3f6443f4bb8df8098056f9e598512c34cccd7))
* **test:** pretrained_models 目录测试改为自动创建（目录被 gitignore，CI checkout 不含模型目录） ([2503f0f](https://github.com/ReSerendipity/TTS_MultiModel/commit/2503f0f3141c2314b95e61623343495362c16fab))
* **tests:** 修复测试反模式-PytestReturnNotNone/硬编码/残缺断言/吞没异常/视觉回归无对比, 删除废弃脚本 ([f0bcc63](https://github.com/ReSerendipity/TTS_MultiModel/commit/f0bcc63008721b98c5422c85a89596af5898d1e1))
* **tests:** 消除永真断言与零断言反模式并强化认证行为级测试 ([a982676](https://github.com/ReSerendipity/TTS_MultiModel/commit/a982676f4eb1da7677027b9e8e17d700aa375e8b))
* treat dots_tts as optional in compat check; skip playwright test when dep missing ([629a1bd](https://github.com/ReSerendipity/TTS_MultiModel/commit/629a1bd76996cd6f9ee5ac85bf52c8412fd74bfd))
* zh voice warning in script dubbing play paths ([0f82d8a](https://github.com/ReSerendipity/TTS_MultiModel/commit/0f82d8abcebd7ae9ab508641306c0c386de8da79))
* zh voice warning, full-text display in continue generation to match spoken text ([b7c9f0a](https://github.com/ReSerendipity/TTS_MultiModel/commit/b7c9f0a026b3be8493d33af17806a64c50c6c8a7))
* 修复 task_queue 协程泄漏并消除测试弃用警告 ([4f1f4e0](https://github.com/ReSerendipity/TTS_MultiModel/commit/4f1f4e0bb405d6dbd9f915c1206278d76db79c66))
* 补 psutil 依赖（health.py 顶层导入，CI 测试收集失败） ([d961d90](https://github.com/ReSerendipity/TTS_MultiModel/commit/d961d90230d33fe6809891d03c244743c07adf24))


### Reverts

* pyproject addopts 移除 --timeout（与 pytest-playwright 同名选项冲突致 visual gate 失败）；CI 命令里保留 --timeout=180 ([c2d347e](https://github.com/ReSerendipity/TTS_MultiModel/commit/c2d347e8c0e48bf544257d9ac392d200887347fb))


### Documentation

* add model download & verification examples ([0c4384f](https://github.com/ReSerendipity/TTS_MultiModel/commit/0c4384f533ed743f9a51f4cf03772b1517e14d23))
* add module responsibility boundaries (model_manager/registry/config/service_layer/optimizer); gitignore: unify template ([1b70a86](https://github.com/ReSerendipity/TTS_MultiModel/commit/1b70a86f0ea67faff5d6813b5629450149144ccb))
* **compliance:** add independent third-party declaration vs model owners (ByteDance Seed / Alibaba Tongyi / bilibili) ([499355b](https://github.com/ReSerendipity/TTS_MultiModel/commit/499355b556b21a0b268bca4ccf9f24d4744daf8a))
* **compliance:** rebrand subtitle, unify IndexTTS version naming, add third-party disclaimer to demo footer ([61fa452](https://github.com/ReSerendipity/TTS_MultiModel/commit/61fa452cbcf2e0fe2198cd0e52ea67dbf1f8525f))
* link MODEL_DOWNLOADS.md from README ([93bc422](https://github.com/ReSerendipity/TTS_MultiModel/commit/93bc4223a3c3e06f7d35a1ba256fccdec8cf5ce7))
* record watermark v2 known boundaries (1s-white-noise payload info-theoretic limit, SNR-crest coupling) with measured data and optional breakthrough paths ([fc2585f](https://github.com/ReSerendipity/TTS_MultiModel/commit/fc2585febd45cc7f9ea1609847bbaba700f05d74))
* remove dots.tts references and add VoxCPM source baseline ([c24ec79](https://github.com/ReSerendipity/TTS_MultiModel/commit/c24ec79ba7c1087a707ef49f967aceda1ecfb309))
* restore open-source essentials (LICENSE, NOTICE, USER_AGREEMENT, COC, SECURITY, upstream source declaration) ([dea71ee](https://github.com/ReSerendipity/TTS_MultiModel/commit/dea71ee78c4d83b9c86af860f53ebd24f530cc74))
* restore README, CI, demo, screenshots to remote; gitignore local-only content; restore pyproject readme ref ([2816758](https://github.com/ReSerendipity/TTS_MultiModel/commit/28167584e6e51d2aa0d223cb33deeb1c3930a3cc))
* restore README, CI, demo, screenshots to remote; restore pyproject; gitignore local-only ([4bbcdf8](https://github.com/ReSerendipity/TTS_MultiModel/commit/4bbcdf8555f081be411af7c0fa9c02b7b7a9cb4a))
* self-check pass, bump v1.7 ([ac8e7c1](https://github.com/ReSerendipity/TTS_MultiModel/commit/ac8e7c11da1c4ff0d234a579ffc291b25d4fecf0))
* trigger pages deploy ([f42a806](https://github.com/ReSerendipity/TTS_MultiModel/commit/f42a806e7a9c11ac4c843720ff70e132884d35d0))
* update README to include dots.tts (three-model support) and API/dirs ([c6084f8](https://github.com/ReSerendipity/TTS_MultiModel/commit/c6084f8abf31223ea920de8035d6cd011c5d3f37))
* 模型下载章节补充 HuggingFace/ModelScope 链接，新增社交预览图 ([19cb124](https://github.com/ReSerendipity/TTS_MultiModel/commit/19cb124bf246f355b6adf87e393dde261746e4a8))
* 界面预览只展示浅色截图，深色截图不再跟踪 ([2a7b926](https://github.com/ReSerendipity/TTS_MultiModel/commit/2a7b9268881f971f9d6b92e89db1f96c055f2ff7))
* 补全项目健康度评估报告全部缺失要素（perf目录+AGENTS.md+ARCHITECTURE.md+pre-commit） ([a5348c2](https://github.com/ReSerendipity/TTS_MultiModel/commit/a5348c2f5481f43752cef31c1dc7bafe0efa8247))
