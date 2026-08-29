"""Pydantic v2 配置模型层 — TTS_MultiModel 应用配置的类型安全中枢。

本模块是 config.yaml 与应用代码之间的类型安全桥梁，负责：

**架构职责：**
    1. **声明式配置契约定义**：以 Pydantic v2 BaseModel 定义所有配置段的
       数据结构、类型约束、取值范围和默认值，使 config.yaml 的结构
       成为可被静态检查的"活文档"。
    2. **与 config.py 的协作**：config.py 负责磁盘 YAML 文件的加载/保存/热
       重载路径管理；本模块负责将 YAML 解析后的原始 dict 转换并校验为强类型
       ``AppConfig`` 单例，供全局通过 ``get_config()`` 访问。
    3. **路由层类型安全入口**：所有路由（routes/）、引擎（engines/）、
       中间件（middleware/）均通过 ``AppConfig`` 的嵌套属性访问配置，
       杜绝 ``config["server"]["port"]`` 式的字符串键访问，避免 KeyError
       和类型漂移。
    4. **校验与熔断**：通过 Pydantic validator 在配置加载早期即发现非法值
       （如 workers > 1、认证 token 为空等），以 fail-safe 策略拒绝带病启动。
    5. **声明式引擎注册**：``EngineSpecConfig`` + ``ModelConfig.engines``
       构成引擎注册表的声明式来源，与 ``engine_interface.py`` 的
       ``InMemoryEngineRegistry`` 协作实现运行时引擎发现。

**协作链路：**
    start.bat -> clean_launch.py -> app_server.py
        -> config.py (读 YAML)
        -> config_models.load_config_dict() (本模块，校验+转换)
        -> get_config() 返回 AppConfig 单例
        -> 路由 / 引擎 / 中间件按属性读取

**向后兼容策略：**
    所有模型均使用 ``extra="ignore"``，config.yaml 中存在的未知字段会被
    Pydantic 静默丢弃，保证旧配置文件在新版本代码上仍可正常加载。
【职责】配置 Pydantic 模式与默认值（Server/Generation/API 等全部字段契约）。【边界】不做文件读写；环境覆盖逻辑在 config.py。

"""

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class AdvancedParamsConfig(BaseModel):
    """高级生成参数配置（不可变，替代全局 _ADVANCED_PARAMS 字典）。

    参考 Fish Speech 的 RAS (Repetition Aware Sampling) 策略，
    适配为音频段级别检测。启用后，在多段生成过程中自动检测
    退化输出（过短、静音、单调重复），并调整 cfg_value 重试。

    Attributes:
        max_len: 最大生成长度（token 数），固定值。
        split_max_chars: 每段最大字符数，用于长文本分段，固定值。
        retry_badcase: 是否自动重试坏案例（过短/静音/重复段）。
        retry_badcase_max_times: 单段最大重试次数，取值范围 [0, 10]。
        retry_badcase_ratio_threshold: 重试时长比率阈值（实际时长/期望时长 < 该值则判定为坏案例），必须 > 0。
        trim_silence_vad: 是否启用 VAD 静音裁切后处理。
        target_lufs: 响度归一化目标值 (LUFS)，取值范围 [-30, 0]。
        idle_timeout: 模型空闲超时自动卸载时间 (秒)，取值范围 [60, 3600]。
        enable_ras: 是否启用 RAS (Repetition Aware Sampling) 段级重复检测。
        ras_max_retries: RAS 每段最大重试次数，取值范围 [0, 5]。
        ras_cfg_increase: RAS 重试时 cfg_value 每次增量，取值范围 (0, 2.0]。
    """

    model_config = ConfigDict(extra="ignore")

    max_len: int = Field(default=3000, description="最大生成长度（固定值）")
    split_max_chars: int = Field(default=200, description="每段最大字符数（固定值）")
    retry_badcase: bool = Field(default=True, description="自动重试坏案例")
    retry_badcase_max_times: int = Field(default=3, ge=0, le=10, description="最大重试次数")
    retry_badcase_ratio_threshold: float = Field(default=6.0, gt=0, description="重试时长比率阈值")
    trim_silence_vad: bool = Field(default=True, description="VAD 静音裁切")
    target_lufs: float = Field(default=-16.0, ge=-30, le=0, description="目标响度 (LUFS)")
    idle_timeout: int = Field(default=300, ge=60, le=3600, description="空闲超时时间 (秒)")
    enable_ras: bool = Field(default=False, description="启用 RAS 段级重复检测（参考 Fish Speech）")
    ras_max_retries: int = Field(default=2, ge=0, le=5, description="RAS 每段最大重试次数")
    ras_cfg_increase: float = Field(default=0.5, gt=0, le=2.0, description="RAS 重试时 cfg_value 每次增量")

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于传递给模型生成函数）。

        Returns:
            dict[str, Any]: 该配置对象的字段名 -> 字段值映射。
        """
        return self.model_dump()


class ServerConfig(BaseModel):
    """HTTP 服务器相关配置。

    控制 FastAPI/Uvicorn 的绑定地址、端口、自动浏览器打开、Worker 数量等。

    Attributes:
        host: 绑定地址，默认 127.0.0.1（仅本地访问）。如需局域网访问可设为 0.0.0.0。
        port: 监听端口号，取值范围 [1, 65535]。
        port_fallback: 端口占用时是否在 fallback 区间内自动选取可用端口。
        port_fallback_min: 自动端口回退的搜索下限，取值范围 [1, 65535]。
        port_fallback_max: 自动端口回退的搜索上限，取值范围 [1, 65535]。
        open_browser: 启动成功后是否自动打开默认浏览器访问 WebUI。
        workers: Uvicorn Worker 进程数，取值范围 [1, 4]。GPU 模式下强制为 1。
    """

    model_config = ConfigDict(extra="ignore")

    host: str = Field(default="127.0.0.1", description="Bind address")
    port: int = Field(default=8080, ge=1, le=65535, description="Port number")
    port_fallback: bool = Field(default=True, description="Auto-select fallback if port occupied")
    port_fallback_min: int = Field(default=8080, ge=1, le=65535)
    port_fallback_max: int = Field(default=8090, ge=1, le=65535)
    open_browser: bool = Field(default=True, description="Auto-open browser on startup")
    workers: int = Field(default=1, ge=1, le=4, description="Worker count (1 for GPU)")

    @field_validator("host")
    @classmethod
    def host_must_be_loopback_or_docker(cls, v: str) -> str:
        """安全强制：host 只允许回环地址或 0.0.0.0（容器场景）。

        0.0.0.0 仅在 run_server 安全网放行时生效：必须配置
        api_auth.enabled=true + token，否则启动即拒绝（见 app_server.run_server）。
        """
        allowed = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
        if v not in allowed:
            raise ValueError(f"host must be loopback (127.0.0.1 / localhost / ::1) or 0.0.0.0, got: {v}")
        return v


class GenerationConfig(BaseModel):
    """生成流程全局参数配置。

    控制文本分段、采样率、语速、剧本工坊段间静音等跨引擎通用的生成行为。

    Attributes:
        max_chars_per_segment: 单段 TTS 最大字符数，取值范围 [50, 500]。
        default_sample_rate: 默认输出音频采样率 (Hz)。
        default_speed: 默认语速倍率，取值范围 (0, 3.0]，1.0 为原速。
        default_seed: 默认随机种子，保证可复现生成。
        script_studio_silence_secs: 剧本工坊多角色对话段间插入的静音时长 (秒)，取值范围 (0, 2.0]。
    """

    model_config = ConfigDict(extra="ignore")

    max_chars_per_segment: int = Field(default=200, ge=50, le=500, description="Max chars per TTS segment")
    default_sample_rate: int = Field(default=24000, description="Default audio sample rate")
    default_speed: float = Field(default=1.0, gt=0, le=3.0, description="Default speech speed")
    default_seed: int = Field(default=42, description="Default random seed")
    script_studio_silence_secs: float = Field(default=0.4, gt=0, le=2.0, description="Silence between script segments")


class MemoryConfig(BaseModel):
    """显存 / 内存管理策略配置。

    控制 Persona 嵌入缓存大小、GPU 显存目标占用率、检测间隔、预加载缓冲区等。

    Attributes:
        max_cache_size: LRU 音色克隆嵌入缓存的最大条目数，取值范围 [1, 100]。
        target_max_usage: GPU 显存目标占用率上限（触发主动清理的阈值），取值范围 (0, 0.95]。
        check_interval: GPU 显存轮询检测间隔 (秒)，取值范围 (0, 5.0]。
        preload_buffer: 模型预加载时预留的安全缓冲区大小 (MB)，取值范围 [256, 4096]。
    """

    model_config = ConfigDict(extra="ignore")

    max_cache_size: int = Field(default=15, ge=1, le=100, description="Max voice clone embeddings cached")
    target_max_usage: float = Field(default=0.75, gt=0, le=0.95, description="Target GPU memory usage ratio")
    check_interval: float = Field(default=0.5, gt=0, le=5.0, description="GPU memory check interval (seconds)")
    preload_buffer: int = Field(default=1024, ge=256, le=4096, description="Preload buffer size (MB)")


# ---------------------------------------------------------------------------
# 声明式引擎规格与 engine_interface.py 的 InMemoryEngineRegistry 协作原理：
#
#   1. 用户/开发者在 config.yaml 的 models.engines 下声明每个引擎（voxcpm2、
#      indextts2 等）的元数据：名称、仓库、显存需求、支持特性、语言等。
#
#   2. 启动时本模块将 YAML dict 解析为 dict[str, EngineSpecConfig]，挂在
#      ModelConfig.engines 上。
#
#   3. app_server.py 的 lifespan 回调通过 model_manager.py 读取
#      config.models.engines，遍历每个 EngineSpecConfig，调用
#      InMemoryEngineRegistry.register() 将声明式规格注册为运行时可用引擎。
#
#   4. UI 层（tabs 路由）同样遍历 engines 字典动态渲染引擎选择下拉框和
#      特性标签，无需在模板中硬编码引擎列表。
#
#   5. 新增引擎只需：在 config.yaml 添加一段声明 + 在 engines/ 下实现
#      TTSEngine 协议类，注册表自动发现，无需修改多处代码。
# ---------------------------------------------------------------------------
class EngineSpecConfig(BaseModel):
    """声明式引擎规格配置（对齐 VoiceBox 的 ModelConfig dataclass）。

    统一管理所有引擎元数据：名称、HF 仓库、大小、语言、显存需求、支持特性等。
    新引擎只需在 config.yaml 中声明即可自动注册和渲染。

    Attributes:
        name: 引擎内部标识符（如 voxcpm2, indextts2），用作 engines 字典的 key。
        display_name: UI 显示的人类可读名称（如 "VoxCPM 2.0 旗舰引擎"）。
        hf_repo: HuggingFace/ModelScope 仓库地址，用于下载引导脚本。
        model_dir: 本地模型目录名（相对于 model/ 根目录）。
        vram_gb: 最低显存需求 (GB)，用于加载前的显存预检，必须 > 0。
        ram_gb: 最低内存需求 (GB)，用于低配机器的友好提示，必须 > 0。
        languages: 该引擎支持的语言代码列表（如 ["zh", "en", "ja"]）。
        quality: 质量等级标签，可选值：x-low / low / medium / high。
        license: 引擎许可证类型（如 "MIT"、"Apache-2.0"、"CC BY-NC-SA 4.0"）。
        supported_features: 支持的特性标签列表，可选值：
            voice_design / clone / ultimate / script / streaming /
            emotion_control / lora。
        sample_rate: 该引擎的默认输出采样率 (Hz)。
        requires_gpu: 是否必须 GPU 运行；False 表示纯 CPU 推理也可用。
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="引擎内部标识符（如 voxcpm2, indextts2）")
    display_name: str = Field(default="", description="UI 显示名称")
    hf_repo: str = Field(default="", description="HuggingFace/ModelScope 仓库地址")
    model_dir: str = Field(default="", description="本地模型目录名（相对于 model/）")
    vram_gb: float = Field(default=6.0, gt=0, description="最低显存需求 (GB)")
    ram_gb: float = Field(default=16.0, gt=0, description="最低内存需求 (GB)")
    languages: list[str] = Field(default_factory=lambda: ["zh", "en"], description="支持语言列表")
    quality: str = Field(default="high", description="质量等级: x-low/low/medium/high")
    license: str = Field(default="", description="引擎许可证类型")
    supported_features: list[str] = Field(
        default_factory=list,
        description="支持特性: voice_design/clone/ultimate/script/streaming/emotion_control/lora",
    )
    sample_rate: int = Field(default=24000, description="输出采样率 (Hz)")
    requires_gpu: bool = Field(default=True, description="是否需要 GPU（False 表示 CPU 可用）")


class ModelConfig(BaseModel):
    """模型路径、显存需求与引擎注册表配置。

    P1-3 改造（来源：Image_MultiModel）：新增 ``model_source_mode`` 和
    ``shared_models_root`` 字段，支持 shared / portable 双模式模型路径解析。

    Attributes:
        model_source_mode: 模型路径模式：
            - ``portable``（默认）：使用项目内 ``model/`` 目录，自包含
            - ``shared``：使用 ``shared_models_root`` 指定的外部共享目录，
              可与 Seedvr2 / Image_MultiModel 共享模型文件，节省磁盘
        shared_models_root: shared 模式下的根目录绝对路径（如 ``C:/AI_Shared_Models``）。
            为空时回退到 portable 模式。
        base_dir: 模型权重根目录名（相对于项目根目录）。
        voxcpm_vram: VoxCPM2 引擎的最低显存需求 (GB)，必须 > 0。
        indextts2_vram: IndexTTS 2.5 引擎的最低显存需求 (GB)，必须 > 0。
        engines: 声明式引擎注册表，key 为引擎名称，value 为 EngineSpecConfig。
    """

    model_config = ConfigDict(extra="ignore")

    model_source_mode: Literal["shared", "portable"] = Field(
        default="portable",
        description="模型路径模式: portable(项目内) 或 shared(外部共享目录)",
    )
    shared_models_root: str = Field(
        default="",
        description="shared 模式下的根目录，如 C:/AI_Shared_Models",
    )
    base_dir: str = Field(default="model", description="Base directory for model weights")
    voxcpm_vram: float = Field(default=6.0, gt=0, description="VoxCPM2 VRAM requirement (GB)")
    indextts2_vram: float = Field(default=6.0, gt=0, description="IndexTTS 2.5 VRAM requirement (GB)")
    engines: dict[str, EngineSpecConfig] = Field(
        default_factory=dict,
        description="声明式引擎注册表，key 为引擎名称",
    )

    def get_engine_spec(self, engine_name: str) -> EngineSpecConfig | None:
        """获取指定引擎的声明式规格配置。

        优先使用 engines 字典中的配置，若不存在则返回 None。

        Args:
            engine_name: 引擎内部标识符（如 "voxcpm2"、"indextts2"）。

        Returns:
            Optional[EngineSpecConfig]: 匹配的引擎规格；若该引擎未在
                config.yaml 中声明则返回 None。
        """
        return self.engines.get(engine_name)


class I18nConfig(BaseModel):
    """国际化 (i18n) 配置。

    Attributes:
        default_lang: 默认语言代码，目前仅允许 "zh" 或 "en"。
        supported_langs: 支持的语言代码列表（UI 语言切换下拉框依据此字段渲染）。
    """

    model_config = ConfigDict(extra="ignore")

    default_lang: str = Field(default="zh", pattern="^(zh|en)$", description="Default language code")
    supported_langs: list[str] = Field(default=["zh", "en"], description="Supported language codes")


class SSEConfig(BaseModel):
    """SSE (Server-Sent Events) 事件流配置。

    控制 /api/sse/events 端点的推送节奏、空闲退避、心跳间隔，平衡实时性与带宽开销。

    Attributes:
        active_interval: 生成进行中的轮询等待超时 (秒)，越小越实时。
        idle_base_interval: 无任务时的基础等待间隔 (秒)，后续按 idle_step 递增退避。
        idle_max_interval: 空闲等待间隔上限 (秒)，避免无限制退避。
        idle_step: 每次空闲检测后的等待间隔增量 (秒)。
        heartbeat_interval: 空闲时向客户端发送 :ping 心跳的间隔 (秒)，防止中间代理断连。
    """

    model_config = ConfigDict(extra="ignore")

    active_interval: float = Field(default=0.3, description="活跃状态等待超时（秒）")
    idle_base_interval: float = Field(default=1.0, description="空闲基础等待超时（秒）")
    idle_max_interval: float = Field(default=3.0, description="空闲最大等待超时（秒）")
    idle_step: float = Field(default=0.5, description="空闲间隔递增步长（秒）")
    heartbeat_interval: float = Field(default=30.0, description="心跳间隔（秒）")


class AudioPlayerConfig(BaseModel):
    """WebUI 内嵌音频播放器配置。

    Attributes:
        waveform_steps: 波形可视化的采样点数（越高越精细，渲染开销越大）。
        default_sample_rate: 播放器默认重采样率 (Hz)。
        progress_update_ms: 播放进度条刷新间隔 (毫秒)。
    """

    model_config = ConfigDict(extra="ignore")

    waveform_steps: int = Field(default=300, description="波形采样步数")
    default_sample_rate: int = Field(default=44100, description="默认采样率")
    progress_update_ms: int = Field(default=100, description="进度更新间隔（毫秒）")


class UIConfig(BaseModel):
    """WebUI 全局布局配置。

    Attributes:
        sidebar_width: 侧边栏展开宽度 (px)。
        sidebar_collapsed_width: 侧边栏折叠宽度 (px)。
    """

    model_config = ConfigDict(extra="ignore")

    sidebar_width: int = Field(default=240, description="侧边栏展开宽度（px）")
    sidebar_collapsed_width: int = Field(default=52, description="侧边栏折叠宽度（px）")


class RuntimeTaskConfig(BaseModel):
    """运行时任务队列配置（P1-2: 断点续跑支持）。

    Attributes:
        checkpoint_dir: checkpoint 文件存储目录（相对于项目根目录）。
        checkpoint_every: 每隔多少个子任务写一次 checkpoint。
        auto_recover: 启动时是否自动恢复未完成的批量任务。
    """

    model_config = ConfigDict(extra="ignore")

    checkpoint_dir: str = Field(
        default="data/checkpoints",
        description="Checkpoint 文件存储目录",
    )
    checkpoint_every: int = Field(
        default=5,
        ge=1,
        le=100,
        description="每隔多少个子任务写一次 checkpoint",
    )
    auto_recover: bool = Field(
        default=False,
        description="启动时是否自动恢复未完成的批量任务",
    )


class RuntimeConfig(BaseModel):
    """运行时配置。

    Attributes:
        task: 任务队列与断点续跑配置。
    """

    model_config = ConfigDict(extra="ignore")

    task: RuntimeTaskConfig = Field(default_factory=RuntimeTaskConfig)


class ApiAuthConfig(BaseModel):
    """API Bearer Token 认证配置。

    Attributes:
        enabled: 是否启用 API 认证中间件（AppServer 启动时按需挂载）。
        token: Bearer Token 明文。启用后非空，否则所有请求将被拒绝（fail-safe）。
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(default=False, description="Whether API auth is enabled")
    token: str = Field(default="", description="API auth token")

    @model_validator(mode="after")
    def validate_auth_config(self) -> "ApiAuthConfig":
        """校验认证配置的完整性。

        当启用认证 (enabled=True) 但 token 为空时发出 UserWarning：
        所有请求将被 401 拒绝。这是 fail-safe 默认安全策略 —— 宁可让用户
        发现"为什么全被拒绝"而去配置正确的 token，也不能因为漏填 token
        导致认证名义上启用、实际零保护。

        Returns:
            ApiAuthConfig: 校验通过后的自身实例（Pydantic after validator
                必须返回实例以继续后续处理）。

        Raises:
            无显式 raise；当 enabled + 空 token 组合时发出 Python UserWarning。
        """
        # ------------------------------------------------------------------
        # Why warn + fail-safe 默认拒绝：
        #   用户可能误将 enabled=true 写入 config.yaml 但忘记设置 token。
        #   如果此时我们静默"关闭认证"或"允许空 token"，等于给用户制造
        #   已启用认证的错觉，实则门户大开。
        #   因此选择"看起来坏了"的行为（所有请求被拒），引导用户立刻
        #   发现配置缺失并补上正确 token，遵循安全设计的"默认拒绝"原则。
        # ------------------------------------------------------------------
        if self.enabled and not self.token:
            import warnings

            warnings.warn(
                "API auth is enabled but token is empty. All requests will be rejected.",
                UserWarning,
                stacklevel=2,
            )
        return self


class GenerationDefaultsConfig(BaseModel):
    """VoxCPM2 引擎生成默认参数（对应 config.yaml generation 段）。

    这些字段是 VoxCPM2 专属的推理超参，通过前端 WebUI 可逐次覆盖。

    Attributes:
        cfg_value: Classifier-Free Guidance 强度，值越高贴合度越强、多样性越低。
        inference_timesteps: 扩散推理步数，取值范围 [1, +inf)，越多质量越好但越慢。
        normalize: 生成后是否执行响度归一化。
        denoise: 是否启用后处理去噪（针对扩散模型输出的残留噪声）。
        retry_badcase: 是否自动重试坏案例。
        retry_badcase_max_times: 单段最大重试次数，取值范围 [0, 10]。
        retry_badcase_ratio_threshold: 坏案例判定的时长比率阈值，必须 > 0。
        min_len: 最短生成长度 (token)，取值范围 [1, +inf)。
        max_len: 最长生成长度 (token)，取值范围 [1, +inf)。
        split_max_chars: 长文本分段的单段最大字符数，取值范围 [50, 500]。
    """

    model_config = ConfigDict(extra="ignore")

    cfg_value: float = Field(default=2.0, description="CFG value")
    inference_timesteps: int = Field(default=10, ge=1, description="Inference timesteps")
    normalize: bool = Field(default=True, description="Normalize audio")
    denoise: bool = Field(default=True, description="Denoise audio")
    retry_badcase: bool = Field(default=True, description="Auto retry bad cases")
    retry_badcase_max_times: int = Field(default=3, ge=0, le=10, description="Max retry times")
    retry_badcase_ratio_threshold: float = Field(default=6.0, gt=0, description="Retry ratio threshold")
    min_len: int = Field(default=2, ge=1, description="Min generation length")
    max_len: int = Field(default=4096, ge=1, description="Max generation length")
    split_max_chars: int = Field(default=200, ge=50, le=500, description="Max chars per split")


class SecurityConfig(BaseModel):
    """安全配置。

    P2-1 改造：新增音频水印开关配置。

    Attributes:
        audio_watermark_enabled: 是否在输出音频中自动嵌入可溯源水印。
            注意：底层水印嵌入由代码常量 ``WATERMARK_SOURCE_ID`` 强制启用，
            此字段仅控制文件级水印的额外嵌入（用于批量后处理场景）。
    """

    model_config = ConfigDict(extra="ignore")

    audio_watermark_enabled: bool = Field(
        default=True,
        description="输出音频自动嵌入可溯源水印",
    )

    content_safety_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="文本内容安全检测置信度阈值（0.0~1.0），默认 0.3：单条强关键词命中（置信度≈0.333）即可拦截",
    )


class AppConfig(BaseModel):
    """根应用配置模型 — 整个配置树的顶层容器。

    所有嵌套子模型在此聚合，加载后通过 ``get_config()`` 作为单例全局共享。

    Attributes:
        version: 应用版本号（从 config.yaml 顶层读取，去引号清洗）。
        server: ServerConfig — HTTP 服务器配置。
        generation: GenerationConfig — 跨引擎通用生成流程参数。
        generation_defaults: GenerationDefaultsConfig — VoxCPM2 专属默认超参。
        memory: MemoryConfig — 显存/内存管理策略。
        models: ModelConfig — 模型路径、显存需求、引擎注册表。
        i18n: I18nConfig — 国际化配置。
        api_auth: ApiAuthConfig — API Bearer Token 认证配置。
        sse: SSEConfig — SSE 事件流参数。
        audio_player: AudioPlayerConfig — WebUI 播放器参数。
        ui: UIConfig — WebUI 布局参数。
        runtime: RuntimeConfig — 运行时任务队列与断点续跑配置。
        security: SecurityConfig — 安全配置（音频水印等）。
    """

    model_config = ConfigDict(extra="ignore")

    version: str = Field(default="0.0.0", description="Application version")
    server: ServerConfig = Field(default_factory=ServerConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    generation_defaults: GenerationDefaultsConfig = Field(default_factory=GenerationDefaultsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
    i18n: I18nConfig = Field(default_factory=I18nConfig)
    api_auth: ApiAuthConfig = Field(default_factory=ApiAuthConfig)
    sse: SSEConfig = Field(default_factory=SSEConfig)
    audio_player: AudioPlayerConfig = Field(default_factory=AudioPlayerConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @field_validator("server")
    @classmethod
    def validate_worker_count(cls, v: ServerConfig) -> ServerConfig:
        """校验 Worker 数量：GPU 工作负载下不允许多 Worker。

        Args:
            v: 解析后的 ServerConfig 子模型实例。

        Returns:
            ServerConfig: 校验通过时原样返回 v。

        Raises:
            ValueError: 当 v.workers > 1 时抛出，启动流程将中断并将错误展示给用户。
        """
        # ------------------------------------------------------------------
        # Why workers > 1 不支持：
        #   1. GPU 上下文串行限制：CUDA Context / PyTorch CUDA 张量在多进程
        #      场景下不共享，每个 Uvicorn Worker 都会独立加载整套模型权重，
        #      显存占用线性倍增，几乎必然 OOM。
        #   2. 显存隔离限制：即使拥有多张 GPU，Pydantic 校验阶段无法获知
        #      实际硬件拓扑，由用户在每张 GPU 上单独起进程 + 反向代理更可控。
        #   3. 生成串行化设计：model_manager.py 已通过线程锁将所有生成任务
        #      串行化排队，多 Worker 并不能提升吞吐，反而增加上下文切换开销。
        #   因此在配置层即硬拒绝 workers > 1，避免用户误配置后出现诡异的
        #   显存不足或死锁现象。
        # ------------------------------------------------------------------
        if v.workers > 1:
            raise ValueError("Workers > 1 not supported for GPU workloads")
        return v

    def to_dict(self) -> dict[str, Any]:
        """将整个配置树序列化为嵌套字典（用于保存/导出/调试）。

        Returns:
            dict[str, Any]: 顶层字段名 -> 子模型 dict/原始值 的嵌套结构。
        """
        return self.model_dump()


def load_config_dict(yaml_data: Any) -> AppConfig:
    """从 YAML 解析后的字典加载并校验配置，返回强类型 AppConfig。

    借助所有子模型的 ``extra="ignore"`` 策略，Pydantic 会自动静默过滤
    config.yaml 中的未知字段，因此无需手动对每个 section 做白名单裁剪。

    Args:
        yaml_data: 由 PyYAML / ruamel.yaml 等解析得到的顶层对象。
            正常期望类型为 dict[str, Any]；当传入其他类型（空字符串、
            列表、标量等）时会被显式拒绝并给出明确错误信息。

    Returns:
        AppConfig: 校验通过的完整配置树根实例。

    Raises:
        pydantic.ValidationError: 当 yaml_data 不是 dict 类型，或其
            内部字段无法通过嵌套子模型的类型/取值校验时抛出。错误信息
            包含精确的字段定位和失败原因，供用户快速排查 config.yaml。
    """
    if yaml_data is None:
        return AppConfig()

    # ------------------------------------------------------------------
    # 显式类型校验：YAML 解析的顶层对象必须是 dict（映射结构）。
    # 若 YAML 文件被误写为根级列表、字符串或其他标量，PyYAML 会返回
    # list/str/int 等非 dict 对象。此时若直接传入 AppConfig(**yaml_data)
    # 会得到隐晦的 TypeError。我们在这里提前拦截，给出明确的 ValidationError，
    # 让错误信息直接告诉用户"你的 config.yaml 顶层不是键值对结构"。
    # ------------------------------------------------------------------
    if not isinstance(yaml_data, dict):
        raise ValidationError.from_exception_data(
            title="AppConfig",
            line_errors=[
                {
                    "type": "dict_type",
                    "loc": ("__root__",),
                    "msg": f"Expected config root to be a dict/mapping, got {type(yaml_data).__name__}. "
                    "Please check that config.yaml has a valid key-value structure at the top level.",
                    "input": yaml_data,
                }
            ],
        )

    if not yaml_data:
        return AppConfig()

    version: str = str(yaml_data.get("version", "0.0.0")).strip().strip('"').strip("'")

    return AppConfig(
        version=version,
        server=yaml_data.get("server", {}),
        generation=yaml_data.get("generation", {}),
        generation_defaults=yaml_data.get("generation", {}),
        memory=yaml_data.get("memory", {}),
        models=yaml_data.get("models", {}),
        i18n=yaml_data.get("i18n", {}),
        api_auth=yaml_data.get("api_auth", {}),
        sse=yaml_data.get("sse", {}),
        audio_player=yaml_data.get("audio_player", {}),
        ui=yaml_data.get("ui", {}),
        runtime=yaml_data.get("runtime", {}),
        security=yaml_data.get("security", {}),
    )
