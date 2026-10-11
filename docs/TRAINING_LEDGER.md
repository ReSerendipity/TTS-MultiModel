# LoRA 训练实验台账（NEXT 评估 P0-3）

> 用途：把「训练/微调」从**调参靠手感、不可复现**变成**每跑一次留一条可回滚记录**。
> 规则：每次真机训练（`scripts/train_voxcpm_finetune.py --config_path=configs/lora_config.yaml`）
> 完成（或失败中断）后，**必须**追加一行到下方表格；不记 = 该次训练不可复现，
> 且无法回滚到任何已知基线（评估报告「训练不可复现（实战层面）」反模式即指此）。

## 复现四要素（与 `app/integrated_app/training/lora_card.py` 的 card.yaml 血缘一致）

1. **基座**：`pretrained_path` + 基座权重 SHA256（`configs/model_integrity_manifest.json` 或实测 `git hash-object`）
2. **代码**：训练脚本所在 git commit（`git rev-parse HEAD`）+ 工作树是否干净（`git status --porcelain`）
3. **数据**：`train_manifest` / `val_manifest` 路径及其 SHA256（`sha256sum` 或 `Get-FileHash`）
4. **配置**：`configs/lora_config.yaml` 当时的完整内容（或 diff）+ 随机种子

## 台账模板（每次一行，按时间追加，不得改写历史行）

| 日期 | 音色/persona | 基座 commit@sha256 | 数据 manifest@sha256 | 超参(batch/grad_accum/lr/r/α/iters) | 训练 loss | 验证 loss | 产物 card.yaml 路径 | 备注/结论 |
|---|---|---|---|---|---|---|---|---|
| （示例）2026-10-11 | new_persona | 69c1738@abc123… | train@def456… | 1/8/1e-4/8/16/2000 | 0.12 | 0.15 | lora/new_persona/card.yaml | 首跑，听感可接受，未达基线对比 |

## 真跑前检查清单

- [ ] `pretrained_path` 目录存在且非空（`model/` 为 AGENTS.md 禁区，只读不改内容）
- [ ] train/val manifest 存在（`data/lora/` 不随仓库分发，需自备）
- [ ] 显存口径：batch_size=1 + grad_accum_steps=8 起步（RTX 5070 Ti Laptop 12GB）
- [ ] 记录复现四要素后再开始训练

## 产物一致性

- 训练产物落 `save_path`（默认 `lora/{persona}/`），加载侧见 `engines/voxcpm2/lora.py`。
- 每个产物目录须有 `card.yaml`（`app/integrated_app/training/lora_card.py` 生成），
  缺 card 的产物在复查时应视为来源不明、不可上线。
