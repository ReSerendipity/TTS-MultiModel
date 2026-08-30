"""运行时设置 API 路由。

架构说明：
- 供 WebUI Settings 页使用：Generation 默认参数、Cache 策略、UI 语言/主题、Audio Player 配置、SSE 参数
- 路径前缀：``/api/system``
- 持久化机制：写入内存单例 + 立即同步保存到 ``config.yaml``（BOM UTF-8）
  * 写盘使用 **先写 .tmp 再 os.replace** 原子替换，防止断电/杀进程导致 YAML 半写损坏
  * 下一次启动通过 ``config.get_config()`` 从 config.yaml 重新加载
- 接口清单：
  * ``GET  /api/system/settings`` — 当前运行时完整设置（对应 config.yaml 各段）
  * ``PUT  /api/system/settings`` — 部分更新（deep_merge 语义，只改传了的字段）
  * ``POST /api/system/settings/reset`` — 恢复出厂默认（config.yaml.bak 或硬编码 DefaultConfig）
  * 向后兼容原接口：
    - ``GET/POST /api/system/advanced_params``
    - ``GET/POST /api/system/general_settings``
    - ``GET/POST /api/system/generation_defaults``
    - ``GET  /api/system/settings``（Settings 页原返回 device/vram/cache 信息的混合格式）
"""

import contextlib
import copy
import json
import logging
import os
import tempfile
from typing import Any

import aiofiles
import yaml
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("tts_multimodel")

router = APIRouter(prefix="/api/system", tags=["system"])

from .gpu import _get_gpu_device, _get_gpu_utilization  # noqa: E402
from .logs import log_operation  # noqa: E402

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------


def _project_root() -> str:
    """定位项目根目录（config.yaml 所在目录）。"""
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))


PROJECT_ROOT: str = _project_root()
CONFIG_YAML_PATH: str = os.path.join(PROJECT_ROOT, "config.yaml")
CONFIG_BAK_PATH: str = os.path.join(PROJECT_ROOT, "config.yaml.bak")
ADVANCED_PARAMS_PATH: str = os.path.join(PROJECT_ROOT, "advanced_params.json")
GENERAL_SETTINGS_PATH: str = os.path.join(PROJECT_ROOT, "general_settings.json")
GENERATION_DEFAULTS_PATH: str = os.path.join(PROJECT_ROOT, "generation_defaults.json")


# 不可热更新字段黑名单：命中任一路径 → 403 Forbidden
BLACKLIST_PATHS: frozenset = frozenset(
    {
        ("models",),  # 模型路径改动需要重启才生效
        ("server", "host"),  # Uvicorn 绑定地址启动后不可改
        ("server", "port"),  # Uvicorn 监听端口启动后不可改
        ("server", "workers"),  # workers 改变需重启 Uvicorn
        ("server", "ssl_certfile"),  # SSL 证书加载在启动期
        ("server", "ssl_keyfile"),  # SSL 证书加载在启动期
        ("api_auth", "enabled"),  # 中间件注册在启动期
        ("api_auth", "token"),  # 同上
        ("environment",),  # 环境变量设置在启动早期
        ("i18n",),  # i18n 配置改动需要重启重载 locale
    }
)

# 各数值字段合法范围（用于 PUT 校验，失败 → 400 ValidationError）
FIELD_RANGES: dict[tuple, tuple] = {
    ("generation", "cfg_value"): (1.0, 15.0),
    ("generation", "inference_timesteps"): (1, 400),
    ("generation", "retry_badcase_max_times"): (0, 10),
    ("generation", "retry_badcase_ratio_threshold"): (1.0, 20.0),
    ("generation", "split_max_chars"): (50, 500),
    ("generation", "target_lufs"): (-30.0, 0.0),
    ("generation", "idle_timeout"): (60, 3600),
    ("generation", "default_speed"): (0.1, 3.0),
    ("generation", "script_studio_silence_secs"): (0.0, 2.0),
    ("memory", "persona_cache_size"): (1, 2000),
    ("sse", "reconnect_interval"): (0.5, 30.0),
    ("sse", "heartbeat_interval"): (1.0, 300.0),
    ("ui", "sidebar_width"): (120, 600),
    ("ui", "sidebar_collapsed_width"): (24, 200),
    ("audio_player", "waveform_steps"): (20, 5000),
    ("audio_player", "progress_update_ms"): (10, 5000),
}


# ---------------------------------------------------------------------------
# Pydantic 响应/请求模型
# ---------------------------------------------------------------------------


class GenerationDefaultsUpdate(BaseModel):
    """VoxCPM2 生成默认参数（全部 Optional → 部分更新）。"""

    model_config = {"extra": "forbid"}

    cfg_value: float | None = None
    inference_timesteps: int | None = None
    normalize: bool | None = None
    denoise: bool | None = None
    retry_badcase: bool | None = None
    retry_badcase_max_times: int | None = None
    retry_badcase_ratio_threshold: float | None = None
    min_len: int | None = None
    max_len: int | None = None
    split_max_chars: int | None = None
    default_sample_rate: int | None = None
    default_speed: float | None = None
    default_seed: int | None = None
    script_studio_silence_secs: float | None = None
    target_lufs: float | None = None
    trim_silence_vad: bool | None = None
    idle_timeout: int | None = None


class ServerSettingsUpdate(BaseModel):
    """服务器运行时设置更新模型（部分更新，所有字段 Optional）。

    Attributes:
        auto_load_model: 启动时是否自动加载默认引擎模型。
        auto_open_browser: 启动后是否自动打开浏览器访问 WebUI。
        log_level: 日志级别（DEBUG/INFO/WARNING/ERROR）。
    """

    auto_load_model: bool | None = None
    auto_open_browser: bool | None = None
    log_level: str | None = None


class MemorySettingsUpdate(BaseModel):
    """显存与缓存策略设置更新模型。

    Attributes:
        persona_cache_size: Persona 嵌入缓存最大条目数（1-2000）。
        target_vram_usage_pct: 目标显存占用百分比（超过则触发 LRU 淘汰）。
    """

    persona_cache_size: int | None = None
    target_vram_usage_pct: float | None = None


class UISettingsUpdate(BaseModel):
    """UI 外观与布局设置更新模型。

    Attributes:
        language: 界面语言代码（zh-CN/en/ja/ko）。
        theme: 主题（dark/light）。
        sidebar_width: 侧边栏展开宽度（像素，120-600）。
        sidebar_collapsed_width: 侧边栏折叠宽度（像素，24-200）。
    """

    language: str | None = None
    theme: str | None = None
    sidebar_width: int | None = None
    sidebar_collapsed_width: int | None = None


class SSESettingsUpdate(BaseModel):
    """SSE 事件流配置更新模型。

    Attributes:
        enabled: 是否启用 SSE 实时进度推送。
        reconnect_interval: 断线重连间隔（秒，0.5-30.0）。
        heartbeat_interval: 心跳包发送间隔（秒，1.0-300.0）。
    """

    enabled: bool | None = None
    reconnect_interval: float | None = None
    heartbeat_interval: float | None = None


class AudioPlayerSettingsUpdate(BaseModel):
    """音频播放器设置更新模型。

    Attributes:
        waveform_steps: 波形渲染采样步数（20-5000）。
        default_sample_rate: 默认输出采样率（Hz）。
        progress_update_ms: 播放进度更新间隔（毫秒，10-5000）。
        auto_play: 生成完成后是否自动播放。
        auto_save: 生成完成后是否自动保存到输出目录。
        notifications: 是否启用完成通知（浏览器 Notification API）。
        output_format: 默认输出格式（wav/mp3/flac）。
    """

    waveform_steps: int | None = None
    default_sample_rate: int | None = None
    progress_update_ms: int | None = None
    auto_play: bool | None = None
    auto_save: bool | None = None
    notifications: bool | None = None
    output_format: str | None = None


class SettingsUpdateRequest(BaseModel):
    """运行时设置部分更新请求（所有字段 Optional，未传则保持原值）。"""

    model_config = {"extra": "allow"}

    server: ServerSettingsUpdate | None = None
    generation: GenerationDefaultsUpdate | None = None
    memory: MemorySettingsUpdate | None = None
    ui: UISettingsUpdate | None = None
    sse: SSESettingsUpdate | None = None
    audio_player: AudioPlayerSettingsUpdate | None = None


class SettingsResponse(BaseModel):
    """GET /api/system/settings 完整设置响应（只读）。"""

    model_config = {"extra": "allow"}

    version: str = Field(default="0.0.0", description="应用版本")
    server: dict[str, Any] = Field(default_factory=dict, description="服务器配置")
    generation: dict[str, Any] = Field(default_factory=dict, description="生成参数")
    memory: dict[str, Any] = Field(default_factory=dict, description="显存/缓存策略")
    models: dict[str, Any] = Field(default_factory=dict, description="模型路径（只读，不可通过 API 修改）")
    i18n: dict[str, Any] = Field(default_factory=dict, description="国际化")
    sse: dict[str, Any] = Field(default_factory=dict, description="SSE 事件流参数")
    audio_player: dict[str, Any] = Field(default_factory=dict, description="音频播放器")
    ui: dict[str, Any] = Field(default_factory=dict, description="UI 布局/语言/主题")
    cache: dict[str, Any] | None = Field(default=None, description="缓存命中统计（仅 Settings 页填充）")


# ---------------------------------------------------------------------------
# 通用 JSON 辅助（general_settings / generation_defaults / advanced_params）
# ---------------------------------------------------------------------------

_DEFAULT_GENERATION_DEFAULTS: dict[str, Any] = {
    "default_sample_rate": 24000,
    "default_speed": 1.0,
    "default_seed": 42,
    "script_studio_silence_secs": 0.4,
}

_DEFAULT_GENERAL_SETTINGS: dict[str, Any] = {
    "language": "zh-CN",
    "theme": "dark",
    "auto_save": True,
    "auto_play": False,
    "notifications": True,
    "output_format": "wav",
}


async def _load_json_file(path: str, defaults: dict[str, Any]) -> dict[str, Any]:
    """读取 JSON 文件并与 defaults 合并；文件不存在/损坏时返回 defaults。"""
    try:
        if os.path.exists(path):
            async with aiofiles.open(path, encoding="utf-8") as f:
                content = await f.read()
            data = json.loads(content)
            if isinstance(data, dict):
                merged = dict(defaults)
                merged.update(data)
                return merged
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning(f"加载 JSON {os.path.basename(path)} 失败: {exc}")
    return dict(defaults)


async def _save_json_file(path: str, payload: dict[str, Any]) -> None:
    """原子化写 JSON：.tmp → os.replace。"""
    dir_ = os.path.dirname(path) or "."
    os.makedirs(dir_, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
            json.dump(payload, tmp_f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except (OSError, TypeError, ValueError) as exc:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise RuntimeError(f"写入 JSON {os.path.basename(path)} 失败: {exc}") from exc


# ---------------------------------------------------------------------------
# YAML config.yaml 读写（核心）
# ---------------------------------------------------------------------------


def _load_yaml_raw() -> dict[str, Any]:
    """读取 config.yaml 原生 dict；失败返回 {}。"""
    if not os.path.exists(CONFIG_YAML_PATH):
        return {}
    try:
        with open(CONFIG_YAML_PATH, encoding="utf-8-sig") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError, ValueError) as exc:
        logger.error(f"config.yaml 读取失败: {exc}")
        return {}


def _save_yaml_raw(payload: dict[str, Any]) -> None:
    """原子化写 config.yaml：.tmp → os.replace，UTF-8 BOM + yaml.safe_dump。

    Why 先写 .tmp 再 os.replace：
        如果直接 ``yaml.safe_dump`` 写入 ``config.yaml``，写中途（比如写了一半
        server 段，还没写 models 段）进程被 kill（断电/任务管理器杀），
        ``config.yaml`` 会变成半写的残缺 YAML。下次启动 yaml.safe_load 抛
        ScannerError，整个应用打不开。原子替换保证磁盘上要么是完整的旧版、
        要么是完整的新版，不存在中间态。
    """
    dir_ = os.path.dirname(CONFIG_YAML_PATH) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_config_", suffix=".yaml", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as tmp_f:
            yaml.safe_dump(
                payload,
                tmp_f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
                width=120,
            )
        os.replace(tmp_path, CONFIG_YAML_PATH)
    except (OSError, yaml.YAMLError, TypeError) as exc:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise RuntimeError(f"写入 config.yaml 失败: {exc}") from exc


def _deep_update(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """深度合并 patch 到 base（非原地，返回新 dict）。用于 PUT /settings 部分更新语义。"""
    result = copy.deepcopy(base)
    stack: list[tuple] = [(result, patch)]
    while stack:
        cur, p = stack.pop()
        for k, v in p.items():
            if isinstance(v, dict) and isinstance(cur.get(k), dict):
                stack.append((cur[k], v))
            else:
                cur[k] = copy.deepcopy(v)
    return result


def _check_blacklist(patch: dict[str, Any], prefix: tuple = ()) -> str | None:
    """检查 patch 是否包含黑名单路径。返回第一个命中的路径描述，没命中返回 None。"""
    for k, v in patch.items():
        path = prefix + (k,)
        if path in BLACKLIST_PATHS:
            return ".".join(path)
        if isinstance(v, dict):
            hit = _check_blacklist(v, path)
            if hit:
                return hit
    return None


def _validate_ranges(patch: dict[str, Any], prefix: tuple = ()) -> str | None:
    """对传入的 patch 做数值范围校验；第一个非法字段返回描述，没发现返回 None。"""
    for k, v in patch.items():
        path = prefix + (k,)
        if isinstance(v, dict):
            hit = _validate_ranges(v, path)
            if hit:
                return hit
        elif path in FIELD_RANGES and isinstance(v, (int, float)) and not isinstance(v, bool):
            lo, hi = FIELD_RANGES[path]
            if v < lo or v > hi:
                return f"{'.'.join(path)} = {v}（合法范围 [{lo}, {hi}]）"
    return None


def _apply_patch_to_runtime(patch: dict[str, Any]) -> None:
    """把 patch 的值同步到内存单例 AppConfig（浅同步：仅支持已存在字段）。"""
    try:
        from ...config import get_config

        cfg = get_config()
        for section_name, section_value in patch.items():
            if not isinstance(section_value, dict):
                continue
            if not hasattr(cfg, section_name):
                continue
            target = getattr(cfg, section_name)
            # 对子模型逐个 setattr（Pydantic BaseModel 或 AppConfig 普通 dataclass）
            for fk, fv in section_value.items():
                try:
                    if hasattr(target, fk):
                        object.__setattr__(target, fk, fv)
                    elif hasattr(cfg, section_name):
                        # fallback：直接改 dict-like
                        with contextlib.suppress(TypeError, KeyError):
                            target[fk] = fv
                except (AttributeError, TypeError, ValueError):
                    pass
    except (ImportError, AttributeError, TypeError) as exc:
        logger.warning(f"运行时内存配置同步失败（非致命，下次启动生效）: {exc}")


# ---------------------------------------------------------------------------
# 新规范 API：GET/PUT /settings + POST /reset
# ---------------------------------------------------------------------------


@router.get(
    "/settings",
    summary="读取运行时设置",
    description="返回 config.yaml 全量设置 + 当前状态（向后兼容 Settings 页混合格式）",
)
async def get_settings() -> dict[str, Any]:
    """读取完整运行时设置。

    响应混合格式（100% 向后兼容原 Settings 页使用方式）：
    老字段（status/version/device/vram_used/cache_*）+ 新段落（server/generation/...）。
    """
    # --- 新结构：各段设置（来自 AppConfig Pydantic 模型或 YAML raw） ---
    yaml_data = _load_yaml_raw()
    from_pydantic: dict[str, Any] = {}
    try:
        from ...config import get_config

        cfg = get_config()
        if hasattr(cfg, "to_dict") and callable(cfg.to_dict):
            from_pydantic = cfg.to_dict()
        elif hasattr(cfg, "model_dump") and callable(cfg.model_dump):
            from_pydantic = cfg.model_dump()
    except (ImportError, AttributeError, TypeError, RuntimeError) as exc:
        logger.debug(f"AppConfig 取数失败，回退 yaml raw: {exc}")
    merged: dict[str, Any] = _deep_update(yaml_data, from_pydantic)

    response = SettingsResponse(
        version=str(merged.get("version", "2.0.2")),
        server=dict(merged.get("server", {})),
        generation=dict(merged.get("generation", {})),
        memory=dict(merged.get("memory", {})),
        models=dict(merged.get("models", {})),
        i18n=dict(merged.get("i18n", {})),
        sse=dict(merged.get("sse", {})),
        audio_player=dict(merged.get("audio_player", {})),
        ui=dict(merged.get("ui", {})),
    ).model_dump()

    # --- 向后兼容：Settings 页原字段（device/vram/cache 等混合信息） ---
    response["status"] = "ok"
    response["model_path"] = (
        merged.get("models", {}).get("voxcpm2", {}).get("path", "") if isinstance(merged.get("models"), dict) else ""
    )

    # --- 设备名 & VRAM ---
    try:
        from ...config import PRETRAINED_DIR

        response["model_path"] = PRETRAINED_DIR
    except (ImportError, AttributeError):
        pass

    response["device"] = "加载中..."
    response["vram_used"] = "--"
    response["vram_total"] = "--"
    response["vram_free"] = "--"
    response["vram_percent"] = 0
    response["gpu_util"] = "--"
    response["memory_used"] = "--"
    response["memory_total"] = "--"
    response["memory_free"] = "--"
    response["memory_percent"] = 0
    response["cpu_util"] = "--"
    response["current_lora"] = "无"
    response["cache_hits"] = 0
    response["cache_misses"] = 0
    response["cache_rate"] = 0
    response["cache_entries"] = 0
    response["cache_size_mb"] = 0

    try:
        from ...gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        if backend != GPUBackend.CPU:
            device_name = GPUBackendManager.get_device_name() or f"{backend.value.upper()}"
            response["device"] = f"{backend.value.upper()}: {device_name}"
        else:
            response["device"] = "CPU"
    except (ImportError, OSError, RuntimeError, AttributeError, ValueError):
        response["device"] = "CPU"

    # CPU 内存
    if response["device"] == "CPU":
        try:
            import psutil

            cpu_mem = psutil.virtual_memory()
            used = cpu_mem.total - cpu_mem.available
            free = cpu_mem.available
            response["memory_used"] = f"{round(used / (1024**3), 2)} GB"
            response["memory_total"] = f"{round(cpu_mem.total / (1024**3), 2)} GB"
            response["memory_free"] = f"{round(free / (1024**3), 2)} GB"
            response["memory_percent"] = round(used / cpu_mem.total * 100, 1) if cpu_mem.total > 0 else 0
            response["cpu_util"] = f"{round(psutil.cpu_percent(interval=0), 1)}%"
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            logger.debug(f"CPU 内存读取失败: {exc}")

    # GPU VRAM
    try:
        from ...gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        if backend != GPUBackend.CPU:
            device = _get_gpu_device()
            props = GPUBackendManager.get_device_properties(device)
            total = props.get("total_memory", 0)
            allocated = GPUBackendManager.memory_allocated(device)
            reserved = GPUBackendManager.memory_reserved(device)
            used = max(allocated, reserved)
            free = max(total - used, 0)
            response["vram_used"] = f"{round(used / (1024**3), 2)} GB"
            response["vram_total"] = f"{round(total / (1024**3), 2)} GB"
            response["vram_free"] = f"{round(free / (1024**3), 2)} GB"
            response["vram_percent"] = round(used / total * 100, 1) if total > 0 else 0
            try:
                util = _get_gpu_utilization()
                response["gpu_util"] = f"{int(util)}%"
            except (OSError, RuntimeError, ValueError):
                response["gpu_util"] = "N/A"
    except (ImportError, OSError, RuntimeError, AttributeError, ValueError):
        pass

    # LoRA 状态
    try:
        from ...model_registry import registry as _reg

        if _reg.current_engine == "voxcpm2":
            try:
                from ...engines.voxcpm2_engine import fn_voxcpm_get_lora_state

                lora_state = fn_voxcpm_get_lora_state()
                response["current_lora"] = lora_state.get("name", "已加载") if lora_state.get("loaded") else "无"
            except (ImportError, AttributeError, TypeError):
                response["current_lora"] = "不适用"
        else:
            response["current_lora"] = "不适用"
    except (ImportError, AttributeError, TypeError):
        pass

    # Cache 统计
    try:
        from ...model_manager import get_persona_cache_stats

        stats = get_persona_cache_stats()
        response["cache_hits"] = int(stats.get("hits", 0))
        response["cache_misses"] = int(stats.get("misses", 0))
        response["cache_rate"] = round(float(stats.get("hit_rate", 0)), 1)
        response["cache_entries"] = int(stats.get("size", 0))
        response["cache_size_mb"] = int(stats.get("size", 0)) * 2
        response["cache"] = {
            "hit_rate": response["cache_rate"],
            "hits": response["cache_hits"],
            "misses": response["cache_misses"],
            "size": response["cache_entries"],
            "maxsize": int(stats.get("maxsize", 0)),
        }
    except (ImportError, AttributeError, TypeError, ValueError, KeyError):
        response["cache"] = {
            "hit_rate": 0.0,
            "hits": 0,
            "misses": 0,
            "size": 0,
            "maxsize": 0,
        }

    # 通用设置 & 默认生成参数（向后兼容：Settings 页需要）
    try:
        response["general_settings"] = await _load_json_file(GENERAL_SETTINGS_PATH, _DEFAULT_GENERAL_SETTINGS)
    except Exception:  # noqa: BLE001
        response["general_settings"] = {}
    try:
        response["generation_defaults"] = await _load_json_file(GENERATION_DEFAULTS_PATH, _DEFAULT_GENERATION_DEFAULTS)
    except Exception:  # noqa: BLE001
        response["generation_defaults"] = dict(_DEFAULT_GENERATION_DEFAULTS)

    return response


@router.put(
    "/settings",
    summary="部分更新运行时设置",
    description="deep_merge 语义，未传字段保持不变；命中黑名单或范围非法返回 403/400",
)
async def update_settings(request: Request) -> dict[str, Any]:
    """运行时部分更新（PUT 语义）。

    三步：(1) 黑名单校验 → 403；(2) 数值范围校验 → 400；(3) 原子写 YAML + 同步内存单例。
    """
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"请求体不是合法 JSON：{exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请求体必须是 JSON 对象",
        )

    # 1) 黑名单校验
    hit_path = _check_blacklist(payload)
    if hit_path:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"字段 '{hit_path}' 不可热更新，请重启应用前手动修改 config.yaml",
        )

    # 2) 数值范围校验
    invalid = _validate_ranges(payload)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"数值越界：{invalid}",
        )

    # 3) 读取当前 → 深度合并 → 原子落盘 → 同步内存
    current = _load_yaml_raw()
    merged = _deep_update(current, payload)

    try:
        _save_yaml_raw(merged)
    except RuntimeError as exc:
        logger.error(f"[settings/PUT] 写入 config.yaml 失败: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    _apply_patch_to_runtime(payload)

    with contextlib.suppress(Exception):
        log_operation("config_update", "Runtime settings updated", list(payload.keys()))

    return {"status": "ok", "updated_fields": list(payload.keys()), "message": "已保存，热更新生效"}


@router.post(
    "/settings/reset", summary="恢复出厂默认", description="读取 config.yaml.bak，没有则回退硬编码 DefaultConfig"
)
def reset_settings() -> dict[str, Any]:
    """恢复出厂默认设置。

    优先级：config.yaml.bak（若存在且合法）> config_models 硬编码默认值。
    """
    defaults: dict[str, Any] = {}

    # 1) 尝试读 config.yaml.bak
    if os.path.exists(CONFIG_BAK_PATH):
        try:
            with open(CONFIG_BAK_PATH, encoding="utf-8-sig") as f:
                bak = yaml.safe_load(f)
            if isinstance(bak, dict):
                defaults = bak
                source = "config.yaml.bak"
        except (OSError, yaml.YAMLError, ValueError) as exc:
            logger.warning(f"读取 config.yaml.bak 失败，回退硬编码默认值: {exc}")
            defaults = {}

    if not defaults:
        # 2) 回退：从 Pydantic 默认值构建
        try:
            from ...config_models import AppConfig as PydanticAppConfig

            obj = PydanticAppConfig()
            defaults = obj.model_dump()
            source = "DefaultConfig（硬编码）"
        except (ImportError, AttributeError, TypeError, ValidationError) as exc:
            logger.warning(f"DefaultConfig 构造失败: {exc}")
            # 3) 最低保障：保留 version，其他段仅留空骨架
            defaults = {
                "version": "2.0.2",
                "server": {},
                "generation": {},
                "memory": {},
                "models": {},
                "i18n": {},
                "sse": {},
                "audio_player": {},
                "ui": {},
            }
            source = "fallback skeleton"

    # 原子写盘
    try:
        _save_yaml_raw(defaults)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    with contextlib.suppress(Exception):
        log_operation("config_update", "Settings reset to defaults", {"source": source})

    return {"status": "ok", "source": source, "message": "已恢复出厂设置，建议重启应用"}


# ---------------------------------------------------------------------------
# 向后兼容原接口：/advanced_params
# ---------------------------------------------------------------------------


@router.get("/advanced_params", summary="高级生成参数", description="读取 VoxCPM2 引擎专用高级参数")
def get_advanced_params() -> dict[str, Any]:
    try:
        from ...engines.voxcpm2_engine import get_advanced_params as _get_params

        params = _get_params()
        return {"status": "ok", "params": params.to_dict() if hasattr(params, "to_dict") else dict(params)}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"获取高级参数失败: {exc}")
        return {"status": "error", "message": str(exc), "params": {}}


@router.post("/advanced_params", summary="保存高级生成参数", description="更新并持久化 VoxCPM2 高级参数")
async def save_advanced_params(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    try:
        from ...engines.voxcpm2_engine import build_advanced_params

        validated: dict[str, Any] = {}
        if "retry_badcase" in payload:
            validated["retry_badcase"] = bool(payload["retry_badcase"])
        if "retry_badcase_max_times" in payload:
            v = int(payload["retry_badcase_max_times"])
            validated["retry_badcase_max_times"] = max(0, min(10, v))
        if "retry_badcase_ratio_threshold" in payload:
            v = float(payload["retry_badcase_ratio_threshold"])
            validated["retry_badcase_ratio_threshold"] = max(1.0, min(20.0, v))
        if "trim_silence_vad" in payload:
            validated["trim_silence_vad"] = bool(payload["trim_silence_vad"])
        if "target_lufs" in payload:
            v = float(payload["target_lufs"])
            validated["target_lufs"] = max(-30.0, min(0.0, v))
        if "idle_timeout" in payload:
            v = int(payload["idle_timeout"])
            validated["idle_timeout"] = max(60, min(3600, v))

        new_config = build_advanced_params(**validated)
        current = new_config.to_dict() if hasattr(new_config, "to_dict") else dict(new_config)

        await _save_json_file(ADVANCED_PARAMS_PATH, current)
        log_operation("config_update", "Advanced params updated", validated)
        return {"status": "ok", "params": current}

    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"保存高级参数失败: {exc}", exc_info=True)
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# 向后兼容原接口：/general_settings
# ---------------------------------------------------------------------------


@router.post("/general_settings", summary="保存通用设置", description="持久化 UI 语言/主题/自动保存等通用偏好")
async def save_general_settings(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    validated: dict[str, Any] = {}
    for k in ("language", "theme", "output_format"):
        if k in payload:
            validated[k] = str(payload[k])
    for k in ("auto_save", "auto_play", "notifications"):
        if k in payload:
            validated[k] = bool(payload[k])

    try:
        existing = await _load_json_file(GENERAL_SETTINGS_PATH, _DEFAULT_GENERAL_SETTINGS)
        existing.update(validated)
        await _save_json_file(GENERAL_SETTINGS_PATH, existing)
        log_operation("config_update", "General settings updated", validated)
        return {"status": "ok", "settings": existing}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.error(f"保存通用设置失败: {exc}", exc_info=True)
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# 向后兼容原接口：/generation_defaults
# ---------------------------------------------------------------------------


@router.get(
    "/generation_defaults", summary="默认生成参数", description="读取默认生成参数（sample_rate/speed/seed/silence）"
)
async def get_generation_defaults() -> dict[str, Any]:
    try:
        params = await _load_json_file(GENERATION_DEFAULTS_PATH, _DEFAULT_GENERATION_DEFAULTS)
        return {"status": "ok", "params": params}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"获取默认生成参数失败: {exc}")
        return {"status": "error", "message": str(exc), "params": dict(_DEFAULT_GENERATION_DEFAULTS)}


@router.post("/generation_defaults", summary="保存默认生成参数", description="持久化默认生成参数")
async def save_generation_defaults(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    validated: dict[str, Any] = {}
    if "default_sample_rate" in payload:
        v = int(payload["default_sample_rate"])
        validated["default_sample_rate"] = max(16000, min(48000, v))
    if "default_speed" in payload:
        v = float(payload["default_speed"])
        validated["default_speed"] = max(0.1, min(3.0, v))
    if "default_seed" in payload:
        validated["default_seed"] = int(payload["default_seed"])
    if "script_studio_silence_secs" in payload:
        v = float(payload["script_studio_silence_secs"])
        validated["script_studio_silence_secs"] = max(0.0, min(2.0, v))

    try:
        existing = await _load_json_file(GENERATION_DEFAULTS_PATH, _DEFAULT_GENERATION_DEFAULTS)
        existing.update(validated)
        await _save_json_file(GENERATION_DEFAULTS_PATH, existing)
        log_operation("config_update", "Generation defaults updated", validated)
        return {"status": "ok", "params": existing}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.error(f"保存默认生成参数失败: {exc}", exc_info=True)
        return {"status": "error", "message": str(exc)}
