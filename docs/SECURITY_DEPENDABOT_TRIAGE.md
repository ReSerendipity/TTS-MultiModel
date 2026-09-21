# 依赖漏洞告警分诊（Dependabot alerts triage）

日期：2026-09-21　适用：`main` @ PR #100 之后　责任人：仓库所有者
状态：**20 条 open 告警 = 10 个公告 × 2 份清单**（`requirements-lock.txt`、
`launcher/requirements-small.txt` 各计一次）。本文只谈"能不能修 / 该不该修 / 谁挡住"。

## 1. 结论速览

| # | CVE | 包 | 严重度 | 修复版本 | 谁挡住它 | 本仓可达性 | 建议处置 |
|---|---|---|---|---|---|---|---|
| A1 | CVE-2025-5197 | transformers | medium | 4.53.0 | `indextts==…`钉 `transformers==4.52.1` | tokenizer 正则 ReDoS，且仅 Marian/部分 tokenizer；我们走 GPT2/CLIP 路径，输入是本机用户自己键入的文本 | 关闭理由：本机自用 + 代码路径不涉及；待引擎适配后随版本升级自然消除 |
| A2 | CVE-2025-6051 | transformers | medium | 4.53.0 | 同上 | 同 A1 | 同 A1 |
| A3 | CVE-2025-6638 | transformers | medium | 4.53.0 | 同上 | MarianTokenizer —— 本仓不加载 Marian 模型 | 同 A1 |
| A4 | CVE-2025-6921 | transformers | medium | 4.53.0 | 同上 | AdaLight tokenizer 正则 —— 未使用 | 同 A1 |
| A5 | CVE-2026-1839 | transformers | medium | 5.0.0rc3 | 引擎钉 4.52.1（且 5.x 是大版本破坏性升级） | `Trainer` 代码执行：**全仓无 HF `Trainer` 用法**（`training/trainer.py` 里的 `LoRATrainer` 是我们自己的类，`grep from transformers import Trainer` 零命中） | 关闭理由：代码路径不存在 |
| A6 | CVE-2026-4372 | transformers | high | 5.3.0 | 同上 | 远程代码执行类：需要加载不可信仓库且 `trust_remote_code=True`。本仓权重是本地目录 + 人工核验 + SHA-256（`LOCAL_RULES.md` 禁区流程）；唯一从 Hub 取物的是 `security/content_safety.py` 的 CLIP `AutoTokenizer.from_pretrained(model_name, revision=…)`（固定 revision、非攻击者可控） | 关闭理由：不接受用户指定模型源；保留复点 |
| A7 | CVE-2026-5241 | transformers | high | 5.5.0 | 同上 | LightGlue 等模型初始化路径 —— 本仓不加载该类模型 | 关闭理由：未使用组件 |
| A8 | CVE-2026-9856 | transformers | high | 5.10.0 | 同上 | `save_pretrained` 经 chat template 造成任意文件写：**全仓无 `save_pretrained` 调用**（音色保存写的是我们自己的目录） | 关闭理由：代码路径不存在 |
| P1 | CVE-2025-4565 | protobuf | high | 4.25.8 | `descript-audiotools 0.7.2` 要 `>=3.9.2,<3.20`；`modelscope` 的 audio extra 要 `>=3.19.0,<3.21.0` | protobuf 在本仓只被 tensorboard / tensorboardX / modelscope / descript-audiotools 间接使用，服务端**不解析任何来自网络的 protobuf/JSON wire 数据**（`grep google.protobuf app/` 零命中） | 关闭理由：无攻击面；要真修得换掉 descript-audiotools/modelscope 的锁上界 |
| P2 | CVE-2026-0994 | protobuf | high | 5.29.6 | 同上（且跨两个大版本） | 同 P1 | 同 P1 |

一句话：**10 个公告里没有一个能靠"升个版"现在就消掉** —— 4 个 ReDoS 被 IndexTTS 引擎的精确 pin 挡住，
4 个需要 transformers 5.x（大版本），2 个需要 protobuf 越过 `descript-audiotools<3.20` /
`modelscope<3.21` 两道上游上界。

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

**这里有一个必须所有者拍的岔口**（PR #101 实测撞上的）：把声明下界改回 4.52.x 会让
`transformers 4.52.4` 进入解析结果，于是 CI 的两道安全门禁同时变红 ——
`Dependency Vulnerability Scan (pip-audit)`（PYSEC-2025-216、PYSEC-2026-198/228/229/392）与
`docker-build.yml` 的 Trivy 扫描步骤（镜像构建本身成功）。反过来，留着 `>=4.57.0` 门禁是绿的，
但任何人按 `pyproject`/`requirements.txt` 装环境，IndexTTS 两个引擎都起不来。
也就是说**当前 main 的"安全门禁绿"是踩在引擎跑不起来的版本声明上的**。

两条出路，本 PR 只做了无争议的那半（锁集合法性 + 检查器接线），下界原样留着并加注释指向本文：

1. 下界改回 `>=4.52.1,<4.53`（运行时正确），同时给 pip-audit / Trivy 加**逐条带理由**的豁免
   （理由引用 §1 表的可达性判定与 §4 的复点条件）；
2. 或者先让 IndexTTS 侧适配 transformers ≥4.53（上游放开精确 pin，或我们改 vendored 拷贝），
   再自然抬下界、告警一并消掉。

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

## 4. 复点条件（满足其一就重新评估对应告警）

1. IndexTTS 官方放开 `transformers` 精确 pin → 立刻可关 A1–A4（4.53+），届时再判 5.x 的 A5–A8。
2. `descript-audiotools` / `modelscope` 放开 `protobuf<3.20 / <3.21` → 可关 P1–P2。
3. 若本服务将来**监听非本机地址**或**接受用户指定模型名/HF 仓库**，A6/A8/P1/P2 的
   "无攻击面"前提立即失效，必须重判。当前证据：`config.yaml host: "127.0.0.1"`、
   `run_server(ip="127.0.0.1")`。
4. 若 `vllm_backend.py`（现在无人引用，其 `trust_remote_code` 默认 True）被接进入口，A6 立即升为必修。

## 5. 需要仓库所有者点头的动作

GitHub 安全页的 20 条 dismiss 属**共享状态写操作**，我没有代做，也没有在这份文档里写死
`dismissed_reason` 的取值 —— 那个枚举我该现查而不是照记忆写。执行前先取真值：

```bash
# 看某条告警现在的状态与字段名（读操作，不改状态）
gh api repos/ReSerendipity/TTS_MultiModel/dependabot/alerts/22 \
  -q '{state, dismiss_reason, advisory: .security_advisory.cve_id}'
```

拿到合法枚举后，按 §1 表里"建议处置"一列逐条 PATCH（`state=dismissed` + 对应的
`dismissed_reason` + 注释里引用本文对应行号），**先跑 1 条确认语义再批量**。
每条注释必须自带绑定理由（哪条代码路径不存在 / 被哪个上游上界挡住 / 复点条件），
不接受"误报"三个字了事。
