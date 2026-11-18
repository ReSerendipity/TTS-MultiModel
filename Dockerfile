# syntax=docker/dockerfile:1
# Multi-stage build for TTS MultiModel.
# Stage 1 installs build/compilation dependencies and builds a wheel.
# Stage 2 copies only the wheel + runtime dependencies for a smaller image.
#
# 注意：模型权重（model/，约 26GB+）与 .env 已被 .dockerignore 排除，
# 必须运行时通过卷挂载提供，切勿烤入镜像层（违反不可变基础设施）。

# 基础镜像版本钉死（非 latest），保证构建可复现。
# 如需进一步锁定，可改为 nvidia/cuda:12.1.0-runtime-ubuntu22.04@sha256:<digest>
# （注意：锁定 digest 后 apt-get upgrade 的补丁集也被冻结，需随 CVE 公告定期 bump digest）
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# P1-1（云原生评估 2026-09-05）：Python 3.10 → 3.12，与 CI（ci.yml 3.12）及
# README 推荐版本统一，消除"测的是 3.12、发的是 3.10"漂移。
# --no-install-recommends：不拉 idle/lib2to3 等推荐包，减小体积与 CVE 面。
#
# Python 3.12 获取链 2026-11-18 重构（审查批次遗留项①）：弃 launchpad deadsnakes
# PPA，改用 astral-sh/python-build-standalone 的 GitHub Releases 钉版。
# 动因：PPA 索引抖动已两次把 main 的 docker 流水线打红（2026-09-21 run
# 35598902698/35599696780；2026-11-17 run 44117415148/44129119161，5 轮×15s
# 守卫也扛不住 launchpad 侧的小时级故障，而 runner 到 github.com 的链路
# 从未抖过）。钉版 = 发行 tag 20261110 → cpython 3.12.16，SHA256SUMS 同源
# 下载、逐字校验通过才解压——不引入「装到一半的半套索引」这类静默漂移。
# 升级 Python 补丁版时：改下方两个 ARG（对照 release 资产名）。
# CUDA 基底维持 12.1（noble/CUDA≥12.4 才有 ubuntu24.04 变体，换基底需 GPU
# 冒烟重验 torch 钉版，另立决定，不在本次范围）。
ARG PBS_TAG=20261110
ARG PBS_FILE=cpython-3.12.16+20261110-x86_64-unknown-linux-gnu-install_only.tar.gz
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git git-lfs ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL --retry 5 --retry-all-errors -o /tmp/pbs.tar.gz \
         "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_FILE}" \
    && curl -fsSL --retry 5 --retry-all-errors -o /tmp/SHA256SUMS \
         "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/SHA256SUMS" \
    && cd /tmp && grep -F "${PBS_FILE}" SHA256SUMS | sha256sum -c - \
    && mkdir -p /opt/python312 && tar -xzf pbs.tar.gz -C /opt/python312 --strip-components=1 \
    && ln -s /opt/python312/bin/python3.12 /usr/local/bin/python3.12 \
    && python3.12 -m ensurepip --upgrade \
    && rm -f /tmp/pbs.tar.gz /tmp/SHA256SUMS

RUN git lfs install

WORKDIR /build

COPY pyproject.toml requirements.txt ./
COPY app ./app

# Install build tooling and project dependencies, then build the wheel.
# 显式 python3.12 -m pip：Ubuntu 22.04 的裸 pip3/python3 仍指向系统 3.10。
RUN python3.12 -m pip install --no-cache-dir --user -r requirements.txt \
    && python3.12 -m pip install --no-cache-dir --user build "setuptools>=68.0" \
    && python3.12 -m build --wheel

# ------------------------------------------------------------------------------

FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# 安全更新：拉取 Ubuntu 22.04 安全补丁，修复 Trivy 扫描发现的 14 个 HIGH CVE
# （openssl CVE-2026-45447、gnupg/dirmngr CVE-2025-68973 等；修复版本均已在 22.04 安全仓库发布）。
# 不显式升级则 Trivy 安全门禁会因未修复的高危漏洞持续报红。DEBIAN_FRONTEND=noninteractive 已设，无交互阻塞。
# ⚠ 可复现性提示（P2-2）：upgrade 的补丁集随构建日期漂移，如需可复现构建请锁定基础镜像 digest 并定期 bump。
# ⚠ Python 3.12 来源与 builder 阶段一致（python-build-standalone 钉版 20261110 =
#   cpython 3.12.16，SHA256SUMS 校验后解压、软链 /usr/local/bin/python3.12）——
#   两阶段必须用同一 PBS_TAG/PBS_FILE，否则 builder 编译的 wheel 与 runtime
#   解释器 minor 版本错位（PBS 无 Ubuntu 拆包问题，无需 python3.12-venv）。
ARG PBS_TAG=20261110
ARG PBS_FILE=cpython-3.12.16+20261110-x86_64-unknown-linux-gnu-install_only.tar.gz
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL --retry 5 --retry-all-errors -o /tmp/pbs.tar.gz \
         "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_FILE}" \
    && curl -fsSL --retry 5 --retry-all-errors -o /tmp/SHA256SUMS \
         "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/SHA256SUMS" \
    && cd /tmp && grep -F "${PBS_FILE}" SHA256SUMS | sha256sum -c - \
    && mkdir -p /opt/python312 && tar -xzf pbs.tar.gz -C /opt/python312 --strip-components=1 \
    && ln -s /opt/python312/bin/python3.12 /usr/local/bin/python3.12 \
    && python3.12 -m ensurepip --upgrade \
    && rm -f /tmp/pbs.tar.gz /tmp/SHA256SUMS \
    && groupadd -r ttsuser \
    && useradd -r -g ttsuser -m -d /home/ttsuser -s /sbin/nologin ttsuser

WORKDIR /app

# Copy installed Python packages from the builder stage.
# 建用户时必须给真实 home（-m -d /home/ttsuser）：ttsuser 的 home 若是 /app，
# Python 的 user-site 就成了 /app/.local/...，而这里把 pip --user 的产物放到
# /home/ttsuser/.local —— 包装上了却永远不在 sys.path 上（容器起不来的根因之一）。
COPY --from=builder /root/.local /home/ttsuser/.local

# Copy the built wheel and install it so package metadata is available.
COPY --from=builder /build/dist/*.whl /tmp/
RUN python3.12 -m pip install --no-cache-dir --user /tmp/*.whl \
    && rm -f /tmp/*.whl

# Copy application source for templates/static and editable-style imports.
# 注意：.dockerignore 已排除 model/ / .env / lora/ / data/ 等庞大或敏感目录，
# 这些目录必须运行时挂载（见 docker-compose.yml 的 volumes）。
COPY --chown=ttsuser:ttsuser . .

RUN chown -R ttsuser:ttsuser /home/ttsuser/.local /app

USER ttsuser
ENV PATH=/home/ttsuser/.local/bin:$PATH
# 让 `import integrated_app` 命中 /app/app 的源码而非只读 site-packages：
# app_server._PROJECT_ROOT = dirname(dirname(__file__ 所在包)) 只有从 /app/app 导入时
# 才等于 /app，而 /app/data、/app/outputs、/app/logs 是 compose 与冒烟用例挂的可写卷。
# 走 site-packages 会把工程根算进只读镜像层 —— CSRF 密钥等运行态文件写不进去
# （旧代码对此只 warning 后静默关防护，现改为拒绝启动，故必须先修导入路径）。
# wheel 仍保留安装：提供包元数据（版本号）与 ~/.local/bin 入口。
ENV PYTHONPATH=/app/app

EXPOSE 7869

# ---- 容器化配置（均可通过 ENV 覆盖，符合 12-Factor III）----
# 绑定地址/端口（run_server 内部读取，便于反向代理前置于 127.0.0.1）
ENV TTS_BIND_HOST=0.0.0.0
ENV TTS_BIND_PORT=7869

# 容器内默认开启 API Auth：0.0.0.0 监听必须配合认证（run_server 安全网要求）。
# Token 优先取 TTS_API_AUTH_TOKEN；未提供时由应用启动时生成一次性随机 token 并打印到日志。
ENV TTS_API_AUTH_ENABLED=1

# 自动加载模型（app_server.lifespan 实际消费这两个变量，非死配置）
ENV TTS_AUTO_LOAD_MODEL=1
ENV TTS_AUTO_LOAD_ENGINE=voxcpm2

# 结构化日志：设为 json 可输出 JSON 格式便于 Loki/ELK 采集
ENV TTS_LOG_FORMAT=text

# 存活探针（liveness）：ping 仅证明进程响应，**不等于模型就绪**（12-Factor IX 的
# 深度就绪检查由 k8s readinessProbe→/readyz 承担；docker 路线无独立 readiness 概念，
# 客户端应自行轮询 /api/health/ready 确认模型加载完成后再发合成请求，见 docker-compose.yml 注释）。
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD python3.12 -c "import urllib.request; urllib.request.urlopen('http://localhost:7869/api/health/ping')" || exit 1

# 优雅停机：收到 SIGTERM 后由 uvicorn timeout_graceful_shutdown 排空在途请求。
# 应用层默认 60s（app_server.py: TTS_GRACEFUL_SHUTDOWN_S 默认值，可 ENV 覆盖）；
# k8s terminationGracePeriodSeconds=90 已留出 60s 排水 + 30s 余量（P2-4 三处对齐）。
STOPSIGNAL SIGTERM

# 0.0.0.0 仅在 TTS_API_AUTH_ENABLED=1 且 token 就绪时安全网放行（否则容器拒绝启动）
CMD ["python3.12", "-c", "import os; from integrated_app.app_server import run_server; run_server(os.environ.get('TTS_BIND_HOST','0.0.0.0'), int(os.environ.get('TTS_BIND_PORT','7869')))"]
