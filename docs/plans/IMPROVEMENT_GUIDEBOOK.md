# TTS_MultiModel 改进指南书

> 最后更新: 2026-06-02

> 基于11维度技术审计报告 | 版本2.0.0 | 2026-05-13

---

## 前言

本指南书旨在将11维度技术审计报告中发现的问题，系统性地转化为可操作的实施计划。审计覆盖了依赖管理、CI/CD、启动性能、API安全、版本约束、代码重复、状态管理、SSE架构、CSS工程化、测试体系、模块拆分、协议对接、无障碍访问、国际化、样式规范等维度，共识别出19项改进需求。

本指南按优先级分为三个阶段，每项改进均包含**问题描述**、**影响评估**、**实施步骤（含代码示例）**和**验证方法**，确保团队可直接参照执行，无需二次解读。

---

## 如何使用本指南

### 三阶段优先级体系

| 阶段 | 标识 | 含义 | 建议时间 |
|------|------|------|----------|
| 阶段一 | 🔴 高优先级 | 部署/安全/可靠性问题，不修复将导致生产事故 | 第1-2周 |
| 阶段二 | 🟠 中优先级 | 可维护性/性能问题，长期忽视将严重拖慢开发效率 | 第3-5周 |
| 阶段三 | 🟡 低优先级 | 用户体验/合规性问题，影响用户满意度和法律合规 | 第6-8周 |

### 每项改进的标准格式

每项改进均包含以下四部分：

1. **问题描述** — 现状是什么，问题出在哪里
2. **影响评估** — 严重度 X/5，附带具体影响说明
3. **实施步骤** — 含基于项目实际代码的代码示例
4. **验证方法** — 具体可执行的验证命令或测试步骤

### 依赖关系标注

每项改进标题后标注前置依赖，例如 `→ 依赖 1.1` 表示需先完成改进项 1.1。

---

## 阶段一：🔴 高优先级（部署/安全/可靠性）

---

### 改进项 1.1：修复依赖管理

**问题描述**

`requirements.txt` 与 `pyproject.toml` 的依赖列表不一致。`pyproject.toml` 声明了20个依赖，而 `requirements.txt` 仅有14个，缺少以下5个关键依赖：

- `funasr>=1.0.0` — ASR模型加载核心依赖
- `modelscope>=1.9.0` — 模型下载生态依赖
- `jinja2>=3.1.0` — 模板渲染依赖
- `python-multipart>=0.0.6` — FastAPI文件上传依赖
- `aiofiles>=23.0` — 异步文件操作依赖

缺少这些依赖会导致：`pip install -r requirements.txt` 后应用无法启动（ASR模型加载失败、模板渲染报错、文件上传接口崩溃）。

**影响评估**：严重度 5/5 — 直接导致部署失败

**实施步骤**

**步骤1**：以 `pyproject.toml` 为唯一真实来源，重新生成 `requirements.txt`

```txt
# requirements.txt — 由 pyproject.toml 自动生成，请勿手动编辑
# 深度学习框架
torch>=2.5.1
torchvision>=0.16.0
torchaudio>=2.1.0
transformers>=4.57.0
tokenizers>=0.19.0
# AI 生态
funasr>=1.0.0
modelscope>=1.9.0
# Web 框架
fastapi>=0.110.0
uvicorn>=0.29.0
jinja2>=3.1.0
python-multipart>=0.0.6
# 音频处理
soundfile>=0.12.1
pydub>=0.25.1
# 数值计算
numpy>=1.24.0
scipy>=1.11.0
# 工具库
pyyaml>=6.0
httpx>=0.24.0
aiofiles>=23.0
cryptography>=41.0.0
```

**步骤2**：添加自动同步脚本 `scripts/sync_requirements.py`

```python
"""从 pyproject.toml 同步生成 requirements.txt"""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def sync():
    pyproject = ROOT / "pyproject.toml"
    requirements = ROOT / "requirements.txt"

    with open(pyproject, "rb") as f:
        data = tomllib.load(f)

    deps = data["project"]["dependencies"]
    header = (
        "# requirements.txt — 由 pyproject.toml 自动生成，请勿手动编辑\n"
    )

    with open(requirements, "w", encoding="utf-8") as f:
        f.write(header)
        for dep in deps:
            f.write(dep + "\n")

    print(f"已同步 {len(deps)} 个依赖到 {requirements}")

if __name__ == "__main__":
    sync()
```

**步骤3**：在 CI 流水线中添加一致性检查（见改进项 1.2）

**验证方法**

```bash
# 1. 运行同步脚本
python scripts/sync_requirements.py

# 2. 验证依赖数量一致
python -c "
import tomllib
with open('pyproject.toml', 'rb') as f:
    deps = tomllib.load(f)['project']['dependencies']
with open('requirements.txt') as f:
    lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]
print(f'pyproject.toml: {len(deps)} deps')
print(f'requirements.txt: {len(lines)} deps')
assert len(deps) == len(lines), '依赖数量不一致！'
print('✅ 依赖数量一致')
"

# 3. 全新环境安装测试
pip install -r requirements.txt
python -c "import funasr; import jinja2; import aiofiles; print('✅ 关键依赖可导入')"
```

---

### 改进项 1.2：建立 CI/CD 流水线

**问题描述**

项目当前无任何 CI/CD 配置。代码提交后无自动化检查，依赖不一致、语法错误、测试失败等问题只能靠人工发现，极易漏入生产环境。

**影响评估**：严重度 4/5 — 缺乏质量守门员，问题发现滞后

**实施步骤**

**步骤1**：创建 GitHub Actions 工作流 `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main, dev]
  pull_request:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]

    steps:
      - uses: actions/checkout@v4

      - name: 设置 Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: 安装依赖
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install ruff pytest pytest-asyncio

      - name: 依赖一致性检查
        run: python scripts/sync_requirements.py --check

      - name: 代码风格检查
        run: ruff check app/integrated_app/

      - name: 运行测试
        run: pytest tests/ -v --tb=short
        env:
          TRANSFORMERS_OFFLINE: "1"
          HF_HUB_OFFLINE: "1"
```

**步骤2**：创建 `scripts/sync_requirements.py` 的 `--check` 模式

在步骤1的脚本中追加：

```python
import sys

def check():
    pyproject = ROOT / "pyproject.toml"
    requirements = ROOT / "requirements.txt"

    with open(pyproject, "rb") as f:
        deps = tomllib.load(f)["project"]["dependencies"]

    with open(requirements) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]

    if set(deps) != set(lines):
        missing = set(deps) - set(lines)
        extra = set(lines) - set(deps)
        print(f"❌ 依赖不一致！缺失: {missing}, 多余: {extra}")
        sys.exit(1)
    print("✅ 依赖一致性检查通过")

if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        sync()
```

**步骤3**：创建基础测试目录 `tests/`

```
tests/
├── __init__.py
├── test_config_models.py
├── test_exceptions.py
└── test_i18n.py
```

**验证方法**

```bash
# 1. 本地模拟 CI 流程
pip install ruff pytest pytest-asyncio
python scripts/sync_requirements.py --check
ruff check app/integrated_app/
pytest tests/ -v

# 2. 推送后在 GitHub Actions 面板查看运行结果
git push origin dev
```

---

### 改进项 1.3：启用后台模型加载

**问题描述**

当前 `app_server.py` 的 `startup_event` 同步加载 VoxCPM2 模型，导致服务启动阻塞约30-60秒。期间所有HTTP请求（包括健康检查）均无法响应。同时，`_preload_voxcpm2_in_background` 函数已定义但未被使用。

```python
# 当前代码 — 同步阻塞启动
def startup_event():
    from .model_manager import load_voxcpm2
    logger.info("[启动] 正在加载 VoxCPM2 模型...")
    try:
        gen = load_voxcpm2()
        for status_text, _, _, _ in gen:
            logger.info(f"[启动] {status_text}")
        logger.info("[启动] VoxCPM2 模型已就绪，服务完全启动")
    except Exception as e:
        logger.error(f"[启动] VoxCPM2 模型加载失败: {e}")
```

**影响评估**：严重度 4/5 — 启动期间服务完全不可用

**实施步骤**

**步骤1**：修改 `app_server.py`，将同步启动改为后台线程启动

```python
def create_app() -> FastAPI:
    app = FastAPI(title="TTS MultiModel Voice Studio")

    # ... 中间件、静态文件、模板等配置保持不变 ...

    def startup_event():
        from .model_manager import load_voxcpm2, _preload_mgr
        import threading

        def _load_in_background():
            logger.info("[启动] 后台加载 VoxCPM2 模型中...")
            try:
                gen = load_voxcpm2()
                for status_text, _, _, _ in gen:
                    logger.info(f"[启动] {status_text}")
                app.state.models_ok = True
                logger.info("[启动] VoxCPM2 模型已就绪")
            except Exception as e:
                logger.error(f"[启动] VoxCPM2 模型后台加载失败: {e}")
                app.state.models_ok = False
                logger.info("[启动] 用户可手动点击加载按钮进行加载")

        app.state.models_ok = False
        t = threading.Thread(target=_load_in_background, daemon=True, name="model-startup-load")
        t.start()
        logger.info("[启动] 服务已启动，模型正在后台加载...")

    app.add_event_handler("startup", startup_event)
    return app
```

**步骤2**：更新健康检查端点以反映加载状态

`/api/health/ready` 已存在，只需确保 `app.state.models_ok` 在后台加载完成后被正确设置（步骤1已包含）。

**步骤3**：删除未使用的 `_preload_voxcpm2_in_background` 函数，其逻辑已被步骤1的后台线程替代。

**验证方法**

```bash
# 1. 启动服务并测量响应时间
start_time=$(date +%s)
python -c "from integrated_app.app_server import create_app; import uvicorn; uvicorn.run(create_app(), host='127.0.0.1', port=7869)" &
sleep 2
end_time=$(date +%s)
echo "服务启动耗时: $((end_time - start_time))秒（应<5秒）"

# 2. 验证健康检查在启动期间可响应
curl http://127.0.0.1:7869/api/health/ping
# 应立即返回 {"status":"ok",...}

# 3. 验证就绪检查反映加载状态
curl http://127.0.0.1:7869/api/health/ready
# 模型加载中: {"status":"degraded","models_available":false}
# 模型加载后: {"status":"ok","models_available":true}
```

---

### 改进项 1.4：实现 API 认证

**问题描述**

`config.yaml` 中已定义 `api_auth` 配置项但始终处于禁用状态，所有API端点完全开放，无任何认证机制。任何能访问服务端口的用户均可调用生成接口，存在资源滥用风险。

```yaml
# 当前配置 — 认证禁用
api_auth:
  enabled: false
  token: ""
```

**影响评估**：严重度 4/5 — 裸API可被未授权调用

**实施步骤**

**步骤1**：创建认证中间件 `app/integrated_app/auth.py`

```python
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from .config import load_app_config


class APIAuthMiddleware(BaseHTTPMiddleware):
    """Bearer Token 认证中间件"""

    _PUBLIC_PATHS = {
        "/api/health/ping",
        "/api/health/ready",
        "/static/",
        "/",
    }

    async def dispatch(self, request: Request, call_next):
        config = load_app_config()
        auth_cfg = config.get("api_auth", {})
        if not auth_cfg.get("enabled", False):
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) or path == p for p in self._PUBLIC_PATHS):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        expected_token = auth_cfg.get("token", "")
        if not expected_token:
            return await call_next(request)

        if auth_header != f"Bearer {expected_token}":
            raise HTTPException(status_code=401, detail="未授权访问")

        return await call_next(request)
```

**步骤2**：在 `app_server.py` 的 `create_app()` 中注册中间件

```python
from .auth import APIAuthMiddleware

def create_app() -> FastAPI:
    app = FastAPI(title="TTS MultiModel Voice Studio")

    app.add_middleware(APIAuthMiddleware)

    app.add_middleware(
        CORSMiddleware,
        # ... 保持不变 ...
    )
    # ... 其余配置 ...
```

**步骤3**：更新 `config.yaml`，生成安全令牌

```yaml
api_auth:
  enabled: true
  token: "${TTS_API_TOKEN}"  # 从环境变量读取
```

**步骤4**：更新 `config_models.py` 添加认证配置模型

```python
class AuthConfig(BaseModel):
    enabled: bool = Field(default=False, description="是否启用API认证")
    token: str = Field(default="", description="API访问令牌")

class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
    i18n: I18nConfig = Field(default_factory=I18nConfig)
    api_auth: AuthConfig = Field(default_factory=AuthConfig)
```

**验证方法**

```bash
# 1. 启用认证后，无Token请求应被拒绝
curl -X POST http://127.0.0.1:7869/api/generate/voxcpm_design \
  -d "text=测试"
# 应返回 401 Unauthorized

# 2. 带正确Token的请求应通过
curl -X POST http://127.0.0.1:7869/api/generate/voxcpm_design \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d "text=测试"
# 应正常处理

# 3. 公开路径无需Token
curl http://127.0.0.1:7869/api/health/ping
# 应返回 200 OK
```

---

### 改进项 1.5：修复 torch 生态版本约束

**问题描述**

`pyproject.toml` 中 torch、torchvision、torchaudio 的版本约束相互独立，未锁定兼容组合。PyTorch 生态要求三者版本严格匹配，否则会导致运行时 CUDA 错误或算子缺失。当前声明：

```toml
"torch>=2.5.1",
"torchvision>=0.16.0",
"torchaudio>=2.1.0",
```

实际上 `torch 2.5.1` 对应 `torchvision 0.20.1` 和 `torchaudio 2.5.1`，而非声明中的 `0.16.0` 和 `2.1.0`。

**影响评估**：严重度 4/5 — 版本不匹配导致运行时崩溃

**实施步骤**

**步骤1**：修正 `pyproject.toml` 中的版本约束

```toml
dependencies = [
    # === 深度学习框架（版本必须匹配） ===
    "torch>=2.5.1",
    "torchvision>=0.20.1",
    "torchaudio>=2.5.1",
    # ... 其余依赖不变 ...
]
```

**步骤2**：添加版本兼容性检查工具 `scripts/check_torch_compat.py`

```python
"""检查 torch/torchvision/torchaudio 版本兼容性"""
import sys

def check():
    try:
        import torch
        import torchvision
        import torchaudio
    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        sys.exit(1)

    torch_ver = tuple(int(x) for x in torch.__version__.split("+")[0].split(".")[:2])
    tv_ver = tuple(int(x) for x in torchvision.__version__.split("+")[0].split(".")[:2])
    ta_ver = tuple(int(x) for x in torchaudio.__version__.split("+")[0].split(".")[:2])

    # PyTorch 2.5.x 应匹配 torchvision 0.20.x 和 torchaudio 2.5.x
    expected_tv = (0, torch_ver[1] + 15)  # 2.5 -> 0.20
    expected_ta = (2, torch_ver[1])        # 2.5 -> 2.5

    ok = True
    if tv_ver[0] != expected_tv[0] or tv_ver[1] != expected_tv[1]:
        print(f"❌ torchvision {tv_ver} 与 torch {torch_ver} 不匹配，期望 {expected_tv}")
        ok = False
    if ta_ver[0] != expected_ta[0] or ta_ver[1] != expected_ta[1]:
        print(f"❌ torchaudio {ta_ver} 与 torch {torch_ver} 不匹配，期望 {expected_ta}")
        ok = False

    if ok:
        print(f"✅ torch {torch.__version__} / torchvision {torchvision.__version__} / torchaudio {torchaudio.__version__} 版本兼容")
    else:
        sys.exit(1)

if __name__ == "__main__":
    check()
```

**步骤3**：在 CI 流水线中添加兼容性检查步骤

```yaml
      - name: torch 生态版本兼容性检查
        run: python scripts/check_torch_compat.py
```

**验证方法**

```bash
# 1. 运行兼容性检查
python scripts/check_torch_compat.py

# 2. 验证 CUDA 可用性
python -c "import torch; print(f'CUDA可用: {torch.cuda.is_available()}, 版本: {torch.__version__}')"

# 3. 验证 torchaudio 功能
python -c "import torchaudio; print(f'torchaudio版本: {torchaudio.__version__}')"
```

---

## 阶段二：🟠 中优先级（可维护性/性能）

---

### 改进项 2.1：重构 generate.py 消除重复代码 → 依赖 1.3

**问题描述**

`generate.py` 中存在大量重复模式：

| 重复模式 | 出现次数 | 位置 |
|----------|---------|------|
| `_check_model_ready()` | 7次 | 每个端点开头 |
| `_DIALECT_NAMES` 方言集合定义 | 4次 | design/clone/ultimate/streaming_sse |
| `load_persona_embedding` 音色加载逻辑 | 5次 | design/clone/ultimate/script/streaming |
| `_run_with_oom_retry` + 计时 + 日志 + 后处理 | 5次 | design/clone/ultimate/script/prompt_continue |
| `voice_enhancement.lower() in ("true", "1", "yes")` | 6次 | 各端点 |

**影响评估**：严重度 3/5 — 修改一处逻辑需同步多处，易遗漏

**实施步骤**

**步骤1**：提取公共常量和工具函数到 `app/integrated_app/routes/_common.py`

```python
from ..model_manager import voxcpm_model
from ..persona_manager import load_persona_embedding
from ..gpu_utils import is_oom_error, free_gpu_memory
from ..monitor import get_health_monitor
from ..history_db import create_history_db
from ..config import SAVE_DIR
from ..audio_processing import enhance_audio
from ..exceptions import TTSError
from .system import log_operation, increment_generation
from ..model_manager import _time_estimator
import os
import time
import html
import logging
import numpy as np
from datetime import datetime
from urllib.parse import quote
from typing import Optional
from fastapi.responses import HTMLResponse

logger = logging.getLogger("tts_multimodel")

DIALECT_NAMES = frozenset({
    "四川话", "粤语", "吴语", "东北话",
    "河南话", "闽南语", "湖南话", "湖北话", "客家话",
})


def parse_bool_string(value: str) -> bool:
    return value.lower() in ("true", "1", "yes")


def check_model_ready():
    if voxcpm_model is None:
        return error_html("模型正在加载，请稍后再试...")
    return None


def merge_dialect_instruction(lang: str, instruction: str) -> str:
    if lang in DIALECT_NAMES:
        return (lang + "，" + instruction) if instruction.strip() else lang
    return instruction


def load_persona_ref_audio(persona_name: str, endpoint_label: str = ""):
    safe_name = os.path.basename(persona_name)
    persona_data = load_persona_embedding(safe_name)
    if persona_data is not None:
        wav_path, ref_text = persona_data
        if wav_path and os.path.isfile(wav_path):
            logger.info(f"[{endpoint_label}] 已加载音色 '{safe_name}' 的参考音频")
            return wav_path, None
        else:
            return None, f"音色文件不存在: {safe_name}"
    logger.warning(f"[{endpoint_label}] 音色 '{safe_name}' 不存在，将使用默认音色")
    return None, None


def error_html(error_message: str) -> HTMLResponse:
    return HTMLResponse(
        f'<div class="tts-error-block">'
        f'<div class="error-title">生成失败</div>'
        f'<div class="error-message">{html.escape(error_message)}</div>'
        f'</div>'
    )


def success_html(filename: str, status_message: str) -> HTMLResponse:
    safe_filename = quote(filename, safe='')
    return HTMLResponse(
        f'<div data-audio-filename="{html.escape(filename)}">'
        f'<audio controls src="/api/audio/{safe_filename}" style="width:100%;margin:8px 0;"></audio>'
        f'<div class="status-message success">{html.escape(status_message)}</div>'
        f'</div>'
    )


def partial_success_html(filename, message, degraded_note):
    safe_filename = quote(filename, safe='')
    return HTMLResponse(
        f'<div data-audio-filename="{html.escape(filename)}">'
        f'<audio controls src="/api/audio/{safe_filename}" style="width:100%;margin:8px 0;"></audio>'
        f'<div class="status-message success">{html.escape(message)}</div>'
        f'<div class="status-message warning" style="margin-top:8px;color:#f59e0b;">{html.escape(degraded_note)}</div>'
        f'</div>'
    )


_history_db = None

def get_history_db():
    global _history_db
    if _history_db is None:
        _history_db = create_history_db(SAVE_DIR)
    return _history_db


def record_to_history_db(filepath, text, engine, duration, **kwargs):
    try:
        db = get_history_db()
        filename = os.path.basename(filepath) if filepath else ""
        file_size = os.path.getsize(filepath) if filepath and os.path.exists(filepath) else 0
        db.insert({
            "filename": filename,
            "filepath": filepath or "",
            "created_at": datetime.now().isoformat(),
            "file_size_bytes": file_size,
            "duration_seconds": round(duration, 2),
            "text_preview": text[:100] if text else "",
            "engine": engine,
            "is_success": kwargs.get("is_success", True),
            **{k: v for k, v in kwargs.items() if k != "is_success"},
        })
    except Exception as e:
        logger.debug(f"History DB recording failed: {e}")


_generation_retry_counter = {"total": 0, "oom_retries": 0}


def run_with_oom_retry(run_fn, endpoint_name, degraded_fn=None):
    _generation_retry_counter["total"] += 1
    degraded_note = None
    try:
        result, msg = run_fn()
        return result, msg, degraded_note
    except Exception as e:
        if not is_oom_error(e):
            logger.error(f"{endpoint_name} failed (non-OOM): {e}")
            raise
        logger.warning(f"{endpoint_name} hit OOM, retrying with degraded params...")
        _generation_retry_counter["oom_retries"] += 1
        free_gpu_memory()
        degraded_note = "由于显存限制，已自动降低生成质量参数以完成生成。"
        if degraded_fn:
            try:
                result, msg = degraded_fn()
                return result, msg, degraded_note
            except Exception as e2:
                logger.error(f"{endpoint_name} failed after OOM retry (degraded): {e2}")
                raise
        else:
            try:
                result, msg = run_fn()
                return result, msg, degraded_note
            except Exception as e2:
                logger.error(f"{endpoint_name} failed after OOM retry: {e2}")
                raise


def safe_error_msg(e):
    if isinstance(e, TTSError):
        return str(e)
    return "生成失败，请稍后重试"


def log_generation(endpoint_name, text, engine, voice_or_persona, success, duration,
                   is_degraded=False, error_msg=None):
    if success:
        increment_generation(success=True)
        details = {
            "endpoint": endpoint_name, "engine": engine,
            "voice_persona": voice_or_persona,
            "text_length": len(text), "duration": round(duration, 2),
        }
        if is_degraded:
            details["degraded"] = True
        log_operation("generation", f"{endpoint_name} success ({duration:.1f}s)", details)
    else:
        increment_generation(success=False)
        details = {
            "endpoint": endpoint_name, "engine": engine,
            "voice_persona": voice_or_persona,
            "text_length": len(text), "duration": round(duration, 2),
        }
        if error_msg:
            details["error"] = str(error_msg)
        log_operation("generation", f"{endpoint_name} failed ({duration:.1f}s)", details)


def apply_post_processing(filename, tempo_factor, voice_enhancement, target_lufs):
    if tempo_factor == 1.0 and not voice_enhancement and target_lufs == -16.0:
        return filename
    from scipy.io import wavfile
    audio_path = os.path.join(SAVE_DIR, filename) if not os.path.isabs(filename) else filename
    if not os.path.isfile(audio_path):
        logger.warning(f"Post-processing: audio file not found: {audio_path}")
        return filename
    try:
        sr, data = wavfile.read(audio_path)
        if data.dtype == np.int16:
            audio = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            audio = data.astype(np.float32) / 2147483648.0
        elif data.dtype == np.float32:
            audio = data.copy()
        else:
            audio = data.astype(np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        processed = enhance_audio(audio, sr, normalize=True,
                                 tempo_factor=tempo_factor,
                                 voice_enhancement=voice_enhancement,
                                 target_lufs=target_lufs)
        base, ext = os.path.splitext(filename)
        new_filename = f"{base}_pp{ext}"
        new_path = os.path.join(SAVE_DIR, new_filename) if not os.path.isabs(new_filename) else new_filename
        output = (processed * 32768.0).clip(-32768, 32767).astype(np.int16)
        wavfile.write(new_path, sr, output)
        logger.info(f"Post-processing applied: {filename} -> {new_filename}")
        return new_filename
    except Exception as e:
        logger.error(f"Post-processing failed for {filename}: {e}")
        return filename
```

**步骤2**：重构各端点使用公共函数，以 `generate_voxcpm_design` 为例

```python
from ._common import (
    check_model_ready, merge_dialect_instruction, parse_bool_string,
    load_persona_ref_audio, run_with_oom_retry, log_generation,
    record_to_history_db, apply_post_processing, safe_error_msg,
    error_html, success_html, partial_success_html,
)

@router.post("/voxcpm_design")
async def generate_voxcpm_design(
    request: Request,
    text: str = Form(""),
    instruction: str = Form(""),
    persona_name: str = Form(""),
    lang: str = Form("Auto"),
    cfg: float = Form(2.0),
    steps: int = Form(10),
    denoise: str = Form("true"),
    tempo_factor: float = Form(1.0),
    voice_enhancement: str = Form("false"),
    target_lufs: float = Form(-16.0),
):
    not_ready = check_model_ready()
    if not_ready:
        return not_ready
    if not text.strip():
        return error_html("文本不能为空")

    instruction = merge_dialect_instruction(lang, instruction)
    advanced_denoise = parse_bool_string(denoise)

    actual_ref_path = None
    if persona_name:
        ref_path, err = load_persona_ref_audio(persona_name, "VoxCPM声音设计")
        if err:
            return error_html(err)
        actual_ref_path = ref_path

    loop = asyncio.get_running_loop()

    def _run():
        return fn_voxcpm_design(text, instruction, cfg_value=cfg,
                                inference_timesteps=steps, denoise=advanced_denoise,
                                ref_audio_path=actual_ref_path)

    start_time = time.monotonic()
    try:
        result, msg, degraded_note = await loop.run_in_executor(
            None, lambda: run_with_oom_retry(_run, "VoxCPM design")
        )
        duration = time.monotonic() - start_time
        if result is None:
            log_generation("VoxCPM design", text, "voxcpm2", instruction[:50], False, duration, error_msg=msg)
            return error_html(msg)
        is_degraded = degraded_note is not None
        log_generation("VoxCPM design", text, "voxcpm2", instruction[:50], True, duration, is_degraded=is_degraded)
        _time_estimator.record(len(text), duration, "voxcpm2", segment_count=1)
        if isinstance(result, tuple) and len(result) >= 3:
            audio_path = os.path.join(SAVE_DIR, result[2]) if not os.path.isabs(result[2]) else result[2]
            record_to_history_db(filepath=audio_path, text=text, engine="voxcpm2",
                                 duration=duration, model_type="声音设计",
                                 output_format=result[1] if len(result) > 1 else "wav",
                                 is_success=True)
        get_health_monitor().record_generation(success=True)
        filename = result[2]
        pp_ve = parse_bool_string(voice_enhancement)
        filename = apply_post_processing(filename, tempo_factor, pp_ve, target_lufs)
        if degraded_note:
            return partial_success_html(filename, msg, degraded_note)
        return success_html(filename, msg)
    except Exception as e:
        duration = time.monotonic() - start_time
        logger.error(f"VoxCPM design generation failed: {e}")
        log_generation("VoxCPM design", text, "voxcpm2", instruction[:50], False, duration, error_msg=str(e))
        return error_html(safe_error_msg(e))
```

**验证方法**

```bash
# 1. 确认公共模块可导入
python -c "from integrated_app.routes._common import DIALECT_NAMES, parse_bool_string, check_model_ready; print('✅ 公共模块导入成功')"

# 2. 功能回归测试 — 各端点仍正常工作
curl -X POST http://127.0.0.1:7869/api/generate/voxcpm_design \
  -d "text=测试文本" -d "denoise=true" -d "voice_enhancement=false"

# 3. 代码行数对比
wc -l app/integrated_app/routes/generate.py
# 重构前约1092行，目标减少30%+
```

---

### 改进项 2.2：统一状态管理 → 依赖 2.6

**问题描述**

`model_manager.py` 使用6个模块级全局变量管理状态，缺乏统一入口，容易导致状态不一致：

```python
voxcpm_model = None
voxcpm_asr = None
current_engine = "voxcpm2"
current_type = "voxcpm2"
current_size = "voxcpm2"
_persona_embedding_cache = AdaptiveLRUCache(default_maxsize=15)
_gen_tracker = GenerationTracker()
_progress_mgr = ProgressManager()
_model_lock = threading.RLock()
```

**影响评估**：严重度 3/5 — 全局变量散落，状态变更难以追踪

**实施步骤**

**步骤1**：创建 `app/integrated_app/state.py` 集中管理应用状态

```python
import threading
from .model_manager import AdaptiveLRUCache, GenerationTracker, ProgressManager


class AppState:
    """应用全局状态单例，提供线程安全的状态访问"""

    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self._lock = threading.RLock()
        self._voxcpm_model = None
        self._voxcpm_asr = None
        self._current_engine = "voxcpm2"
        self._current_type = "voxcpm2"
        self._current_size = "voxcpm2"
        self._persona_cache = AdaptiveLRUCache(default_maxsize=15)
        self._gen_tracker = GenerationTracker()
        self._progress_mgr = ProgressManager()
        self._model_lock = threading.RLock()

    @property
    def voxcpm_model(self):
        with self._lock:
            return self._voxcpm_model

    @voxcpm_model.setter
    def voxcpm_model(self, value):
        with self._lock:
            self._voxcpm_model = value

    @property
    def voxcpm_asr(self):
        with self._lock:
            return self._voxcpm_asr

    @voxcpm_asr.setter
    def voxcpm_asr(self, value):
        with self._lock:
            self._voxcpm_asr = value

    @property
    def current_engine(self):
        with self._lock:
            return self._current_engine

    @current_engine.setter
    def current_engine(self, value):
        with self._lock:
            self._current_engine = value

    @property
    def persona_cache(self):
        return self._persona_cache

    @property
    def gen_tracker(self):
        return self._gen_tracker

    @property
    def progress_mgr(self):
        return self._progress_mgr

    @property
    def model_lock(self):
        return self._model_lock

    def is_model_ready(self) -> bool:
        return self._voxcpm_model is not None

    def get_snapshot(self) -> dict:
        with self._lock:
            return {
                "engine": self._current_engine,
                "model_loaded": self._voxcpm_model is not None,
                "asr_loaded": self._voxcpm_asr is not None,
                "cache_stats": self._persona_cache.get_stats(),
                "queue_depth": self._gen_tracker.queue_depth,
            }


def get_state() -> AppState:
    return AppState()
```

**步骤2**：逐步迁移 `model_manager.py` 中的全局变量引用到 `AppState`

此步骤应渐进式进行，先在 `model_manager.py` 中添加兼容性别名，再逐个替换外部引用。

**验证方法**

```bash
# 1. 验证状态单例
python -c "
from integrated_app.state import get_state
s1 = get_state()
s2 = get_state()
assert s1 is s2, '单例不一致'
print('✅ 状态单例验证通过')
"

# 2. 验证线程安全
python -c "
from integrated_app.state import get_state
import threading
state = get_state()
errors = []
def set_engine(name):
    try:
        state.current_engine = name
    except Exception as e:
        errors.append(e)
threads = [threading.Thread(target=set_engine, args=(f'engine_{i}',)) for i in range(100)]
for t in threads: t.start()
for t in threads: t.join()
assert not errors, f'线程安全错误: {errors}'
print('✅ 线程安全验证通过')
"
```

---

### 改进项 2.3：合并 SSE 端点 → 依赖 2.1

**问题描述**

`sse.py` 中有5个独立的SSE端点，各自以不同轮询间隔（0.5s-2s）运行，导致前端需要建立5个长连接，服务端需要维护5个独立的异步生成器：

| 端点 | 轮询间隔 | 数据源 |
|------|---------|--------|
| `/sse/progress` | 0.5s | `_progress_mgr` |
| `/sse/status` | 2s | `_gen_tracker` + 全局状态 |
| `/sse/engine_switch` | 2s | `app.state.engine_switch_state` |
| `/sse/cancel` | 0.5s | `_progress_mgr.is_cancelled()` |
| `/sse/time_estimate` | 1s | `_time_estimator` + `_gen_tracker` |

**影响评估**：严重度 3/5 — 资源浪费，前端连接管理复杂

**实施步骤**

**步骤1**：创建统一的 SSE 端点 `/sse/events`

```python
@router.get("/events")
async def sse_unified(request: Request):
    """统一SSE端点，合并所有事件类型"""
    from ..model_manager import _progress_mgr, _gen_tracker, _time_estimator

    async def event_stream():
        start_time = time.time()
        last_status = None
        last_cancel_check = False

        while True:
            if time.time() - start_time > 600:
                break
            if await request.is_disconnected():
                break

            # 进度事件
            progress_html = _progress_mgr.get_progress_html()
            if progress_html:
                yield f"event: progress\ndata: {progress_html}\n\n"

            if _progress_mgr._is_complete:
                yield "event: complete\ndata: done\n\n"
                await asyncio.sleep(1)
                _progress_mgr.reset()

            # 状态事件
            import sys
            _mm = sys.modules.get("integrated_app.model_manager")
            status_data = json.dumps({
                "status_text": _gen_tracker.status_text(),
                "engine": (_mm.current_engine if _mm else None) or "none",
                "model_type": (_mm.current_type if _mm else None) or "none",
                "model_size": (_mm.current_size if _mm else None) or "none",
            }, ensure_ascii=False)
            if status_data != last_status:
                yield f"event: status\ndata: {status_data}\n\n"
                last_status = status_data

            # 取消事件
            if _progress_mgr.is_cancelled() and not last_cancel_check:
                cancel_data = json.dumps({"status": "cancelled", "message": "生成已取消"}, ensure_ascii=False)
                yield f"event: cancelled\ndata: {cancel_data}\n\n"
                last_cancel_check = True
            elif not _progress_mgr.is_cancelled():
                last_cancel_check = False

            # 时间估算事件
            current_depth = _gen_tracker.queue_depth
            if current_depth > 0:
                remaining = _gen_tracker.estimate_wait()
                est_data = json.dumps({
                    "status": "generating",
                    "remaining": round(remaining, 1),
                    "text": _format_time_estimate(remaining),
                }, ensure_ascii=False)
                yield f"event: time_estimate\ndata: {est_data}\n\n"

            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

**步骤2**：保留旧端点作为兼容别名（标记为 deprecated）

```python
@router.get("/progress", include_in_schema=False, deprecated=True)
async def sse_progress_compat(request: Request):
    """[已废弃] 请使用 /sse/events"""
    return await sse_unified(request)
```

**步骤3**：更新前端 JavaScript 使用统一端点

**验证方法**

```bash
# 1. 验证统一端点返回多种事件类型
curl -N http://127.0.0.1:7869/sse/events 2>&1 | head -20
# 应看到 event: progress, event: status, event: time_estimate 等多种事件

# 2. 验证旧端点仍可访问
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:7869/sse/progress
# 应返回 200

# 3. 连接数对比
# 重构前: 5个SSE连接
# 重构后: 1个SSE连接
```

---

### 改进项 2.4：修复 responsive.css

**问题描述**

`responsive.css` 使用了硬编码像素值和 `!important` 覆盖，且缺少对超宽屏幕的适配。当前文件262行中存在以下问题：

- 所有尺寸使用 `rem` 和 `px` 混合，无统一规范
- 缺少 `@media (min-width: 1440px)` 超宽屏断点
- 触摸目标 `min-height: 44px` 使用 `px` 而非 `rem`

**影响评估**：严重度 2/5 — 响应式体验不一致

**实施步骤**

**步骤1**：在 `responsive.css` 顶部添加 CSS 自定义属性

```css
:root {
    --touch-target: 2.75rem;   /* 44px / 16 = 2.75rem */
    --spacing-xs: 0.25rem;
    --spacing-sm: 0.5rem;
    --spacing-md: 0.75rem;
    --spacing-lg: 1rem;
    --font-sm: 0.85rem;
    --font-md: 1rem;
    --font-lg: 1.2rem;
}
```

**步骤2**：替换硬编码值为自定义属性

```css
/* 修改前 */
.btn {
    min-height: 44px;
}

/* 修改后 */
.btn {
    min-height: var(--touch-target);
}
```

**步骤3**：添加超宽屏断点

```css
/* 超宽屏 */
@media (min-width: 1440px) {
    .container, .main-content {
        max-width: 1200px;
        margin: 0 auto;
    }

    .persona-grid {
        grid-template-columns: repeat(4, 1fr);
    }

    .speaker-grid {
        grid-template-columns: repeat(4, 1fr);
    }
}
```

**验证方法**

```bash
# 1. 检查CSS语法
npx stylelint "app/integrated_app/static/css/responsive.css" --allow-empty-input

# 2. 浏览器测试各断点
# 375px (iPhone SE) / 768px (iPad) / 1024px (iPad Pro) / 1440px (桌面)
```

---

### 改进项 2.5：使用 pytest 重构测试 → 依赖 1.2

**问题描述**

当前测试文件 `test_gpu_utilization.py`、`test_integration.py`、`test_system_enhancements.py` 使用简单的脚本式测试，无统一框架、无 fixture、无参数化、无覆盖率报告。

**影响评估**：严重度 3/5 — 测试不可扩展，无法衡量覆盖率

**实施步骤**

**步骤1**：创建 `pyproject.toml` 中的测试依赖组

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "ruff>=0.4",
]
```

**步骤2**：创建 `pytest.ini` 或在 `pyproject.toml` 中配置

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = [
    "gpu: 需要GPU的测试",
    "slow: 运行缓慢的测试",
]
```

**步骤3**：编写核心测试文件

`tests/test_exceptions.py`：

```python
import pytest
from integrated_app.exceptions import (
    TTSError, ModelLoadError, InsufficientVRAMError,
    PersonaError, GenerationError, EngineSwitchError,
    tts_error_handler,
)


def test_error_hierarchy():
    assert issubclass(ModelLoadError, TTSError)
    assert issubclass(InsufficientVRAMError, TTSError)
    assert issubclass(PersonaError, TTSError)
    assert issubclass(GenerationError, TTSError)
    assert issubclass(EngineSwitchError, TTSError)


def test_error_codes():
    e = ModelLoadError()
    assert e.error_code == "MODEL_LOAD_ERROR"
    e = InsufficientVRAMError()
    assert e.error_code == "INSUFFICIENT_VRAM"


def test_tts_error_handler_wraps_unknown():
    @tts_error_handler
    def failing():
        raise ValueError("unknown")

    with pytest.raises(GenerationError, match="未知错误"):
        failing()


def test_tts_error_handler_passes_through():
    @tts_error_handler
    def failing():
        raise ModelLoadError("test")

    with pytest.raises(ModelLoadError):
        failing()
```

`tests/test_config_models.py`：

```python
import pytest
from integrated_app.config_models import (
    ServerConfig, GenerationConfig, MemoryConfig, AppConfig, load_config_dict,
)


def test_server_config_defaults():
    cfg = ServerConfig()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8080
    assert cfg.workers == 1


def test_server_config_rejects_multi_worker():
    with pytest.raises(ValueError, match="Workers > 1"):
        ServerConfig(workers=4)


def test_app_config_from_dict():
    data = {"server": {"port": 9999}, "generation": {"max_chars_per_segment": 300}}
    cfg = load_config_dict(data)
    assert cfg.server.port == 9999
    assert cfg.generation.max_chars_per_segment == 300


def test_app_config_ignores_unknown_keys():
    data = {"server": {"port": 8080, "unknown_key": "value"}}
    cfg = load_config_dict(data)
    assert cfg.server.port == 8080
```

`tests/test_i18n.py`：

```python
import pytest
from integrated_app.i18n import t, get_i18n_json, _I18N_TRANSLATIONS


def test_translate_zh_cn():
    assert t("app_title", "zh-CN") == "TTS MultiModel 语音工坊"


def test_translate_en():
    assert t("app_title", "en") == "TTS MultiModel Voice Studio"


def test_fallback_to_en():
    result = t("app_title", "ko")
    assert result == "TTS MultiModel 보이스 스튜디오"


def test_fallback_to_key():
    result = t("nonexistent_key", "en")
    assert result == "nonexistent_key"


def test_i18n_json_serializable():
    import json
    data = get_i18n_json("zh-CN")
    parsed = json.loads(data)
    assert isinstance(parsed, dict)
    assert "app_title" in parsed


def test_all_langs_have_same_keys():
    en_keys = set(_I18N_TRANSLATIONS["en"].keys())
    for lang in ["zh-CN", "ja", "ko"]:
        lang_keys = set(_I18N_TRANSLATIONS[lang].keys())
        missing = en_keys - lang_keys
        assert not missing, f"{lang} 缺少键: {missing}"
```

**验证方法**

```bash
# 1. 安装测试依赖
pip install -e ".[dev]"

# 2. 运行测试套件
pytest tests/ -v

# 3. 生成覆盖率报告
pytest tests/ --cov=integrated_app --cov-report=term-missing

# 4. 排除GPU测试（无GPU环境）
pytest tests/ -v -m "not gpu"
```

---

### 改进项 2.6：拆分 model_manager.py → 依赖 2.2

**问题描述**

`model_manager.py` 当前1146行，承担了过多职责：缓存管理（LRUCache/AdaptiveLRUCache）、生成追踪（GenerationTracker）、进度管理（ProgressManager）、GPU监控（GPUMemoryMonitor）、模型加载/卸载/切换、预加载。违反单一职责原则。

**影响评估**：严重度 3/5 — 文件过大，修改任一功能需理解全部代码

**实施步骤**

**步骤1**：拆分为以下模块

```
app/integrated_app/
├── cache.py           # LRUCache, AdaptiveLRUCache
├── progress.py        # ProgressManager
├── gen_tracker.py     # GenerationTracker
├── gpu_monitor.py     # GPUMemoryMonitor, get_nvidia_gpu_device, get_nvidia_gpu_memory_info
├── model_manager.py   # 仅保留模型加载/卸载/切换逻辑（~300行）
```

**步骤2**：`cache.py` 提取缓存类

```python
# app/integrated_app/cache.py
from collections import OrderedDict
from typing import Any, Optional
import threading
import torch
import logging

logger = logging.getLogger("tts_multimodel")


class LRUCache:
    # ... 从 model_manager.py 原样搬入 LRUCache 类 ...


class AdaptiveLRUCache(LRUCache):
    # ... 从 model_manager.py 原样搬入 AdaptiveLRUCache 类 ...
```

**步骤3**：`model_manager.py` 改为从新模块导入

```python
# app/integrated_app/model_manager.py
from .cache import AdaptiveLRUCache
from .progress import ProgressManager
from .gen_tracker import GenerationTracker
from .gpu_monitor import get_nvidia_gpu_device, get_nvidia_gpu_memory_info, GPUMemoryMonitor
```

**步骤4**：在 `model_manager.py` 中保留向后兼容的重新导出

```python
# 向后兼容 — 允许外部 from .model_manager import LRUCache
from .cache import LRUCache, AdaptiveLRUCache
from .progress import ProgressManager
from .gen_tracker import GenerationTracker
from .gpu_monitor import GPUMemoryMonitor, get_nvidia_gpu_device, get_nvidia_gpu_memory_info
```

**验证方法**

```bash
# 1. 验证所有导入路径仍有效
python -c "
from integrated_app.model_manager import LRUCache, AdaptiveLRUCache
from integrated_app.model_manager import GenerationTracker, ProgressManager
from integrated_app.model_manager import GPUMemoryMonitor, get_nvidia_gpu_device
print('✅ 向后兼容导入验证通过')
"

# 2. 验证新模块可直接导入
python -c "
from integrated_app.cache import LRUCache
from integrated_app.progress import ProgressManager
from integrated_app.gen_tracker import GenerationTracker
from integrated_app.gpu_monitor import GPUMemoryMonitor
print('✅ 新模块导入验证通过')
"

# 3. 运行测试
pytest tests/ -v
```

---

### 改进项 2.7：将 Protocol 与引擎实现对接 → 依赖 2.6

**问题描述**

`engine_interface.py` 定义了 `TTSEngine` 和 `ControllableTTSEngine` 两个 Protocol，但 `voxcpm2_engine.py` 并未显式实现这些接口，路由层直接调用引擎模块函数而非通过 Protocol。Protocol 定义形同虚设。

**影响评估**：严重度 2/5 — Protocol 定义存在但未使用，未来扩展引擎时无法保证接口一致性

**实施步骤**

**步骤1**：创建 `app/integrated_app/engines/voxcpm2_engine_impl.py`，将函数式接口封装为类

```python
from typing import Generator, Any, Optional, Tuple
from ..engine_interface import TTSEngine, ControllableTTSEngine
from .voxcpm2_engine import (
    fn_voxcpm_design, fn_voxcpm_clone, fn_voxcpm_ultimate_clone,
    fn_voxcpm_script_studio, fn_voxcpm_streaming, fn_voxcpm_prompt_continue,
)
from ..model_manager import voxcpm_model


class VoxCPM2Engine:
    """VoxCPM2 引擎实现，满足 TTSEngine 和 ControllableTTSEngine Protocol"""

    def is_ready(self) -> bool:
        return voxcpm_model is not None

    def load(self) -> None:
        from ..model_manager import load_voxcpm2
        for _ in load_voxcpm2():
            pass

    def unload(self) -> None:
        from ..model_manager import unload_model
        unload_model()

    def generate_voice_design(self, text: str, instruction: str = "",
                              normalize: bool = True) -> Tuple[str, str]:
        result, msg = fn_voxcpm_design(text, instruction, denoise=normalize)
        if result is None:
            raise GenerationError(msg)
        return result[2], msg

    def generate_voice_clone(self, text: str,
                             reference_audio_path: Optional[str] = None,
                             instruction: str = "", normalize: bool = True,
                             **kwargs) -> Tuple[str, str]:
        result, msg = fn_voxcpm_clone(text, instruction, reference_audio_path,
                                      normalize=normalize, **kwargs)
        if result is None:
            raise GenerationError(msg)
        return result[2], msg

    def generate_script(self, text: str, speaker_map: dict,
                        persona_map: dict = None, **kwargs) -> Tuple[str, str]:
        result, msg = fn_voxcpm_script_studio(text, persona_map_with_wav=persona_map,
                                               **kwargs)
        if result is None:
            raise GenerationError(msg)
        return result[2], msg

    def generate_streaming(self, text: str,
                           reference_audio_path: Optional[str] = None,
                           **kwargs) -> Generator:
        return fn_voxcpm_streaming(text, reference_audio_path, **kwargs)

    def generate_ultimate_clone(self, text: str, lang: str, ref_audio: str,
                                denoise_strength: str, use_random_seed: bool,
                                cfg_scale: float, denoise_steps: int,
                                seed: int) -> Tuple[str, str]:
        result, msg = fn_voxcpm_ultimate_clone(text, instruction="", ref_audio_path=ref_audio,
                                                cfg=cfg_scale, steps=denoise_steps, seed=seed)
        if result is None:
            raise GenerationError(msg)
        return result[2], msg

    def generate_with_prompt(self, text: str, prompt_wav_path: str,
                             prompt_text: str, **kwargs) -> Tuple[str, str]:
        result, msg = fn_voxcpm_prompt_continue(text, prompt_wav_path, prompt_text)
        if result is None:
            raise GenerationError(msg)
        return result[2], msg
```

**步骤2**：添加 Protocol 一致性检查测试

```python
# tests/test_engine_interface.py
from integrated_app.engine_interface import TTSEngine, ControllableTTSEngine


def test_voxcpm2_satisfies_tts_engine_protocol():
    from integrated_app.engines.voxcpm2_engine_impl import VoxCPM2Engine
    engine = VoxCPM2Engine()
    assert isinstance(engine, TTSEngine), "VoxCPM2Engine 未满足 TTSEngine Protocol"


def test_voxcpm2_satisfies_controllable_protocol():
    from integrated_app.engines.voxcpm2_engine_impl import VoxCPM2Engine
    engine = VoxCPM2Engine()
    assert isinstance(engine, ControllableTTSEngine), "VoxCPM2Engine 未满足 ControllableTTSEngine Protocol"
```

**验证方法**

```bash
# 1. 运行 Protocol 一致性测试
pytest tests/test_engine_interface.py -v

# 2. 验证运行时检查
python -c "
from integrated_app.engine_interface import TTSEngine
from integrated_app.engines.voxcpm2_engine_impl import VoxCPM2Engine
assert isinstance(VoxCPM2Engine(), TTSEngine)
print('✅ Protocol 运行时检查通过')
"
```

---

## 阶段三：🟡 低优先级（用户体验/合规性）

---

### 改进项 3.1：添加 ARIA 属性和表单标签关联

**问题描述**

当前无障碍现状极差：仅5处 `aria-label`，0处 `role` 属性，表单 `<label>` 无 `for` 属性关联。屏幕阅读器无法正确朗读界面元素。

**影响评估**：严重度 2/5 — 违反 WCAG 2.1 AA 标准

**实施步骤**

**步骤1**：为所有表单控件添加关联 `<label>`

在模板文件中（如 `tabs/voice_design.html`），将：

```html
<label>合成文本</label>
<textarea name="text"></textarea>
```

改为：

```html
<label for="design-text">合成文本</label>
<textarea id="design-text" name="text" aria-required="true"></textarea>
```

**步骤2**：为交互元素添加 `role` 和 `aria-label`

```html
<!-- 进度条 -->
<div class="tts-progress-bar" role="progressbar" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100" aria-label="生成进度">
  <div class="tts-progress-fill" style="width:0%"></div>
</div>

<!-- 标签页导航 -->
<nav role="tablist" aria-label="功能标签页">
  <button role="tab" aria-selected="true" aria-controls="panel-design" id="tab-design">声音设计</button>
</nav>
<div role="tabpanel" id="panel-design" aria-labelledby="tab-design">...</div>

<!-- 音频播放器 -->
<audio controls aria-label="生成结果音频" src="..."></audio>
```

**步骤3**：为动态内容添加 `aria-live` 区域

```html
<div id="generation-status" aria-live="polite" aria-atomic="true">
  <!-- 生成状态将在此更新 -->
</div>
```

**验证方法**

```bash
# 1. 使用 axe-core 自动化检测
npx @axe-core/cli http://127.0.0.1:7869/ --include-critical

# 2. 使用 Lighthouse 无障碍审计
npx lighthouse http://127.0.0.1:7869/ --only-categories=accessibility --output=html

# 3. 手动验证：使用 NVDA/VoiceOver 朗读页面
```

---

### 改进项 3.2：翻译字典外部化

**问题描述**

`i18n.py` 中4种语言的翻译字典（约200+键/语言）全部硬编码在 Python 文件中，总计1652行。添加新语言或修改翻译需要修改 Python 源码，不利于非开发人员参与翻译。

**影响评估**：严重度 2/5 — 翻译维护成本高，无法协作

**实施步骤**

**步骤1**：创建外部翻译文件目录结构

```
app/integrated_app/
├── locales/
│   ├── en.json
│   ├── zh-CN.json
│   ├── ja.json
│   └── ko.json
```

**步骤2**：编写导出脚本 `scripts/export_i18n.py`

```python
"""将 i18n.py 中的内嵌字典导出为 JSON 文件"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from integrated_app.i18n import _I18N_TRANSLATIONS

LOCALES_DIR = Path(__file__).resolve().parent.parent / "app" / "integrated_app" / "locales"
LOCALES_DIR.mkdir(exist_ok=True)

for lang, translations in _I18N_TRANSLATIONS.items():
    if translations is None:
        continue
    filepath = LOCALES_DIR / f"{lang}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(translations, f, ensure_ascii=False, indent=2)
    print(f"导出 {lang}: {len(translations)} 键 -> {filepath}")
```

**步骤3**：修改 `i18n.py` 从 JSON 文件加载

```python
_I18N_TRANSLATIONS = {}

_LOCALES_DIR = os.path.join(os.path.dirname(__file__), "locales")

def _load_translations():
    global _I18N_TRANSLATIONS
    if not os.path.isdir(_LOCALES_DIR):
        return
    for filename in os.listdir(_LOCALES_DIR):
        if not filename.endswith(".json"):
            continue
        lang = filename[:-5]  # 去掉 .json
        filepath = os.path.join(_LOCALES_DIR, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            _I18N_TRANSLATIONS[lang] = json.load(f)

_load_translations()
_I18N_TRANSLATIONS["zh-Hans"] = _I18N_TRANSLATIONS.get("zh-CN", {})
_I18N_TRANSLATIONS["zh"] = _I18N_TRANSLATIONS.get("zh-CN", {})

# 回退填充
for _d in _I18N_TRANSLATIONS.values():
    if _d is not None:
        for _k, _v in _I18N_TRANSLATIONS.get("en", {}).items():
            _d.setdefault(_k, _v)
```

**验证方法**

```bash
# 1. 运行导出脚本
python scripts/export_i18n.py

# 2. 验证 JSON 文件生成
ls -la app/integrated_app/locales/
# 应看到 en.json, zh-CN.json, ja.json, ko.json

# 3. 验证加载后翻译一致
python -c "
from integrated_app.i18n import t
assert t('app_title', 'zh-CN') == 'TTS MultiModel 语音工坊'
assert t('app_title', 'en') == 'TTS MultiModel Voice Studio'
print('✅ 翻译加载验证通过')
"
```

---

### 改进项 3.3：消除 CSS !important

**问题描述**

CSS 中使用 `!important` 覆盖样式，破坏了级联规则的可预测性。需通过提高选择器特异性替代。

**影响评估**：严重度 1/5 — 样式维护困难但功能不受影响

**实施步骤**

**步骤1**：扫描所有 `!important` 使用

```bash
grep -rn "!important" app/integrated_app/static/css/
```

**步骤2**：用选择器特异性替代 `!important`

```css
/* 修改前 */
.btn-primary {
    color: white !important;
}

/* 修改后 — 使用更高特异性 */
.tts-app .btn-primary {
    color: white;
}
```

**步骤3**：仅在第三方覆盖场景保留 `!important`，并添加注释说明原因

```css
/* !important: 覆盖 audio 元素浏览器默认样式 */
audio.tts-player {
    width: 100% !important;
}
```

**验证方法**

```bash
# 1. 统计 !important 数量（目标: 0 或仅第三方覆盖场景）
grep -c "!important" app/integrated_app/static/css/*.css

# 2. 视觉回归测试 — 对比修改前后截图
```

---

### 改进项 3.4：添加依赖锁定文件 → 依赖 1.1

**问题描述**

项目缺少依赖锁定文件（`requirements.lock` 或 `poetry.lock`），不同环境安装可能得到不同版本的依赖，导致"在我机器上能跑"问题。

**影响评估**：严重度 2/5 — 环境不一致导致难以复现的Bug

**实施步骤**

**步骤1**：生成锁定文件

```bash
pip freeze > requirements.lock
```

**步骤2**：在 CI 中使用锁定文件安装

```yaml
# .github/workflows/ci.yml
- name: 安装依赖（锁定版本）
  run: |
    pip install -r requirements.lock
```

**步骤3**：添加 `.gitignore` 规则（可选，视团队策略而定）

如果选择将 `requirements.lock` 提交到版本控制，则无需忽略。如果使用 `pip-tools`，则生成方式如下：

```bash
pip install pip-tools
pip-compile pyproject.toml -o requirements.lock
```

**验证方法**

```bash
# 1. 在两个不同环境使用锁定文件安装
pip install -r requirements.lock

# 2. 验证版本一致
pip list | grep torch
# 两个环境应输出相同版本
```

---

### 改进项 3.5：字体大小改为 rem 单位

**问题描述**

CSS 中大量使用 `px` 作为字体大小单位，无法随浏览器字体设置缩放，影响可访问性和高DPI显示效果。

**影响评估**：严重度 1/5 — 影响用户自定义字体大小

**实施步骤**

**步骤1**：在根 CSS 文件中定义字体比例

```css
:root {
    --font-size-xs: 0.75rem;    /* 12px */
    --font-size-sm: 0.85rem;    /* ~14px */
    --font-size-base: 1rem;     /* 16px */
    --font-size-md: 1.125rem;   /* 18px */
    --font-size-lg: 1.25rem;    /* 20px */
    --font-size-xl: 1.5rem;     /* 24px */
}
```

**步骤2**：逐步替换 `px` 为 `rem`

```css
/* 修改前 */
.header-title {
    font-size: 1.2rem;  /* 已经是 rem，保持 */
}

.tab-btn {
    font-size: 0.8rem;  /* 已经是 rem，保持 */
}

/* 修改前（styles.css 中可能存在的 px） */
.some-element {
    font-size: 14px;
}

/* 修改后 */
.some-element {
    font-size: var(--font-size-sm);
}
```

**验证方法**

```bash
# 1. 检查是否还有 px 字体大小
grep -rn "font-size.*px" app/integrated_app/static/css/
# 目标: 0 个匹配

# 2. 浏览器测试 — 调整默认字体大小，验证页面等比缩放
```

---

### 改进项 3.6：添加跳过导航链接

**问题描述**

页面缺少 skip-link（跳过导航链接），键盘用户每次需 Tab 穿过整个导航栏才能到达主内容区。

**影响评估**：严重度 2/5 — WCAG 2.1 SC 2.4.1 必要条件

**实施步骤**

**步骤1**：在 `base.html` 的 `<body>` 开头添加 skip-link

```html
<body>
    <a href="#main-content" class="skip-link">跳到主要内容</a>
    <!-- 其余页面内容 -->
    <main id="main-content">
        <!-- 主内容 -->
    </main>
</body>
```

**步骤2**：添加 skip-link 样式

```css
.skip-link {
    position: absolute;
    top: -40px;
    left: 0;
    background: #000;
    color: #fff;
    padding: 8px 16px;
    z-index: 10000;
    text-decoration: none;
    font-size: 1rem;
}

.skip-link:focus {
    top: 0;
}
```

**验证方法**

```bash
# 1. 键盘测试：按 Tab 键，第一个焦点应为 skip-link
# 2. 按 Enter 后焦点应跳转到 #main-content
# 3. Lighthouse 无障碍审计应通过 "Skip-link" 检查
```

---

### 改进项 3.7：模态框焦点陷阱实现

**问题描述**

当前模态框（删除确认、帮助对话框等）无焦点陷阱，Tab 键可跳出模态框到背景元素，违反 WCAG 2.1 SC 2.4.3 焦点顺序要求。

**影响评估**：严重度 2/5 — 键盘用户可能迷失在背景内容中

**实施步骤**

**步骤1**：创建焦点陷阱工具 `static/js/focus-trap.js`

```javascript
(function() {
    window.TTSFocusTrap = function(container) {
        var focusableSelectors = [
            'a[href]', 'button:not([disabled])', 'input:not([disabled])',
            'select:not([disabled])', 'textarea:not([disabled])',
            '[tabindex]:not([tabindex="-1"])'
        ];
        var focusableElements;
        var firstFocusable;
        var lastFocusable;
        var previousFocus = document.activeElement;

        function updateElements() {
            focusableElements = container.querySelectorAll(focusableSelectors.join(','));
            firstFocusable = focusableElements[0];
            lastFocusable = focusableElements[focusableElements.length - 1];
        }

        function handleKeydown(e) {
            if (e.key !== 'Tab') return;
            updateElements();
            if (e.shiftKey) {
                if (document.activeElement === firstFocusable) {
                    e.preventDefault();
                    lastFocusable.focus();
                }
            } else {
                if (document.activeElement === lastFocusable) {
                    e.preventDefault();
                    firstFocusable.focus();
                }
            }
        }

        this.activate = function() {
            updateElements();
            container.addEventListener('keydown', handleKeydown);
            if (firstFocusable) {
                firstFocusable.focus();
            }
        };

        this.deactivate = function() {
            container.removeEventListener('keydown', handleKeydown);
            if (previousFocus) {
                previousFocus.focus();
            }
        };
    };
})();
```

**步骤2**：在模态框打开/关闭时激活/停用焦点陷阱

```javascript
// 打开模态框
var modal = document.getElementById('delete-confirm-modal');
modal.style.display = 'block';
modal.setAttribute('role', 'dialog');
modal.setAttribute('aria-modal', 'true');
modal.setAttribute('aria-labelledby', 'modal-title');
var trap = new TTSFocusTrap(modal);
trap.activate();

// 关闭模态框
modal.style.display = 'none';
trap.deactivate();
```

**验证方法**

```bash
# 1. 键盘测试：打开模态框后按 Tab，焦点应在模态框内循环
# 2. 按 Escape 关闭模态框后，焦点应返回触发按钮
# 3. 使用 axe-core 检测 "ARIA dialog" 规则
npx @axe-core/cli http://127.0.0.1:7869/
```

---

## 改进路线图

### 依赖关系图

```
阶段一（高优先级）
  1.1 修复依赖管理 ──────────────────────────────────┐
  1.2 建立 CI/CD ───→ 依赖 1.1                       │
  1.3 启用后台模型加载                                │
  1.4 实现 API 认证                                  │
  1.5 修复 torch 版本约束                            │
                                                      │
阶段二（中优先级）                                     │
  2.1 重构 generate.py ──→ 依赖 1.3                  │
  2.2 统一状态管理                                    │
  2.3 合并 SSE 端点 ──→ 依赖 2.1                     │
  2.4 修复 responsive.css                            │
  2.5 使用 pytest 重构测试 ──→ 依赖 1.2              │
  2.6 拆分 model_manager.py ──→ 依赖 2.2             │
  2.7 Protocol 对接 ──→ 依赖 2.6                     │
                                                      │
阶段三（低优先级）                                     │
  3.1 ARIA 属性和标签关联                             │
  3.2 翻译字典外部化                                  │
  3.3 消除 CSS !important                            │
  3.4 添加依赖锁定文件 ──→ 依赖 1.1 ←────────────────┘
  3.5 字体大小改为 rem                                │
  3.6 添加跳过导航链接                                │
  3.7 模态框焦点陷阱                                  │
```

### 实施时间线

```
第1周  ──→ 1.1 修复依赖管理 + 1.5 torch版本约束
第2周  ──→ 1.2 CI/CD + 1.3 后台模型加载 + 1.4 API认证
第3周  ──→ 2.1 重构 generate.py + 2.2 统一状态管理
第4周  ──→ 2.3 合并SSE + 2.4 修复responsive.css
第5周  ──→ 2.5 pytest重构 + 2.6 拆分model_manager.py
第6周  ──→ 2.7 Protocol对接 + 3.1 ARIA属性
第7周  ──→ 3.2 翻译外部化 + 3.3 CSS !important + 3.4 依赖锁定
第8周  ──→ 3.5 rem单位 + 3.6 skip-link + 3.7 焦点陷阱
```

### 快速启动检查清单

以下10项是最关键的改进，建议优先完成：

- [ ] **1.1** `requirements.txt` 与 `pyproject.toml` 依赖一致（5个缺失依赖）
- [ ] **1.2** GitHub Actions CI 流水线已配置并运行通过
- [ ] **1.3** 服务启动时间 < 5秒（模型后台加载）
- [ ] **1.4** API 认证中间件已启用，公开路径白名单正确
- [ ] **1.5** torch/torchvision/torchaudio 版本兼容性检查通过
- [ ] **2.1** `generate.py` 重复代码已提取到 `_common.py`
- [ ] **2.5** pytest 测试套件运行通过，核心模块有基础覆盖
- [ ] **2.6** `model_manager.py` 已拆分为 cache/progress/gen_tracker/gpu_monitor
- [ ] **3.1** 所有表单控件有 `<label for>` 关联
- [ ] **3.6** 页面顶部有 skip-link 跳过导航

---

*本文档由技术审计报告自动生成，所有代码示例基于项目实际代码结构。实施前请在开发分支验证，确保不破坏现有功能。*
