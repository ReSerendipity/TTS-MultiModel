# 2026-09 Agent 工作记忆归档（TTS_MultiModel）

> 入库目的：把 `.workbuddy/`、`.workbuddy-ai/`、`.zcode/` 中即将清理的会话记忆里**仍有长期价值**的事实固化到仓库文档，原始文件随后删除。
> 合并口径：按环境/构建/门禁/测试/发版/坑点/决策主题重排；只保留可执行事实（命令、路径、参数、版本号、阈值、判据、结论），删除寒暄与纯过程流水。
> 每条事实后缀标注来源日期（MM-DD）；与现状冲突或已失效的条目显式写「已过时（日期）」及现状。原文无法确认的写「未核实」，不臆造。

## 1. 来源表

| 来源路径 | 日期 | 原始行数 | 本文保留了什么 |
|---|---|---:|---|
| `.workbuddy/memory/2026-09-17.md` | 2026-09-17 | 22 | CSRF 原生 fetch 修复定位与「浏览器 stale 页面」纠正；壳更新包重生成；旧多卷打包链路与 7z/运行时路径 |
| `.workbuddy/memory/2026-09-18.md` | 2026-09-18 | 102 | 安装版 runtime 缺 torch 事故；壳扁平布局解析修复；73/72 全量测试判据；磁盘与 Git 事故处置 |
| `.workbuddy/memory/2026-09-19.md` | 2026-09-19 | 49 | v2.2.2 草稿 Release 回滚；上传 422/2GB 限制；safe-delete 垫片事实与永久删除口径 |
| `.workbuddy-ai/memory/2026-09-14.md` | 2026-09-14 | 123 | 引擎接入口径；模型下载/许可；流式 404 与双渲染路径；Git/沙箱/代理坑 |
| `.workbuddy-ai/memory/2026-09-15.md` | 2026-09-15 | 19 | PR #65 合并、服务重启方式、健康检查真实路径、清理处置 |
| `.zcode/plans/plan-sess_161bfa58-7fe2-4878-8c7f-0283db4938a7.md` | 2026-09-30（文件 mtime，正文无日期） | 73 | v2.4.0 三阶段发版计划，改写成「已执行，保留为下版 runbook」 |

## 2. 环境与工具链

| 事实 | 判据 / 做法 | 来源 |
|---|---|---|
| 项目解释器 `.venv/Scripts/python.exe` | 9 月当时有效；**已过时（2026-11-03）**：`.venv` 是指向 `D:\_venvs\tts` 的目录联接，目标已不存在。现行唯一解析入口 `python scripts/run_python.py …`（见 `docs/SRE_RUNBOOK.md` §3.3） | （09-17）（09-18） |
| 当前受支持解释器 | 系统 `C:\Python312\python.exe` 3.12.10，已装 `funasr==1.4.16`；`.venv` 未重建（G4 待决） | 现状（11-03；`docs/SRE_RUNBOOK.md` §3.3） |
| 7z 可执行文件 | `C:\Users\Doro\Tools\7z-extra\7za.exe` 与 `C:\Users\Doro\Tools\7zr.exe`；打包脚本 `Find-TTSMultiModelSevenZip` 只搜 PATH + 固定目录，须把 `C:\Users\Doro\Tools\7z-extra` 加入 PATH 才会被发现 | （09-17） |
| 运行时与权重路径 | WPy64 runtime：`C:\Users\Doro\Downloads\TTSMultiModel\runtime\`；模型约 28GB：`C:\Users\Doro\Downloads\TTSMultiModel\model\` | （09-17） |
| 大文件复制速度 | 7z 流式 `-mx=0` 约 570MB/s；`robocopy /MT:64` 明显快于逐文件复制；torch 约 5000 个文件逐文件拷易被沙箱杀 | （09-18） |
| 沙箱写盘限制 | 向 workspace 外（如 Downloads）批量写会被 SIGTERM，需 `dangerouslyDisableSandbox`；工具启动的 server 不能跨调用存活（`nohup`/`Start-Process` 均被收割），跨调用测试须在单次命令内「启动+测试」（约 2min 上限） | （09-18） |
| 临时文件位置 | Write 工具建的 `C:\tmp` 在 Bash 沙箱不可见（`cp /c/tmp/...` 报 No such file）；跨工具临时文件放仓库内 gitignored 目录（如 `dist/`） | （09-19） |
| HTTP 代理 | 本机 `127.0.0.1:7923` 代理会让 `curl http://127.0.0.1:7869/...` 返 502，必须 `curl --noproxy '*'`；`-o /dev/null -w %{size_download}` 也可能误报 0，完整性核对用 `-o <文件>` + `cmp` | （09-14） |
| 日志与文档编码 | Windows 日志 GBK，`grep` 中文需先解码；用 bash heredoc 追加中文会按 CP936/GBK 落盘，中文写入一律走 Edit/Write 工具 | （09-14）（09-30） |
| 磁盘读数 | 沙箱 `rm`/Delete 只进 `C:\$Recycle.Bin`、不释放空间；`df -h` 在 Git Bash/MSYS 的 Windows 卷读数不可靠（曾恒定报 158G），真实可用空间用 Python `shutil.disk_usage()`（底层 `GetDiskFreeSpaceEx`） | （09-19） |
| safe-delete 垫片 | WorkBuddy 运行时在 `sitecustomize.py` 把 `os.remove`/`os.rmdir`/`shutil.rmtree`/`Path.unlink` 包装为「优先移入回收站」；`CODEBUDDY_SAFE_DELETE_ENABLED=0` 可关闭；回收站 API 偶发 `IFileOperation ... Some operations were aborted` 时垫片 fail-closed（文件留下、不降级真删） | （09-19） |
| 回收站占用量法 | `ctypes.windll.shell32.SHQueryRecycleBinW(c_wchar_p('C:\\'), byref(SHQUERYRBINFO))`，结构 `SHQUERYRBINFO{cbSize, i64Size, i64NumItems}`；本次实测 C: 回收站 33.39GB / 32 项 | （09-19） |
| 磁盘「删了再 cp」陷阱 | 删除不释放空间时，「删一份再 cp 一份」会吃两遍空间；7z 输出必须直接写最终目录，禁止先打 `_newvol` 再 `cp` | （09-18） |

## 3. 构建与打包

### 3.1 旧多卷安装包链路（v2.2.x）——**已过时（2026-10-31）**

现状：安装包已改为**单文件瘦包**（`TTSMultiModel-Setup-v2.9.0.exe` 约 395MB，不含 torch 运行时与模型权重），数据分卷退出 Release 分发范围；见 `docs/发布与分发-单文件安装包.md`。以下步骤保留为旧布局 runbook。

- 既定链路（09-17）：① `scripts/build_portable_bundle.ps1`（需 `-RuntimeDir`<WPy64>、`-TorchWheelDir`、`-ModelDir`）→ ② `scripts/assemble_installer_data.ps1`（7z `-v1900M` 分卷）→ ③ `scripts/assemble_release_staging.ps1` → ④ `makensis scripts/installer/setup.nsi`（安装器 exe）。
- 产物构成（09-18）：`TTSMultiModel-Setup-v2.2.2.exe`（4.3MB；两版实测 4,321,184B 与 4,317,541B）+ `TTSMultiModel-Data.7z.001..017`（17 卷，31～33.14GB）+ `SHA256SUMS.txt`（18 条，标准 `hash  name` 格式）+ `upload-list.txt`；单卷 1.99GB，刚好低于 GitHub 单资产 2GB 上限。
- 卷数与体量存在多组实测：14 卷 / 26.8GB / 68,404 文件（09-17 16:04–16:33 构建）、17 卷 / 33.14GB（09-18 重建）、17 卷 / 31G（09-18 最终入 `dist/installer-data`）；引用时以具体产物清单为准（差异原因未核实）。
- `makensis` 前置（09-18）：`scripts/installer/` 需放 `TTSMultiModel.exe`（新壳改名）、`version.json`、`pyproject.toml`、`7za.exe`；`-DSRC_7ZA` 经 Git Bash 会被 MSYS 转坏路径，直接把 `7za.exe` 放同目录用默认查找。
- NSIS 口径（09-30）：NSIS 3.10；`.nsi` 必须 UTF-8 带 BOM；Unicode 版无 `${IDCANCEL}` 常量（#103）。
- 壳更新包（09-17）：`powershell -ExecutionPolicy Bypass -File scripts/make_shell_update.ps1 -Version 2.2.2`，产物 `dist/shell-update/app-v2.2.2.zip`（2.1MB，暂存 273 文件/5.4MB）+ `.sha256` + `shell-update.json` + `SHA256SUMS.txt`；脚本用 Windows 内置 bsdtar 打 zip，不依赖 7z；包内不含 runtime/model/data/logs；`shell-update.json` 指向 GitHub Release 下载 URL。
- 壳 exe 只是启动器：`persona.html` 等运行期模板未编入二进制，只改模板/JS 无须重编壳（09-17）。
- 只改壳时数据卷不必重打（`setup.nsi` 注释口径，09-18）。
- 完整性自检判据（09-18）：`7za t` exit 0；能从包内提取 `runtime/Lib/site-packages/torch/version.py`；包内 `persona.html` 含 `X-CSRF-Token`×2、`getCsrfToken`×1、`method:'DELETE'`×1。
- runtime 必须按 `requirements-lock.txt` 装齐依赖（09-18）：安装实例缺 torch 及 64 个引擎依赖，`persona` 导入失败，**只挂载 34 条路由（正常 82 条）**；从权威 dev `.venv` 补 65 个包（torch 2.13.0+cu132 / torchaudio 2.11.0 / torchvision 0.28.0 / soundfile / whisper / wetext / cn2an / nltk / pyarrow / tensorboard / einx / unidic_lite 等）后 `torch.cuda.is_available()=True`、路由恢复 82 条，再重打 17 卷（`-mx=0`）。
- 同批修复（09-18）：`torchvision` 残缺副本（513K/1 项，完整应 27M/24 项，缺 `transforms`）；`indextts` 是 editable 安装（`_editable_impl_indextts.pth` → 开发机 `reference_repos/index-tts-main`），不随 site-packages 走，实体捆绑 25M/298 文件进 runtime；`seed` 透传回归（代码仍 `infer_kwargs["seed"]=int(seed)`，seed=42 必 400），改为引擎内 `random`/`numpy`/`torch.manual_seed` 消费，实测 seed=42 两次 MD5 一致。
- 磁盘清道夫顺序（09-30）：壳就位后 `cargo clean`；分卷验证通过后立即删 staging（释放 33GB 给安装）；删除一律 `mv` 改名确认后再永久删（防同前缀误删，例如 `rm -rf X` 连带 `X-new`）。
- 长进程构建需在沙箱外执行（09-30）。

### 3.2 桌面壳布局解析修复（09-18）

- 旧行为：安装版 `TTSMultiModel.exe` 启动后子进程回退到**编译期项目路径**（`C:\Users\Doro\TTS_MultiModel\.venv\Scripts\python.exe …\start_portable.py --port 12095`，cwd=项目目录）。根因：`desktop/src-tauri/src/python_process.rs::resolve_app_dir()` 步骤 1 只查 `exe_dir/app/start_portable.py`，而安装包把 `start_portable.py` 放安装根；步骤 2 回退 `env!("CARGO_MANIFEST_DIR")/../../`（编译机项目根恰好存在）。`resolve_runtime_dir` 同型回退 `app_dir/.venv`。
- 影响：开发机「碰巧能用」，无该项目的机器找不到 payload，**全量自包含安装包在壳层不自包含**（09-18）。
- 修复（方案 A，09-18）：新增纯函数 `resolve_app_dir_from_exe(exe)`，顺序为 `exe_dir/start_portable.py`（扁平布局 = installer-data 轨）→ `exe_dir/app/start_portable.py`（bundle 轨）→ 开发模式回退；配 3 个单测；`cargo test` 39 passed / 0 failed，clippy 零警告；release 壳产物 `desktop/src-tauri/target/release/tts-multimodel-desktop.exe`，入包前改名 `TTSMultiModel.exe`。
- 行为验证（09-18）：新壳放入 `C:/tmp/fakeflat`（扁平布局），壳日志输出 `应用目录: C:\tmp\fakeflat`（旧行为会回退项目根）。
- 安装版实测（09-18）：进程树为 `TTSMultiModel.exe → <install>\runtime\python.exe <install>\start_portable.py --port 13653`，cwd=`<install>`；使用安装目录自带 runtime。
- 动态端口（09-18）：双击 `TTSMultiModel.exe` 后后端监听动态端口（实测 12095，而 `config.yaml`/`.server_port` 仍写 7869）；查真实端口用 `netstat -ano | grep <python.exe pid>`；直接 `curl http://127.0.0.1:7869` 返 000 会被误判「没起来」。（现状未核实）

## 4. 发布、Git 与事故处置

### 4.1 GitHub Release 上传（09-18 / 09-19）

- Release **禁止同名资产**：`installer-data/SHA256SUMS.txt` 与 `shell-update/SHA256SUMS.txt` 同名导致 `HTTP 422` 并中止后续；解法是改名其一（本次 `shell-update-SHA256SUMS.txt`）（09-19）。
- `gh release upload` **不支持字节级断点续传**：每文件独立 HTTP 请求，中断则整文件重传；已落地资产保留，可任务级续传（只补未传文件）；单文件硬上限 2GB（09-19）。
- 大文件易在长链路失败：上次 4 个大分卷（`.014`–`.017`）未传成，重传时单独分批并逐批确认（09-19）。
- v2.2.2 草稿 Release 回滚（09-19）：`gh release delete v2.2.2 --yes` 删除草稿及 19 个资产，**保留 git tag `v2.2.2`**（仍指向 `70e3de3`，在 origin）；PR #77 保持开放；草稿从未发布。**已过时**：版本已推进至 v2.9.0（2026-10-27，见 `docs/release-governance.md` §1），PR/tag 具体状态未核实。
- 09-18 上传规模：23 个资产，Setup(4.3MB)+17 卷（各≤1.99GB）+`installer-data/SHA256SUMS`+shell-update 四件，约 33GB；上传需用户另行授权（发布动作）。

### 4.2 Git 仓库恢复（09-14 / 09-18 / 09-19）

| 坑 | 现象 | 可复用处置 | 来源 |
|---|---|---|---|
| 浅克隆坏索引 | `.git/shallow` 存在；索引残留 646 个 gc'd blob 伪暂存项；分支处于 unborn HEAD | `git read-tree --empty` → `git reset --mixed af8d3ba`（对齐 `origin/main`，工作树不动）→ `git update-ref HEAD af8d3ba`；无需全量 `--unshallow`（曾 SIGTERM） | （09-18）（09-19） |
| refs 松散引用写入缺陷 | `git branch` / `git branch -m` / `git update-ref` 对该仓 `refs/heads/**` 静默不落盘（exit 0 但 ref 不生成） | 唯一可靠做法：`mkdir -p .git/refs/heads/<path> && printf '%s\n' <完整40位SHA> > .git/refs/heads/<path>`；缩写 SHA 会被判 broken ref | （09-18）（09-19） |
| 带斜杠分支名 | `git checkout -b feat/xxx` 在沙箱静默写不出 `.git/refs/heads/feat/` 子目录，HEAD 指向不存在 ref | 改用平铺分支名，或 `git push <SHA>:refs/heads/<平铺名>` | （09-14） |
| 对象库损坏 | `git rebase --autostash` 内部 fetch 被 SIGTERM，`.pack` 只剩 `.idx`、`.git/refs/` 消失、commit 对象丢失 | 备份改动文件 → 取含完整 `.pack` 的 depth-1 克隆作 donor（verify-pack OK）→ 坏 `.git` 改名 `.git.broken` → `cp -a` donor `.git` → `git reset --hard <tip>` → 覆盖备份文件 → 重建提交（PR #65） | （09-14） |
| stash 事故 | `git stash push -- <path>` 被 SIGTERM 后 `.git/refs/` 消失、`.git/index` 被重写，所有 git 命令报 not a git repository | 从 `.git/logs/refs/heads/main` + `packed-refs` 取 SHA → `mkdir -p .git/refs/heads .git/refs/tags` → `git update-ref` → 备份 index → `git reset --mixed` → `git fsck --connectivity-only`；**对照实验用 `git worktree`，永不 stash 工作区** | （09-14） |
| mypy 基线误降 | managed Python 没装 mypy，`check_mypy_ratchet.py` 报「检查 0 个源文件 / 0 错误」并把 `configs/mypy_baseline.json` 降到 0 | `git checkout <base> -- configs/mypy_baseline.json` 还原 | （09-14） |
| 回收站还原 | `rm -rf dist/installer-data` 连带 `dist/installer-data-new` 一起回收 | 解析 `C:\$Recycle.Bin\<SID>\` 的 `$I<id>`（offset 24 起 UTF-16LE 原路径）配对 `$R<id>`，20 个文件 31G 全数还原；同卷 `shutil.move` 只改名不占额外空间 | （09-18） |
| 并行会话 | 仓库被切到 `fix/path-portability-20260918`（非 main），HEAD `374d9eb`，本会话 seed 修复与 2.2.2 版本提升丢失 | 按「一仓库同时只跑一个 fixer」暂停 commit，等用户确认后再继续 | （09-18） |

- 流式修复的落地链路（09-14 / 09-15）：提交 `cc57579`（12 文件，+1400/−742）→ 对象库损坏后重建为 `ee16159` → PR #65 → 合并后 `main`=`4924ec8`；`ee16159` 被合并提交取代，`streaming-fix-recover` 分支可删（09-15 已确认 ref 不存在）。

## 5. 门禁与测试

- 全量 pytest（09-14）：`1918 passed / 36 skipped / 0 failed`；ruff check+format 全绿；mypy 棘轮 103=103。
- 安装版全量测试矩阵（09-18）：首次 73/73 通过（Part1 基础设施/系统/安全/UI 50/50、Part2 引擎+生成+音色+seed 16/16、Part3 流式+多语言+错误处理 7/7）；壳修复后按新包重装复测 72/72（Part1 50/50、Part2 16/16、Part3 6/6，端口 13653）。测试脚本 `C:/tmp/fulltest.py`（分 part）；须 `cd` 到运行实例的数据根（项目目录）才能正确引用 `outputs/`。
- 安装版单点判据（09-18）：应用 5s 就绪、82 条路由全挂载、`health` 200；VoxCPM2 加载+合成成功（vram 6.1G / 12.4s）；音色保存→列表→删除全通过（无 CSRF→403、带 CSRF→200「音色已删除」）；`/api/audio/*` 200。
- 沙箱 safe-delete 批量守卫导致全量挂起（09-14）：`sitecustomize.py` 按每轮命令累计删除数，`CODEBUDDY_SAFE_DELETE_BULK_THRESHOLD=50` 超阈值即 `raise SystemExit(1)`；在应用事件循环线程内触发会杀死 portal 事件循环线程 → 主线程永久等待 future（挂起在 `tests/test_routes_htmx.py` 首个真上传文件的 `persona_save` 用例）；在主线程触发则干净退出但吞掉 summary（EXIT=1、无 `N passed`）。
- 当时可用绕过（09-14）：`pytest tests/ -q --ignore=tests/e2e -k "not persona_save" --timeout=90 --timeout-method=thread -p no:cacheprovider` → `1914 passed / 36 skipped / 0 failed，68.12s`；被摘掉的 11 个 `persona_save` 用例单文件跑 `17 passed`。**现状**：测试库写入已由 F6 的 `tests/conftest.py` 全局隔离 + 哨兵收口（见 `docs/runbooks/2026-11-02-f6-test-db-isolation.md`），该绕过仅作历史。
- pytest 输出重定向到文件是块缓冲，日志行数不增长不代表卡住，要用 `--timeout` 插桩定位（09-14）。
- mypy 棘轮 103→104 的真凶（09-14）：`024572e` 的 `routes/generate/engine_discovery.py::engine_info` 声明 `-> dict` 却返回 `JSONResponse`；只改标注为 `dict | JSONResponse` 会在**模块导入期**炸 `FastAPIError: Invalid args for response field`，正确修法是装饰器加 `response_model=None` + 诚实联合标注。
- 路由可达性判据（09-18）：判断路由是否挂载要用 `/openapi.json` 核对，**不能只看 404**——404 可能是路由未挂载（本次 persona 即如此，曾误判为「CSRF 通过」）。
- CSRF double-submit 契约（09-17）：真实 `create_app()` + Starlette TestClient 端到端复现——无 header → `403 CSRF_MISSING`；cookie + 匹配 `X-CSRF-Token` → 200（抵达路由）；不匹配 → `403 CSRF_INVALID`。只发 header 不带 cookie 会误判。
- `check_spec_refs` 幻影引用（09-14）：CHANGELOG 历史条目中的 `Scripts/jp.py` 被判为仓库幻影（实为 WinPython 分发包内脚本）；改述为「分发包内 jp.py 脚本」后退出码归 0。现状门禁读 stdout `SPEC_REFS STATE=`，审计器缺失时为 `STATE=SKIPPED verdict=not-audited`（本轮实测即此状态，见 §10）。
- `check_engine_specs` 常态（09-14）：`14 OK / 1 WARN / 2 FAIL`，2 FAIL 仅因 `model/Step-Audio-EditX`、`model/OpenVoice` 权重目录不存在——框架-only 分发、用户自取权重，与代码改动无关。
- 完整性清单覆盖边界（09-14）：`scripts/check_integrity_manifest_sync.py` 只覆盖 **16 个 Python 核心模块**（history_db/i18n/auth/middleware/security），不含静态 JS 与模板 → 改 `app_init.js`/模板无需重签清单。
- 前端守卫（09-14）：`test_fe_be_consistency.py` 原守卫 1 只扫 `hx-*`，漏掉 `fetch()`/内联 JS 的 URL 字面量；新增守卫 5 扫 `templates/` + `static/js/` 全部 `'/xxx'` 字面量，全量 100 个字面量 **0 失配**；守卫 6 校验每个含 SSE 解析器的模板都调用 `window.wireGenerationResult`；`tests/frontend/wire_generation_result.js` jsdom 10 项行为断言；`smoke.js` 覆盖 19 个模板 / 31 个内联脚本块语法检查（Jinja 占位符不能带引号，否则造假语法错误）。
- pre-commit 在沙箱的失败口径（09-14）：`core.hooksPath=.githooks`，`.githooks/pre-commit` 委托 `pre_commit run`，但沙箱无外网 → 拉取 ruff-pre-commit/gitleaks 等 hook 仓库 `git clone` 失败 → `exit 1`；绕过前须手动跑网络无关钩子自证（ruff、check_engine_compat/specs、training-isolation、declared-capabilities、integrity-manifest-sync、layout、spec_refs、OpenAPI 快照、前端守卫）。（该环境口径已由 AGENTS §4.3/§5.2 与本地 FIX_LOG 承接）

## 6. 服务运行与前端行为

- 模板热更与「仍 403」真因（09-17）：Starlette 1.6.0 的 `Jinja2Templates` **默认 `auto_reload=True`**（`app_server.py:868` 仅在 `TTS_DEBUG=1` 时再显式设 True），即后端每次请求都从磁盘重读模板，改 `persona.html` 后新 `GET /tab/persona` 即返回含修复后 JS 的页面，**无需重启后端**。真正原因是浏览器侧 stale 页面（旧 JS 发起 DELETE 不带 header）→ 正确处置是重载/硬刷新 persona 标签页；`routes/tabs.py:47` 的 templates 实例也接 `TTS_DEBUG`，在默认 True 时功能上等同 no-op。
- 模板路径（09-17）：目录是 `templates/tabs/`（**单 t**），不是 `ttabs`。
- 端口与健康检查（09-14 / 09-15）：本项目端口 7869（`config.yaml`）；7869 被占会自动后移；**7870 曾被 SeedVR2（视频修复 App，openapi title `SeedVR2 v1.5.8`）占用，与本项目无关**。健康检查真实路径是 `GET /api/health/ping` 与 `GET /api/engines/list`；裸 `/ping`、`/api/engines` 会 404；`/health/ready` 在未加载引擎时返 degraded 属预期。
- 重启服务的可用姿势（09-14）：`run_in_background=true` 且让 `python app/clean_launch.py` 成为该任务的**前台进程**（`exec python … >> log 2>&1`）；放 `nohup … &` 会在任务收尾时被连带杀掉（pid 秒没）。09-15 同法重启，13s 后 READY（pid 33848）。
- tab 片段取法（09-14）：`/tab/voice_design` GET 会 303 重定向到 `/?tab=voice_design`（SPA 壳），真正片段需浏览器带 `HX-Request: true` 的 htmx 请求；`curl` 拿片段返回 0 字节是交互怪象，不代表修复未生效。
- 生成结果有两条渲染路径（09-14）：htmx（`hx-post` → swap → `afterSwap`/`afterSettle`）与 SSE 流式（`fetch` + 手动 `innerHTML`，不派发 htmx 事件）。流式分支曾漏显示后处理区 `vc-pp-section`、漏回填 `vd-result-audio`，均无报错、纯静默；统一入口 `window.wireGenerationResult(rootEl)` 按 `<prefix>-result` / `<prefix>-pp-section` / `<prefix>-result-audio` 约定接线。
- 易踩命名（09-14）：`voice_clone.html` 的 `submitCloneStreaming` 中 `statusEl` 实际绑定 `#vc-result`（不是 `#vc-status`）；改前先看函数开头的 `getElementById`。
- 服务启动行为（09-14）：`auto_load_model: false` 时启动日志 `[lifespan] 自动加载已禁用，请通过界面手动加载模型`；引擎注册成功但未加载，`/health/ready` degraded +「无可用推理引擎」是预期，非 bug。
- 重建 staging 的沙箱绕过（09-14）：`scripts/assemble_release_staging.ps1` 第一步删整棵 staging 树会触发两层 safe-delete 守卫（bulk-guard throw + genie-trash 失败 fail-closed）；守卫只拦删除不拦复制 → 先 `cp -r staging _staging_bak` 备份，再 `robocopy app dist\tauri-release\staging\app /E /XD __pycache__ .pytest_cache data logs cache torch_compile_cache outputs /XF *.pyc …`；robocopy 按 mtime 跳过，对改动文件用 `Copy-Item -Force` 强覆盖，最后 `cmp` 校验。临时物用 `mv` 改名移出 `dist/`。

## 7. 模型分发与引擎接入（09-14）

- 接入口径：`app/integrated_app/engine_interface.py` 提供 `TTSEngine`/`ControllableTTSEngine` Protocol + 线程安全、懒导入的 `InMemoryEngineRegistry`；新增引擎 = 实现协议 + `register` + 可选薄路由模块。后续补了 `BaseTTSEngine`/`BaseControllableTTSEngine` 安全桩与 `get_engine_class`/`iter_registered_engines`/`engine_implements_controllable`（用 `hasattr` 探测，避开含 `lora_enabled` 非方法成员的 Protocol 的 `issubclass` 崩）。
- 引擎发现端点：`routes/generate/engine_discovery.py` 提供 `/api/engines`、`/api/engines/list`、`/api/engines/{id}/info`；原拟名 `generic.py` 与既有 `generic/` 包冲突被遮蔽，已改名（GOTCHAS #84）。
- 统一模型下载器 `scripts/download_models.py`：覆盖 7 模型；**HF 镜像 `hf-mirror.com` 优先、ModelScope 回退**（`speech_zipenhancer` 仅 ModelScope）；支持断点续传；SHA256 比对，清单无条目时退化为体积预检；OpenVoice 自动摆 `config.json`/`checkpoint.pth`。
- `docs/SHA256SUMS.models`：注释目录由 `pretrained_models/` 改为 `model/`；实测填入本地 5 模型共 **65 条**真实 SHA256；OpenVoice / Step-Audio-EditX 留模板；`scripts/verify_model_checksums.py` 目录清单补这两项，跑通 exit 0（09-14）。（现状条数未核实）
- 官方来源与许可（09-14）：VoxCPM2 → `openbmb/VoxCPM2`（HF）/`OpenBMB/VoxCPM2`（ModelScope），Apache-2.0 商用 OK；SenseVoiceSmall → `FunAudioLLM/SenseVoiceSmall`，许可待确认；`speech_zipenhancer` → `iic/speech_zipenhancer_ans_multiloss_16k_base`（仅 ModelScope）；IndexTTS-2.5 → `IndexTeam/IndexTTS-2.5`、IndexTTS-2.0 → `IndexTeam/IndexTTS-2`（bilibili 自定义许可）；OpenVoice `myshell-ai/OpenVoice`（MIT 代码）；Step-Audio-EditX `stepfun-ai/Step-Audio-EditX`（代码 Apache-2.0，权重许可未明示）。
- Apple Silicon 结论（09-14，模型级、非本地 fork）：VoxCPM2 官方支持 MPS/CPU（高置信）；OpenVoice Mac 社区部署多（中高）；IndexTTS 2.5/2.0 Mac 可跑但需关 DeepSpeed/CUDA kernel、开 FP16（中高）；Step-Audio-EditX 官方需求表写需 NVIDIA（中·偏 Nvidia）；SDXL/Flux 走 Core ML/MLX（高）；SeedVR2 上游明确 NVIDIA/ROCm/Apple MPS（高）；MiniMax H3 有社区 Apple Silicon 移植（中）。教训：本地 fork 砍了 MPS ≠ 模型不支持；「能跑」≠「跑得好」（MPS 慢、统一内存吃紧、算子静默回退 CPU）。

## 8. v2.4.0 三阶段发版计划——**已执行，保留为下版 runbook**

> 计划文件 mtime 2026-09-30；v2.4.0 已于 **2026-10-02** 发布（`AGENTS.md` §7 `RELEASE_PROFILE` 记载 v2.4.0 周期）。以下为原计划步骤，作为下版发版的检查清单保留；与实际/现状的差异列在 §8.5。

### 8.1 基线事实（09-30）
- 当时版本 2.3.0；存在待合并 release PR **#173「chore(main): release 2.4.0」**（MERGEABLE，`release-gate=FAILURE`，原因是 5 类人工版本位停在 2.3.0）；本地 main 领先 origin/main 1 个提交 `0dd7c56`，未推送。
- C 盘空闲 88GB：打包链路（staging ~33GB + 分卷 ~30GB）+ 安装（31GB）必须**串行清理**才够。
- `config.yaml auto_load_model: false` → HTTP 服务秒起，模型在 UI 按需加载（各模型 4.7–11GB，加载分钟级）。
- 安装器 = NSIS 壳 exe（~4MB）+ 7z 数据分卷（~18 卷，合计 ~30GB），两者同目录分发；历史 Release 资产只有 wheel/sdist/SHA256SUMS，安装包从未上传，大分卷「需单独点头」。
- 安装验证后按 SOP-22 附标准卸载并复验三清空。

### 8.2 阶段 0–1：预检与浏览器全流程模拟
- 阶段 0：确认 `core.hooksPath=.githooks`；push `0dd7c56` 到 main（admin 直推授权）→ pre-push 自动跑 `precheck.ps1` → `gh run watch` 盯 CI 到终态；push 后 release-please 自动刷新 #173。
- 启动 `python app/clean_launch.py`（后台；自动弹系统浏览器属预期；7869 被占自动递增，实际端口读 `.server_port`）；健康判据 `/api/health/ping` → 200。
- UI 矩阵：侧栏 18 个 tab 逐一点击渲染；明/暗主题；语言 `zh-CN→zh-TW→en→ja→ko` 五循环（无 raw key 漏译）；顶栏引擎徽标、迷你监控→健康面板。
- VoxCPM2 组：先加载模型（`/readyz` 200）；voice_design 纯文本生成 → voice_clone 用既有 persona（gf1 等）+ 勾选 `has_consent` → `ultimate_clone`/`script`/`prompt_continue` 各真实生成 1 条；验证播放器、SSE `/api/sse/events` 有事件、历史列表出现、「保存为音色」→ persona 出现 → 删除测试音色。
- 切 IndexTTS 2.5：`indextts2_clone` 真实生成 1 条 + emotion/duration 表单渲染；切 IndexTTS 2.0：`indextts20_clone` 真实生成 1 条 + emotion 表单。
- history 页列表、hide/show 往返、回收站只读（**跳过** `clear_all` 等破坏性按钮）；persona 页 6 个既有音色列表/试听；settings 页全卡片渲染、改安全项（auto_play/notifications/seed）→保存→回读→`/api/system/settings/reset` 还原；help 页版本号一致。
- API-only 降级路径：浏览器 fetch `POST /api/generate/voicebox/convert`、`POST /api/generate/step-audio-editx/edit`（模型未装）→ 断言 **503 优雅片段而非 500**。
- 全程跳过：`/api/system/shutdown`、`/api/history/clear_all`、训练 start/stop（training/log 只读）。
- 每步截图留档 `%TEMP%/tts_ui_shots/`；发现 bug → 修复+补测试 → pytest → precheck → conventional commit（卷入 2.4.0）。
- 收尾前单独跑一次 `pytest tests/ -q --ignore=tests/e2e --cov=app/integrated_app --cov-fail-under=45`（SOP-25：`precheck -Full` 的覆盖率步在本机可能不真正执行）；结束关服务、确认 `.server_port` 清理。

### 8.3 阶段 2：版本位同步与 NSIS 打包
- 版本位补到 2.4.0（release 分支一次提交，暂不合并）：`config.yaml` version（驱动 `?v=`）、`scripts/installer/setup.nsi` 三处（OutFile/APP_VERSION/VIProductVersion）、`deploy/kubernetes/deployment.yaml` 镜像 tag、`desktop/src-tauri/Cargo.lock`（cargo 生成）、`version.json` 的 changelog/release_date、`docs/release-governance.md` 首行散文；覆写 `scripts/installer/version.json` = 根 `version.json`（装配中间物漂移是已钉住的坑）。
- 门禁：`pytest tests/test_version_consistency.py` + `python scripts/check_release_readiness.py` 全 PASS → push release 分支 → #173 `release-gate` 转绿。
- 从 release 分支工作树打包（该树 = 未来 v2.4.0 tag 内容）：壳门禁 `cd desktop/src-tauri && cargo test` → `cargo clippy --all-targets -- -D warnings` → `cargo build --release`，产物改名 `TTSMultiModel.exe`，核对 ProductVersion=2.4.0。
- runtime 两步自检（不过就不打包）：`runtime/python.exe -c "import torch;print(torch.cuda.is_available())"` 必须 True；`import persona` 必须成功。
- `scripts\assemble_release_staging.ps1 -Version 2.4.0` → staging；`python scripts\diag_integrity.py --app-dir ...staging\app\integrated_app --enforce`（16/16 + `VERIFY=True`）。
- `scripts\assemble_installer_data.ps1 -Version 2.4.0` → `dist\installer-data\TTSMultiModel-Data.7z.001..NNN`（脚本自带 `7za t` 回读 + `SHA256SUMS.txt` + `upload-list.txt`）。
- 新壳覆盖 `scripts\installer\TTSMultiModel.exe`，`version.json`/`pyproject.toml`/`7za.exe` 就位 → `makensis`（NSIS 3.10、`.nsi` UTF-8 带 BOM）→ `TTSMultiModel-Setup-v2.4.0.exe` 拷入 `dist\installer-data`，哈希追加进 `SHA256SUMS.txt`。
- 磁盘顺序：壳就位后 `cargo clean`；分卷验证通过后立即删 staging（释放 33GB 给安装）；删除一律 `mv` 改名确认后再永久删。

### 8.4 阶段 3–5：安装模拟、Release、收尾
- 阶段 3：双击 Setup → NSIS 向导（欢迎→许可→目录默认 `%LOCALAPPDATA%\Programs\TTSMultiModel`→开始菜单→安装解压 ~31GB→完成），全程截图读屏；装后断言 `$INSTDIR\TTSMultiModel.exe` VersionInfo=2.4.0、`uninstall.exe`、`config.yaml`/`start_portable.py` 为文件、`model/` 体量符合预期、快捷方式在位、HKCU Uninstall 键存在；启动安装版壳 → 首页渲染、关于页版本=2.4.0、`/api/health/ping` 200、（可选）1 条 voice_design；GUI 卸载复验三清空（目录/快捷方式/注册表）。
- 阶段 4：终验 `check_release_readiness` PASS + #173 `release-gate` 绿 + 阶段 1–3 全过 → 合并 #173 → CI 自动打 tag v2.4.0 + 建 Release + 上传 wheel/sdist/SHA256SUMS；`gh run watch` 盯终态；`gh release view v2.4.0` 核对 4 件资产与 CHANGELOG；补漏 `gh workflow run docker-publish.yml --ref v2.4.0`，核 ghcr `:2.4.0` 标签真存在；pull main 同步合并提交；默认不上传安装包，可 `gh release upload v2.4.0 ...` 追加。
- 阶段 5：滚动清理上一轮 dist 的 2.2.4 wheel/sdist；本轮 Setup exe+分卷+SHA256SUMS 保留待分发；`model/`、`outputs/` 不动；如踩新坑按自进化协议追加 GOTCHAS + 协议版本 +0.1 + REVISION_LOG；最终报告测试矩阵、产物哈希、release 链接、遗留事项。

### 8.5 与原计划/现状的差异（2026-11-04 复核）
- **安装包形态已变（2026-10-31）**：改为单文件瘦包（约 395MB，不含 torch/模型），不再发布 `-Data.7z.001` 分卷；构建命令改为 `scripts\build_desktop_bundle.ps1` + `makensis -DAPP_VERSION=<ver> -DSRC_DATA=<...\.7z.001>`，`-DSRC_DATA` 缺省即 `!error`。
- **runtime 独立发布**：torch 环境包改发 `runtime-v1` 标签（`<Product>-Runtime-<cuda>.zip.001..NNN` + `downloads_catalog.json` + `SHA256SUMS.txt`），下载页按 catalog 离线装并写 `bootstrap/RUNTIME.json`；主安装包内嵌同一份 catalog。
- 计划的 ~18 卷/30GB 与 §3.1 的 14/17 卷均属旧形态，不再适用于现行发布。
- 版本位清单：计划列的是当时子集；现行完整清单以 `docs/release-governance.md` §1 为准（共 11 处，另含 `desktop/package.json`、`tauri.conf.json` 等）。
- 当前版本基线 v2.9.0（2026-10-27）；#173、v2.4.0 tag 与 Release 现状未核实。

## 9. 决策与红线（09-14～09-30）

- **一仓一 fixer**：检测到并行会话（分支切换、WIP 被回退）即暂停 commit，等用户确认（09-18）。
- **不碰他人未提交 WIP**：并行会话的 `test-content/`、`tests/test_upload_transcode.py`、`_wip_backup/` 只登记；确需移除时用 `ctypes SHFileOperationW`（`FOF_ALLOWUNDO=0x0040`）移入回收站，并按 `$R` 大小匹配确认（09-15）。
- **删除纪律**：沙箱内删除只进回收站、不释放空间；真正腾空间需关掉垫片做永久删；回收站可能混用户其他文件，**不擅自清空**，需用户明确确认（09-19）。
- **发布动作需授权**：上传 GitHub Release、重打分卷、推送远端均属发布动作；本次多轮仅本地构建，上传/推送等待用户点头（09-17）（09-18）。
- **验收以断言为准**：`exit=0` 会骗人；每步要有可读判据（版本位、路由数、`7za t`、文件字节/sha256、`/openapi.json`）（09-30）。
- **v2.2.2 回滚决策**：用户发现未修 bug 后删除本地旧打包产物（`dist/installer-data` ~31G、`dist/shell-update` ~37M），保留 2.2.0 旧 sdist，待修复后重新打包（09-19）。**已过时**：后续版本已推进至 v2.9.0，该批产物状态未核实。

## 10. 未核实与证据边界

- 09-17 的「Starlette 1.6.0 `Jinja2Templates` 默认 `auto_reload=True`」是当时版本实测；当前依赖版本是否仍如此**未核实**。该事实的结论（后端每请求重读模板、修模板无需重启）只在当时成立。
- 09-18 的「82 条路由」「动态端口 12095」为当时安装版实测；当前版本路由数与桌面壳端口行为**未核实**。
- 09-19 的「工作树 19 个未提交文件（15 modified + 4 untracked，+550/−124）」是当日状态，随后已提交/演进，**已过时**；文件清单仅作历史参考。
- `docs/SHA256SUMS.models` 的 65 条基线、Apple Silicon 置信度矩阵均为 09-14 口径，**未复验**。
- 计划文件的日期取自文件 mtime（2026-09-30 20:50:25），正文无日期，故标「文件 mtime」而非确认的撰写日。
- 本文件只归档事实，不替代现有权威文档：运行命令、版本位、发布链、备份恢复一律以 `AGENTS.md`（本地）、`docs/release-governance.md`、`docs/SRE_RUNBOOK.md` 与对应 runbook 为准。

## 11. 已由既有文档覆盖，不再重复

| 主题 | 既有覆盖 | 处理 |
|---|---|---|
| CSRF 原生 `fetch` 写操作必须带 `X-CSRF-Token` | `docs/DOD.md` §5.2 十一条一致性守卫（#130）；`AGENTS.md` §3.4 | 不搬 #105 全文，仅保留 §2/§5 的契约判据 |
| 流式音频 404、`/output/` 与 `/api/audio/` 路由、统一 `wireGenerationResult` | `docs/DOD.md` §5.2「生成后接线」；`AGENTS.md` §3.4 自检项（#91/SOP-20） | 不搬 09-14 §5/§7 的三处修复细节 |
| 测试命令、解释器入口、`check_*` 门禁清单 | `AGENTS.md` §4.1–4.2（`run_python.py`、pytest、ruff、mypy、check_spec_refs/upstream_sync） | 不重复列命令表 |
| 版本位清单与发版/回滚流程 | `docs/release-governance.md` §1/§3/§5；`docs/rollback_sop.md` | 不重复 11 处版本位与 release-please 流程 |
| 备份/恢复、数据库演练、F6 噪声隔离、O3/O4/R4 门禁 | `docs/SRE_RUNBOOK.md` §3 + 2026-10-31～11-03 的 8 篇 runbooks + `docs/TODO.md` | 不重复演练数字；本文件只保留 9 月事故 |
| F7 `.venv` 失效、F8 钩子漂移、funasr 前置 | `docs/TODO.md` F7/F8；`docs/runbooks/2026-11-03-*.md` | 不重复，只在 §2 给现行入口 |
| 发布前人工点验（听音频、遮罩、字体、断网首屏） | `docs/DOD.md` §5.1/§5.2 | v2.4.0 计划只保留其未覆盖的 18 tab / 5 语言 / 503 降级矩阵 |
| `.qoder` 快照/回退点机制 | `docs/qoder-snapshot.md` | 完全不搬 |
| 模型许可与「框架-only、用户自取、自担许可」免责 | 根 `README.md` 模型许可表；`docs/发布与分发-单文件安装包.md` §七 | 不重复许可表，只保留 09-14 的下载器与 Apple 矩阵 |
| Bus Factor=1、本地资产边界、「探测不到 ≠ 缺失」 | `docs/runbooks/BUS-FACTOR-2026-11-01.md`；`AGENTS.md` §3.1 | 不重复 |
| 端口 7869 / 禁止 0.0.0.0 / 自动后移、liveness/readiness 分工 | `AGENTS.md` §0/§4.1；`docs/SRE_RUNBOOK.md` §1.3/§5；`docs/DOD.md` §4 | 不重复基础事实，只保留 7870=SeedVR2 与 `/api/engines/list` 两个坑 |
| 安装器 runtime 缺 torch（路由静默减半） | 本地 `GOTCHAS` #112 / `SOPS` SOP-24（本地归档、不随仓库分发） | 本文只留 65 包清单与 34→82 路由判据 |
| 壳回退编译期项目路径 | 本地 `GOTCHAS` #116 | 本文只留 `resolve_app_dir_from_exe` 符号与验证结论 |
| seed 透传回归、torchvision 残缺、editable indextts | 本地 `GOTCHAS` #90/#114/#115 | 本文只留修复口径与 seed=42 MD5 判据 |

## 12. 检查记录（2026-11-04）

- 文档一致性在仓内的唯一机械门禁是 `scripts/check_spec_refs.py`（CI 由 `.github/workflows/docs-consistency.yml` 与 `structure-guard.yml` 共用 `.github/scripts/spec_refs_gate.sh`）。`.githooks/` 下无文档检查脚本，`scripts/check_local.py` 只做 ruff/format/compileall/UTF-8 扫描，不校验 docs 索引篇数。
- 本轮实跑：`python scripts/run_python.py scripts/check_spec_refs.py` → `SPEC_REFS STATE=SKIPPED verdict=not-audited`，exit 0（仓外审计器 `.spec_audit/audit_spec_refs.py` 缺失，CI 中同样 SKIPPED；这表示「本轮未审计」，不表示检查通过）。
