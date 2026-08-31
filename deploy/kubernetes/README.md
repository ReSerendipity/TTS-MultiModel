# TTS_MultiModel Kubernetes 部署

> 对应容器化成熟度评估 P2-9：引入 K8s 资产（Deployment + ConfigMap + Service + 探针）。

本目录提供单机单副本的 Kubernetes 部署清单，作为 Docker Compose 之外的集群化选项。
当前 TTS 推理受「单 Worker 串行 + 单 GPU」硬约束限制（见 `AGENTS.md` §3 硬约束 #4），
因此默认 `replicas: 1`。多副本水平扩展需先解除该约束并解决多卡调度（SRE 评估 §1.4）。

## 前置条件

- 集群已安装 [NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator) 或 `nvidia-container-toolkit`，
  提供 `nvidia.com/gpu` 可分配资源。
- 镜像已推送到 `ghcr.io/reserendipity/tts-multimodel`（见 `.github/workflows/ci.yml` 的 `docker-publish`）。
- 模型权重通过**独立的大文件分发流程**提供（`.dockerignore` 已排除 `model/`），
  运行时以 PVC / hostPath 挂载到 `/app/model`。

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

# 4. 创建持久卷声明（历史库等可写数据）
kubectl -n tts apply -f pvc.yaml

# 5. 部署
kubectl -n tts apply -f configmap.yaml
kubectl -n tts apply -f deployment.yaml

# 6. 校验
kubectl -n tts rollout status deployment/tts-multimodel
kubectl -n tts get pods -l app=tts-multimodel
```

## 可观测性

- 指标：`kubectl -n tts port-forward svc/tts-multimodel 7869` 后访问
  `/api/system/metrics`（Prometheus 文本格式），配合 Prometheus Operator 的 ServiceMonitor 抓取。
- 告警：配置 `observability.alerting.webhook_url` 指向 Alertmanager / 企业微信 / 飞书。
- 日志：`TTS_LOG_FORMAT=json` 输出结构化日志，配合集群日志采集（Loki/ELK）。

## 回滚

镜像按 semver + git sha 双 tag 推送；回滚执行：

```bash
kubectl -n tts rollout undo deployment/tts-multimodel
# 或指定历史版本
kubectl -n tts rollout undo deployment/tts-multimodel --to-revision=<N>
```

详见仓库根 `docs/SRE_RUNBOOK.md` 与 `docs/rollback_sop.md`。
