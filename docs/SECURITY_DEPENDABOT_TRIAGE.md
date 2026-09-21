# 依赖漏洞告警分诊（Dependabot alerts triage）

日期：2026-09-21（同日按 CI 真实输出 + api.osv.dev 复核修订过一次）
适用：`main` @ PR #100 之后　责任人：仓库所有者
状态：**GitHub 上 20 条 open 告警 = 10 个公告 × 2 份清单**（`requirements-lock.txt`、
`launcher/requirements-small.txt` 各计一次）。但**扫描器视角的下界代价比这个大**：
transformers 4.52.x 实际带 **16 个公告**，其中 8 个只有 PYSEC 号、没有 GHSA 记录 ——
Dependabot 只跟 GHSA，所以那 8 条**根本不开单**，只有 pip-audit（OSV 全源）看得见。
也就是说："把 20 条 dismiss 完" ≠ "4.52.x 的风险登记完"。本文 §1 是完整的 16+2 行。
号与来源全部绑定证据（§1a），不凭记忆。

## 1. 结论速览

A1–A8 有 GHSA 记录（= Dependabot 那 16 条告警的来源）；A9–A16 是 PYSEC-only 的 ZDI 系列
（Dependabot 不开单，pip-audit 会红）。P1/P2 与 transformers 无关。

| # | CVE | PYSEC / GHSA | 严重度 | 修复版本 | 具体代码路径 | 本仓可达性判据 | 处置 |
|---|---|---|---|---|---|---|---|
| A1 | CVE-2025-5197 | PYSEC-2026-1983 / GHSA-9356-575x-2w9m | MODERATE | 4.53.0 | `convert_tf_weight_name_to_pt_weight_name()` 正则灾难性回溯 | `grep -rl convert_tf_weight_name_to_pt_weight_name app/ scripts/` → **0 命中**；本仓不做 TF→PT 权重名转换 | 已接受风险（pip-audit 豁免） |
| A2 | CVE-2025-6051 | PYSEC-2026-1988 / GHSA-rcv9-qm8p-9p6j | MODERATE | 4.53.0 | `EnglishNormalizer.normalize_numbers()` 正则 | 同法 **0 命中**（`normalize_numbers` / `EnglishNormalizer` 各 0）；文本前处理走 voxcpm / indextts 自带前端，不用 transformers normalizer | 已接受风险 |
| A3 | CVE-2025-6638 | PYSEC-2026-1981 / GHSA-59p9-h35m-wg4g | MODERATE | 4.53.0 | `MarianTokenizer.remove_language_code()` 正则 | `grep Marian app/ scripts/` → **0 命中**，本仓不加载 Marian 模型 | 已接受风险 |
| A4 | CVE-2025-6921 | PYSEC-2026-1980 / GHSA-4w7r-h757-3r74 | MODERATE | 4.53.0 | `AdamWeightDecay._do_use_weight_decay()` 处理用户可控正则 | **0 命中**；且是**训练期**优化器，本仓不发训练（`training/` 由 AST 门禁隔离） | 已接受风险 |
| A5 | CVE-2026-1839 | PYSEC-2026-2288 / GHSA-69w3-r845-3855 | MODERATE（AV:L/UI:R） | 5.0.0rc3 | `Trainer._load_rng_state()` 调 `torch.load()` 未带 `weights_only=True` | `grep "from transformers import Trainer"` → **0 命中**；`training/trainer.py` 的 `LoRATrainer` 是我们自己的类，不继承 HF `Trainer` | 已接受风险 |
| A6 | CVE-2026-4372 | PYSEC-2026-2289 / GHSA-29pf-2h5f-8g72 | **HIGH** | 5.3.0 | 恶意 `config.json` 里 `_attn_implementation_internal` 指向攻击者仓库，`AutoModelForCausalLM.from_pretrained()` 加载时 RCE | 前提是**加载别人给的模型目录**。本仓权重全部来自 `model/` 本地目录，入库前人工确认 + SHA-256 复验（`LOCAL_RULES.md` 禁区流程）；唯一从 Hub 取物的是 `security/content_safety.py` 里固定 revision 的 CLIP tokenizer，模型名不接受用户输入 | 已接受风险；Trivy 侧另需 `.trivyignore.yaml` |
| A7 | CVE-2026-5241 | PYSEC-2026-2290 / GHSA-fgcw-684q-jj6r | **HIGH**（AV:N） | 5.5.0 | LightGlue 模型加载路径 `trust_remote_code` 失效 → 初始化期任意代码执行 | 本仓只加载 TTS 语音模型（voxcpm / indextts / funasr / zipenhancer），无视觉匹配模型；`grep LightGlue` → 0 | 已接受风险；Trivy 侧同上 |
| A8 | CVE-2026-9856 | PYSEC-2026-3929 / GHSA-xrqw-3rrv-vx5w | **HIGH**（AV:N） | 5.10.0 | `save_pretrained()` 经 chat template 造成路径穿越任意文件写 | `grep -rn save_pretrained app/ scripts/` → **0 命中**；音色保存写的是我们自己的目录 | 已接受风险；Trivy 侧同上 |
| A9 | CVE-2025-14920 | PYSEC-2025-211 / 无 GHSA | 未评（CVSS3.0 `AV:L/…/UI:R/C:H/I:H/A:H`） | **无** | Perceiver 原始 checkpoint 反序列化 | ZDI 系列，入口都是**人工执行权重转换器**：`grep -riE "convert_[a-z_]*original_checkpoint|PerceiverModel" app/ scripts/` → **0 命中** | 已接受风险（Dependabot 不开单） |
| A10 | CVE-2025-14921 | PYSEC-2025-212 / 无 GHSA | 同上 | **无** | Transformer-XL 原始 checkpoint 反序列化 | 同 A9（`TransformerXL` 0 命中） | 同 A9 |
| A11 | CVE-2025-14924 | PYSEC-2025-213 / 无 GHSA | 同上 | **无** | megatron_gpt2 反序列化 | 同 A9（`megatron` 0 命中） | 同 A9 |
| A12 | CVE-2025-14926 | PYSEC-2025-214 / 无 GHSA | 同上 | **无** | SEW `convert_config` 代码注入 | 同 A9（`SEWModel` 0 命中） | 同 A9 |
| A13 | CVE-2025-14927 | PYSEC-2025-215 / 无 GHSA | 同上 | **无** | SEW-D `convert_config` 代码注入 | 同 A9（`SEWD` 0 命中） | 同 A9 |
| A14 | CVE-2025-14928 | PYSEC-2025-216 / 无 GHSA | 同上 | **无** | HuBERT `convert_config` 代码注入 | 同 A9（`HuBERT` 0 命中）；ASR 侧走 funasr 自有权重格式 | 同 A9 |
| A15 | CVE-2025-14929 | PYSEC-2025-217 / 无 GHSA | 同上 | **无** | X-CLIP checkpoint 转换反序列化 | 同 A9（`XCLIP` 0 命中）；CLIP 在本仓只做安全判定、且用 `AutoTokenizer` 不换 checkpoint | 同 A9 |
| A16 | CVE-2025-14930 | PYSEC-2025-218 / 无 GHSA | 同上 | **无** | GLM4 反序列化 | 同 A9；本仓无 LLM 对话模型 | 同 A9 |
| P1 | CVE-2025-4565 | PYSEC-2026-1806 / GHSA-8qvm-5x2c-j2w7 | high | 4.25.8（另有 5.29.5 / 6.31.1 两条并行修复线） | protobuf JSON 解析 DoS | **无人挡住**：95 个钉版包里对 protobuf 的 13 条约束全是 extra 门控，我们没请求任何 extra（§3a 实测表）；本仓 `grep google.protobuf app/` 零命中，服务端不解析来自网络/wire 的数据 | 待复算 + 真机复验后随批量升级一起抬（§7） |
| P2 | CVE-2026-0994 | PYSEC-2026-1805 / GHSA-7gcm-g887-7qv7 | high | 5.29.6（另有 6.33.5） | protobuf JSON 递归深度绕过 | 同 P1 | 同 P1 |

一句话：**16 个 transformers 公告里没有一个能靠"升个版"现在就消掉** —— A1–A4 要 4.53（被
IndexTTS 的精确 pin 挡住，§2），A5–A8 要 5.x 大版本，A9–A16 **上游根本没有修复版本**，
只能在 5.x 之后才可能出现。P1/P2 另说。

## 1a. 两道扫描器的豁免号是从哪来的（本轮实测，不凭记忆）

下界回到 4.52.x 后，两道门禁各自报一批号，**口径不同所以数量差 5 倍**：

| 门禁 | 过滤条件 | 看到的号 | 条数 |
|---|---|---|---|
| `docker-build.yml` / `docker-publish.yml` 的 Trivy | `severity: CRITICAL,HIGH` + `ignore-unfixed: true` | CVE-2026-4372 / 5241 / 9856（A6/A7/A8，唯一"HIGH 且有修复版本"的三条；同一包在 root 与 ttsuser 两处 site-packages 各计一次，所以 `Total: 6`） | 3 |
| `security.yml` 的 pip-audit | 无 severity、无 unfixed 过滤（它也没这两个开关） | §1 表 A1–A16 的全部 PYSEC 号 | 16 |

run 35575129704 job 106255145288（pip-audit，"Found 22 known vulnerabilities, ignored 1"）
与 run 35575129698 job 106255144801（Trivy）是这两行的出处；A9–A16 的 CVE↔PYSEC 对应、
A1–A4 的"具体函数"列取自 `POST api.osv.dev/v1/query`
（`{"package":{"name":"transformers","ecosystem":"PyPI"},"version":"4.52.4"}`）：
返回 24 条记录，按 CVE 去重后 16 个公告 —— 4.52.1 与 4.52.4 的集合**实测完全一致**。

**两次自己踩出来的纠正，留在这里当反例：**

1. 上一版这里写的 5 个 PYSEC 号有 **4 个是假的**：`PYSEC-2026-198 / 228 / 229 / 392` 是把真号
   `…1980 / …2288 / …2290 / …3929` 从中间截断了。pip-audit 按**全号精确匹配**，所以那次
   `--ignore-vuln` 5 条里只豁免掉 1 条，CI 照红（"ignored 1"）。教训：号要从结构化输出里取，
   不能从表格里目抄 —— 表格列宽会把号截断。
2. Trivy 的豁免我原先写成 `ignorefile: ".trivyignore.yaml"`，而 trivy-action v0.36.0 的合法输入
   叫 **`trivyignores`**（没有 `ignorefile`）。写错**不会**报错，只出一条
   `Unexpected input(s) 'ignorefile'` 警告然后照常变红 —— 也就是"以为豁免了"。
   同理 `.trivyignore.yaml` 的 schema 是 `package: {name, version}`（`version` 单数、字符串），
   我原先写的 `versions: [...]` 不生效。这两条现在由 `tests/test_dependency_consistency.py`
   的 D4 守卫钉住（输入名与 schema 键各断言一次），不再靠下一次 CI 才发现。

另外两个由"版本绑死"暴露出来的事实，一并记清：

3. **镜像里装的是 4.52.4，锁里钉的是 4.52.1** —— 见 §3b。`.trivyignore.yaml` 因此把
   `package.version` 绑在 **4.52.4**（被扫的那个产物），哪天解析结果变了豁免自动失效、门禁变红。
4. GitHub 的 20 条 Dependabot 告警只覆盖 A1–A8 + P1/P2（GHSA 有记录的那 10 个），
   **A9–A16 那 8 条 PYSEC-only 的永远不会开单**。所以 §5 那批 dismiss 做完也不代表登记完成，
   pip-audit 那 16 条豁免才是完整账本。


## 2. 本次实测证据（为什么明知有告警还是不升）

```
transformers 4.57.6 + tokenizers 0.22.2（满足当时声明的 >=4.57.0）：
  VoxCPM2     加载 200 → 合成 RIFF、243,164 B、RMS 5360                     ← 正常
  IndexTTS 2.5 加载失败：ImportError: indextts 缺少 indextts.infer_v2_5 推理模块
  IndexTTS 2.0 加载失败：ImportError: indextts 缺少 indextts.infer_v2    推理模块

transformers 4.52.1 + tokenizers 0.21.0（引擎元数据要求的组合，最终留在树里的状态）：
  IndexTTS 2.5  加载 200 → 回取 RIFF 214,040 B、RMS 6176（约 2.2 s 音频）
  IndexTTS 2.0  加载 200 → 回取 RIFF 205,124 B、RMS 6926
  VoxCPM2       加载 200 → 回取 RIFF 230,148 B、RMS 4615
  三次卸载后显存回到 3.5 GB 基线（RTX 5070 Ti 12227 MiB，加载前空闲 8.2~8.4 GB）
```

即"升到 4.57 就能顺手关掉 4 条 ReDoS"这条路，代价是**产品两个引擎直接不可用**。

**岔口已于 2026-09-21 定为出路 1 并落地**：`requirements.txt` / `pyproject.toml` 的下界回到
`transformers>=4.52.1,<4.53`（`tokenizers>=0.21.0,<0.22`），同时给两道安全门禁加**逐条带理由**的
豁免（号与理由见 §1a；到期即重新变红）。原先那种"留着 `>=4.57.0` 让门禁显示绿色"的状态，
本质是**扫描器的颜色盖住了引擎装不起来这件事**。

两点必须一起记清，免得下次又据此误判：

1. **Docker 路径本来就没有 IndexTTS**：`.dockerignore` 排除 `reference_repos/`，`requirements.txt`
   也不含 `indextts`，镜像里只有 vendored 的 VoxCPM2（`app/integrated_app/vendor/voxcpm`）。
   所以"下界≥4.57 会弄坏容器里的两个 IndexTTS"这个说法是**错的**——真实情况是容器部署形态
   只有 1 个引擎可用。受影响的是"源码安装 + 自带 indextts"的环境（正是 `GPU Smoke` 那条路径）。
2. **唯一能抓到这类运行时断裂的 CI 作业是每周一次、跑在 self-hosted GPU runner 上的
   `GPU Smoke (real inference, self-hosted)`**：最近一次记录是 2026-09-14 success
   （正好是那条错误下界进 main 的当天），此后没有新 run。也就是说：这类问题在 CI 上的
   暴露延迟是以"周"计的，且依赖 runner 在线。

> 复现这套对比时的坑（已记 GOTCHAS #137）：本服务的端口被占时会**自动顺延到下一个端口**，
> 而沙箱里"停掉后台命令"只杀外层 shell、不杀 `python` 子进程。结果是新起的干净服务落在 7870，
> 脚本按 7869 打到的却是**上一代还在跑的旧进程**，把"已恢复环境"误报成"仍然失败"。
> 判据：跑之前先 `netstat -ano | grep LISTENING | grep :786`，确认监听只有一个且 PID 是新的。

## 3. 顺带修掉的、比告警更要紧的一件事

`requirements-lock.txt` / `launcher/requirements-small.txt` 当时**自相矛盾**（与告警无关的独立缺陷）：

| 锁里的值 | 违反谁的要求 | 已改为 |
|---|---|---|
| `tokenizers==0.23.2` | `transformers 4.52.1` 要 `>=0.21,<0.22`；`indextts` 要 `==0.21.0` | `0.21.0` |
| `antlr4-python3-runtime==4.13.2` | `hydra-core 1.3.x` / `omegaconf 2.3.x` 要 `==4.9.*` | `4.9.3` |
| `mpmath==1.4.1` | `sympy 1.14.0` 要 `<1.4` | `1.3.0` |

后果不是"CI 红"，而是**任何人按 lock 装环境都装出一个 `pip check` 报错的破图**，而开发机能跑
是因为它比 lock 早（本机实装 30/73 条与 lock 不一致）。之所以一直没人发现：
`scripts/check_pin_crossconflicts.py`（PR #98 引入）**只挂在 pre-commit 的 `files:` 条件上**
—— 只有改动 lock 才触发，已经坏在 main 上的锁集它永远不会去看。本次把它接进
`.github/workflows/ci.yml` 的 lint job，每次 PR 与 push 都跑；改完检查器输出：
`校验 PyPI 版本 95 个，冲突 0，未核验 0 → PASS`。

## 3a. protobuf 那条上界：**已经实测过了，没人挡住**（更正前两版说法）

本文第一版把 P1/P2 的阻断者写成"被 `descript-audiotools<3.20` 与 `modelscope<3.21` 挡住"，
第二版改口为"未证实"。2026-09-21 做了实测，两个结论都不对：**当前解析图里没有任何一条
对 protobuf 的无条件约束**。

方法：读 `requirements-lock.txt` 的 95 个钉版包，逐个取 PyPI 元数据（`/pypi/<name>/<ver>/json`，
只请求元数据、不下载包），筛出 `requires_dist` 里提到 protobuf 的行。95 个全部查询成功，
命中 **13 行，13 行全部带 `extra ==` 门控**：

| 包 | 提到 protobuf 的行 | 我们的图里生效吗 |
|---|---|---|
| `modelscope==1.40.1` | `protobuf<3.21.0,>=3.19.0`（`nlp`、`all`）、`protobuf`（`audio`、`audio-tts`） | **不生效**：`requirements.txt:8` 写的是 `modelscope>=1.9.0`，没请求任何 extra |
| `sentencepiece==0.2.2` | `protobuf`（`protobuf`、`test`） | 不生效（同上） |
| `transformers==4.52.1` | `protobuf`（`all`、`dev`、`dev-torch`、`dev-tensorflow`、`deepspeed-testing`、`sentencepiece`、`torchhub`） | 不生效（我们只装基础包） |

也就是说：**`protobuf==3.19.6` 不是被谁钉住的，是这份锁当年编译时留下的历史值**；
`funasr==1.4.15` 的元数据里根本不提 protobuf，第一版引它当"阻断者"是拿开发环境多装的包
（`descript-audiotools` / `tensorboardX` 都**不在锁里**）当成了发版图。

所以 #90（protobuf → 7.36.1）**在元数据层面完全可解**，且能一次关掉 P1/P2 两条告警 ——
按 OSV 的修复线，其实不必跳到 7.x：`4.25.8` 关 P1、`5.29.6` 关 P2（同一公告还给了 6.31.1 /
6.33.5 两条并行修复线）。**没合**的原因不是"被挡住"，而是剩下的风险不在元数据里：
protobuf 跨大版本最常坏在 **gencode 与运行时不匹配**（旧 `_pb2.py` 在新运行时 import 期即报
`Descriptors cannot be created directly`），而这条只有**真装环境 + 真加载模型**才走得到 ——
本仓 `app/` 对 `google.protobuf` / `_pb2` 是**零直接引用**，CI 那 12 个 CPU pytest 矩阵覆盖不到。
判据与复算命令见 §7。

## 3b. 顺手发现的一个已知缺口：镜像与便携包装的不是同一个 transformers 版本

本轮为了核对 Trivy 报的版本读了一遍 `Dockerfile`：

```
Dockerfile:33  COPY pyproject.toml requirements.txt ./
Dockerfile:38  RUN python3.12 -m pip install --no-cache-dir --user -r requirements.txt
```

装的是**声明** `requirements.txt`（`transformers>=4.52.1,<4.53`），解析结果是 **4.52.4**；
而 `requirements-lock.txt` 钉的是 **4.52.1**（便携包 / 桌面包按锁装）。CI 的镜像扫描输出了
`transformers-4.52.4.dist-info`，两边对不上，是被扫描的产物自己说的。

后果分两层：
1. **安全口径**：镜像里那 3 条 HIGH 的修复版本都在 5.x，4.52.1 与 4.52.4 的公告集合
   实测相同（§1a 的 OSV 查询两个版本都跑过），所以**豁免结论不受影响**；
2. **可复现性**：同一份代码经两条分发路径装出两个不同的 transformers 补丁号，
   "锁已验证"这件事对容器部署形态并不成立 —— 与 §6 末尾"这份锁从没被真装验证过"是同一类问题。

本轮**没有**改 `Dockerfile`（改成装锁会连带改变镜像里全部 90+ 个包的版本，属于 §B1 relock
之后才能做的动作），只是把 `.trivyignore.yaml` 的 `package.version` 绑在镜像真实版本上，
让"哪天版本变了 → 豁免失效 → 门禁变红"这条链路保持有效。

## 4. 复点条件（满足其一就重新评估对应告警）

1. IndexTTS 官方放开 `transformers` 精确 pin（或我们改 vendored 拷贝适配）→ 抬到 4.53+ 立刻
   可关 A1–A4；抬到 5.10+ 则 A1–A8 一次全消。**A9–A16 只能等它们各自出修复版本**
   （OSV 现在给的是"无修复版本"，所以只要停在 4.52.x，这 8 条就会一直在 pip-audit 里红着）。
2. protobuf 的 P1/P2：**阻断者已排除** —— §3a 实测 95 个钉版包里对 protobuf 的 13 条约束
   全是 extra 门控，我们一个 extra 都没请求，所以"被 `descript-audiotools<3.20` /
   `modelscope<3.21` 挡住"那两版说法都不成立。现在只剩"没做过真机复验"这一件事，
   复算与验收命令在 §7；做完可一次关掉这两条（4.25.8 关 P1、5.29.6 关 P2，不必跳到 #90 的 7.x）。
3. 若本服务将来**监听非本机地址**或**接受用户指定模型名/HF 仓库**，A6/A7/A8/A9–A16/P1/P2 的
   "无攻击面"前提立即失效，必须重判。当前证据：`config.yaml host: "127.0.0.1"`、
   `run_server(ip="127.0.0.1")`。
4. 若 `vllm_backend.py`（现在无人引用，其 `trust_remote_code` 默认 True）被接进入口，A6/A7 立即升为必修。
5. **到期复审**：`.trivyignore.yaml` 三条的 `expiration` 都是 2026-12-31，到期那一步自动变红；
   pip-audit 侧没有到期机制（它不支持 expiration），所以 A1–A16 的 16 个号靠本文 + 人守 ——
   2026-12-31 之前要么按条件 1 抬版本消掉，要么把这份表带着做一次显式再确认。

## 5. 需要仓库所有者点头的动作（枚举已查成真值，命令可直接跑）

GitHub 安全页的 20 条 dismiss 属**共享状态写操作**，我没有代做。原先说"`dismissed_reason`
的取值我要现查"—— 已查，两个独立来源一致：

- REST 文档（`docs.github.com/en/rest/dependabot/alerts`）：`state` ∈ `dismissed` / `open`；
  `dismissed_reason` ∈ **`fix_started` / `inaccurate` / `no_bandwidth` / `not_used` / `tolerable_risk`**；
  没有独立的 `comment` 字段，注释走 **`dismissed_comment`**。
- GraphQL 内省（权威，不依赖文档是否过期）：

```bash
gh api graphql -f query='{ __type(name: "DismissReason") { kind enumValues { name } } }'
# → ENUM: FIX_STARTED, NO_BANDWIDTH, TOLERABLE_RISK, INACCURATE, NOT_USED
```

**告警号 ↔ §1 行的对应（`gh api .../dependabot/alerts?state=open` 实测 20 条 = 10 个公告 × 2 份清单）**：

| 告警号 | 公告 | 对应 §1 | 建议 `dismissed_reason` | 一句话理由（写进 `dismissed_comment`） |
|---|---|---|---|---|
| 4、14 | CVE-2025-5197 | A1 | `not_used` | `convert_tf_weight_name_to_pt_weight_name` 在本仓 0 命中，不做 TF→PT 权重名转换 |
| 5、15 | CVE-2025-6638 | A3 | `not_used` | 不加载 Marian 模型，`remove_language_code` 走不到 |
| 6、16 | CVE-2025-6051 | A2 | `not_used` | 不用 transformers 的 normalizer，文本前处理在引擎自带前端里 |
| 7、17 | CVE-2025-6921 | A4 | `not_used` | `AdamWeightDecay` 是训练期优化器，本服务不训练 |
| 9、19 | CVE-2026-1839 | A5 | `not_used` | 全仓无 `from transformers import Trainer`；自己的 `LoRATrainer` 不继承它 |
| 10、20 | CVE-2026-4372 | A6 | `tolerable_risk` | 需要加载别人给的 `config.json`；权重走 `model/` 本地目录 + 人工确认 + SHA-256 |
| 11、21 | CVE-2026-5241 | A7 | `tolerable_risk` | 本仓只加载 TTS 语音模型，无 LightGlue 一类视觉模型 |
| 12、22 | CVE-2026-9856 | A8 | `not_used` | 全仓 0 处 `save_pretrained`；音色保存走自己的目录写入 |
| 3、13 | CVE-2025-4565 | P1 | `tolerable_risk` | 服务端不解析网络来的 protobuf/JSON wire；待 §7b 复算后抬到 4.25.8 自然消除 |
| 8、18 | CVE-2026-0994 | P2 | `tolerable_risk` | 同 P1，修复线 5.29.6 |

跑法（**先只跑 1 条确认语义，再批量**）：

```bash
# 单条试跑（先读后写，确认字段语义与返回）
gh api repos/ReSerendipity/TTS_MultiModel/dependabot/alerts/4 \
  --method PUT \
  -f state=dismissed \
  -f dismissed_reason=not_used \
  -f dismissed_comment='本仓可达性判定见 docs/SECURITY_DEPENDABOT_TRIAGE.md §1 A1（convert_tf_weight_name_to_pt_weight_name 零命中）；复点条件 §4-1。'

# 确认无误后再按上表批量（号与 reason 逐条对，别用同一个值一把梭）
```

两点别忘：
1. **`not_used` / `tolerable_risk` 要按上表分开设**，全用 `inaccurate` 会把"我们确实带着这些公告"
   这件事从台账上抹掉 —— 这 16 条的账本在 pip-audit 豁免清单与本文 §1，不在告警页。
2. 关掉这 20 条 **不等于登记完成**：A9–A16 那 8 条只有 PYSEC 号、没有 GHSA 记录，
   Dependabot 从不开单（上面的告警列表里确实没有它们）。别以"告警清零"当验收口径。

## 6. 开发环境一致性核对（`pip check` 16 条，2026-09-21）

`python -m pip check` 在本机报 16 条，逐条分类后**没有一条影响发版产物**：

| 类别 | 条数 | 内容 | 判定 |
|---|---|---|---|
| `indextts 2.0.0` 声明但没装的包 | 4 | cython、ffmpeg-python、keras、opencv-python | 不影响：三引擎真推理今天全部跑通（2.5 出 214,040 B / RMS 6176；2.0 出 205,124 B / RMS 6926；VoxCPM2 出 230,148 B / RMS 4615）→ 这几项是它打包元数据里的构建/训练期依赖，推理路径不需要 |
| `indextts 2.0.0` 声明版本与实装不符 | 11 | numpy 2.2.6→2.5.2、torch 2.8.*→2.13.0+cu132、torchaudio 2.8.*→2.11.0、pandas 2.3.2→3.0.5、librosa 0.10.2.post1→1.0.0、modelscope 1.27.0→1.39.1、safetensors 0.5.2→0.8.0、numba、matplotlib、cn2an、json5 | 同上：本机是"整套比 indextts 声明更新"的环境，实测可用。注意**别据此抬 `transformers` 上界**（§2 已证 4.57 会把两个引擎打死） |
| 与发版无关的额外包 | 1 | `tensorboardX 2.6.5` 要 `protobuf>=3.20`，实装 3.19.6 | 不影响产物：`tensorboardX` 不在 `requirements-lock.txt` 里，只是开发机多装的 |

顺带说清两件事：`requirements-lock.txt` 是 `pip-compile --no-annotate` 的产物（95 条钉版），
本机实装与它有 **30/73 条**可对比项不一致，且锁里没有 `descript-audiotools`/`tensorboardX`
一类开发机依赖 —— 所以"锁 vs 开发机"的差**不等于**锁错，但它同时说明**这份锁从没被真装验证过**（见 §B1 建议）。

## 7. 锁集全量审计结果与复算命令（B1 的可复现部分）

### 7a. 全锁审计（2026-09-21，OSV `querybatch`，95 个钉版包一次问完）

| 结果 | 数字 |
|---|---|
| 有公告的包 | **2 / 95** |
| `transformers==4.52.1` | 24 条记录 = **16 个**去重公告（§1 A1–A16） |
| `protobuf==3.19.6` | 4 条记录 = **2 个**去重公告（§1 P1/P2） |
| 其余 93 个包 | **0 公告** |

即：整份锁的已知漏洞面**全部集中在两个包上**，且都在本文登记完了。
（记录数是 2× 公告数，因为 OSV 对同一公告同时发 GHSA 与 PYSEC 两条记录。）

复现：

```bash
# 一次性批量问 OSV，不下载任何包
python - <<'PY'
import json, re, urllib.request, pathlib
pairs = re.findall(r'^([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][0-9a-zA-Z.+-]*)',
                   pathlib.Path('requirements-lock.txt').read_text(encoding='utf-8'), re.M)
body = json.dumps({'queries': [{'package': {'name': n, 'ecosystem': 'PyPI'}, 'version': v}
                              for n, v in pairs]}).encode()
req = urllib.request.Request('https://api.osv.dev/v1/querybatch', data=body,
                             headers={'Content-Type': 'application/json'})
res = json.load(urllib.request.urlopen(req, timeout=180))
for (n, v), r in zip(pairs, res['results']):
    if r.get('vulns'):
        print(f'{n}=={v}:', ', '.join(x['id'] for x in r['vulns']))
PY
```

同一把尺子也解释了"为什么 pip-audit 报 16 而 Trivy 报 3"：见 §1a 的过滤口径表。

### 7b. 复算锁 + 真装复验（**需要你拍板的那一步**）

以下命令会访问 PyPI 索引并把 GB 级依赖装进一个**新建的** venv（不动现有 `.venv`，
不动 `model/`）。按本仓规矩，这种量级的传输我不擅自跑，命令交给你：

```bash
cd /c/Users/Doro/TTS_MultiModel

# ① 复算（只重解析，不改 requirements.txt 的声明）
python -m venv .venv-relock && ./.venv-relock/Scripts/python.exe -m pip install -q pip-tools
./.venv-relock/Scripts/python.exe -m piptools compile --no-annotate \
  --output-file=requirements-lock.relock.txt requirements.txt

# ② 只对比差异，先不覆盖真锁
diff <(sort requirements-lock.txt) <(sort requirements-lock.relock.txt) | head -60

# ③ 关键问题要在复算里逐条回答：
#    - transformers 解析到哪个补丁号（4.52.1 还是 4.52.4）？两者公告集合实测相同（§1a），
#      但 #25 那批手改过锁，锁与"按声明重解析"的关系从没验证过；
#    - protobuf 能抬到哪（4.25.8 / 5.29.6 / 7.36.1）？§3a 已证无人挡住，
#      真拦路的是 funasr/modelscope 的 gencode 运行时兼容，import 期才暴露；
#    - 锁里 tokenizers==0.21.0 / antlr4-python3-runtime==4.9.3 / mpmath==1.3.0 三处手改
#      是否被复算保留（不保留就说明声明侧还需要写约束）。

# ④ 装进复算环境后做真机验收（三引擎各一段，数字要与 §2 基线同量级）
#    基线：indextts2 214,040 B / RMS 6176；indextts20 205,124 B / RMS 6926；
#          voxcpm2 230,148 B / RMS 4615；每次卸载显存回到 ~3.5 GB
./.venv-relock/Scripts/python.exe -m pip install -r requirements-lock.relock.txt
./.venv-relock/Scripts/python.exe scripts/gpu_smoke_minimal.py   # 再手工补 2.0 那条（冒烟脚本没覆盖，DOD §5 已知缺口）
./.venv-relock/Scripts/python.exe -m pytest tests/ -q --cov=app --cov-fail-under=45
./.venv-relock/Scripts/python.exe scripts/check_pin_crossconflicts.py   # 期望：冲突 0
```

跑完把 `requirements-lock.relock.txt` 的 diff 贴回来，我据此决定 #89/#90/#91 是批量合、
还是只取其中能过真机的那几条。
