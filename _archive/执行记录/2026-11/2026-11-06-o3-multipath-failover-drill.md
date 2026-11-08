# O3 多路径切换演练（2026-11-06 · 进程内离线扩展 · 留痕）

> 对应评估门禁：**O3**（无核心组件单点且切换演练过，⛔P0 绝对）
> 本单边界：属主已裁决单点本体「接受为既定形态」（风险处置，非工程缺陷）——**本轮不消除单点**，只把「切换演练」从仅 `streaming_audio` 单路径扩到多路径并留痕。
> 执行方式：**进程内演练**（真实应用 + 真实 HTTP 层 + 真实降级代码路径，`TestClient` 承载），离线（降级合成一律走 seam 桩，不导入/不安装 edge_tts，不出网），不触 GPU（不加载任何权重），**F6 隔离**（历史库指向进程独占系统临时目录，生产 `data/history.db` 零接触）。
> 演练工具：`scripts/o3_multipath_drill.py`（本轮新增，ruff 0.16.5 / mypy 通过）。
> 既有演练基线：`docs/runbooks/2026-11-01-o3-failover-drill.md`（阶段 2 纸面 + 阶段 2b 真机）。

---

## 1. 多路径清单（代码证据：全仓降级调用点唯一性）

```text
$ grep -rn "_maybe_edge_tts_fallback" app/ --include="*.py" | grep -v "def _maybe"
    → app/integrated_app/routes/generate/voxcpm2/streaming.py:859   （唯一调用点）
$ grep -n "def _maybe_edge_tts_fallback" app/integrated_app/routes/generate/utils.py
    → :782（定义点）
$ grep -rn "@router.post" app/integrated_app/routes/generate/ --include="*.py" | wc -l
    → 18（生成面 POST 端点总数）
```

**结论（代码级登记）**：18 条生成 POST 路径中，**仅** `POST /api/generate/voxcpm2/streaming_audio`（挂载名 `/api/generate/streaming_audio`）接了自动降级 seam；其余 17 条（voxcpm_design / voxcpm_clone / voxcpm_ultimate / voxcpm_prompt_continue / streaming_sse / streaming / post_process / breeze_tts2 三条 / indextts2 / step_audio_editx / voicebox / generic clone / edge_tts 直连 / cancel 等）主引擎不可用时走各自守卫/校验硬失败，无降级接管——与评估报告「自动降级仅 streaming_audio 单路径」逐字一致。

## 2. 演练矩阵与实测（2026-11-06 00:5x，`python scripts/run_python.py scripts/o3_multipath_drill.py`，exit 0）

| # | 路径 | 注入 | 预期（来自代码） | 实测 | 判定 |
|---|---|---|---|---|---|
| P1 | `/api/generate/streaming_audio` | 主引擎不可用（未加载+权重缺失桩）+ 降级目标可用（seam 桩） | 200 JSON，`degraded.from=voxcpm2/to=edge-tts` | `status=200 engine=edge-tts degraded={'from': 'voxcpm2', 'to': 'edge-tts', 'reason': 'main_engine_unavailable'}` | PASS |
| P2 | `/api/generate/streaming_audio` | 同上 + 降级目标 seam 桩抛 `EdgeTTSFallbackUnavailableError` | 503 `fallback_failed`（与 `engine_not_ready` 可区分，不假成功、无 filename） | `status=503 error_type=fallback_failed` | PASS |
| P3 | `/api/generate/voxcpm_design` | 主引擎不可用（同 P1 注入） | **无自动降级接管**：≥400 硬失败、响应无 `degraded` 字段 | `status=400 content-type=text/html degraded_absent=True` | PASS |
| P4 | `/api/generate/edge-tts/tts` | 降级目标直连（seam 桩可用） | 200 JSON `engine=edge-tts` | `status=200 engine=edge-tts size_bytes=62` | PASS |
| P5 | `/api/model/switch`（engine=edge-tts） | 真实切换链（无桩）；降级目标可选依赖**未安装**（阶段 2b 演练后已 uninstall，本轮禁装新依赖） | 当前实态 = fail-fast：503 `TTSError(ENGINE_LOAD_ERROR)` + status=error + 补救信息；不产生半切换态 | `status=503 body.status=error`（fail-fast 分支，日志原文「edge-tts 未安装（CPU 兜底引擎不可用，不影响其它引擎）。启用：pip install "edge-tts>=6.1,<8"」） | PASS |

演练脚本原文输出（5/5 PASS，exit 0；F6 隔离库 = 进程独占系统临时目录 `o3_drill_f6_*`，路径含本机用户名按仓规打码）：

```text
[o3-drill] F6 隔离库: <系统临时目录>\o3_drill_f6_wtk6xo3u\o3_drill_history.db
[o3-drill] P1 streaming_audio 自动降级成功支路
[o3-drill]   URL    : /api/generate/streaming_audio
[o3-drill]   实测   : status=200 engine=edge-tts degraded={'from': 'voxcpm2', 'to': 'edge-tts', 'reason': 'main_engine_unavailable'}
[o3-drill]   判定   : PASS
[o3-drill] P2 streaming_audio 自动降级失败支路
[o3-drill]   URL    : /api/generate/streaming_audio
[o3-drill]   实测   : status=503 error_type=fallback_failed
[o3-drill]   判定   : PASS
[o3-drill] P3 voxcpm_design 非流式同引擎路径（预期无自动降级）
[o3-drill]   URL    : /api/generate/voxcpm_design
[o3-drill]   实测   : status=400 content-type=text/html; charset=utf-8 degraded_absent=True
[o3-drill]   判定   : PASS
[o3-drill] P4 edge-tts/tts 降级目标直连路径
[o3-drill]   URL    : /api/generate/edge-tts/tts
[o3-drill]   实测   : status=200 engine=edge-tts size_bytes=62
[o3-drill]   判定   : PASS
[o3-drill] P5 /api/model/switch 人工切换路径（当前实态：降级目标缺依赖 fail-fast）
[o3-drill]   URL    : /api/model/switch
[o3-drill]   实测   : status=503 body.status=error (fail-fast 分支，依赖缺席实态)
[o3-drill]   判定   : PASS
[o3-drill] 汇总: 5/5 PASS, 0 FAIL
[o3-drill] 零生产写入: 历史库指向进程临时目录，降级合成走 seam 桩（无真实音频/网络/权重加载）。
```

**零生产写入复核**（演练后实测）：`data/history.db` = 2,469,888 B、`action_logs` 333 行、`MAX(ts_ms)` = 1,793,594,173,715（与 2026-11-03 收口哨兵逐字一致）；`-wal` 0 B；演练库全部落在系统临时目录。

## 3. 与既有演练记录的关系（口径对齐与行为演进登记）

1. **P1/P2 对阶段 2b 实测 3 的补全**：2b 真机演练中 `streaming_audio` 降级输出为止损结论（edge-tts 未安装，两次 400）。本轮在 seam 桩下把该路径**两支路全链打通**：200 degraded 成功支路 + 503 fallback_failed 可区分失败支路——「降级链最后一公里」（2b 缺陷 D-2 的输出半边）在代码层面被证实可走通，卡点仅在可选依赖 `edge-tts` 的在场性。
2. **P3 是 2b 实测 1 的进程内复现**：2b 真机下 voxcpm_design 主引擎不可用 → 硬失败无接管；本轮进程内同判（400 HTML、无 degraded）——两口径一致，该路径覆盖缺口为**设计现状**而非环境偶然。
3. **P5 行为演进登记（本轮演练新发现）**：2b 曾实测「`POST /api/model/switch engine=edge-tts` → 2.1s 成功（edge-tts 未安装）」；当前 HEAD 同请求 **fail-fast 503**（`TTSError code=ENGINE_LOAD_ERROR`，补救信息明确）。即切换链在 2b 之后**加严**了可选依赖校验——2b 记录的「切换成功」形态在当前代码不再复现，后续演练若复测 2b 步骤应以本 runbook P5 口径为准。
4. **自动降级 seam 的单元/集成回归面**：`tests/test_edge_tts_fallback.py` 5 用例（含 2026-11-05 `179c7d3` 对权重在盘分支的 CI 桩定修正）与本演练矩阵同源；演练不替代测试，二者互为留痕。

## 4. 未覆盖项（如实登记，不伪造）

| 项 | 未覆盖原因 |
|---|---|
| clone 类 3 端点（voxcpm_clone / voxcpm_ultimate / voxcpm_prompt_continue）及其余 14 条 POST 路径的进程内演练 | clone 类需先过授权门禁（`clone.py:111` `_check_clone_consent`）+ 有效参考音频 fixture 才能到达引擎分支；本轮以 §1 代码证据登记其「无降级接管」（全仓调用点唯一性 grep），不做进程内演练 |
| 真机 GPU 窗口的多路径注入 A/B | 需属主指定可释放整卡的时间窗（2026-11-01 o3 drill §7 决策 1 仍悬置）；本轮不自行启停任何进程 |
| `edge-tts` 在场的真实降级产物（真实 MP3 落盘 + 出网到微软在线服务） | 安装 edge-tts 属新依赖 = 属主决策项（本轮硬约束禁装）；出网属降级告知面（aigc 专题挂账），不在本轮 |
| 引擎级 OOM 自动重载路径 | 本轮无 OOM 场景可注入（未加载权重），维持 2b「设计在位、未实测」口径 |

## 5. 对门禁 O3 判定的影响

- **不改变 O3 判定**：单点本体（`deployment.yaml:22` replicas:1、单卡）经属主裁决接受为既定形态，本轮未触碰任何部署/代码生产面；O3 的「无核心单点」半句维持实测否定，「切换演练过」半句的**覆盖面**从 1 条路径（streaming_audio，且 2b 为止损结论）扩至 **5 条路径实测 + 18 条路径代码级登记**。
- 本轮新增演练工具 `scripts/o3_multipath_drill.py` 可重复执行（幂等、离线、F6 隔离），作为后续 O3 复轮的固定演练面。

## 6. 需属主决策项（本单只陈述，不代答）

1. `docs/runbooks/2026-11-01-o3-failover-drill.md` §7 决策三问（实测窗口 / 单卡算账结论书面接受 / 阶段 1 遗留缺口排期）**仍悬置**，本轮未代答。
2. 是否安装 `edge-tts`（可选依赖）：安装后 P5 切换链走「完成分支」、真机降级产物可实测；不安装则维持 fail-fast 现状（补救信息已引导）。本轮按硬约束未安装。
3. 其余 17 条生成路径是否需要降级接管（或按 P3 口径接受「硬失败 + 无接管」为既定形态）：属产品决策，本轮只留痕不改代码。
