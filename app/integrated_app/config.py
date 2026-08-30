"""Path configuration, constants, model path mapping, language list, etc.
Multi-engine configuration (VoxCPM2 + IndexTTS 2.5).

Configuration is managed through the AppConfig class, accessible via get_config().
Module-level deprecated variables have been removed; use get_config() instead.

P0-1 改造：新增 ``save_config()`` 原子写入函数（来源：Seedvr2），
使用 tempfile + os.replace 策略避免配置文件半写损坏。

P0-2 改造：新增 ``load_config()`` 宽松接口（来源：Seedvr2），
Pydantic 验证失败时回退到原始 YAML 加载，保证启动不被配置错误阻塞。
【职责】config.yaml 读写与访问入口（原子写入、宽松加载、环境变量 setup）。【边界】字段 schema 定义在 config_models，本模块不做校验。

"""

import contextlib
import logging
import os
import re
import tempfile

from .config_models import (
    ApiAuthConfig,
    GenerationDefaultsConfig,
    load_config_dict,
)
from .config_models import (
    AppConfig as _PydanticAppConfig,
)

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------


def _load_dotenv():
    """Load .env file if it exists (no third-party dependency).

    Parses simple KEY=VALUE lines, ignoring comments (#) and blank lines.
    Values are set with os.environ.setdefault so explicit system env vars
    always take precedence over .env file entries.
    """
    env_path = os.path.join(ROOT_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Remove surrounding quotes if present
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                # Convert literal \n to actual newlines (for multi-line PEM keys)
                value = value.replace("\\n", "\n")
                if key:
                    os.environ.setdefault(key, value)
    except Exception as e:
        import logging

        logging.getLogger("tts_multimodel").warning(f".env load failed: {e}")


def _set_env():
    """Set default offline environment variables."""
    _load_dotenv()
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["MODELSCOPE_OFFLINE"] = "1"


def _setup_environment(yaml_config: dict):
    """Set environment variables from config.yaml environment section."""
    if yaml_config and "environment" in yaml_config:
        env_cfg = yaml_config["environment"]
        if isinstance(env_cfg, dict):
            for key, value in env_cfg.items():
                os.environ[key] = str(value)


# ---------------------------------------------------------------------------
# Path configuration (computed from project root, not from YAML)
# ---------------------------------------------------------------------------


def get_project_root():
    current_path = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(current_path)
    if os.path.basename(parent).lower() == "app":
        return os.path.dirname(parent)
    if os.path.basename(current_path).lower() == "app":
        return os.path.dirname(current_path)
    return parent


ROOT_DIR = get_project_root()
PROJECT_ROOT = ROOT_DIR
DATA_DIR = os.path.join(ROOT_DIR, "data")
CACHE_DIR = os.path.join(ROOT_DIR, "cache")
PRETRAINED_DIR = os.path.join(ROOT_DIR, "model")
SAVE_DIR = os.path.join(ROOT_DIR, "outputs")
PERSONA_DIR = os.path.join(ROOT_DIR, "personas")

# --- VoxCPM2 Model Paths ---
VOXCPM2_MODEL_PATH = os.path.join(PRETRAINED_DIR, "VoxCPM2")
VOXCPM2_ASR_PATH = os.path.join(PRETRAINED_DIR, "SenseVoiceSmall")
VOXCPM2_DENOISER_PATH = os.path.join(PRETRAINED_DIR, "speech_zipenhancer")
LORA_DIR = os.path.join(ROOT_DIR, "lora")

# --- IndexTTS 2.5 Model Paths ---
INDEXTTS2_MODEL_PATH = os.path.join(PRETRAINED_DIR, "IndexTTS-2.5")
# --- IndexTTS 2.0 Model Paths（独立权重目录，与 2.5 不通用）---
INDEXTTS20_MODEL_PATH = os.path.join(PRETRAINED_DIR, "IndexTTS-2.0")


# ---------------------------------------------------------------------------
# P1-3: 模型路径 shared / portable 双模式（来源：Image_MultiModel）
# ---------------------------------------------------------------------------


def get_pretrained_dir() -> str:
    """根据配置解析预训练模型根目录（P1-3 改造，来源：Image_MultiModel）。

    - ``portable`` 模式（默认）：返回项目内 ``model/`` 目录
    - ``shared`` 模式：返回 ``config.yaml -> models.shared_models_root`` 指定的外部目录
      若 ``shared_models_root`` 为空，回退到 portable 模式

    Returns:
        预训练模型根目录的绝对路径。
    """
    try:
        cfg = get_config()
        models_cfg = cfg.pydantic_config.models
        if models_cfg.model_source_mode == "shared" and models_cfg.shared_models_root:
            shared_root = models_cfg.shared_models_root
            logger.info(f"[P1-3] 使用共享模型目录: {shared_root}")
            return shared_root
    except Exception as e:
        logger.debug(f"[P1-3] 读取模型路径模式失败，回退到 portable: {e}")
    return PRETRAINED_DIR


def get_voxcpm2_model_path() -> str:
    """获取 VoxCPM2 模型路径（考虑 shared/portable 模式）。"""
    return os.path.join(get_pretrained_dir(), "VoxCPM2")


def get_voxcpm2_asr_path() -> str:
    """获取 SenseVoiceSmall ASR 模型路径（考虑 shared/portable 模式）。"""
    return os.path.join(get_pretrained_dir(), "SenseVoiceSmall")


def get_voxcpm2_denoiser_path() -> str:
    """获取 speech_zipenhancer 去噪器路径（考虑 shared/portable 模式）。"""
    return os.path.join(get_pretrained_dir(), "speech_zipenhancer")


def get_indextts2_model_path() -> str:
    """获取 IndexTTS 2.5 模型路径（考虑 shared/portable 模式）。"""
    return os.path.join(get_pretrained_dir(), "IndexTTS-2.5")


def get_indextts20_model_path() -> str:
    """获取 IndexTTS 2.0 模型路径（考虑 shared/portable 模式）。"""
    return os.path.join(get_pretrained_dir(), "IndexTTS-2.0")


def _ensure_dirs():
    """Create required directories."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(SAVE_DIR, exist_ok=True)
    os.makedirs(PERSONA_DIR, exist_ok=True)
    os.makedirs(LORA_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Config parsing helpers (split from the old monolithic _load_config)
# ---------------------------------------------------------------------------


def _load_yaml_config() -> dict:
    """Load config.yaml and return parsed dict, or empty dict on failure.

    P0-2 改造：内部调用 ``load_config()`` 宽松接口，Pydantic 验证失败时
    自动回退到原始 YAML 加载，保证启动不被配置错误阻塞。
    """
    return load_config(None)


def _parse_version(yaml_config: dict) -> str:
    """Parse version string from config."""
    if not yaml_config:
        return "0.0.0"
    try:
        version = yaml_config.get("version", "0.0.0")
        return str(version).strip().strip('"').strip("'")
    except Exception:
        return "0.0.0"


def _parse_generation_defaults(yaml_config: dict) -> GenerationDefaultsConfig:
    """Parse generation defaults from config.yaml generation section."""
    defaults = GenerationDefaultsConfig()
    if not yaml_config:
        return defaults
    try:
        gen = yaml_config.get("generation", {})
        if isinstance(gen, dict):
            valid_keys = GenerationDefaultsConfig.model_fields.keys()
            filtered = {k: v for k, v in gen.items() if k in valid_keys}
            return GenerationDefaultsConfig(**filtered)
    except Exception as e:
        import logging

        logging.getLogger("tts_multimodel").warning(f"Generation defaults parse failed: {e}")
    return defaults


def _parse_api_auth(yaml_config: dict) -> ApiAuthConfig:
    """Parse API auth settings from config.yaml api_auth section."""
    if not yaml_config:
        return ApiAuthConfig()
    try:
        auth_cfg = yaml_config.get("api_auth", {})
        if isinstance(auth_cfg, dict):
            return ApiAuthConfig(
                enabled=bool(auth_cfg.get("enabled", False)),
                token=str(auth_cfg.get("token", "")),
            )
    except Exception as e:
        import logging

        logging.getLogger("tts_multimodel").debug(f"API auth config parse failed (using defaults): {e}")
    return ApiAuthConfig()


# ---------------------------------------------------------------------------
# Centralized AppConfig
# ---------------------------------------------------------------------------


class AppConfig:
    """Centralized application configuration.

    Holds all configuration values loaded from config.yaml and provides
    property access for backward compatibility with module-level variables.

    Configuration is loaded lazily on first property access, not at construction
    time. This avoids triggering side effects (environment variables, directory
    creation, YAML parsing) during import or testing.

    Use ``get_config()`` to obtain the singleton instance.
    """

    def __init__(self):
        self._loaded = False
        self._yaml_config: dict = {}
        self._version: str = "0.0.0"
        self._generation_defaults: GenerationDefaultsConfig | None = None
        self._api_auth: ApiAuthConfig | None = None
        self._pydantic_config: _PydanticAppConfig | None = None

    def _ensure_loaded(self):
        """Trigger lazy initialization on first property access."""
        if self._loaded:
            return
        self._loaded = True

        # Initialize environment and directories
        _set_env()
        _ensure_dirs()

        # Load YAML config
        self._yaml_config = _load_yaml_config()

        # Apply environment overrides from YAML
        _setup_environment(self._yaml_config)

        # Parse all configuration sections
        self._version = _parse_version(self._yaml_config)
        self._generation_defaults = _parse_generation_defaults(self._yaml_config)
        self._api_auth = _parse_api_auth(self._yaml_config)

        # Build validated Pydantic config
        # P0-2 改造：Pydantic 验证失败时回退到默认配置，保证启动不阻塞
        try:
            self._pydantic_config = load_config_dict(self._yaml_config or {})
        except Exception as e:
            logger.warning(
                f"Pydantic 配置验证失败，使用默认配置。错误信息: {e}。建议修复 config.yaml 中的错误字段后重新保存。"
            )
            self._pydantic_config = _PydanticAppConfig()

    # -- Raw section accessors ------------------------------------------------

    @property
    def version(self) -> str:
        self._ensure_loaded()
        return self._version

    @property
    def generation_defaults(self) -> GenerationDefaultsConfig:
        self._ensure_loaded()
        return self._generation_defaults

    @property
    def api_auth(self) -> ApiAuthConfig:
        self._ensure_loaded()
        return self._api_auth

    @property
    def pydantic_config(self) -> _PydanticAppConfig:
        self._ensure_loaded()
        return self._pydantic_config

    # -- Computed properties (backward compat with old module-level vars) -----

    @property
    def gen_defaults_dict(self) -> dict:
        """Generation defaults as a plain dict (backward compat with GEN_DEFAULTS)."""
        self._ensure_loaded()
        return self._generation_defaults.model_dump()

    @property
    def api_auth_dict(self) -> dict:
        """API auth as a plain dict (backward compat with API_AUTH)."""
        self._ensure_loaded()
        return {"enabled": self._api_auth.enabled, "token": self._api_auth.token}


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_config_instance: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the application configuration singleton."""
    global _config_instance
    if _config_instance is None:
        _config_instance = AppConfig()
    return _config_instance


def force_load_config() -> AppConfig:
    """Force immediate configuration loading.

    Entry points (app_server.run_server, cli.main) should call this before
    create_app() to ensure environment variables and directories are set up
    proactively, rather than relying on lazy initialization.
    """
    cfg = get_config()
    cfg._ensure_loaded()
    return cfg


# ---------------------------------------------------------------------------
# P0-1: 原子写入配置文件（来源：Seedvr2）
# ---------------------------------------------------------------------------


def save_config(config_dict: dict, config_path: str | None = None) -> None:
    """原子写入保存配置到 YAML 文件（来源：Seedvr2 的 ``save_config`` 函数）。

    使用临时文件 + 原子替换策略避免写入过程中断导致配置文件损坏：
    1. 在目标目录创建隐藏临时文件（``.config_*.tmp``）
    2. 写入配置内容到临时文件
    3. 使用 ``os.replace`` 原子替换目标文件（同文件系统内是原子操作）
    4. 失败时安全清理临时文件

    Args:
        config_dict: 要保存的配置字典。
        config_path: 目标配置文件路径，为 None 时默认保存到项目根目录的 config.yaml。

    Raises:
        OSError: 目录创建、文件写入或替换失败时抛出（临时文件会被自动清理）。
        yaml.YAMLError: YAML 序列化失败时抛出。
    """
    import yaml

    if config_path is None:
        config_path = os.path.join(ROOT_DIR, "config.yaml")

    config_dir = os.path.dirname(config_path)
    if config_dir:
        os.makedirs(config_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=config_dir or None,
        prefix=".config_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(config_dict, f, default_flow_style=False, allow_unicode=True)
        os.replace(tmp_path, config_path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


# ---------------------------------------------------------------------------
# P0-2: 配置验证失败回退机制（来源：Seedvr2）
# ---------------------------------------------------------------------------


def load_config(config_path: str | None = None) -> dict:
    """对外宽松接口：Pydantic 验证失败时回退到原始 YAML 加载，保证启动不阻塞。

    内部先调用 ``load_config_dict`` 进行 Pydantic 严格验证，验证通过则返回
    ``model_dump()`` 序列化后的字典；验证失败时捕获异常，回退到原始
    ``yaml.safe_load`` 加载，并在日志中记录 warning 告知具体验证错误，
    方便用户排查。

    Args:
        config_path: 配置文件路径，为 None 时默认使用项目根目录的 config.yaml。

    Returns:
        dict: 配置字典。配置文件不存在时返回空字典 ``{}``。
    """
    if config_path is None:
        config_path = os.path.join(ROOT_DIR, "config.yaml")

    # 尝试 Pydantic 严格验证
    try:
        import yaml

        if not os.path.exists(config_path):
            return {}
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        validated = load_config_dict(raw)
        return validated.model_dump()
    except Exception as e:
        # 验证失败，回退到原始 YAML 加载
        logger.warning(
            f"配置验证失败，已回退到原始 YAML 加载。错误信息: {e}。"
            "部分字段可能使用默认值，建议修复 config.yaml 后重新保存以生成合法版本。"
        )
        if not os.path.exists(config_path):
            return {}
        try:
            import yaml

            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e2:
            logger.warning(f"原始 YAML 加载也失败: {e2}")
            return {}


# ---------------------------------------------------------------------------
# Module-level variables (backward compatible, deprecated)
#
# New code should use get_config() instead.
# ---------------------------------------------------------------------------

# NOTE: Module-level deprecated variables have been removed.
# Use get_config() to access configuration values instead.
# Examples:
#   get_config().version           (was VERSION)
#   get_config().generation_defaults  (was GEN_DEFAULTS)
#   get_config().api_auth_dict        (was API_AUTH)


def _has_model_weights(model_dir: str, min_size_mb: float = 10.0) -> bool:
    """Check if a model directory contains at least one weight file >= min_size_mb.

    Scans for common weight file extensions (.safetensors, .bin, .pt, .pth)
    and returns True if any file meets the minimum size threshold.
    """
    if not os.path.isdir(model_dir):
        return False
    weight_exts = {".safetensors", ".bin", ".pt", ".pth"}
    min_bytes = int(min_size_mb * 1024 * 1024)
    for fname in os.listdir(model_dir):
        ext = os.path.splitext(fname)[1].lower()
        if ext in weight_exts:
            fpath = os.path.join(model_dir, fname)
            try:
                if os.path.isfile(fpath) and os.path.getsize(fpath) >= min_bytes:
                    return True
            except OSError as e:
                import logging

                logging.getLogger("tts_multimodel").debug(f"[Config] 模型权重文件检查失败 {fpath}: {e}")
    return False


def check_models_available() -> tuple[bool, list[str]]:
    """Check if model files are complete and ready for loading.

    P1-3 改造：使用 ``get_pretrained_dir()`` 动态解析模型路径，支持 shared/portable 双模式。

    Returns (all_ok, missing_list) where missing_list contains descriptive
    strings for each engine whose model weights are missing or incomplete.
    """
    missing = []

    # P1-3: 动态获取模型路径（支持 shared/portable 双模式）
    voxcpm2_path = get_voxcpm2_model_path()
    indextts2_path = get_indextts2_model_path()

    # VoxCPM2: directory must exist and contain weight files
    if not os.path.isdir(voxcpm2_path):
        missing.append(f"VoxCPM2 ({voxcpm2_path} 目录不存在)")
    elif not _has_model_weights(voxcpm2_path):
        missing.append(f"VoxCPM2 ({voxcpm2_path} 缺少模型权重文件)")

    # IndexTTS2: directory must exist and contain weight files
    if not os.path.isdir(indextts2_path):
        missing.append(f"IndexTTS 2.5 ({indextts2_path} 目录不存在)")
    elif not _has_model_weights(indextts2_path):
        missing.append(f"IndexTTS 2.5 ({indextts2_path} 缺少模型权重文件)")

    return len(missing) == 0, missing


def get_download_hints() -> dict[str, str]:
    """获取模型下载提示（P1-3: 使用动态路径解析）。"""
    hints = {}
    voxcpm2_path = get_voxcpm2_model_path()
    indextts2_path = get_indextts2_model_path()
    if not os.path.isdir(voxcpm2_path) or not _has_model_weights(voxcpm2_path):
        hints["voxcpm2"] = (
            "VoxCPM2 模型未找到。下载命令:\n"
            "  pip install modelscope\n"
            "  python scripts/download_voxcpm2.py\n"
            "  或: modelscope download OpenBMB/VoxCPM2 --local_dir model/VoxCPM2"
        )
    if not os.path.isdir(indextts2_path) or not _has_model_weights(indextts2_path):
        hints["indextts2"] = (
            "IndexTTS 2.5 模型未找到。下载命令:\n"
            "  pip install modelscope\n"
            "  python scripts/download_indextts2.py\n"
            "  或: modelscope download IndexTeam/IndexTTS-2.5 --local_dir model/IndexTTS-2.5"
        )
    # IndexTTS 2.0 为可选变体（不进必检 missing 清单），仅在缺权重时给下载指引
    indextts20_path = get_indextts20_model_path()
    if not os.path.isdir(indextts20_path) or not _has_model_weights(indextts20_path):
        hints["indextts20"] = (
            "IndexTTS 2.0 模型未找到（可选引擎）。下载命令:\n"
            "  modelscope download IndexTeam/IndexTTS-2 --local_dir model/IndexTTS-2.0\n"
            "  或: python scripts/download_indextts20.py\n"
            "  依赖代码包与 2.5 共用（同一 index-tts 仓库，安装一次即可）"
        )
    return hints


# --- Language list ---
# --- 语言代码归一 ---
# WHY 需要这一层：仓库里存在三套互不相交的语言词表——
#   1. 本模块 _LANGS（UI 下拉的中文显示名，也是表单实际提交的 value）
#   2. text_frontend.TextNormalizer.normalize() 按 zh/en/ja/ko 分支
#   3. IndexTTS 引擎的 supported_langs 是 {Auto, ZH, EN, JA, ES, AR}（大写）
# 三者不互通的后果：UI 提交 "中文" 时，normalize() 走 else 分支打
# 「不支持的语言 '中文'，跳过规范化」并原样返回，引擎又因不在
# supported_langs 内静默回退 Auto —— 语种下拉框端到端零效果。
# 这里提供唯一映射入口，两个消费方各自再适配自己的大小写形态。
_LANG_ALIASES: dict[str, str] = {
    # 中文显示名（_LANGS 的取值）
    "中文": "zh",
    "英语": "en",
    "日语": "ja",
    "韩语": "ko",
    "德语": "de",
    "法语": "fr",
    "俄语": "ru",
    "葡萄牙语": "pt",
    "西班牙语": "es",
    "意大利语": "it",
    "自动检测": "auto",
    # ISO 代码自身与常见大小写 / 区域变体
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-tw": "zh",
    "zh-hant": "zh",
    "en": "en",
    "ja": "ja",
    "ko": "ko",
    "de": "de",
    "fr": "fr",
    "ru": "ru",
    "pt": "pt",
    "es": "es",
    "it": "it",
    "auto": "auto",
}


def to_lang_code(value: str | None) -> str:
    """把任意来源的语言标识归一为小写 ISO 代码或 ``auto``。

    Args:
        value: UI 显示名（``"中文"``）、小写代码（``"zh"``）、
            大写代码（``"ZH"``）、区域标签（``"zh-CN"``）或空值。

    Returns:
        str: 归一后的代码；无法识别时返回 ``"auto"``（让下游做自动检测，
        而不是抛错或静默跳过规范化）。
    """
    if not value:
        return "auto"
    key: str = str(value).strip().lower()
    if not key:
        return "auto"
    if key in _LANG_ALIASES:
        return _LANG_ALIASES[key]
    # 区域标签兜底：取主语言子标签（pt-BR -> pt），仍未知则 auto
    base: str = key.split("-", 1)[0]
    return _LANG_ALIASES.get(base, "auto")


_LANGS = [
    "中文",
    "英语",
    "日语",
    "韩语",
    "德语",
    "法语",
    "俄语",
    "葡萄牙语",
    "西班牙语",
    "意大利语",
    "自动检测",
]

# --- Dialect list (Chinese dialects supported by VoxCPM2) ---
_DIALECTS = [
    ("四川话", "四川话"),
    ("粤语", "粤语"),
    ("吴语", "吴语"),
    ("东北话", "东北话"),
    ("河南话", "河南话"),
    ("闽南语", "闽南语"),
    ("湖南话", "湖南话"),
    ("湖北话", "湖北话"),
    ("客家话", "客家话"),
]

# --- Audio extensions ---
_AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".flac"}

# --- Input validation limits ---
MAX_TEXT_LENGTH = 10000
MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024  # 100MB

# --- Persona name validation regex ---
_PERSONA_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\u4e00-\u9fff]{1,50}$")

# --- Role color mapping ---
_ROLE_COLOR_MAP = {
    "御姐": ("pink", "#EC4899"),
    "旁白": ("gray", "#6B7280"),
    "萝莉": ("pink", "#F472B6"),
    "萝莉音": ("pink", "#F472B6"),
    "御姐音": ("pink", "#EC4899"),
    "少年": ("blue", "#3B82F6"),
    "少年音": ("blue", "#3B82F6"),
    "大叔": ("purple", "#8B5CF6"),
    "正太": ("green", "#22C55E"),
    "女王": ("red", "#EF4444"),
    "暖男": ("orange", "#FB923C"),
    "暖男音": ("orange", "#FB923C"),
    "低音炮": ("purple", "#8B5CF6"),
    "少女音": ("pink", "#F472B6"),
    "青年男音": ("blue", "#3B82F6"),
    "中年男音": ("purple", "#8B5CF6"),
    "日系甜音": ("pink", "#F9A8D4"),
    "韩系甜音": ("pink", "#F9A8D4"),
}
