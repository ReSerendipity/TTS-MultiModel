"""VoxCPM2 引擎 Facade 模块 —— 基于 Protocol 的薄门面实现。

架构说明：
    VoxCPM2Engine 是 :class:`TTSEngine` 与 :class:`ControllableTTSEngine` 两个
    Protocol 的具体实现类，采用"薄 Facade（门面）模式"设计。类本身不承载
    任何实际的推理或业务逻辑，所有核心功能均通过方法内部的延迟导入，
    委托给下方独立子模块中以 ``fn_voxcpm_*`` 命名的顶级函数：

    - 语音设计（design.py）→ :func:`fn_voxcpm_design`
    - 语音克隆（clone.py）→ :func:`fn_voxcpm_clone`
    - 终极克隆（ultimate.py）→ :func:`fn_voxcpm_ultimate_clone`
    - 剧本工坊（script.py）→ :func:`fn_voxcpm_script_studio`
    - 流式生成（streaming.py）→ :func:`fn_voxcpm_streaming`
    - Prompt 续写（prompt.py）→ :func:`fn_voxcpm_prompt_continue`
    - LoRA 管理（lora.py）→ ``load_lora_weights`` / ``unload_lora_weights`` 等

为什么使用委托而非继承：
    1. 向后兼容：各 ``fn_voxcpm_*`` 函数作为独立 API 暴露给路由层
       （:mod:`routes.generate.voxcpm2`）直接调用，Facade 类只是额外的
       Protocol 适配层；若改为继承体系，子函数需要重构为方法，破坏
       现有路由层的调用约定。
    2. 解耦子模块状态：design / clone / script 等子模块之间存在复杂的
       交叉依赖（共享 ``_base.py`` 中的 ``_progress_mgr``、
       ``_advanced_kwargs`` 等全局上下文），由 Facade 类作为状态持有者
       会引入额外的生命周期管理复杂度；当前设计下 Facade 仅做聚合，
       不持有任何可变状态，天然线程安全。
    3. 单元测试友好：测试 VoxCPM2Engine 时，只需 mock 子模块函数的
       返回值即可验证 Facade 的参数透传、异常处理等逻辑，无需加载
       数 GB 的模型权重；若使用继承，则需要 mock 基类方法或引入
       复杂的依赖注入框架。

懒加载链路（registry.voxcpm_model）：
    模型对象 ``registry.voxcpm_model`` 的初始化遵循以下严格生命周期，
    确保就绪状态的一致性：

    1. 应用启动：``app_server.py`` 创建 FastAPI 应用，注册生命周期钩子。
    2. 触发加载：用户通过 WebUI 点击"加载模型"或配置 ``auto_load_model: true``
       → 路由层调用 ``model_manager.load_voxcpm2()``。
    3. 进度流式产出：``load_voxcpm2`` 是一个 Generator，依次产出
       "初始化中" → "下载/加载权重" → "加载 VAE" → "加载声码器" →
       "加载完成" 等进度事件（供 SSE 推送至前端）。
    4. 状态写入：当 Generator 产出最后一个"加载完成"事件时，
       ``model_manager`` 内部调用 ``registry.set_voxcpm_loaded(model_obj)``，
       将模型对象赋值给 ``registry.voxcpm_model``。
    5. 就绪判定：:meth:`VoxCPM2Engine.is_ready` 返回 ``True``，
       所有 ``generate_*`` 方法可以安全调用。

    注意：``is_ready() == True`` 仅代表模型对象存在，不保证 GPU 显存
    一定充足（可能被其他进程抢占），实际推理时仍可能触发
    :class:`InsufficientVRAMError`，调用方应做好异常捕获。
"""

import logging
from collections.abc import Generator
from typing import Any

from ...engine_interface import ControllableTTSEngine, TTSEngine
from ...exceptions import (
    EngineLoadError,
    EngineNotLoadedError,
    GenerationError,
    TTSError,
)
from ...model_registry import registry

logger = logging.getLogger("tts_multimodel")


class VoxCPM2Engine(TTSEngine, ControllableTTSEngine):
    """VoxCPM2 引擎薄门面实现。

    聚合 design / clone / ultimate / script / streaming / prompt / lora
    七大子模块的 ``fn_voxcpm_*`` 函数，对外暴露符合 ``TTSEngine`` 与
    ``ControllableTTSEngine`` Protocol 约束的统一接口。

    本类不持有任何实例状态（无 ``__init__`` 自定义属性），所有状态
    均由全局单例 ``registry`` 与子模块内的上下文管理器维护，因此
    可以安全地被多线程共享、重复实例化而无副作用。
    """

    def is_ready(self) -> bool:
        """检查 VoxCPM2 引擎是否已加载并就绪。

        Readiness 判断逻辑：
            仅检查 ``registry.voxcpm_model is not None``，即模型对象
            是否已被写入全局注册表。该赋值操作发生在
            ``model_manager.load_voxcpm2()`` Generator 的最后一个
            "加载完成" 进度事件之后。

        重要注意事项：
            返回 ``True`` **不代表 GPU 一定就绪**。以下场景均可能出现
            ``is_ready() == True`` 但推理失败的情况：

            1. 模型刚加载完成，但另一进程突发显存占用导致可用显存不足
               （触发 :class:`InsufficientVRAMError`）。
            2. CUDA 运行时在模型加载后出现设备侧错误（如驱动崩溃、
               GPU 被物理移除）。
            3. 模型对象存在但部分子组件（VAE / 声码器）未正确初始化
               （极罕见，通常意味着 ``load_voxcpm2`` 流程异常）。

            调用方在执行 ``generate_*`` 时仍应使用 try/except 捕获
            :class:`GenerationError` 系列异常。

        Returns:
            bool: 模型对象已注册返回 ``True``；未加载、已卸载或加载
                  流程中断返回 ``False``。
        """
        return registry.voxcpm_model is not None

    def load(self) -> None:
        """阻塞式加载 VoxCPM2 模型（同步接口）。

        实现机制：
            内部调用 ``model_manager.load_voxcpm2()`` 并通过
            ``for _ in gen: pass`` 消费其产出的所有进度事件，退化为
            同步阻塞调用，待生成器耗尽（加载完成）后才返回。

        适用场景 vs 不推荐场景：
            ✅ 适用：CLI 脚本、单元测试、自动化部署等无需进度反馈的
                    同步上下文。
            ❌ 不推荐：Web 路由层（FastAPI endpoints）。路由层应直接
                    调用 ``model_manager.load_voxcpm2()`` Generator，
                    将每个进度事件通过 SSE（``/api/sse/events``）推送
                    至前端，避免用户在加载期间看到空白页面超时。

        Raises:
            EngineLoadError: 加载过程中发生的任何非预期异常（OSError、
                CUDA 错误、依赖缺失等）均被捕获并包装为此异常，保留
                原始异常链（``__cause__``）以便排障。
            TTSError: 若子流程抛出已归类的 TTS 异常（如
                :class:`ModelLoadError`、:class:`InsufficientVRAMError`），
                则原样透传，不重复包装。
        """
        from ...model_manager import load_voxcpm2

        try:
            # WHY 用 for _ in load_voxcpm2(): pass 而不直接调内部同步方法：
            # load_voxcpm2 被设计为 Generator 流式产出进度事件，
            # 以便路由层通过 SSE 实时推送给前端。若单独维护一套
            # "同步加载函数 + 异步生成器函数" 会导致两套逻辑分叉，
            # 增加 Bug 风险与维护成本。此处通过阻塞消费生成器退化为
            # 同步调用，既复用了完整的加载流程（含显存预检、状态机、
            # 日志记录），又避免了代码重复。
            for _ in load_voxcpm2():
                pass
        except TTSError:
            raise
        except Exception as e:
            logger.exception("[VoxCPM2Engine] 阻塞式加载模型失败")
            raise EngineLoadError(
                message=f"VoxCPM2 引擎加载失败: {type(e).__name__}: {e}",
                engine="voxcpm2",
            ) from e

    def unload(self) -> None:
        """阻塞式卸载 VoxCPM2 模型并释放资源（同步接口）。

        实现机制：
            委托给 ``model_manager.unload_model()``，执行：
            1. 删除 ``registry.voxcpm_model`` 引用。
            2. 调用 ``torch.cuda.empty_cache()`` 释放 CUDA 缓存。
            3. 触发 Python GC 回收不再被引用的张量。

        与路由层的差异：
            路由层（``routes/model.py`` 的 ``/api/model/unload`` 端点）
            会在卸载前后通过 SSE 推送 ``engine_switch`` / ``status``
            事件通知前端；本方法仅执行纯卸载逻辑，无事件产出。

        异常处理策略：
            卸载失败 **不重新抛出异常**。原因：
            1. 卸载失败通常是由于张量引用未完全释放（如用户代码仍持有
               某层权重引用），属于非致命问题，不应影响后续操作。
            2. 在引擎切换场景（VoxCPM2 → IndexTTS2）中，若旧引擎卸载
               失败就中断切换流程，会导致用户卡在无法使用的状态；
               记录日志后继续执行，允许新引擎加载，整体可用性更佳。
            完整堆栈通过 ``logger.exception`` 记录，便于事后排障。
        """
        from ...model_manager import unload_model

        try:
            unload_model()
        except Exception as e:
            logger.exception(
                "[VoxCPM2Engine] 卸载模型失败（非致命，继续执行）: %s",
                type(e).__name__,
            )

    def generate_voice_design(
        self,
        text: str,
        instruction: str = "",
        normalize: bool = True,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """根据文本描述生成语音设计（Text + Voice Instruction → Audio）。

        无需参考音频，仅通过自然语言 ``instruction`` 描述期望的音色、
        情感、语速等属性即可合成目标语音。是 VoxCPM2 区别于传统
        TTS 的核心能力之一。

        Args:
            text: 待合成的正文文本，支持多段落与中英文混合。空字符串
                  会被子模块抛出 ``ValueError``。
            instruction: 语音设计指令字符串，如"温柔的女声，语速偏慢，
                        带有一点沙哑"。空串时使用引擎内置默认音色。
            normalize: 是否对输出音频执行响度归一化（目标 -16 LUFS，
                      广播级标准）。True 可避免不同生成结果之间音量
                      差异过大；False 保留模型原始输出响度，适合需要
                      精细后处理的专业场景。
            **kwargs: 引擎特定扩展参数透传。支持但不限于：
                      - ``seed`` (int): 随机种子，用于可复现生成。
                      - ``cfg_value`` (float): CFG 引导强度，
                        覆盖默认 2.0。
                      - ``inference_timesteps`` (int): 扩散采样步数，
                        覆盖默认 10。

        Returns:
            tuple[Any, str]: 二元组：
                - 第 0 位：合成结果。由子函数 ``fn_voxcpm_design`` 返回，
                  通常为本地 .wav 文件的绝对路径字符串（推荐形式），
                  极少数降级场景下可能是 ``numpy.ndarray`` PCM 张量。
                - 第 1 位：人类可读的结果消息字符串。包含成功提示
                  或非致命警告（如"文本过长被截断"、"启用了 CPU 回退"）。

        Raises:
            EngineNotLoadedError: 调用前 ``is_ready()`` 返回 False，
                即模型尚未加载。前端应引导用户先点击"加载模型"。
            GenerationError: 推理过程中发生运行时错误（CUDA OOM、
                数值不稳定等），由子模块抛出并透传。
        """
        if not self.is_ready():
            raise EngineNotLoadedError(
                message="VoxCPM2 引擎未加载，请先加载模型后再尝试语音设计生成",
                engine="voxcpm2",
            )
        from .design import fn_voxcpm_design

        return fn_voxcpm_design(
            text=text,
            instruction=instruction,
            normalize=normalize,
            **kwargs,
        )

    def generate_voice_clone(
        self,
        text: str,
        reference_audio_path: str | None = None,
        instruction: str = "",
        normalize: bool = True,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """零样本语音克隆（参考音频 + 文本 → 克隆语音）。

        基于用户提供的参考音频（推荐 3~30 秒清晰单人语音），通过
        VoxCPM2 内置的说话人编码器提取嵌入向量，合成出具有相同
        音色、韵律特征的目标语音。

        Args:
            text: 待合成的正文文本。
            reference_audio_path: 参考音频文件的绝对/相对路径
                                  （.wav / .mp3 / .flac 等格式）。
                                  ``None`` 时回退为引擎默认说话人音色
                                  （等同于未提供参考音频的 voice_design）。
            instruction: 风格/情感修饰指令。在克隆音色基础上叠加额外
                        的情感或风格控制，如"用悲伤的语气朗读"。
            normalize: 是否执行响度归一化（目标 -16 LUFS），含义
                      同 :meth:`generate_voice_design`。
            **kwargs: 引擎特定扩展参数透传（seed / cfg_value /
                      inference_timesteps 等）。

        Returns:
            tuple[Any, str]: 二元组（音频输出路径或 ndarray, 结果消息），
                            结构与 :meth:`generate_voice_design` 返回值一致。

        Raises:
            EngineNotLoadedError: 模型未加载时抛出。
            FileNotFoundError: ``reference_audio_path`` 指定的文件
                不存在或无法读取，由子模块抛出。
            GenerationError: 推理运行时错误（CUDA OOM、说话人编码失败
                等）。
        """
        if not self.is_ready():
            raise EngineNotLoadedError(
                message="VoxCPM2 引擎未加载，请先加载模型后再尝试语音克隆",
                engine="voxcpm2",
            )
        from .clone import fn_voxcpm_clone

        return fn_voxcpm_clone(
            text=text,
            ref_audio_path=reference_audio_path,
            instruction=instruction,
            normalize=normalize,
            **kwargs,
        )

    def generate_script(
        self,
        text: str,
        speaker_map: dict[str, Any] | None = None,
        persona_map: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """剧本工坊模式：多角色对话批量合成。

        解析包含说话人标签的剧本格式文本（如 ``[Alice] 你好啊
        [Bob] 嗨，好久不见``），根据 ``persona_map`` 为每个角色
        分配独立音色，逐句合成后拼接输出整段对话音频。

        Args:
            text: 剧本格式文本，每行或每个 ``[角色名]`` 标签后跟随
                  对应台词。具体格式由子函数 ``fn_voxcpm_script_studio``
                  内部解析（参见 script.py 中的正则）。
            speaker_map: Protocol 通用占位参数（**VoxCPM2 未使用**）。
                        IndexTTS2 风格的"说话人名称 → 音色配置"映射，
                        语义与 VoxCPM2 剧本工坊的需求不匹配，此处有意
                        忽略，参见下方 Why 注释。
            persona_map: VoxCPM2 实际使用的映射字典，格式为
                        ``{角色名(str): 参考音频wav路径(str)}``。
                        路由层在调用前会将用户选择的 Persona 音色
                        转换为该格式。若传入 ``None``，子函数会回退
                        读取所有已注册 Persona 的默认映射。
            **kwargs: 剧本工坊高级参数透传。包括但不限于：
                      - ``advanced_cfg`` / ``advanced_norm`` /
                        ``advanced_denoise`` / ``advanced_steps`` /
                        ``advanced_seed``：终极克隆级别的精细参数。
                      - ``lang``：语言偏好（默认"中文"）。

        Returns:
            tuple[Any, str]: 二元组：
                - 第 0 位：拼接后完整对话音频的 .wav 文件路径（str），
                  或在空剧本时可能为 ``None``。
                - 第 1 位：结果消息字符串，通常包含已合成的角色数量
                  与总耗时统计。

        Raises:
            EngineNotLoadedError: 模型未加载时抛出。
            GenerationError: 某句台词合成失败，或剧本解析无有效行。
            KeyError: 剧本中出现未在 ``persona_map`` 中定义的角色名
                （子函数严格校验，不做模糊匹配）。
        """
        if not self.is_ready():
            raise EngineNotLoadedError(
                message="VoxCPM2 引擎未加载，请先加载模型后再使用剧本工坊",
                engine="voxcpm2",
            )
        from .script import fn_voxcpm_script_studio

        # WHY speaker_map 被"吞掉"，只用 persona_map 透传：
        # TTSEngine Protocol 的 generate_script 同时声明了 speaker_map
        # 和 persona_map 两个参数，是为了兼容不同引擎的设计哲学：
        #   - IndexTTS2 等引擎使用 speaker_map：语义是"剧本中的说话人名
        #     → 该说话人对应的音色配置对象"，适合说话人集合预先通过
        #     API 注册的场景。
        #   - VoxCPM2 剧本工坊使用 persona_map：语义是"角色名 → 该角色
        #     的参考音频 wav 路径"，直接与 Persona 目录结构耦合，且
        #     fn_voxcpm_script_studio 的历史实现就是接受
        #     persona_map_with_wav 参数。
        # 两者键值结构完全不同，若强行将 speaker_map 转换为 persona_map
        # 会丢失信息或引入歧义。因此本方法显式忽略 speaker_map，仅将
        # persona_map（以及 **kwargs 中的高级参数）透传给子函数，
        # 路由层在调用时应保证构造正确的 persona_map 格式。
        return fn_voxcpm_script_studio(
            script_text=text,
            persona_map_with_wav=persona_map,
            **kwargs,
        )

    def generate_streaming(
        self,
        text: str,
        reference_audio_path: str | None = None,
        **kwargs: Any,
    ) -> Generator[Any, None, None]:
        """流式生成模式：长文本分段合成并实时输出音频块。

        将超长文本按标点或语义边界切分为多个片段（由子函数
        ``split_text_for_tts`` 执行），逐段推理并以 Generator 形式
        即时 yield 结果，降低首字延迟（TTFB）并允许前端边生成边播放。

        产出格式说明：
            Generator 每次 ``yield`` 的项取决于底层模型是否支持
            ``generate_streaming`` 原生流式接口：

            * **支持原生流式**（``hasattr(voxcpm_model, "generate_streaming")``）：
              产出由模型直接返回的音频块，通常为 ``numpy.ndarray``
              形状 ``(num_samples,)`` 的单声道 PCM 浮点张量。
            * **回退到常规生成**（模型无原生流式）：
              产出为每段完整合成结果的 ``numpy.ndarray`` 或文件路径。

            路由层在消费时应使用 ``isinstance(chunk, np.ndarray)`` 等
            动态判断来统一处理不同格式，并在 Generator 耗尽
            （``StopIteration``）时标记全部完成。

        Args:
            text: 待合成的长文本（可超过模型单次最大 token 限制，
                  会被自动切分）。
            reference_audio_path: 可选参考音频路径（克隆模式）。
                                  ``None`` 时使用默认音色。
            **kwargs: 引擎特定扩展参数。子函数内部会展开为：
                      ``cfg_value``（默认 2.0）、
                      ``inference_timesteps``（默认 10）、
                      ``denoise``（默认 True）、``seed``（默认 -1）等。

        Yields:
            Any: 每个音频分段的产出，通常为 ``numpy.ndarray`` PCM 块。
                 具体类型取决于模型原生支持情况。

        Raises:
            EngineNotLoadedError: 模型未加载时抛出。
            GenerationError: 1) 任一分段推理失败；或
                             2) ``fn_voxcpm_streaming`` 未返回 Generator，
                                出现类型不匹配（显式校验，给出明确提示）。
        """
        if not self.is_ready():
            raise EngineNotLoadedError(
                message="VoxCPM2 引擎未加载，请先加载模型后再使用流式生成",
                engine="voxcpm2",
            )
        from .streaming import fn_voxcpm_streaming

        result = fn_voxcpm_streaming(
            text=text,
            ref_audio_path=reference_audio_path,
            **kwargs,
        )
        if not isinstance(result, Generator):
            raise GenerationError(
                message=(
                    "VoxCPM2 流式生成返回值类型错误："
                    f"期望 Generator，实际得到 {type(result).__name__}。"
                    "可能是模型未正确初始化或回退逻辑异常。"
                ),
                engine="voxcpm2",
            )
        return result

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

        开放扩散模型推理的全部核心超参数，允许用户在保真度、自然度、
        降噪强度之间进行精细权衡调节，适合对语音质量有极致要求的
        专业场景（如配音、有声书、播客后期）。

        Args:
            text: 待合成的正文文本。
            instruction: 风格/情感修饰指令字符串。
            ref_audio_path: 参考音频文件路径（克隆音色来源）。
                            ``None`` 时回退默认音色。
            advanced_cfg: Classifier-Free Guidance 强度系数。
                          推荐范围 ``[1.0, 5.0]``。
                          2.0（默认）为平衡点：值越高音色/情感越贴近
                          指令与参考音频，但可能导致语音僵硬或伪影；
                          值越低（如 1.0）越自然但偏离度增大。
            advanced_norm: 是否执行响度归一化（目标 -16 LUFS）。
            advanced_denoise: 扩散模型去噪强度系数。
                              推荐范围 ``[0.0, 2.0]``。
                              1.0（默认）为标准完全去噪；
                              < 1.0 保留更多原始采样噪声，音色更"原始"；
                              > 1.0 过度去噪可能导致声音失真。
            advanced_steps: 扩散采样步数。推荐范围 ``[4, 50]``。
                            10（默认）为速度/质量平衡点；
                            4 步可极速生成（质量略降）；
                            50 步接近质量上限但推理线性变慢 5x。
            advanced_seed: 随机数种子。
                           -1（默认）表示使用随机种子（每次不同）；
                           指定正整数（如 42）可实现确定性复现生成，
                           便于 A/B 测试与调试。
            **kwargs: 引擎特定扩展参数透传。

        Returns:
            tuple[Any, str]: 二元组（音频输出路径或 ndarray, 结果消息）。

        Raises:
            EngineNotLoadedError: 模型未加载时抛出。
            ValueError: 数值型参数超出有效范围（由子函数校验）。
            GenerationError: 推理运行时错误。
            FileNotFoundError: ``ref_audio_path`` 不存在。
        """
        if not self.is_ready():
            raise EngineNotLoadedError(
                message="VoxCPM2 引擎未加载，请先加载模型后再使用终极克隆",
                engine="voxcpm2",
            )
        from .ultimate import fn_voxcpm_ultimate_clone

        return fn_voxcpm_ultimate_clone(
            text=text,
            instruction=instruction,
            ref_audio_path=ref_audio_path,
            advanced_cfg=advanced_cfg,
            advanced_norm=advanced_norm,
            advanced_denoise=advanced_denoise,
            advanced_steps=advanced_steps,
            advanced_seed=advanced_seed,
            **kwargs,
        )

    def generate_with_prompt(
        self,
        text: str,
        prompt_wav_path: str,
        prompt_text: str,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """Prompt 续写模式：基于参考音频与文本的音素级延续生成。

        与 :meth:`generate_voice_clone`（零样本克隆）的关键区别：
            - 克隆模式：先从参考音频提取 **说话人嵌入**，再用该嵌入
              独立合成目标文本（两段音频无音素级关联）。
            - 续写模式：将 ``prompt_wav_path`` 音频 + ``prompt_text``
              转写作为 **生成前缀上下文**，模型直接在其后"续写"
              ``text`` 对应的音频，韵律与音色的一致性通常显著优于
              零样本克隆（但要求 prompt 转写严格对齐）。

        Args:
            text: 待续写的目标文本（prompt 之后的新内容）。
            prompt_wav_path: 作为前缀的参考音频文件路径（必填，不可空）。
            prompt_text: 前缀音频对应的 **精确转写文本**，必须与
                        ``prompt_wav_path`` 的内容严格逐字对齐（包括
                        标点、语气词、停顿标记）。若对齐不准确，模型
                        会出现音素错位，导致生成质量急剧下降甚至失败。
            **kwargs: 引擎特定扩展参数透传。

        Returns:
            tuple[Any, str]: 二元组（续写后的完整音频路径或 ndarray,
                            结果消息）。注意：输出音频包含前缀 prompt
                            部分，不是仅包含 ``text`` 对应片段。

        Raises:
            EngineNotLoadedError: 模型未加载时抛出。
            FileNotFoundError: ``prompt_wav_path`` 不存在或无法读取。
            GenerationError: 音素对齐失败或推理运行时错误。
        """
        if not self.is_ready():
            raise EngineNotLoadedError(
                message="VoxCPM2 引擎未加载，请先加载模型后再使用 Prompt 续写",
                engine="voxcpm2",
            )
        from .prompt import fn_voxcpm_prompt_continue

        return fn_voxcpm_prompt_continue(
            text=text,
            prompt_wav_path=prompt_wav_path,
            prompt_text=prompt_text,
            **kwargs,
        )

    def load_lora(self, lora_weights_path: str) -> tuple[list[str], list[str]]:
        """加载 LoRA（Low-Rank Adaptation）微调权重并注入基础模型。

        将独立训练的低秩适配矩阵（通常 < 基础模型 1% 的参数量）
        与 VoxCPM2 基础模型对应层合并，实现用少量参数定制化
        模型音色或风格（如特定声优音色、方言、歌唱风格等）。

        返回值语义：
            返回 ``(loaded_keys, skipped_keys)`` 二元组，帮助用户
            诊断 LoRA 是否完全生效：

            * ``loaded_keys``（list[str]）：成功注入权重的参数名列表。
              每项对应模型 ``state_dict`` 中被 LoRA 修改的层键名，
              可用于调试哪些层被实际修改。
            * ``skipped_keys``（list[str]）：跳过的不匹配参数名列表。
              当 LoRA 权重文件中某些 key 在当前基础模型中找不到
              对应层时会被跳过并记录在此。非空通常意味着：
              - LoRA 训练时使用的基模型版本与当前推理版本不一致
                （架构变更、层重命名等）。
              - 该 LoRA 是为另一引擎训练的（如 IndexTTS2 的 LoRA
                误用到 VoxCPM2）。
              遇到非空 skipped_keys 时应检查权重文件兼容性。

        Args:
            lora_weights_path: LoRA 权重文件路径（通常为 ``.pt`` 或
                              ``.safetensors`` 格式）。

        Returns:
            tuple[list[str], list[str]]: ``(loaded_keys, skipped_keys)``。

        Raises:
            EngineNotLoadedError: 基础模型未加载（LoRA 无法注入）。
            TTSError: 权限不足或权重文件格式损坏等错误，经
                ``logger.exception`` 记录后包装抛出。
        """
        if not self.is_ready():
            raise EngineNotLoadedError(
                message="VoxCPM2 引擎未加载，请先加载基础模型后再加载 LoRA 权重",
                engine="voxcpm2",
            )
        try:
            # WHY LoRA 相关方法每次调用都动态 import：
            # LoRA 训练/推理功能依赖可选的 `peft` 包与特定版本的
            # `transformers`、`accelerate`。若用户使用精简分发版
            # （不含 LoRA 训练依赖），在模块顶层 import lora.py
            # 会抛出 ImportError，导致整个应用启动失败。
            # 通过在方法内部延迟导入，并配合 try/except ImportError
            # 捕获，可以保证：
            #   1) 不使用 LoRA 功能的用户即使缺少 peft 也能正常启动。
            #   2) 实际调用 load_lora 时若缺依赖，能给出明确的中文
            #      安装提示，而不是模糊的 ImportError。
            from .lora import load_lora_weights
        except ImportError as e:
            logger.exception("[VoxCPM2Engine] LoRA 模块导入失败")
            raise TTSError(
                message=(
                    "VoxCPM2 LoRA 功能所需的依赖未安装。"
                    "请执行: pip install peft>=0.7.0 transformers>=4.36.0 "
                    f"accelerate>=0.25.0。原始错误: {e}"
                ),
                code="LORA_DEPENDENCY_MISSING",
                status_code=503,
            ) from e

        try:
            return load_lora_weights(lora_weights_path)
        except FileNotFoundError as e:
            logger.exception(
                "[VoxCPM2Engine] LoRA 权重文件不存在: %s",
                lora_weights_path,
            )
            raise TTSError(
                message=(f"LoRA 权重文件不存在: {lora_weights_path}。请检查路径是否正确，或通过训练界面重新导出权重。"),
                code="LORA_WEIGHTS_NOT_FOUND",
                status_code=404,
            ) from e

    def unload_lora(self) -> None:
        """卸载当前已加载的 LoRA 权重，恢复为基础模型原始权重。

        与 :meth:`load_lora` 构成对偶操作：从模型中移除 LoRA 适配层
        的影响，恢复到调用 ``load_lora`` 之前的推理输出。

        幂等性保证：
            未加载 LoRA 时调用本方法不会抛出异常，静默无操作。
            与 :meth:`set_lora_enabled(False)` 的区别：本方法会
            真正释放 LoRA 权重占用的显存，并移除适配层注册；
            而 ``set_lora_enabled`` 仅切换开关，权重仍驻留显存。
        """
        try:
            from .lora import unload_lora_weights
        except ImportError as e:
            logger.exception("[VoxCPM2Engine] LoRA 模块导入失败（卸载）")
            raise TTSError(
                message=(
                    "VoxCPM2 LoRA 功能所需的依赖未安装，无法执行卸载。"
                    "请执行: pip install peft>=0.7.0 transformers>=4.36.0 "
                    f"accelerate>=0.25.0。原始错误: {e}"
                ),
                code="LORA_DEPENDENCY_MISSING",
                status_code=503,
            ) from e

        unload_lora_weights()

    def set_lora_enabled(self, enabled: bool) -> None:
        """启用或禁用 LoRA 层（不卸载权重）。

        与 :meth:`unload_lora` 的区别：
            - ``unload_lora``：**真正移除** LoRA 适配层并释放显存，
              下次启用需重新加载权重文件（耗时数秒~数十秒）。
            - ``set_lora_enabled``：仅切换 LoRA 适配矩阵是否参与
              前向计算的布尔开关，**不释放显存**，切换瞬时完成，
              适合需要快速在"基础模型 ↔ LoRA 模型"之间对比 A/B 测试
              的场景。

        Args:
            enabled: True 启用 LoRA 层（需已先调用 :meth:`load_lora`）。
                    False 临时绕过 LoRA 层，使用基础模型原始输出。

        幂等性保证：
            未加载 LoRA 权重时调用本方法，静默无操作（不抛异常）。
            即使用户调用 ``set_lora_enabled(True)`` 但未加载权重，
            :attr:`lora_enabled` 属性仍会正确返回 False。
        """
        try:
            from .lora import fn_voxcpm_set_lora_enabled
        except ImportError as e:
            logger.exception("[VoxCPM2Engine] LoRA 模块导入失败（开关）")
            raise TTSError(
                message=(
                    "VoxCPM2 LoRA 功能所需的依赖未安装，无法切换状态。"
                    "请执行: pip install peft>=0.7.0 transformers>=4.36.0 "
                    f"accelerate>=0.25.0。原始错误: {e}"
                ),
                code="LORA_DEPENDENCY_MISSING",
                status_code=503,
            ) from e

        fn_voxcpm_set_lora_enabled(enabled)

    def get_lora_state_dict(self) -> dict[str, Any]:
        """获取当前 LoRA 参数的 state_dict 快照。

        用于调试、序列化或权重迁移场景：返回当前已加载 LoRA 适配层
        的完整参数字典（仅包含 LoRA 低秩矩阵，不含基础模型参数）。

        典型用法：
            * 调试：对比两次 ``load_lora`` 之间 state_dict 的差异，
              确认权重是否正确注入。
            * 热迁移：将 state_dict 通过网络发送至另一推理节点，
              无需重复读取磁盘文件即可恢复 LoRA 状态。
            * 可视化：提取特定层的权重进行直方图或分布分析。

        Returns:
            dict[str, Any]: 层名（str）→ 参数张量/数值（Any）的映射。
                           未加载 LoRA 时返回空字典 ``{}``。
        """
        try:
            from .lora import get_lora_state_dict
        except ImportError as e:
            logger.exception("[VoxCPM2Engine] LoRA 模块导入失败（状态）")
            raise TTSError(
                message=(
                    "VoxCPM2 LoRA 功能所需的依赖未安装，无法获取状态。"
                    "请执行: pip install peft>=0.7.0 transformers>=4.36.0 "
                    f"accelerate>=0.25.0。原始错误: {e}"
                ),
                code="LORA_DEPENDENCY_MISSING",
                status_code=503,
            ) from e

        return get_lora_state_dict()

    @property
    def lora_enabled(self) -> bool:
        """查询当前 LoRA 是否处于 **已加载且启用** 状态。

        只读属性。语义严格定义为"LoRA 权重已成功加载 AND 当前启用
        开关为 True"，两者缺一不可：

        ==============  =================  ==========  ==================
        场景            load_lora 已调用   开关状态    lora_enabled 返回
        ==============  =================  ==========  ==================
        从未加载 LoRA   ❌                 N/A         False
        已加载且启用    ✅                 True        True
        已加载但禁用    ✅                 False       False
        已卸载 LoRA     ❌（已卸载）       N/A         False
        ==============  =================  ==========  ==================

        与 :meth:`get_lora_state_dict` 的关系：
            ``lora_enabled == True`` ⇒ ``len(get_lora_state_dict()) > 0``；
            反之不必然成立（若底层实现支持加载权重但禁用，state_dict
            可能非空但启用开关为 False）。

        Returns:
            bool: 仅当 LoRA 权重已加载 AND 当前启用开关为 True 时
                  返回 True；其他所有情况均返回 False。
        """
        try:
            from .lora import is_lora_enabled
        except ImportError:
            return False

        return is_lora_enabled()
