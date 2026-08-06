"""Path configuration, constants, model path mapping, language list, etc.
Multi-engine configuration (VoxCPM2 + IndexTTS 2.0).

Configuration is managed through the AppConfig class, accessible via get_config().
Module-level deprecated variables have been removed; use get_config() instead.
"""

import os
import re

from .config_models import (
    ApiAuthConfig,
    GenerationDefaultsConfig,
    _apply_env_overrides,
    load_config_dict,
)
from .config_models import (
    AppConfig as _PydanticAppConfig,
)

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
    if os.path.basename(parent).lower() == "bin":
        return os.path.dirname(parent)
    if os.path.basename(current_path).lower() == "bin":
        return os.path.dirname(current_path)
    return parent


ROOT_DIR = get_project_root()
PROJECT_ROOT = ROOT_DIR
DATA_DIR = os.path.join(ROOT_DIR, "data")
CACHE_DIR = os.path.join(ROOT_DIR, "cache")
PRETRAINED_DIR = os.path.join(ROOT_DIR, "pretrained_models")
SAVE_DIR = os.path.join(ROOT_DIR, "outputs")
PERSONA_DIR = os.path.join(ROOT_DIR, "personas")

# --- VoxCPM2 Model Paths ---
VOXCPM2_MODEL_PATH = os.path.join(PRETRAINED_DIR, "VoxCPM2")
VOXCPM2_ASR_PATH = os.path.join(PRETRAINED_DIR, "SenseVoiceSmall")
VOXCPM2_DENOISER_PATH = os.path.join(PRETRAINED_DIR, "speech_zipenhancer")
LORA_DIR = os.path.join(ROOT_DIR, "lora")

# --- IndexTTS 2.0 Model Paths ---
INDEXTTS2_MODEL_PATH = os.path.join(PRETRAINED_DIR, "IndexTTS2")

# --- dots.tts Model Paths ---
DOTSTTS_MODEL_PATH = os.path.join(PRETRAINED_DIR, "dots.tts")


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
    """Load config.yaml and return parsed dict, or empty dict on failure."""
    config_path = os.path.join(ROOT_DIR, "config.yaml")
    if not os.path.exists(config_path):
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        import logging

        logging.getLogger("tts_multimodel").warning(f"Config load failed: {e}")
        return {}


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
        self._pydantic_config = load_config_dict(self._yaml_config or {})

        # Apply environment variable overrides for sensitive fields
        # (e.g. VAPID keys from .env file)
        _apply_env_overrides(self._pydantic_config)

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

    Returns (all_ok, missing_list) where missing_list contains descriptive
    strings for each engine whose model weights are missing or incomplete.
    """
    missing = []

    # VoxCPM2: directory must exist and contain weight files
    if not os.path.isdir(VOXCPM2_MODEL_PATH):
        missing.append(f"VoxCPM2 ({VOXCPM2_MODEL_PATH} 目录不存在)")
    elif not _has_model_weights(VOXCPM2_MODEL_PATH):
        missing.append(f"VoxCPM2 ({VOXCPM2_MODEL_PATH} 缺少模型权重文件)")

    # IndexTTS2: directory must exist and contain weight files
    if not os.path.isdir(INDEXTTS2_MODEL_PATH):
        missing.append(f"IndexTTS 2.0 ({INDEXTTS2_MODEL_PATH} 目录不存在)")
    elif not _has_model_weights(INDEXTTS2_MODEL_PATH):
        missing.append(f"IndexTTS 2.0 ({INDEXTTS2_MODEL_PATH} 缺少模型权重文件)")

    return len(missing) == 0, missing


def get_download_hints() -> dict[str, str]:
    hints = {}
    if not os.path.isdir(VOXCPM2_MODEL_PATH) or not _has_model_weights(VOXCPM2_MODEL_PATH):
        hints["voxcpm2"] = (
            "VoxCPM2 模型未找到。下载命令:\n"
            "  pip install modelscope\n"
            "  python scripts/download_voxcpm2.py\n"
            "  或: modelscope download OpenBMB/VoxCPM2 --local_dir pretrained_models/VoxCPM2"
        )
    if not os.path.isdir(INDEXTTS2_MODEL_PATH) or not _has_model_weights(INDEXTTS2_MODEL_PATH):
        hints["indextts2"] = (
            "IndexTTS 2.0 模型未找到。下载命令:\n"
            "  pip install modelscope\n"
            "  python scripts/download_indextts2.py\n"
            "  或: modelscope download IndexTeam/IndexTTS-2 --local_dir pretrained_models/IndexTTS2"
        )
    return hints


# --- Language list ---
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
