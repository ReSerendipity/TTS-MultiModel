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
# Ubuntu 22.04 官方源无 3.12，经 deadsnakes PPA 提供（torch/funasr 等全量依赖均有 cp312 wheel）。
# --no-install-recommends：不拉 idle/lib2to3 等推荐包，减小体积与 CVE 面。
# 索引就绪守卫：apt-get update 对「某个索引没抓下来」只打一行
#   W: Some index files failed to download. They have been ignored, or old ones used instead.
# 然后 **返回 0**。deadsnakes 的 PPA 一抖，这一步就静默带着半套索引往下走，真正的报错落在
# 下一行、且长得完全不像网络问题：E: Unable to locate package python3.12。
# 2026-09-21 实测（docker-build.yml run 35598902698 / 35599696780 红，35607107888 绿）：
#   红的两次日志里有 Ign:7 https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu jammy/main amd64 Packages，
#   绿的那次同一行是 Get:7 ... Packages [44.3 kB]。同一条 update-alternatives: error:
#   alternative path /usr/share/man/man7/bash-builtins.7.gz 在绿色 run 里也出现 → 它是
#   apt-get upgrade 期间的良性噪声，不是构建失败的原因（先前误把它当根因，已推翻）。
# 所以：把「PPA 索引里真查得到 python3.12」当作继续装的前置条件，取不到就重试，
# 5 轮仍取不到就地硬停说清原因——不带着半套索引继续构建。
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common git git-lfs ffmpeg ca-certificates \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && for i in 1 2 3 4 5; do \
         apt-get update -o Acquire::Retries=3 -o Acquire::http::Timeout=30; \
         if apt-cache show python3.12 >/dev/null 2>&1; then break; fi; \
         if [ "$i" = 5 ]; then \
           echo "E: deadsnakes 索引重试 5 轮仍查不到 python3.12 —— 是 PPA 侧或网络故障，不是本仓依赖声明问题。" >&2; \
           echo "   可手工核对：curl -sI https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu/dists/jammy/main/binary-amd64/Packages.gz" >&2; \
           exit 1; \
         fi; \
         echo "W: deadsnakes 索引未就绪，15s 后第 $((i+1)) 轮重试" >&2; sleep 15; \
       done \
    && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3.12 -m ensurepip --upgrade

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
# 基础镜像 nvidia/cuda:12.1.0-runtime-ubuntu22.04 自带未打补丁的 openssl/libssl3/dirmngr 等，
# 不显式升级则 Trivy 安全门禁会因未修复的高危漏洞持续报红。DEBIAN_FRONTEND=noninteractive 已设，无交互阻塞。
# ⚠ 可复现性提示（P2-2）：upgrade 的补丁集随构建日期漂移，如需可复现构建请锁定基础镜像 digest 并定期 bump。
# ⚠ python3.12-venv 必装：Ubuntu/deadsnakes 的 python3.12 拆包，ensurepip 由 python3.12-venv 提供；
#   漏装则下一步 `python3.12 -m ensurepip --upgrade` 报 "No module named ensurepip" 直接构建失败
#   （与 builder 阶段 :25 保持一致，两处必须同装）。
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common ca-certificates ffmpeg \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && for i in 1 2 3 4 5; do \
         apt-get update -o Acquire::Retries=3 -o Acquire::http::Timeout=30; \
         if apt-cache show python3.12 >/dev/null 2>&1; then break; fi; \
         if [ "$i" = 5 ]; then \
           echo "E: deadsnakes 索引重试 5 轮仍查不到 python3.12（原因与修法见 builder 阶段同一段守卫的注释）。" >&2; \
           exit 1; \
         fi; \
         echo "W: deadsnakes 索引未就绪，15s 后第 $((i+1)) 轮重试" >&2; sleep 15; \
       done \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends python3.12 python3.12-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3.12 -m ensurepip --upgrade \
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
