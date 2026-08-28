# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""TTS 引擎抽象层模块 —— 基于 Python Protocol 的解耦架构。

本模块定义了 TTS 引擎接口协议（Protocol）与引擎注册表机制，是路由层（Routes）
与具体引擎实现（Concrete Engines）之间的中间抽象层。

架构分层：
    Routes（路由层，如 routes/generate/voxcpm2/、routes/generate/indextts2/）
        ↓ 调用
    Protocol 协议层（本模块：TTSEngine / ControllableTTSEngine / EngineRegistry）
        ↓ 运行时动态发现/注册/切换
    Concrete Engines（具体引擎实现，如 engines/voxcpm2/engine.py、engines/indextts2_engine.py）

核心职责：
    1. 定义统一引擎契约：通过 Protocol 进行类型安全的鸭子类型检查，
       所有引擎（VoxCPM2、IndexTTS2 等）必须实现 TTSEngine 协议方法。
    2. 支持高级控制能力：ControllableTTSEngine 扩展协议，为支持终极克隆、
       LoRA 微调、Prompt 续写的引擎提供可选增强接口。
    3. 运行时引擎注册：InMemoryEngineRegistry 提供线程安全的注册表，
       支持立即注册与懒导入两种模式，可在运行时动态发现和切换引擎。
    4. 启动性能优化：通过懒导入（lazy import）避免在应用启动时加载
       所有重型依赖（VoxCPM2 的 voxcpm/funasr、IndexTTS2 等），
       显著缩短冷启动时间并降低初始内存占用。
"""

from collections.abc import Generator
from typing import Any, Protocol, TypeVar, runtime_checkable

#: 泛型类型变量，用于引擎注册表等场景的类型占位
_T = TypeVar("_T")


@runtime_checkable
class TTSEngine(Protocol):
    """所有 TTS 引擎必须实现的基础协议。

    路由层通过本协议与具体引擎解耦，无需感知 VoxCPM2 或 IndexTTS2 的
    实现细节即可调用统一的生成方法。使用 @runtime_checkable 支持
    isinstance() 运行时协议类型检查。

    典型实现类：
        - VoxCPM2Engine（engines.voxcpm2.engine）：核心多模态引擎
        - IndexTTS2Engine（engines.indextts2_engine）：情感控制引擎
    """

    def is_ready(self) -> bool:
        """检查引擎是否已加载并准备就绪可进行推理。

        Returns:
            bool: 若模型权重已载入显存/内存且推理管线已初始化则返回 True，
                  否则返回 False（未调用 load()、已卸载或加载失败）。
        """
        ...

    def load(self) -> None:
        """加载引擎模型并初始化推理管线。

        该方法负责将模型权重从磁盘载入显存/内存，并创建推理所需的
        所有预处理组件（Tokenizer、VAE、声码器等）。通常耗时数秒至数十秒。

        Raises:
            ModelLoadError: 模型文件缺失或权重加载失败（由具体引擎抛出）。
            InsufficientVRAMError: 可用显存不足，无法容纳模型（由具体引擎抛出）。
        """
        ...

    def unload(self) -> None:
        """卸载引擎模型并释放 GPU/CPU 内存资源。

        该方法执行反向清理操作：删除模型引用、清空 CUDA 缓存、
        释放声码器等辅助组件占用的内存。确保引擎切换或程序退出时
        无显存泄漏。

        Raises:
            无：该方法应具有幂等性，重复调用不应抛出异常。
        """
        ...

    def generate_voice_design(
        self,
        text: str,
        instruction: str = "",
        normalize: bool = True,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """根据文本描述生成语音设计（Text-to-Voice Description → 语音）。

        无需参考音频，仅通过自然语言指令描述期望的音色特征（如：
        "温柔的女声，语速偏慢"）即可合成目标语音。是 VoxCPM2 的核心能力之一。

        Args:
            text: 待合成的正文文本，支持多段落与中英文混合。
            instruction: 语音设计指令字符串，描述音色、情感、语速、
                        风格等属性。空字符串时使用引擎默认音色。
            normalize: 是否对输出音频执行响度归一化（LUFS Normalization）。
                      True 时将音频响度标准化至目标 -16 LUFS（广播级标准），
                      避免不同生成结果之间音量差异过大；False 时保留
                      模型原始输出响度，适合需要精细后处理的场景。
            **kwargs: 引擎特定的扩展参数（如 seed、cfg、steps 等），
                      具体引擎可自行解析未在协议中声明的额外参数。

        Returns:
            tuple[Any, str]: 二元组：
                - 第 0 位：音频输出路径（str）或音频张量/ndarray（Any，
                  由具体引擎决定，通常为本地 wav 文件路径字符串）。
                - 第 1 位：人类可读的结果消息（str），包含成功提示
                  或警告信息（如文本被截断、模型回退等）。

        Raises:
            GenerationError: 推理过程异常（CUDA OOM、数值不稳定等，由具体引擎抛出）。
            ValueError: 输入文本为空或包含不支持的字符（由具体引擎抛出）。
        """
        ...

    def generate_voice_clone(
        self,
        text: str,
        reference_audio_path: str | None = None,
        instruction: str = "",
        normalize: bool = True,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """零样本语音克隆（参考音频 + 文本 → 克隆语音）。

        基于用户提供的参考音频（通常 3~30 秒）提取说话人嵌入，
        合成出具有相同音色、韵律特征的目标语音。

        Args:
            text: 待合成的正文文本。
            reference_audio_path: 参考音频文件路径（.wav/.mp3 等常见格式）。
                                  None 时使用引擎的默认说话人或内置音色。
            instruction: 风格/情感修饰指令，在克隆音色基础上叠加情感或风格控制。
            normalize: 是否执行响度归一化（目标 -16 LUFS），含义同 generate_voice_design。
            **kwargs: 引擎特定扩展参数。

        Returns:
            tuple[Any, str]: 二元组（音频输出/路径, 结果消息），
                            结构同 generate_voice_design 返回值。

        Raises:
            GenerationError: 推理异常（由具体引擎抛出）。
            FileNotFoundError: reference_audio_path 指定的文件不存在（由具体引擎抛出）。
        """
        ...

    def generate_script(
        self,
        text: str,
        speaker_map: dict[str, Any] | None = None,
        persona_map: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """剧本工坊模式：多角色对话批量生成。

        解析包含说话人标签的剧本格式文本（如 "Alice: 你好\nBob: 嗨"），
        根据 speaker_map 或 persona_map 为每个角色分配独立音色，
        拼接输出整段对话音频。

        Args:
            text: 剧本格式文本，每行包含 "说话人名: 台词" 结构。
            speaker_map: 说话人名称到音色配置的映射字典，
                        key 为剧本中的说话人名，value 为该角色的克隆配置
                        （参考音频路径、设计指令等，结构由具体引擎定义）。
            persona_map: 已注册 Persona 角色名到配置的映射字典，
                        与 speaker_map 二选一或合并使用。
            **kwargs: 引擎特定扩展参数。

        Returns:
            tuple[Any, str]: 二元组（拼接后音频的输出路径, 结果消息）。

        Raises:
            GenerationError: 某段台词生成失败（由具体引擎抛出）。
            KeyError: 剧本中出现未在 speaker_map/persona_map 中定义的说话人（由具体引擎抛出）。
        """
        ...

    def generate_streaming(
        self,
        text: str,
        reference_audio_path: str | None = None,
        **kwargs: Any,
    ) -> Generator[tuple[Any, str], None, None]:
        """流式生成模式：长文本分段合成并实时输出音频块。

        将超长文本按标点或语义边界切分为多个片段，逐段推理并以
        生成器形式即时 yield 结果，降低首字延迟（TTFB）并允许
        前端边生成边播放。

        Args:
            text: 待合成的长文本（可超过模型单次最大 token 限制）。
            reference_audio_path: 可选参考音频路径（克隆模式）。
            **kwargs: 引擎特定扩展参数。

        Yields:
            tuple[Any, str]: 每个产出音频块的二元组：
                - 第 0 位：当前分段的音频块（路径或 ndarray）。
                - 第 1 位：该分段对应的状态/进度消息（如 "正在生成第 2/5 段"）。
                生成器结束（GeneratorExit）表示全部完成。

        Raises:
            GenerationError: 任一分段推理失败（由具体引擎抛出）。
        """
        ...


@runtime_checkable
class ControllableTTSEngine(Protocol):
    """可细粒度控制的 TTS 引擎扩展协议。

    为 VoxCPM2 等支持高级参数调节的引擎提供可选增强接口。
    路由层应先通过 isinstance(engine, ControllableTTSEngine) 检查
    引擎能力后再调用本协议方法，避免 AttributeError。

    扩展能力包括：
        - 终极克隆模式（ultimate clone）：开放 cfg/denoise/steps/seed 全参数
        - Prompt 续写模式：基于参考音频的音素级延续
        - LoRA 微调权重管理：加载/卸载/启用以 LoRA 适配器
    """

    def generate_ultimate_clone(
        self,
        text: str,
        instruction: str = "",
        ref_audio_path: str | None = None,
        advanced_cfg: float = 2.0,
        advanced_norm: bool = True,
        advanced_denoise: float = 1.0,
        advanced_steps: int = 10,
        advanced_seed: int = -1,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """终极克隆模式：全参数可控的精细语音生成。

        开放扩散模型推理的全部核心超参数，允许用户在
        保真度、自然度、降噪强度之间进行权衡调节。

        Args:
            text: 待合成正文文本。
            instruction: 风格/情感修饰指令。
            ref_audio_path: 参考音频路径（克隆音色来源）。
            advanced_cfg: Classifier-Free Guidance 强度系数。
                          推荐范围 [1.0, 5.0]。
                          2.0 为默认平衡点：值越高，音色/情感越贴近
                          指令与参考音频，但可能导致语音僵硬或伪影；
                          值越低（如 1.0）越自然但偏离度增大。
            advanced_norm: 是否执行响度归一化（目标 -16 LUFS）。
            advanced_denoise: 扩散模型去噪强度系数。
                              推荐范围 [0.0, 2.0]。
                              1.0 为标准完全去噪；
                              < 1.0 保留更多原始采样噪声，音色更"原始"；
                              > 1.0 过度去噪可能导致声音失真。
            advanced_steps: 扩散采样步数（DDIM/PLMS 等采样器）。
                            推荐范围 [4, 50]。
                            10 为默认值：步数越高音质越好但推理线性变慢；
                            4 步可极速生成（质量略降），50 步接近质量上限。
            advanced_seed: 随机数种子。
                           -1 表示使用随机种子（每次生成不同）；
                           指定正整数（如 42）可实现确定性复现生成，
                           便于 A/B 测试与调试。
            **kwargs: 引擎特定扩展参数。

        Returns:
            tuple[Any, str]: 二元组（音频输出/路径, 结果消息）。

        Raises:
            GenerationError: 推理异常（由具体引擎抛出）。
            ValueError: advanced_cfg / advanced_denoise / advanced_steps 超出有效范围（由具体引擎抛出）。
        """
        ...

    def generate_with_prompt(
        self,
        text: str,
        prompt_wav_path: str,
        prompt_text: str,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """Prompt 续写模式：基于参考音频与文本的音素级延续生成。

        与普通克隆不同，该模式将 prompt 音频 + prompt 文本作为
        生成前缀的上下文，模型直接在其后"续写"目标文本的音频，
        韵律与音色的一致性通常优于零样本克隆。

        Args:
            text: 待续写的目标文本（prompt 之后的新内容）。
            prompt_wav_path: 作为前缀的参考音频文件路径。
            prompt_text: 前缀音频对应的精确转写文本，
                        必须与 prompt_wav_path 的内容严格对齐，
                        否则会出现音素错位导致生成失败。
            **kwargs: 引擎特定扩展参数。

        Returns:
            tuple[Any, str]: 二元组（续写后的完整音频路径, 结果消息）。

        Raises:
            GenerationError: 对齐失败或推理异常（由具体引擎抛出）。
            FileNotFoundError: prompt_wav_path 不存在（由具体引擎抛出）。
        """
        ...

    def load_lora(self, lora_weights_path: str) -> tuple[list[str], list[str]]:
        """加载 LoRA（Low-Rank Adaptation）微调权重并注入基础模型。

        将独立训练的低秩适配矩阵与基础模型对应层的权重合并，
        实现用少量参数（通常 < 1%）定制化模型音色或风格。

        Args:
            lora_weights_path: LoRA 权重文件路径（通常为 .pt / .safetensors 格式）。

        Returns:
            tuple[list[str], list[str]]: 二元组：
                - loaded_keys（list[str]）：成功注入权重的参数名列表。
                  列表中的每个字符串是模型 state_dict 中对应 LoRA 层的 key，
                  可用于调试哪些层被实际修改。
                - skipped_keys（list[str]）：跳过的不匹配参数名列表。
                  当 LoRA 文件中某些 key 在当前基础模型中不存在（版本差异、
                  架构变更）时会被跳过并记录在此，非空时意味着
                  LoRA 可能未完全生效，需要检查权重文件与模型兼容性。

        Raises:
            ModelLoadError: 权重文件格式损坏或反序列化失败（由具体引擎抛出）。
            FileNotFoundError: lora_weights_path 路径不存在（由具体引擎抛出）。
        """
        ...

    def unload_lora(self) -> None:
        """卸载当前已加载的 LoRA 权重，恢复为基础模型原始权重。

        与 load_lora 构成对偶操作：从模型中移除 LoRA 适配层的影响，
        恢复 load_lora 调用前的推理输出。具有幂等性，未加载 LoRA
        时调用不产生效果也不报错。

        Raises:
            无：该方法应为幂等操作。
        """
        ...

    def set_lora_enabled(self, enabled: bool) -> None:
        """启用或禁用 LoRA 层（不卸载权重）。

        与 unload_lora 的区别：本方法仅切换 LoRA 适配矩阵是否参与
        前向计算的开关，不释放 LoRA 权重的显存占用，适合需要
        快速在"基础模型 ↔ LoRA 模型"之间对比切换的场景。

        Args:
            enabled: True 启用 LoRA（需已先调用 load_lora）；
                    False 临时绕过 LoRA 层，使用基础模型原始输出。

        Returns:
            None

        Raises:
            无：未加载 LoRA 时禁用/启用均应静默处理。
        """
        ...

    def get_lora_state_dict(self) -> dict[str, Any]:
        """获取当前 LoRA 参数的 state_dict 快照。

        用于调试、序列化或权重迁移：返回当前已加载 LoRA 适配层的
        完整参数字典（不含基础模型参数）。

        Returns:
            dict[str, Any]: LoRA 层名（str）到参数张量/数值（Any）的映射。
                           未加载 LoRA 时返回空字典 {}。

        Raises:
            无：未加载时返回空字典。
        """
        ...

    @property
    def lora_enabled(self) -> bool:
        """查询当前 LoRA 是否处于启用状态。

        只读属性，反映最近一次 set_lora_enabled 的结果，
        以及是否已通过 load_lora 成功加载权重。

        Returns:
            bool: 仅当 LoRA 权重已加载 AND 当前启用开关为 True 时返回 True；
                  其他情况（未加载、已加载但禁用、已卸载）均返回 False。
        """
        ...


@runtime_checkable
class EngineRegistry(Protocol):
    """引擎注册表协议：定义引擎发现与实例化管理的最小契约。

    路由层通过本协议查询可用引擎列表与获取引擎类引用，
    与具体的注册表实现（内存注册表、磁盘注册表、远程注册表等）解耦。
    """

    def register(
        self,
        name: str,
        engine_class: type | None = None,
        display_name: str = "",
        vram_requirement: float = 6.0,
        lazy_module: str = "",
        languages: list[str] | None = None,
        supported_features: list[str] | None = None,
        sample_rate: int = 24000,
        requires_gpu: bool = True,
        quality: str = "high",
    ) -> None:
        """注册一个 TTS 引擎到注册表。

        Args:
            name: 引擎唯一标识符（如 "voxcpm2"、"indextts2"），用于路由寻址。
            engine_class: 引擎类引用（立即注册模式），与 lazy_module 二选一。
            display_name: 前端 UI 显示的人类友好名称。
            vram_requirement: 最小显存需求（单位 GB，float）。
            lazy_module: 懒导入路径字符串，格式 "package.module:ClassName"。
            languages: 支持的语言代码列表（如 ["zh", "en", "ja"]）。
            supported_features: 支持的特性标签列表（如 ["clone", "lora"]）。
            sample_rate: 输出音频采样率（Hz）。
            requires_gpu: 是否必须使用 GPU（CPU 兜底可用时置 False）。
            quality: 质量等级标签（"high" / "medium" / "fast"）。

        Returns:
            None

        Raises:
            ValueError: name 为空或已重复注册（由具体实现决定是否抛出）。
        """
        ...

    def get(self, name: str) -> type | None:
        """根据标识符获取引擎类引用。

        对于懒注册的引擎，首次调用时触发实际的模块导入。

        Args:
            name: 引擎唯一标识符。

        Returns:
            Optional[type]: 找到时返回引擎类（type 对象，可用于实例化）；
                           未注册或懒导入失败时返回 None。
        """
        ...

    def list_engines(self) -> list[str]:
        """列出所有已注册的引擎标识符列表。

        Returns:
            list[str]: 所有已注册引擎的 name 标识列表（包含立即注册与懒导入的）。
        """
        ...


class InMemoryEngineRegistry:
    """内存引擎注册表：基于进程内存的线程安全引擎注册与懒导入实现。

    设计参考 VoiceBox 后端工厂模式，核心特性：
    - 双模式注册：立即注册（传 engine_class）或懒导入（传 lazy_module 路径）
    - 双重检查锁（Double-Checked Locking）：懒导入解析时保证线程安全
      同时不阻塞已缓存条目的快速路径访问
    - 元数据与类引用分离存储：UI 列表渲染仅读取 _metadata，不触发导入

    典型生命周期：
        1. 模块导入时 → 调用 _register_builtin_engines() 注册 VoxCPM2（立即或懒）与 IndexTTS2（懒）
        2. 路由初始化 → 调用 list_engines() / get_all_metadata() 渲染引擎选择 UI
        3. 用户首次切换引擎 → model_manager 调用 get("voxcpm2") 触发懒导入并缓存
    """

    def __init__(self) -> None:
        """初始化内存注册表实例。

        初始化所有内部存储字段，并创建线程同步原语。
        """
        self._engines: dict[str, type] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self._lazy_modules: dict[str, str] = {}
        self._lazy_cache: dict[str, type] = {}
        # WHY RLock（可重入锁）而非普通 Lock：
        # register() 内部在未来扩展中可能调用 get() 做存在性检查，
        # 而 get() 也需要获取同一把锁。若使用普通 Lock，同一线程
        # 在持有锁时递归获取会导致死锁；RLock 允许同线程重入。
        self._lock: Any = __import__("threading").RLock()

    def register(
        self,
        name: str,
        engine_class: type | None = None,
        display_name: str = "",
        vram_requirement: float = 6.0,
        lazy_module: str = "",
        languages: list[str] | None = None,
        supported_features: list[str] | None = None,
        sample_rate: int = 24000,
        requires_gpu: bool = True,
        quality: str = "high",
    ) -> None:
        """注册引擎类或懒导入路径，并同步写入元数据。

        支持 engine_class 和 lazy_module 同时传入：类引用优先存入 _engines
        作为快速路径，lazy_module 作为备用导入策略（如类引用来自测试 mock）。

        Args:
            name: 引擎唯一标识符。
            engine_class: 引擎类引用（type 对象），立即注册到 _engines。
            display_name: UI 显示名称，空时回退为 name。
            vram_requirement: 显存需求（GB，float），默认 6.0。
            lazy_module: 懒导入路径，格式 "package.module:ClassName"。
            languages: 支持语言列表，默认 ["zh", "en"]。
            supported_features: 特性标签列表，默认 []。
            sample_rate: 输出采样率 Hz，默认 24000。
            requires_gpu: 是否必须 GPU，默认 True。
            quality: 质量等级，默认 "high"。

        Returns:
            None
        """
        with self._lock:
            if engine_class is not None:
                self._engines[name] = engine_class
            if lazy_module:
                self._lazy_modules[name] = lazy_module
            self._metadata[name] = {
                "display_name": display_name or name,
                "vram_requirement": vram_requirement,
                "languages": languages or ["zh", "en"],
                "supported_features": supported_features or [],
                "sample_rate": sample_rate,
                "requires_gpu": requires_gpu,
                "quality": quality,
            }

    def get(self, name: str) -> type | None:
        """获取引擎类引用，支持懒导入解析与双重检查锁。

        双重检查锁（Double-Checked Locking）设计说明：
            步骤 1（快速路径，无锁）：先检查 _engines 中是否已存在，
                若已直接返回 —— 99% 的调用落在该分支，完全无锁开销。
            步骤 2（过滤无效名，无锁）：若 name 不在 _lazy_modules 中，
                直接返回 None，避免不必要的锁竞争。
            步骤 3（慢速路径，加锁）：进入 RLock 后 **再次** 检查 _lazy_cache
                —— 因为在步骤 1 和步骤 3 之间，可能有另一线程已完成导入并
                写入缓存，二次检查避免重复导入同一模块。
            步骤 4：实际执行 importlib.import_module + getattr，解析完成后
                同时写入 _lazy_cache 和 _engines，使后续调用走快速路径。

        WHY 不先加锁：对已缓存的引擎而言，每次 get 都经历 lock+unlock 的
        开销在高并发路由场景下不可忽略；快速路径的无锁读取提供了
        数量级的性能提升，而代价仅是懒导入瞬间的一次额外字典读取。

        Args:
            name: 引擎唯一标识符。

        Returns:
            Optional[type]: 引擎类引用（type），未找到或导入失败返回 None。
        """
        # 快速路径：已注册的引擎类（无锁读取，性能优先）
        if name in self._engines:
            return self._engines.get(name)

        # 懒导入路径：若未注册懒导入模块则直接返回
        if name not in self._lazy_modules:
            return None

        # 双重检查锁：先获取锁，再二次检查缓存
        with self._lock:
            if name in self._lazy_cache:
                return self._lazy_cache[name]

            module_path = self._lazy_modules[name]
            try:
                module_name, class_name = module_path.rsplit(":", 1)
                import importlib

                module = importlib.import_module(module_name, package=__package__)
                engine_class = getattr(module, class_name)
                self._lazy_cache[name] = engine_class
                self._engines[name] = engine_class
                return engine_class
            except (ImportError, AttributeError, ValueError) as e:
                import logging

                logger = logging.getLogger("tts_multimodel")
                # 预期内的导入失败类型：仅 debug 级别，避免日志刷屏
                logger.debug(f"[EngineRegistry] 懒导入引擎 '{name}' 预期异常 ({type(e).__name__}): {e}")
                logger.warning(f"[EngineRegistry] 懒导入引擎 '{name}' 失败: {e}")
                return None
            except Exception as e:
                import logging

                logger = logging.getLogger("tts_multimodel")
                # 非预期的通用异常：记录完整堆栈以便排查（如模块内部 SyntaxError、OSError 等）
                logger.exception(f"[EngineRegistry] 懒导入引擎 '{name}' 发生未预期异常: {e}")
                logger.warning(f"[EngineRegistry] 懒导入引擎 '{name}' 失败: {e}")
                return None

    def list_engines(self) -> list[str]:
        """列出所有已注册引擎标识符（立即注册 + 懒导入）。

        合并 _engines.keys() 与 _lazy_modules.keys() 后去重。
        加锁读取确保返回时字典处于一致状态。

        Returns:
            list[str]: 引擎标识符名称列表（顺序不保证稳定）。
        """
        with self._lock:
            return list(set(list(self._engines.keys()) + list(self._lazy_modules.keys())))

    def get_display_name(self, name: str) -> str:
        """获取引擎的 UI 显示名称。

        Args:
            name: 引擎标识符。

        Returns:
            str: 元数据中注册的 display_name；若不存在则回退为 name 本身。
        """
        return self._metadata.get(name, {}).get("display_name", name)

    def get_vram_requirement(self, name: str) -> float:
        """获取引擎的最小显存需求。

        Args:
            name: 引擎标识符。

        Returns:
            float: 显存需求（GB），默认回退 6.0。
        """
        return self._metadata.get(name, {}).get("vram_requirement", 6.0)

    def get_metadata(self, name: str) -> dict[str, Any]:
        """获取单个引擎的完整元数据字典。

        Args:
            name: 引擎标识符。

        Returns:
            dict[str, Any]: 包含 display_name / vram_requirement / languages /
                           supported_features / sample_rate / requires_gpu / quality
                           的元数据字典；未注册时返回空字典 {}。
        """
        return self._metadata.get(name, {})

    def get_all_metadata(self) -> dict[str, dict[str, Any]]:
        """获取所有引擎的元数据映射（供前端 UI 批量渲染引擎列表）。

        返回字典的浅拷贝，避免外部修改内部 _metadata 状态。

        Returns:
            dict[str, dict[str, Any]]: key 为引擎标识符 name，
                                      value 为对应引擎的元数据字典。
        """
        return dict(self._metadata)

    def is_registered(self, name: str) -> bool:
        """检查给定名称的引擎是否已注册（立即或懒导入模式）。

        Args:
            name: 待检查的引擎标识符。

        Returns:
            bool: _engines 或 _lazy_modules 中包含 name 时返回 True，否则 False。
        """
        return name in self._engines or name in self._lazy_modules


#: 全局内存引擎注册表单例。
#: 应用启动时由 ``_register_builtin_engines()`` 自动注册内置引擎（VoxCPM2、IndexTTS2），
#: 路由层、model_manager 等模块通过本单例查询引擎列表、获取引擎类引用。
#: 线程安全：内部通过 ``threading.RLock`` 保护懒导入和注册操作。
engine_registry: InMemoryEngineRegistry = InMemoryEngineRegistry()


def _register_builtin_engines() -> None:
    """注册项目内置的 TTS 引擎到全局注册表 engine_registry。

    注册策略说明：
    1. VoxCPM2：优先尝试立即注册（直接 import 并传入 engine_class），
       失败时回退到懒导入。
       WHY 先立即再回退：
           app_server 启动链路的类型检查、路由自动发现等阶段可能需要
           VoxCPM2Engine 类对象的真实引用（而非 lazy_module 字符串）。
           若环境已正确安装依赖（voxcpm/funasr/torch 等），立即导入
           可以让启动期就发现缺失依赖而不是在用户首次生成时才报错。
           但若依赖未安装（如精简分发版），ImportError 回退到懒导入可
           保证 app_server 仍能正常启动，用户切换 VoxCPM2 时再提示错误。

    2. IndexTTS2：始终使用懒导入。
       WHY 纯懒导入：
           IndexTTS2 是可选的情感控制引擎，其依赖（cosyvoice、matcha 等）
           可能未在所有环境中安装。若在启动期直接 import，ImportError
           会导致整个应用无法启动。懒导入确保 IndexTTS2 缺失时，
           核心的 VoxCPM2 引擎仍可正常使用。
    """
    # VoxCPM2 - 核心引擎（先尝试立即注册，失败回退懒导入）
    try:
        from .engines.voxcpm2.engine import VoxCPM2Engine

        engine_registry.register(
            "voxcpm2",
            engine_class=VoxCPM2Engine,
            display_name="VoxCPM2",
            vram_requirement=6.5,
            languages=["zh", "en", "ja", "ko"],
            supported_features=[
                "voice_design",
                "clone",
                "ultimate",
                "script",
                "streaming",
                "prompt",
                "lora",
            ],
            sample_rate=24000,
            requires_gpu=True,
            quality="high",
        )
    except ImportError:
        engine_registry.register(
            "voxcpm2",
            lazy_module=".engines.voxcpm2.engine:VoxCPM2Engine",
            display_name="VoxCPM2",
            vram_requirement=6.5,
            languages=["zh", "en", "ja", "ko"],
            supported_features=[
                "voice_design",
                "clone",
                "ultimate",
                "script",
                "streaming",
                "prompt",
                "lora",
            ],
            sample_rate=24000,
            requires_gpu=True,
            quality="high",
        )

    # IndexTTS2 - 情感控制引擎（纯懒导入，减少启动依赖）
    engine_registry.register(
        "indextts2",
        lazy_module=".engines.indextts2_engine:IndexTTS2Engine",
        display_name="IndexTTS 2.5",
        vram_requirement=6.0,
        languages=["zh", "en", "ja", "es", "ar"],
        supported_features=["clone", "emotion_control"],
        sample_rate=22050,
        requires_gpu=False,
        quality="high",
    )

    # IndexTTS 2.0 - 与 2.5 共用同一 indextts 代码包的旧版本变体（懒导入）
    # 复用 IndexTTS2Engine 的薄子类 IndexTTS20Engine；仅中英双语、无显式时长控制。
    engine_registry.register(
        "indextts20",
        lazy_module=".engines.indextts2_engine:IndexTTS20Engine",
        display_name="IndexTTS 2.0",
        vram_requirement=5.5,
        languages=["zh", "en"],
        supported_features=["clone", "emotion_control"],
        sample_rate=22050,
        requires_gpu=False,
        quality="high",
    )


_register_builtin_engines()
