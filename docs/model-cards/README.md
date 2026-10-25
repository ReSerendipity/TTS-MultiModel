# 模型卡目录（Model Cards）

本目录为 `model/` 下每个权重目录提供一份**可追溯的模型元数据卡**（MLOps 落地 P0-#2）。

## 为什么不放 `model/` 里

`.gitignore` 第 8 行 `/model/` 已整目录忽略权重下载；把卡片放 `model/` 不会被 git 跟踪，等于没有登记。故卡片统一放本目录，文件名 = 权重目录名 + `.yaml`。

## 字段约定

| 字段 | 含义 |
|---|---|
| `model_dir` | 相对仓库根的权重目录名（必须与 `configs/model_integrity_manifest.json` 的 key 前缀一致） |
| `display_name` | UI / 文档用名 |
| `engine_adapter` | 接入本仓 `app/integrated_app/engines/` 的适配文件；未接入写 `未接入` |
| `upstream_source` | 上游来源（HF id / 仓库）；未在本仓固化写 `未登记`，不要编造 |
| `license_upstream` | 上游 LICENSE 声明；本仓未固化原文时写 `上游声明，未在本仓固化` |
| `integrity` | SHA256 清单指针（本仓统一由 `configs/model_integrity_manifest.json` + `app/integrated_app/security/integrity_selfcheck.py` 校验） |
| `file_count` | 清单中该目录的文件数 |
| `owner` | 本仓负责人（当前唯一：ReSerendipity） |
| `status` | `active`=注册引擎在用；`auxiliary`=辅助模型（ASR/增强/声码器）；`unverified`=未真机验证 |
| `notes` | 补充说明 |

## 红线

- 许可证与上游来源**不得编造**；未核实的字段一律写 `未登记`。
- 权重文件本身仍在 `model/`（gitignored），不在本目录（当前实测约 28.2 GB / 30 文件，见 `configs/model_integrity_manifest.json` 的 `total_size_bytes` / `total_files`）。
- 卡片只登记「这坨权重是什么、从哪来、谁负责、SHA 在哪查」，不替代运行时 `model_registry.py` 的引擎状态总嚎。

## 现有卡片与复验覆盖（2026-10-21 核对）

| 卡片 | 对应权重目录 | 复验覆盖 |
|---|---|---|
| `VoxCPM2.yaml` / `SenseVoiceSmall.yaml` / `speech_zipenhancer.yaml` / `IndexTTS-2.5.yaml` / `IndexTTS-2.0.yaml` | 同名目录 | ✅ 均在 `configs/model_integrity_manifest.json` 内（合计 30 文件） |
| `Breeze-TTS-2.yaml` | `Breeze-TTS-2` | 校验走 `docs/SHA256SUMS.models`（三大权重分片哈希与 ModelScope 官方值一致） |
| （缺卡片） | `model/OpenVoice/`（引擎 `voicebox`）、`model/Step-Audio-EditX/`（引擎 `step-audio-editx`） | ❌ 两目录**未纳入** `model_integrity_manifest.json`，因而也无 SHA 复验 |

> 上述两个缺口是**待补项**（补卡片 + 纳入完整性清单），不是"已核验"；在补齐前，这两个引擎的权重完整性不在此台账可追溯范围内。
