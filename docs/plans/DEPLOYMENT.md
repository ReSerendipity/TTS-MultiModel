# 部署指南

> 本文档覆盖 TTS MultiModel 的生产部署配置，包括反向代理、SSL、超时设置等。
>
> **最后更新**：2026-08-01

---

## 1. 反向代理配置

### Nginx

SSE 连接需要特殊配置以避免超时和缓冲：

```nginx
server {
    listen 443 ssl http2;
    server_name tts.example.com;

    # SSL 配置
    ssl_certificate     /etc/nginx/ssl/tts.crt;
    ssl_certificate_key /etc/nginx/ssl/tts.key;

    # 代理到 TTS MultiModel (127.0.0.1:7869)
    location / {
        proxy_pass http://127.0.0.1:7869;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # SSE 端点特殊配置
    location /api/sse/events {
        proxy_pass http://127.0.0.1:7869;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        # 关键：禁用缓冲
        proxy_buffering off;
        proxy_cache off;

        # 关键：超时设置（SSE 长连接需要长超时）
        proxy_read_timeout 86400s;  # 24h
        proxy_send_timeout 86400s;

        # 支持 HTTP/1.1 chunked transfer
        chunked_transfer_encoding on;
    }

    # 生成端点（需要长超时以等待推理完成）
    location /api/generate/ {
        proxy_pass http://127.0.0.1:7869;
        proxy_http_version 1.1;
        proxy_read_timeout 600s;  # 10min
        proxy_send_timeout 600s;
        client_max_body_size 100M;  # 允许上传大音频文件
    }

    # 模型加载端点
    location /api/model/ {
        proxy_pass http://127.0.0.1:7869;
        proxy_read_timeout 300s;  # 5min
    }
}
```

### 关键配置项说明

| 配置 | 值 | 原因 |
|------|-----|------|
| `proxy_buffering off` | SSE 必须 | 缓冲会导致 SSE 事件延迟到达前端 |
| `proxy_read_timeout 86400s` | SSE 24h | SSE 是长连接，不能被代理超时切断 |
| `proxy_set_header Connection ""` | SSE | 关闭 keepalive 复用，避免连接池问题 |
| `client_max_body_size 100M` | 生成端点 | 允许上传大参考音频文件 |
| `proxy_read_timeout 600s` | 生成端点 | 推理可能需要数分钟 |

---

## 2. Docker 部署

### docker-compose.yml

```yaml
version: "3.8"
services:
  tts:
    build: .
    ports:
      - "7869:7869"
    volumes:
      - ./model:/app/model
      - ./personas:/app/personas
      - ./outputs:/app/outputs
      - ./config.yaml:/app/config.yaml
    environment:
      - TRANSFORMERS_OFFLINE=1
      - HF_HUB_OFFLINE=1
      - MODELSCOPE_OFFLINE=1
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
```

---

## 3. 系统服务 (systemd)

```ini
# /etc/systemd/system/tts-multimodel.service
[Unit]
Description=TTS MultiModel Service
After=network.target

[Service]
Type=simple
User=tts
WorkingDirectory=/opt/tts-multimodel
ExecStart=/opt/tts-multimodel/venv/bin/python app/clean_launch.py
Restart=always
RestartSec=10
Environment=TRANSFORMERS_OFFLINE=1
Environment=HF_HUB_OFFLINE=1
Environment=MODELSCOPE_OFFLINE=1

[Install]
WantedBy=multi-user.target
```

---

## 4. 超时层次架构

TTS MultiModel 有多层超时，从内到外：

| 层级 | 超时 | 默认值 | 配置位置 |
|------|------|--------|----------|
| 引擎推理 | 单次推理 | 无限制 | 引擎内部 |
| SSE 心跳 | 15s | `config.yaml: sse.heartbeat_interval` | 心跳保持连接 |
| SSE 空闲 | 5-30s | `config.yaml: sse.idle_base_interval` | 空闲轮询间隔 |
| HTTP 请求 | 60-300s | Uvicorn / Nginx | 等待推理完成 |
| 反向代理 | 600s-86400s | Nginx `proxy_read_timeout` | 代理层 |
| 浏览器 | 30s | EventSource 内置 | 自动重连 |

### 超时调优建议

1. **单用户场景**：保持默认配置
2. **多用户共享**：减小 `sse.idle_max_interval` 到 10s，降低空闲 CPU
3. **Nginx 代理**：SSE 路径必须 `proxy_buffering off` + `proxy_read_timeout 86400s`
4. **Cloudflare 代理**：需禁用 Cloudflare 的 100s 超时（Enterprise 或用 Spectrum）

---

## 5. 性能调优

### GPU 相关

| 参数 | 建议值 | 说明 |
|------|--------|------|
| VRAM | >= 6.5GB | VoxCPM2 最低要求 |
| RAM | >= 16GB | IndexTTS2 最低要求 |
| `CUDA_VISIBLE_DEVICES` | `0` | 指定 GPU 设备 |

### 并发

- TTS MultiModel 使用**单 Worker 串行**处理生成任务（`task_queue.py`）
- 不要使用 `uvicorn --workers > 1`，会导致显存竞争
- 前端 SSE 连接是**单端点多路复用**，不需要多连接

---

## 相关文档

| 文档 | 描述 |
|------|------|
| [模型下载指南](MODEL_DOWNLOAD_GUIDE.md) | 模型下载与配置 |
| [项目架构](PROJECT_ARCHITECTURE.md) | 系统架构概览 |
| [参数调整](ADJUSTABLE_PARAMETERS.md) | 配置参数参考 |
