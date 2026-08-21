# syntax=docker/dockerfile:1
# Multi-stage build for TTS MultiModel.
# Stage 1 installs build/compilation dependencies and builds a wheel.
# Stage 2 copies only the wheel + runtime dependencies for a smaller image.

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
COPY --chown=ttsuser:ttsuser . .

RUN chown -R ttsuser:ttsuser /home/ttsuser/.local /app

USER ttsuser
ENV PATH=/home/ttsuser/.local/bin:$PATH

EXPOSE 7869

ENV TTS_AUTO_LOAD_MODEL=1
ENV TTS_AUTO_LOAD_ENGINE=voxcpm2

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:7869/api/health/ping')" || exit 1

# 0.0.0.0 仅在 config.yaml 配置 api_auth.enabled=true + token 时被安全网放行（否则容器拒绝启动）
CMD ["python3", "-c", "from integrated_app.app_server import run_server; run_server('0.0.0.0', 7869)"]
