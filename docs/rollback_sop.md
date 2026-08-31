# 回滚 SOP（自动化回滚 / 版本化回滚）

> 对应 SRE 评估 §1.5「自动化回滚机制缺失」与 §2-3「人肉发布」。
> 本文件给出可一键执行的回滚流程，配合 `scripts/rollback_release.py`。

## 0. 前置约定

- 发版由 `release-please` 自动生成 GitHub Release 并打 semver tag（`vX.Y.Z`）。
- 每次发布产物 = Git tag + GitHub Release（含构建产物）。回滚 = 将代码/镜像回退到上一个稳定 tag。
- **原则**：先止血（快速恢复服务），再定位（事后复盘）。MTTR 目标 < 30 分钟。

## 1. 决策：何时回滚

满足任一即触发回滚（参考 SLO 错误预算）：

1. 新版本上线后 5 分钟内 `tts_generation_success_rate` 跌到 SLO 阈值（99.0%）以下；
2. `/api/health/ready` 持续 `degraded`（模型无法加载 / 显存持续熔断）；
3. 出现 P0/P1 级安全告警（未授权访问、权重完整性校验失败且 `block_startup_on_failure=true`）；
4. 关键 API 契约断裂（集成测试在 CI 中本应拦截，但若漏网流入生产）。

## 2. 回滚步骤（推荐：脚本化）

```bash
# 查看最近发布版本，确认要回退到哪个 tag
python scripts/rollback_release.py --list

# 预览回滚（dry-run，不实际改动）
python scripts/rollback_release.py --target v2.2.0 --dry-run

# 执行回滚：在当前分支生成一个「反向提交」回退到目标 tag 的发布提交
python scripts/rollback_release.py --target v2.2.0
```

脚本行为：

- 找到目标 tag 对应的发布提交（`git rev-list -n1 <tag>`）；
- 在该提交**之后**的所有提交被打成一个反向 revert（按时间倒序逐个 `git revert --no-edit`），
  保证不破坏历史、可审计、可再 forward；
- 默认只生成提交，**不自动 push**；需人工 `git push` 后重新部署。
- 容器镜像同步：部署时镜像 tag 锁定为回退版本（如 `tts-multimodel:v2.2.0`），
  见 `deploy/kubernetes/deployment.yaml` 的 `image:` 字段。

## 3. 容器化环境的回滚

```bash
# Kubernetes（最干净：直接切镜像版本，无需改代码）
kubectl -n tts set image deployment/tts-multimodel tts=ghcr.io/reserendipity/tts-multimodel:v2.2.0
kubectl -n tts rollout status deployment/tts-multimodel

# 或回滚到上一个 ReplicaSet
kubectl -n tts rollout undo deployment/tts-multimodel

# Docker Compose
TTS_IMAGE_TAG=v2.2.0 docker compose up -d
```

## 4. 回滚后验证（闭环）

1. `curl /api/health/ping` 返回 `ok`；
2. `curl /api/system/health/ready` 返回 `ready`；
3. 跑最小 smoke：`pytest tests/ -m smoke`；
4. 观察 10 分钟：`/api/system/metrics` 中 `tts_generation_success_rate` 回升、无新告警。

## 5. 数据库 / 历史库回滚说明

- 历史库 `data/tts_history.db`（SQLite）**不随代码回滚**；旧版本代码可继续读写既有 schema
  （`history_db.py` 有迁移兼容逻辑）。
- 若回滚版本依赖的 schema 与当前不同，请先备份：`cp data/tts_history.db data/tts_history.db.bak-$(date +%s)`。
- 模型权重为外部挂载，与回滚无关（见 §3 禁区目录规则）。

## 6. 事后（Postmortem）

回滚完成后 24 小时内产出简短复盘（见 `docs/SRE_RUNBOOK.md` §4），记录：
根因、影响时长（MTTR）、临时止血、永久修复项、负责人。
