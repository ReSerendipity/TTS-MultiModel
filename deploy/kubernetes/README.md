# TTS_MultiModel Kubernetes 部署

> 对应容器化成熟度评估 P2-9：引入 K8s 资产（Deployment + ConfigMap + Service + 探针）。
> 2026-09-05 云原生评估 P0 修复：补全权重/可写卷挂载（deployment.yaml）、拆分并扩容
> PVC（tts-data 30Gi + tts-models 60Gi）、删除手写 ConfigMap 骨架（配置漂移源）。

本目录提供单机单副本的 Kubernetes 部署清单，作为 Docker Compose 之外的集群化选项。
当前 TTS 推理受「单 Worker 串行 + 单 GPU」硬约束限制（见 `AGENTS.md` §3 硬约束 #4），
因此默认 `replicas: 1`。多副本水平扩展需先解除该约束并解决多卡调度（SRE 评估 §1.4）。

> ⚠️ **不要水平扩容**：`replicas > 1` 时每个 Pod 各自加载 ~27G 权重并独占一张 GPU，
> 而应用内部为单 Worker 串行调度（`max_concurrent=1`），加副本只会成倍烧显存，
> 不会提升吞吐。如需扩容请走垂直路径（更大显存 / 多引擎分卡），见评估报告必答 Q3。

## 前置条件

- 集群已安装 [NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator) 或 `nvidia-container-toolkit`，
  提供 `nvidia.com/gpu` 可分配资源。
- 镜像已推送到 `ghcr.io/reserendipity/tts-multimodel`（见 `.github/workflows/ci.yml` 的 `docker-publish`）。
- 模型权重通过**独立的大文件分发流程**提供（`.dockerignore` 已排除 `model/`），
  运行时以 PVC（`tts-models`，见 pvc.yaml）只读挂载到 `/app/model`。
  **部署前必须先把权重预填充进该 PVC**（如经临时 Pod / rsync / 对象存储同步），
  否则 `TTS_AUTO_LOAD_MODEL=1` 会因找不到权重使 `/readyz` 永不 200，Pod 重启循环。

## 部署步骤

```bash
# 1. 创建命名空间
kubectl create namespace tts

# 2. 创建 API Auth Secret（务必替换为强随机 token）
kubectl -n tts create secret generic tts-auth \
  --from-literal=api_token="$(python3 -c 'import secrets;print(secrets.token_hex(16))')"

# 3. 以仓库顶层 config.yaml 创建 ConfigMap（内容会覆盖镜像内默认）
kubectl -n tts create configmap tts-config \
  --from-file=config.yaml=./config.yaml

# 4. 创建持久卷声明（tts-data 可写运行态 30Gi + tts-models 权重 60Gi）
kubectl -n tts apply -f pvc.yaml

# 4b. 预填充模型权重到 tts-models PVC（镜像不含权重，见前置条件）——
#     经临时 Pod / rsync / 对象存储把仓库 model/ 内容放入该卷根目录，
#     使容器内 /app/model 能读到权重；否则 /readyz 永不 200、Pod 重启循环。

# 5. 部署（ConfigMap 已由第 3 步从真实 config.yaml 生成，不再 apply 手写骨架清单）
kubectl -n tts apply -f deployment.yaml

# 6. 校验
kubectl -n tts rollout status deployment/tts-multimodel
kubectl -n tts get pods -l app=tts-multimodel
```

## 可观测性

- 指标：`kubectl -n tts port-forward svc/tts-multimodel 7869` 后访问
  `/metrics`（Prometheus 文本格式，根路径注册，见 `app_server.py`；免鉴权可抓）。
  集群装了 Prometheus Operator 时 apply `servicemonitor.example.yaml`（按 selector 改 label）。
- GPU 指标（显存/利用率/温度/ECC）：应用自身不导出设备级指标，需集群级
  [DCGM-Exporter](https://github.com/NVIDIA/dcgm-exporter)。装了 GPU Operator 时默认已含
  `dcgm-exporter` DaemonSet（指标口 `9400/metrics`）；未装 Operator 可单独部署其 Helm chart。
  注意与显存告警联动：本服务单副本独占 1 卡，`vram_usage_warn_pct`（config.yaml
  `observability.alerting`）只看应用自报值，设备真实余量以 DCGM 指标为准。
- 告警：配置 `observability.alerting.webhook_url` 指向 Alertmanager / 企业微信 / 飞书。
- 日志：`TTS_LOG_FORMAT=json` 输出结构化日志，配合集群日志采集（Loki/ELK）。

## 冷启动 MTTR 与探针参数（运维稳定性评估 P0-2 实测留档，2026-09-05）

在 RTX 4070 SUPER（12G 显存）+ voxcpm2 上 3 轮实测（`TTS_AUTO_LOAD_MODEL=1 python perf/cold-start.py --engine voxcpm2`）：

| 段 | 实测 | 对应探针 |
|---|---|---|
| 进程→ping | 9.9–11.7s | `startupProbe`（initialDelay 10s 起步即覆盖） |
| ready→/readyz=200（模型加载到可用 = **MTTR 核心**） | **33.1–61.6s** | readiness 接 `/readyz` 后自动兜住 |
| 总冷启动 | 44.8–71.5s | `startupProbe` 上限 10+18×10=190s，余量约 2.7× 最差值 |

- 基线门禁：`baselines/gpu-mttr-baseline.json` + `scripts/gpu_baseline_gate.py`（CI `stress.yml` 的 `gpu-baseline-gate` 作业，需 GPU self-hosted runner 手动触发）。
- **换更大权重/设备时必须重测**：保持 `startupProbe failureThreshold×periodSeconds ≥ 1.5×最差 MTTR`。
- 文档中的 "27G" 是**权重磁盘占用**（非显存常驻量）；引擎声明显存 5.5–6.5G，见 `config.yaml models.*.vram_gb`。

## 回滚

镜像按 semver + git sha 双 tag 推送；回滚执行：

```bash
kubectl -n tts rollout undo deployment/tts-multimodel
# 或指定历史版本
kubectl -n tts rollout undo deployment/tts-multimodel --to-revision=<N>
```

详见仓库根 `docs/SRE_RUNBOOK.md` 与 `docs/rollback_sop.md`。
