# syntax=docker/dockerfile:1
# Multi-stage build for TTS MultiModel.
# Stage 1 installs build/compilation dependencies and builds a wheel.
# Stage 2 copies only the wheel + runtime dependencies for a smaller image.
#
# 注意：模型权重（model/，约 26GB+）与 .env 已被 .dockerignore 排除，
# 必须运行时通过卷挂载提供，切勿烤入镜像层（违反不可变基础设施）。

# 基础镜像版本钉死（非 latest），保证构建可复现。
# 如需进一步锁定，可改为 nvidia/cuda:12.1.0-runtime-ubuntu22.04@sha256:<digest>
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3.10 python3-pip python3.10-venv git git-lfs ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN git lfs install

WORKDIR /build

COPY pyproject.toml requirements.txt ./
COPY app ./app

# Install build tooling and project dependencies, then build the wheel.
RUN pip3 install --no-cache-dir --user -r requirements.txt \
    && pip3 install --no-cache-dir --user build setuptools>=68.0 \
    && python3 -m build --wheel

# ------------------------------------------------------------------------------

FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3.10 python3-pip ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r ttsuser \
    && useradd -r -g ttsuser -d /app -s /sbin/nologin ttsuser

WORKDIR /app

# Copy installed Python packages from the builder stage.
COPY --from=builder /root/.local /home/ttsuser/.local

# Copy the built wheel and install it so package metadata is available.
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip3 install --no-cache-dir --user /tmp/*.whl \
    && rm -f /tmp/*.whl

# Copy application source for templates/static and editable-style imports.
# 注意：.dockerignore 已排除 model/ / .env / lora/ / data/ 等庞大或敏感目录，
# 这些目录必须运行时挂载（见 docker-compose.yml 的 volumes）。
COPY --chown=ttsuser:ttsuser . .

RUN chown -R ttsuser:ttsuser /home/ttsuser/.local /app

USER ttsuser
ENV PATH=/home/ttsuser/.local/bin:$PATH

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

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:7869/api/health/ping')" || exit 1

# 优雅停机：容器收到 SIGTERM 后由 uvicorn 默认 30s 宽限期排空在途请求。
STOPSIGNAL SIGTERM

# 0.0.0.0 仅在 TTS_API_AUTH_ENABLED=1 且 token 就绪时安全网放行（否则容器拒绝启动）
CMD ["python3", "-c", "import os; from integrated_app.app_server import run_server; run_server(os.environ.get('TTS_BIND_HOST','0.0.0.0'), int(os.environ.get('TTS_BIND_PORT','7869')))"]
