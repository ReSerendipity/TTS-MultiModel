"""IndexTTS 2.5 引擎适配器模块。

**功能定位**：
    本模块实现 IndexTTS 2.5 语音合成引擎的完整适配器，是 TTS_MultiModel
    项目中支持**零样本语音克隆**和**8 维精细情感控制**的核心引擎模块。
    IndexTTS2 是由 Index Team 开发的先进 TTS 模型，特点是：
    - 仅需 3-10 秒参考音频即可实现高质量零样本音色克隆
    - 提供业界领先的 8 维离散情感空间控制，支持精细情感调节
    - 支持精确时长控制（target_duration），满足对口型等场景需求
    - 多后端 GPU 支持（NVIDIA CUDA / Apple MPS），CPU 兜底

**架构角色**：
    IndexTTS2Engine 是 :class:`TTSEngine` Protocol 的具体实现类。不同于
    :class:`VoxCPM2Engine` 仅作为 Facade（外观模式）将调用转发至内部子模块，
    本类是**真正自包含推理逻辑的引擎适配器**，直接封装 IndexTTS 2.5 的
    推理管线（模型加载、设备迁移、情感控制、语音合成），不依赖额外子模块。

**主要类**：
    - :class:`IndexTTS2Engine`：引擎适配器主类，实现完整的 TTSEngine 协议

**依赖关系**：
    - 上游依赖：
      * ``..engine_interface.TTSEngine``：引擎协议接口定义
      * ``..exceptions``：统一异常体系（EngineLoadError/GenerationError等）
      * ``..gpu_backend``：GPU 后端抽象（CUDA/MPS/CPU 检测与显存管理）
      * ``..text_frontend.normalize_text``：文本预处理规范化
      * ``indextts.infer_v2_5.IndexTTS2``：底层 IndexTTS2 推理库（外部依赖）
    - 下游被依赖：
      * ``..model_manager.ModelManager``：模型加载与引擎切换
      * ``..model_registry.registry``：全局引擎状态注册表
      * ``routes/generate/indextts2/``：API 路由层

**情感向量控制设计说明**：
    IndexTTS2 的情感控制采用 8 维连续向量空间，每个维度取值范围 [0.0, 1.0]，
    维度顺序与语义如下::

        索引  维度名        语义描述
        0     happy         开心/喜悦
        1     angry         愤怒/生气
        2     sad           悲伤/难过
        3     afraid        害怕/恐惧
        4     disgusted     厌恶/反感
        5     melancholic   忧郁/惆怅
        6     surprised     惊讶/吃惊
        7     calm          平静/镇定

    三种情感控制方式（互斥，优先级从高到低）：
        1. **音频参考（emo_audio_prompt）**：从情感参考音频中提取情感嵌入，
           最自然但需要准备参考音频
        2. **向量控制（emo_vector）**：直接传入 8 维向量，最精细可控，
           支持混合情感（如 happy=0.6 + surprised=0.4 表示兴奋）
        3. **文本描述（emo_text + use_emo_text=True）**：自然语言描述，
           最易用，由模型内部解析为情感向量

    emo_alpha 参数控制情感注入强度（0.0-1.0，默认 0.8）：
        - 值越低：情感越弱，越接近参考音频的中性音色
        - 值越高：情感越强烈，但过高（>0.9）可能导致音质失真或不稳定

**系统要求**：
    - GPU 模式：最低 6GB 显存 + 16GB 内存；推荐 8GB+ 显存获得流畅体验
    - CPU 模式：无 GPU 时自动兜底可用，推理速度较慢（实时比约 1:10~1:30）
    - 时长控制：支持 ``target_duration`` 参数精确控制输出音频时长（秒）

**与 model_manager.load_indextts2 的协作流程**：
    1. ``model_manager.load_indextts2()`` 被调用
    2. 创建 ``IndexTTS2Engine`` 实例（``__init__`` 中完成文件校验+模型加载+设备迁移）
    3. 调用 ``registry.set_indextts2_loaded(engine)`` 将实例注册到全局注册表
    4. 设置 ``registry.current_engine = "indextts2"`` 切换当前引擎
    5. 此后 ``is_ready()`` 返回 ``True``，可接受推理请求

**模型组件架构**：
    IndexTTS2 内部包含 4 个核心子模型：
    - **gpt**：GPT 风格自回归模型，负责文本 tokens → mel 谱 tokens 序列生成
    - **s2mel**：语义特征到 mel 谱的转换模型
    - **vocoder**：声码器（BigVGAN 变体），将 mel 谱转换为最终波形
    - **codec**：音频编解码器，用于参考音频的特征提取与重构

**DeepSpeed / BF16 支持说明**：
    - **DeepSpeed**：可选依赖，需额外安装 ``deepspeed`` 包。
      启用（``use_deepspeed=True``）后通过 DeepSpeed Inference Engine
      加速推理，在大 batch 或长文本场景下吞吐量提升显著。
    - **BF16**：CUDA 后端可用时默认开启（``use_bf16=True``），
      显存占用降低约 50%；MPS / CPU 后端会**强制关闭** BF16
      （见 :meth:`__init__` 中的 Why 注释）。

**显存管理策略**：
    - 加载前预检：model_manager 会先检查可用显存是否 ≥ 模型大小的 1.5 倍
    - 半精度推理：CUDA 下默认 BF16，显存占用减半
    - 组件级迁移：逐个迁移子模型到 GPU，codec 可容错留在 CPU
    - 卸载清理：显式删除子属性打破循环引用，配合 gc.collect() 和
      torch.cuda.empty_cache() 确保显存及时释放
"""

import contextlib
import gc
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..engine_interface import TTSEngine
from ..exceptions import (
    EngineLoadError,
    EngineNotLoadedError,
    InsufficientVRAMError,
    TTSError,
)
from ..text_frontend import normalize_text

logger = logging.getLogger("tts_multimodel")


def _save_pcm_wav_soundfile(path: str, wav: Any, sampling_rate: int) -> None:
    """用 soundfile 直写 16-bit PCM WAV，替代 index-tts 的 save_pcm_wav。

    背景：torchaudio>=2.9 把 WAV 编码委托给 TorchCodec（本机未安装、且无 ffmpeg），
    会导致 IndexTTS 合成保存阶段抛 ImportError。本函数复刻原 save_pcm_wav 的语义：
    入参 ``wav`` 处于 PCM 量级 ``[-32767, 32767]``（整型张量或 float 张量均可），
    先归一到 ``[-1, 1]`` float32，再用 soundfile 以 PCM_16 子类型写盘。

    Args:
        path: 输出 WAV 文件路径。
        wav: PCM 量级波形张量（常见形状 ``(channels, frames)``）或 numpy 数组。
        sampling_rate: 采样率（Hz）。
    """
    import soundfile as sf
    import torch

    w = wav
    if isinstance(w, torch.Tensor):
        w = w.detach().to(device="cpu", dtype=torch.float32)
    arr = np.asarray(w, dtype=np.float32)
    arr = np.clip(arr / 32767.0, -1.0, 1.0)
    if arr.ndim == 2:
        # (channels, frames) -> (frames, channels)；单声道压平为一维
        arr = arr.T
        if arr.shape[1] == 1:
            arr = arr[:, 0]
    sf.write(path, arr, int(sampling_rate), subtype="PCM_16")


class IndexTTS2Engine(TTSEngine):
    """IndexTTS 2.5 引擎适配器。

    封装 IndexTTS 2.5 推理接口，提供统一的 API 供 TTS MultiModel 使用。
    支持零样本语音克隆、三种情感控制方式和精确时长控制。

    **类职责**：
        本类是 IndexTTS2 模型在 TTS_MultiModel 中的唯一入口，负责：
        1. 模型文件完整性校验（:meth:`_validate_model_files`）
        2. 模型权重加载与设备迁移（:meth:`_load_model`、:meth:`_move_to_device`）
        3. 多后端自动检测与适配（CUDA/MPS/CPU）
        4. 显存管理与内存释放（:meth:`get_memory_info`、:meth:`unload`）
        5. 情感向量构建与预设模板（:meth:`build_emotion_vector`、:meth:`get_preset_emotions`）
        6. 文本预处理与推理管线执行（:meth:`infer`、:meth:`synthesize`）
        7. TTSEngine Protocol 兼容接口实现（generate_voice_clone 等）

    **Attributes 属性说明**：
        model_dir (str):
            模型文件目录的绝对路径。默认指向 ``<project_root>/model/IndexTTS-2.5``。
        use_deepspeed (bool):
            是否启用 DeepSpeed 推理加速标志。需额外安装 deepspeed 包，
            启用后在长文本/大 batch 场景下吞吐量提升显著。
        lang (str):
            合成语言，默认 ``"Auto"``（自动检测）。
        use_qwen_emo (bool):
            是否启用 QwenEmotion 情感文本模型。构造 IndexTTS2 时作为
            ``use_qwen_emo`` 传入，用于支持情感文本描述模式
            （``emo_text`` + ``use_emo_text=True``）。
        backend (GPUBackend):
            经 GPUBackendManager 检测或用户指定的 GPU 后端枚举值，
            可选值：``GPUBackend.CUDA``（NVIDIA GPU）、``GPUBackend.MPS``（Apple Silicon）、
            ``GPUBackend.CPU``（纯 CPU 模式）。
        device (str):
            最终使用的设备字符串，如 ``"cuda"``、``"cuda:0"``、``"mps"``、``"cpu"``。
            所有可迁移的模型子组件最终都会被放置到此设备上。
        use_bf16 (bool):
            最终生效的 BF16 半精度推理开关。CUDA 后端下默认为 True（显存减半），
            MPS/CPU 后端被强制覆盖为 False（避免算子不兼容或音质劣化）。
        tts (Any):
            底层 IndexTTS2 推理实例，类型为 ``indextts.infer_v2_5.IndexTTS2``。
            未加载或已卸载时为 ``None``，可通过 :meth:`is_ready` 检查状态。
            内部包含 4 个核心子模块：gpt、s2mel、vocoder、codec。

    **支持功能列表**：
        1. **零样本语音克隆**：仅需 3-10 秒参考音频即可克隆任意说话人音色
        2. **8 维情感控制**：happy/angry/sad/afraid/disgusted/melancholic/surprised/calm
           每个维度独立连续可调，支持混合情感
        3. **三种情感输入方式**：音频参考、向量直接控制、自然语言文本描述
        4. **精确时长控制**：通过 target_duration 参数指定输出音频秒数
        5. **时长缩放因子**：duration_factor 兼容参数，支持变速不变调
        6. **多后端支持**：NVIDIA CUDA（推荐）、Apple MPS、CPU 兜底
        7. **BF16 半精度推理**：CUDA 下自动启用，显存占用降低约 50%
        8. **DeepSpeed 加速**：可选推理加速引擎（需额外安装）
        9. **随机种子控制**：seed 参数支持可复现生成
        10. **预设情感模板**：内置 10 种常用情感预设（neutral/happy/angry 等）
        11. **显存实时监控**：get_memory_info() 提供显存使用快照
        12. **安全卸载机制**：完整的资源清理流程，支持引擎动态切换

    **不支持的功能（需切换至 VoxCPM2 引擎）**：
        - 语音设计（Voice Design / 文本描述生成音色）
        - 剧本工坊（多角色对话生成）
        - 流式生成（Streaming / 分段实时输出）
        - LoRA 微调与加载

    系统要求:
        - 最低配置: 6GB 显存 + 16GB 内存 (GPU 模式)
        - 推荐配置: 8GB+ 显存 + 16GB+ 内存
        - CPU 模式: 兜底可用，推理速度较慢（实时比 1:10~1:30）

    Example:
        >>> engine = IndexTTS2Engine(model_dir="/path/to/IndexTTS2")
        >>> engine.is_ready()
        True
        >>> sr, wav, path = engine.infer(
        ...     text="你好，世界",
        ...     spk_audio_prompt="reference.wav",
        ...     emo_vector=IndexTTS2Engine.build_emotion_vector(happy=0.8),
        ...     target_duration=3.0
        ... )
    """

    EMOTION_DIMENSIONS: list[str] = [
        "happy",
        "angry",
        "sad",
        "afraid",
        "disgusted",
        "melancholic",
        "surprised",
        "calm",
    ]
    """8 维情感向量的维度名称列表，顺序固定。

    维度索引映射：
        0 - happy        : 开心/喜悦
        1 - angry        : 愤怒/生气
        2 - sad          : 悲伤/难过
        3 - afraid       : 害怕/恐惧
        4 - disgusted    : 厌恶/反感
        5 - melancholic  : 忧郁/惆怅
        6 - surprised    : 惊讶/吃惊
        7 - calm         : 平静/镇定

    此列表为 emo_vector 参数的维度顺序依据，传入向量必须严格按此顺序排列，
    长度为 8，每个维度取值范围 [0.0, 1.0]。维度顺序不可更改。
    """

    def __init__(
        self,
        model_dir: str | None = None,
        use_bf16: bool = True,
        device: str | None = None,
        use_deepspeed: bool = False,
        lang: str = "Auto",
        use_qwen_emo: bool = False,
        version: str = "2.5",
    ) -> None:
        """初始化 IndexTTS 引擎（2.5 / 2.0 双版本共用实现）。

        版本变体说明：
            ``version="2.5"``（默认，向后兼容全部既有调用）走
            ``indextts.infer_v2_5``，权重目录 ``model/IndexTTS-2.5``，注册名
            ``indextts2``，支持显式时长控制（duration_factor/target_duration）
            与 5 语种（Auto/ZH/EN/JA/ES/AR）。
            ``version="2.0"`` 走 ``indextts.infer_v2``，权重目录
            ``model/IndexTTS-2.0``，注册名 ``indextts20``，仅中英双语、
            无显式时长控制（infer 时忽略 duration 参数，语速走后处理变速）。
            两者共用同一套依赖与代码库，仅推理入口类与权重不同。

        设备选择优先级：
            1. 显式传入的 ``device`` 参数（优先级最高）
            2. ``GPUBackendManager.detect_backend()`` 自动检测
               （CUDA > MPS > CPU 回退链）

        MPS / CPU 自动关闭 BF16 的原因（Why 注释见实现体）。

        Args:
            model_dir: 模型文件目录路径。``None`` 时回退到
                ``<project_root>/model/IndexTTS-2.5``（2.5）或 ``<project_root>/model/IndexTTS-2.0``（2.0）。
            use_bf16: 是否使用 BF16 混合精度推理。仅 CUDA 后端时此参数生效；
                MPS / CPU 后端会被强制覆盖为 ``False``。
            device: 强制指定运行设备（``"cuda"`` / ``"mps"`` / ``"cpu"``）。
                ``None`` 时自动检测。
            use_deepspeed: 是否启用 DeepSpeed 推理加速。
                需额外安装 ``deepspeed`` 依赖，否则加载时抛 ImportError。
            lang: 合成文本的语言代码（如 ``"zh"`` / ``"en"`` / ``"ja"``），
                默认 ``"zh"``。
            use_qwen_emo: 是否启用 Qwen 情感标签解析（依赖 Qwen 模型）。
            version: ``"2.5"`` 或 ``"2.0"``，决定推理入口模块与能力集。

        Attributes:
            self.model_dir (str): 实际使用的模型目录绝对路径。
            self.use_deepspeed (bool): DeepSpeed 加速标志。
            self.backend (GPUBackend): 经 GPUBackendManager 检测/指定的后端枚举。
            self.device (str): 最终使用的设备字符串（如 ``"cuda:0"``）。
            self.use_bf16 (bool): 最终生效的 BF16 开关（MPS/CPU 下恒为 False）。
            self.lang (str): 默认合成语言代码。
            self.use_qwen_emo (bool): Qwen 情感标签解析开关。
            self.version_str (str): 变体版本（"2.5" / "2.0"）。
            self.supports_duration (bool): 是否支持显式时长控制。
            self.supported_langs (set[str]): 允许的语言代码集合。
            self.tts (Any): 底层 IndexTTS2 推理实例，未加载时为 ``None``。
        """
        # ========== 版本变体配置 ==========
        # 2.5 与 2.0 共用本类：唯一差异是推理入口模块、权重目录、能力集。
        self.version_str: str = "2.0" if str(version) == "2.0" else "2.5"
        if self.version_str == "2.0":
            self._infer_module: str = "indextts.infer_v2"
            self._engine_name: str = "indextts20"
            self._model_dir_name: str = "IndexTTS-2.0"
            self._hf_repo: str = "IndexTeam/IndexTTS-2"
            self.supports_duration: bool = False
            self.supported_langs: set[str] = {"Auto", "ZH", "EN"}
        else:
            self._infer_module = "indextts.infer_v2_5"
            self._engine_name = "indextts2"
            self._model_dir_name = "IndexTTS-2.5"
            self._hf_repo = "IndexTeam/IndexTTS-2.5"
            self.supports_duration = True
            self.supported_langs = {"Auto", "ZH", "EN", "JA", "ES", "AR"}

        # ========== 延迟导入 GPU 后端模块 ==========
        # 避免在模块级别导入导致无 CUDA 环境下的导入错误
        from ..gpu_backend import GPUBackend, GPUBackendManager

        # ========== 模型目录路径解析 ==========
        # 默认模型目录：<项目根目录>/model/IndexTTS-2.5（2.5）或 IndexTTS-2.0（2.0）
        # Path(__file__).parent.parent.parent 定位到项目根：
        #   本文件位于 app/integrated_app/engines/，向上3级到达项目根
        if model_dir is None:
            project_root = Path(__file__).parent.parent.parent
            model_dir = str(project_root / "model" / self._model_dir_name)

        self.model_dir: str = model_dir
        self.use_deepspeed: bool = use_deepspeed
        self.lang: str = lang
        self.use_qwen_emo: bool = use_qwen_emo

        # ========== 后端检测与设备选择逻辑 ==========
        # 后端选择优先级链：CUDA > MPS > CPU（自动检测模式）
        # 用户显式指定 device 参数时优先级最高，覆盖自动检测结果
        self.backend: GPUBackend = GPUBackendManager.detect_backend()
        if device:
            # ---------- 用户显式指定设备模式 ----------
            self.device: str = device
            if device.startswith("cuda"):
                # CUDA 设备：尊重用户的 use_bf16 设置
                self.use_bf16: bool = use_bf16
            else:
                # WHY MPS/CPU 强制 use_bf16=False：
                # ① Apple MPS 算子支持不完整：BF16 对应的部分 CUDA kernel
                #    在 MPS 端未实现，会触发 RuntimeError 或静默 fallback 到 CPU，
                #    结果不可控；
                # ② CPU 使用 BF16 不会加速：x86/ARM CPU 的 BF16 指令集支持
                #    有限，softmax / layer norm 等算子会因精度损失导致音质劣化，
                #    且实际推理速度反而比 FP32 更慢。
                self.use_bf16 = False
        else:
            # ---------- 自动检测后端模式 ----------
            # 根据 detect_backend() 结果按优先级选择最佳设备
            if self.backend == GPUBackend.CUDA:
                # NVIDIA GPU：首选 CUDA，启用 BF16 半精度
                # BF16 可将显存占用降低约 50%（从 ~10GB → ~5GB），
                # 且现代 NVIDIA GPU（Turing+ 架构）对 BF16 有 Tensor Core 加速
                self.device = "cuda"
                self.use_bf16 = use_bf16
            elif self.backend == GPUBackend.MPS:
                # Apple Silicon（M1/M2/M3/M4）：使用 Metal Performance Shaders
                # MPS 目前对 BF16 支持不完善，强制使用 FP32
                # 注意：MPS 下显存与系统内存共享，无需担心显存放不下的问题
                self.device = "mps"
                self.use_bf16 = False
            else:
                # CPU 兜底模式：无可用 GPU 时使用纯 CPU 推理
                # 速度较慢（实时比约 1:10~1:30），但保证功能可用
                self.device = "cpu"
                self.use_bf16 = False

        self.tts: Any = None

        logger.info(
            f"[IndexTTS2] 初始化引擎: model_dir={self.model_dir}, "
            f"device={self.device}, bf16={self.use_bf16}, "
            f"backend={self.backend.value}, deepspeed={self.use_deepspeed}"
        )

        try:
            self._validate_model_files()
            self._load_model()
        except Exception:
            self.unload()
            raise

    def _validate_model_files(self) -> None:
        """验证必需的模型文件是否存在且可读。

        必需文件清单及其作用（IndexTTS 2.5 权重布局）：
            - ``gpt.pth``：GPT 文本→mel 谱自回归生成模型权重
            - ``s2mel.pth``：mel 谱预测模型（基于语义 token）权重
            - ``bpe.model``：BPE 分词器（SentencePiece 模型），用于文本 tokenization
            - ``config.yaml``：IndexTTS2 模型结构超参数配置
            - ``feat1.pt``：wav2vec2-BERT 第 1 层投影矩阵（语义特征提取）
            - ``feat2.pt``：wav2vec2-BERT 第 2 层投影矩阵（语义特征提取）
            - ``wav2vec2bert_stats.pt``：wav2vec2-BERT 特征归一化统计量（均值/方差）
            - ``configuration.json``：wav2vec2-BERT 预训练模型配置

        可选但建议文件（缺失仅 warning，不阻塞加载）：
            - ``bigvgan.pth``：BigVGAN 声码器权重（IndexTTS 2.5 使用的声码器）
            - ``campplus.pth``：CAMPPlus 说话人模型权重（音色特征提取）

        Raises:
            EngineLoadError: 当任一必需文件缺失或当前进程无读取权限时抛出，
                错误信息中包含缺失/不可读的文件名列表。
        """
        if self.version_str == "2.0":
            # ⚠️ 2.0 权重仓库（IndexTeam/IndexTTS-2）部分辅助文件名与 2.5 存在
            #    差异（如 feat*.data vs feat*.pt），尚未逐字核实，先只硬校验两项
            #    必然存在的核心文件，其余降为可选告警，避免缺省误报阻塞加载。
            #    权重下载完成后应按实际清单精修此列表。
            required_files: list[str] = [
                "config.yaml",
                "gpt.pth",
            ]
        else:
            required_files = [
                "gpt.pth",
                "s2mel.pth",
                "codec.pth",
                "config.yaml",
                "feat1.pt",
                "feat2.pt",
                "wav2vec2bert_stats.pt",
                "configuration.json",
                "multilingual_zh_ja_yue_char_del.tiktoken",
            ]

        # 可选文件：不同权重布局可能附带或不附带，缺失仅告警不阻塞。
        # bpe.model 在 IndexTeam/IndexTTS-2.5 仓库中不存在（以 *.tiktoken 分词器替代），
        # 保留为可选项以兼容个别历史布局；bigvgan/campplus 为 2.5 建议文件。
        optional_files: list[str] = [
            "bpe.model",
            "bigvgan.pth",
            "campplus.pth",
        ]

        problematic_files: list[str] = []
        for filename in required_files:
            filepath = os.path.join(self.model_dir, filename)
            try:
                if not os.path.exists(filepath):
                    problematic_files.append(f"{filename}(缺失)")
                elif not os.access(filepath, os.R_OK):
                    problematic_files.append(f"{filename}(无读取权限)")
            except PermissionError:
                problematic_files.append(f"{filename}(无读取权限)")
            except OSError as e:
                problematic_files.append(f"{filename}(访问错误: {e})")

        for filename in optional_files:
            if not os.path.exists(os.path.join(self.model_dir, filename)):
                logger.warning(
                    f"[IndexTTS2] 可选文件缺失: {filename}（IndexTTS 2.5 建议文件，"
                    f"缺失时相关能力可能降级，但不阻塞加载）"
                )

        if problematic_files:
            raise EngineLoadError(
                f"IndexTTS 2.5 模型文件不可读: {problematic_files}\n"
                f"请运行: python scripts/download_indextts2.py 下载模型，"
                f"或检查目录权限。",
                engine=self._engine_name,
            )

        logger.info(f"[IndexTTS2] 模型文件验证通过: {self.model_dir}")

    def _load_model(self) -> None:
        """加载 IndexTTS 2.5 模型权重并初始化推理管线。

        执行流程：
            1. 导入 ``indextts.infer_v2_5.IndexTTS2``（未安装时抛 ImportError）
            2. 实例化 IndexTTS2（期间加载所有权重）
            3. 调用 :meth:`_move_to_device` 将模型组件迁移到目标设备
            4. GPU 后端下调用 :meth:`_log_memory_info` 打印显存快照

        Raises:
            ImportError: ``indextts`` 包未安装或 ``IndexTTS2`` 类导入失败。
            InsufficientVRAMError: 加载过程中触发 CUDA OOM，错误信息包含
                预估需求显存与实际可用显存（GB）。
            EngineLoadError: 其他模型权重加载失败场景（损坏、版本不兼容等）。
        """
        try:
            import importlib

            _infer_mod = importlib.import_module(self._infer_module)
            IndexTTS2 = _infer_mod.IndexTTS2
        except ImportError as e:
            raise ImportError(
                f"indextts 未安装或缺少 {self._infer_module} 推理模块。"
                "PyPI 无 'indextts' 包，请从官方仓库安装：\n"
                "  git clone https://github.com/index-tts/index-tts.git\n"
                "  cd index-tts && pip install -e .\n"
                "（2.0 与 2.5 共用同一仓库，安装一次即同时提供 infer_v2 与 infer_v2_5）"
            ) from e

        config_path = os.path.join(self.model_dir, "config.yaml")

        logger.info("[IndexTTS2] 开始加载模型...")
        start_time = time.time()

        # ---- 兼容性补丁 1：用 soundfile 替换 save_pcm_wav，绕开 torchcodec ----
        # torchaudio>=2.9 把 WAV 编码委托给 TorchCodec（本机未安装、且无 ffmpeg），
        # 会导致 infer 保存阶段 ImportError。此处仅替换 IndexTTS 推理模块命名空间内的
        # save_pcm_wav 绑定为 soundfile 实现，作用域限于本引擎，不修改第三方源码。
        try:
            _infer_mod.save_pcm_wav = _save_pcm_wav_soundfile  # type: ignore[attr-defined]
            logger.info("[IndexTTS2] 已启用 soundfile WAV 保存补丁（绕开 torchcodec）")
        except Exception as _patch_err:
            logger.warning(f"[IndexTTS2] save_pcm_wav 补丁应用失败（将回退 torchaudio）: {_patch_err}")

        # ---- 兼容性补丁 2：2.0 与 2.5 构造函数精度参数名不同 ----
        # infer_v2（2.0）用 use_fp16；infer_v2_5（2.5）用 use_bf16。
        precision_kwarg = {"use_fp16": self.use_bf16} if self.version_str == "2.0" else {"use_bf16": self.use_bf16}

        try:
            # WHY use_cuda_kernel=False 默认：
            # CUDA kernel 依赖 Triton JIT 编译，首次使用会触发 30~60 秒的
            # 编译延迟，且要求 CUDA compute capability ≥ 7.0（Volta+）。
            # 普通用户 8GB 显存下关闭 kernel 即可流畅运行；高级用户如需
            # 极致推理吞吐，可手动传入 ``use_cuda_kernel=True``。
            self.tts = IndexTTS2(
                cfg_path=config_path,
                model_dir=self.model_dir,
                use_cuda_kernel=False,
                use_deepspeed=self.use_deepspeed,
                use_qwen_emo=self.use_qwen_emo,
                **precision_kwarg,
            )
        except RuntimeError as e:
            err_msg = str(e).lower()
            if "out of memory" in err_msg or "cuda oom" in err_msg:
                try:
                    from ..gpu_backend import GPUBackendManager

                    _total, _alloc, _reserved, free_bytes = GPUBackendManager.get_memory_info()
                    free_gb = free_bytes / (1024**3)
                except Exception:
                    free_gb = 0.0
                needed_gb: float = 9.0 if self.use_bf16 else 6.0
                raise InsufficientVRAMError(
                    f"IndexTTS2 加载显存不足，需要 {needed_gb:.1f}GB，"
                    f"实际可用 {free_gb:.1f}GB。请关闭其他占用显存的程序"
                    f"或切换到 CPU 模式。"
                ) from e
            raise EngineLoadError(
                f"IndexTTS2 模型加载失败 (RuntimeError): {e}",
                engine=self._engine_name,
            ) from e
        except TTSError:
            raise
        except Exception as e:
            logger.exception(f"[IndexTTS2] 模型加载失败: {e}")
            raise EngineLoadError(
                f"IndexTTS2 模型加载失败: {type(e).__name__}: {e}",
                engine=self._engine_name,
            ) from e

        load_time = time.time() - start_time
        logger.info(f"[IndexTTS2] 模型加载完成，耗时: {load_time:.1f}秒")

        self._move_to_device()

        if self.backend.value != "cpu":
            self._log_memory_info()

    def _move_to_device(self) -> None:
        """将模型组件逐个迁移到目标设备。

        Why 不使用 ``self.tts.to(device)`` 整体迁移：
            IndexTTS2 内部部分组件（如 wav2vec2bert 的预处理）设计为常驻 CPU，
            且 codec（声码器，BigVGAN 风格）权重位于"lazy init"buffer 中，
            整体 ``to(device)`` 对 codec 无效。必须显式遍历各子组件调用
            ``.to(device)`` 才能确保所有推理子模块都在正确设备上。

        每个组件独立 try/except：单个组件（如 codec）迁移失败时，仅记录
        warning 并让其留在 CPU，不影响其他组件享受 GPU 加速。
        """
        device_str: str = self.device

        components_moved: list[str] = []
        for attr in ["gpt", "s2mel", "vocoder", "codec"]:
            sub = getattr(self.tts, attr, None)
            if sub is not None and hasattr(sub, "to"):
                try:
                    sub.to(device_str)
                    components_moved.append(attr)
                    logger.debug(f"[IndexTTS2] {attr} -> {device_str}")
                except Exception as e:
                    logger.warning(f"[IndexTTS2] 移动 {attr} 到 {device_str} 失败(将保留在 CPU): {e}")

        if components_moved:
            logger.info(f"[IndexTTS2] 已移动组件到 {device_str}: {', '.join(components_moved)}")

    def _log_memory_info(self) -> None:
        """记录 GPU 显存使用快照到日志。

        获取失败（驱动异常、MPS 未暴露接口等）时仅打 debug 日志，
        不抛出异常，保证主流程不中断。
        """
        try:
            from ..gpu_backend import GPUBackendManager

            mem_info = GPUBackendManager.get_memory_info()
            total_gb = mem_info[0] / (1024**3)
            allocated_gb = mem_info[1] / (1024**3)
            free_gb = mem_info[3] / (1024**3)

            logger.info(
                f"[IndexTTS2] 显存状态: 总计 {total_gb:.2f}GB, 已分配 {allocated_gb:.2f}GB, 可用 {free_gb:.2f}GB"
            )
        except Exception as e:
            logger.debug(f"[IndexTTS2] 获取显存信息失败: {e}")

    def infer(
        self,
        text: str,
        spk_audio_prompt: str,
        output_path: str | None = None,
        emo_audio_prompt: str | None = None,
        emo_alpha: float = 0.8,
        emo_vector: list[float] | None = None,
        emo_text: str | None = None,
        use_emo_text: bool = False,
        target_duration: float | None = None,
        seed: int | None = None,
        duration_factor: float = 1.0,
        lang: str | None = None,
        **kwargs: Any,
    ) -> tuple[int, np.ndarray, str]:
        """执行 IndexTTS 2.5 语音合成推理。

        三种情感控制方式（互斥优先级从高到低）：
            1. ``emo_audio_prompt``：指定情感参考音频路径，从中提取情感嵌入
            2. ``emo_vector``：直接传入 8 维情感向量
            3. ``emo_text``（配合 ``use_emo_text=True``）：自然语言情感描述

        Args:
            text: 待合成的正文文本。
            spk_audio_prompt: 说话人参考音频文件路径（零样本克隆必需）。
            output_path: 输出音频文件路径。``None`` 时自动创建临时文件，
                调用方负责在使用后删除。
            emo_audio_prompt: 情感参考音频文件路径（优先级最高）。
            emo_alpha: 情感注入强度，范围 ``[0.0, 1.0]``，默认 ``0.8``。
                值越大情感越明显，过高可能导致音质失真。
            emo_vector: 8 维情感向量 ``[happy, angry, sad, afraid,
                disgusted, melancholic, surprised, calm]``。
                维度不等于 8 时记录 warning 并忽略。
            emo_text: 自然语言情感描述文本（如 ``"非常开心的语气"``）。
                需配合 ``use_emo_text=True`` 使用。
            use_emo_text: 是否启用文本情感描述模式。
            target_duration: 目标音频时长（秒），支持精确时长控制。
                ``None`` 或 ``<= 0`` 时由模型自适应。
            seed: 随机数种子，用于可复现生成。``None`` 时使用随机种子。
            duration_factor: 时长缩放因子（兼容参数），``target_duration``
                优先于本参数。默认 ``1.0``（不缩放）。
            lang: 合成语言代码。``None`` 时使用构造时传入的 ``self.lang``
                默认 ``"Auto"``（自动检测）。
            **kwargs: 额外透传给底层 ``IndexTTS2.infer`` 的参数。

        Returns:
            tuple[int, np.ndarray, str]: 三元组 ``(sample_rate, wav, output_path)``：
                - ``sample_rate`` (int)：输出音频采样率（Hz，通常为 22050）
                - ``wav`` (np.ndarray)：合成的波形数据（float32，形状 ``(N,)`` 或 ``(1, N)``）
                - ``output_path`` (str)：保存到磁盘的音频文件路径

        Raises:
            EngineNotLoadedError: 当前引擎未就绪（``is_ready() == False``）。
            FileNotFoundError: ``spk_audio_prompt`` 或 ``emo_audio_prompt`` 指定的文件不存在。
            ValueError: 文本为空字符串，或使用情感文本模式但未启用
                ``use_qwen_emo``。
            GenerationError: 底层推理过程中发生未分类运行时错误。
        """
        from ..exceptions import GenerationError

        if not self.is_ready():
            raise EngineNotLoadedError(
                "IndexTTS2 引擎未加载，请先调用 load() 或切换引擎。",
                engine=self._engine_name,
            )

        if not text or not text.strip():
            raise ValueError("合成文本 text 不能为空。")

        # 文本预处理：清理 Markdown/Emoji + 标点规范化 + 数字展开
        # 注意：TTSError（含内容安全拦截 ContentSafetyError）必须向上传播，
        # 禁止静默降级为原始文本，否则不安全文本会绕过过滤进入合成。
        try:
            text = normalize_text(text, lang if lang is not None else self.lang)
            if emo_text and use_emo_text:
                emo_text = normalize_text(emo_text, lang if lang is not None else self.lang)
        except TTSError:
            raise
        except Exception as e:
            logger.debug(f"[IndexTTS2] 文本预处理失败（使用原始文本）: {e}")

        if not os.path.exists(spk_audio_prompt):
            raise FileNotFoundError(f"说话人参考音频不存在: {spk_audio_prompt}")

        temp_created: bool = False
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".wav", prefix="indextts2_")
            os.close(fd)
            temp_created = True

        logger.info(
            f"[IndexTTS2] 开始合成: text='{text[:50]}...', "
            f"output={output_path}, "
            f"emo_alpha={emo_alpha}, "
            f"target_duration={target_duration}, "
            f"lang={lang if lang is not None else self.lang}"
        )

        try:
            # ========== 推理参数构建 ==========
            # 基础必需参数：说话人参考音频、合成文本、输出路径
            # verbose=False 关闭底层 IndexTTS2 的冗余调试输出
            infer_kwargs: dict[str, Any] = {
                "spk_audio_prompt": spk_audio_prompt,
                "text": text,
                "output_path": output_path,
                "verbose": False,
            }

            # ========== 情感控制参数选择（互斥优先级逻辑） ==========
            # 三种情感控制方式设计为互斥，高优先级方式存在时自动忽略低优先级
            # 优先级顺序：音频参考 > 向量控制 > 文本描述
            # 这种设计避免多种情感信号冲突导致生成结果不稳定
            if emo_audio_prompt and os.path.exists(emo_audio_prompt):
                # 优先级 1：情感参考音频模式
                # 原理：底层模型会从参考音频中提取情感嵌入向量，与说话人嵌入分离
                # 适用场景：有明确情感参考样本，追求最自然的情感表现
                infer_kwargs["emo_audio_prompt"] = emo_audio_prompt
                logger.debug("[IndexTTS2] 情感控制模式：音频参考")
            elif emo_vector is not None:
                # 优先级 2：8 维情感向量模式
                # 原理：直接在情感嵌入空间中指定坐标，每个维度独立控制对应情感强度
                # 维度顺序必须严格遵循 EMOTION_DIMENSIONS 定义
                # 适用场景：需要精确控制情感混合比例，API 程序化调用
                if len(emo_vector) != 8:
                    # 维度不匹配时安全降级：记录警告但不中断流程，使用中性情感
                    logger.warning(f"[IndexTTS2] emo_vector 应为 8 维，当前为 {len(emo_vector)} 维，已忽略。")
                else:
                    # 复制向量避免外部修改影响内部状态
                    infer_kwargs["emo_vector"] = list(emo_vector)
                    logger.debug(f"[IndexTTS2] 情感控制模式：向量控制={emo_vector}")
            elif emo_text and use_emo_text:
                # 优先级 3：自然语言文本描述模式
                # 原理：底层模型通过文本编码器将情感描述映射到情感嵌入空间
                # 适用场景：用户交互场景，最易用但可控性相对较低
                # use_emo_text 作为显式开关防止误将普通文本当作情感描述
                # IndexTTS 2.5 情感文本模式依赖 QwenEmotion 情感文本模型，
                # 未以 use_qwen_emo=True 构造引擎时无法使用该模式。
                if not self.use_qwen_emo:
                    raise ValueError(
                        "IndexTTS2 情感文本描述模式需要以 use_qwen_emo=True 构造引擎"
                        "（加载 QwenEmotion 情感文本模型）才能使用。"
                    )
                infer_kwargs["emo_text"] = emo_text
                infer_kwargs["use_emo_text"] = True
                logger.debug(f"[IndexTTS2] 情感控制模式：文本描述='{emo_text}'")

            # ========== 情感强度控制 ==========
            # emo_alpha 无论使用哪种情感控制方式都生效
            # 原理：情感嵌入 E 与中性说话人嵌入 S 的线性插值：S' = S + alpha * (E - S)
            # - alpha=0.0：完全不注入情感，使用纯音色克隆
            # - alpha=0.8（默认）：适度情感注入，平衡情感表现与音质
            # - alpha=1.0：完全使用情感嵌入，情感最强但可能出现失真
            infer_kwargs["emo_alpha"] = emo_alpha

            # ========== 时长控制参数（互斥，仅 2.5 支持） ==========
            # target_duration 优先级高于 duration_factor
            if not self.supports_duration:
                if (target_duration and target_duration > 0) or duration_factor != 1.0:
                    logger.debug(
                        f"[IndexTTS2] {self.version} 不支持显式时长控制，"
                        f"忽略 target_duration/duration_factor（语速请用后处理 tempo）"
                    )
            elif target_duration and target_duration > 0:
                # 精确时长控制模式：模型会通过时长预测器调整 mel 谱长度
                # 使最终音频时长精确匹配目标值（误差通常 < 0.1 秒）
                # 适用场景：对口型、视频配音、固定时长广告等
                infer_kwargs["target_duration"] = float(target_duration)
                logger.debug(f"[IndexTTS2] 时长控制模式：精确时长={target_duration}秒")
            elif duration_factor != 1.0:
                # 时长缩放因子模式：相对速度调整
                # factor > 1.0 变慢，< 1.0 变快；尽量保持音色自然
                # 适用场景：整体语速微调
                infer_kwargs["duration_factor"] = float(duration_factor)
                logger.debug(f"[IndexTTS2] 时长控制模式：缩放因子={duration_factor}")

            # ========== 随机种子设置 ==========
            # 设置固定种子可使生成结果可复现（相同时输入参数得到相同输出）
            # None 时使用系统随机种子，每次生成结果略有不同
            if seed is not None:
                infer_kwargs["seed"] = int(seed)

            # ========== 语言设置（仅 2.5 的 infer 接受 lang 形参） ==========
            # infer_v2（2.0）的 infer() 无 lang 参数，若下传会落入 **generation_kwargs
            # 被 model.generate 拒绝（ValueError: model_kwargs 'lang' not used）。
            # 故 lang 仅在 2.5 下传；2.0 只校验语言、不注入。
            _lang_val = lang if lang is not None else self.lang
            if _lang_val not in self.supported_langs:
                logger.debug(
                    f"[IndexTTS2] {self.version} 不支持语言 {_lang_val}，回退 Auto"
                )
                _lang_val = "Auto"
            if self.version_str == "2.5":
                infer_kwargs["lang"] = _lang_val

            # 透传额外 kwargs，支持高级用户传入底层 IndexTTS2 的其他参数
            infer_kwargs.update(kwargs)

            # ========== 执行核心推理 ==========
            # 推理管线内部流程：
            #   1. 文本 BPE 分词 → token IDs
            #   2. 参考音频 wav2vec2-BERT 特征提取 → 说话人嵌入 + 语义特征
            #   3. 情感信号注入（音频/向量/文本 → 情感嵌入）
            #   4. GPT 自回归生成 mel tokens 序列（受情感+时长条件控制）
            #   5. s2mel 将语义特征对齐到 mel 谱
            #   6. vocoder 声码器将 mel 谱转换为最终波形
            #   7. 写入 output_path 指定的 WAV 文件
            logger.debug(f"[IndexTTS2] 推理参数: {list(infer_kwargs.keys())}")
            result = self.tts.infer(**infer_kwargs)

            # ========== GPU 同步与结果解析 ==========
            # CUDA 操作是异步的，synchronize() 确保所有 GPU 计算完成
            # 避免返回的 wav tensor 数据未就绪导致后续读取错误
            from ..gpu_backend import GPUBackendManager

            GPUBackendManager.synchronize(device=self.device)

            # 底层 infer 返回格式随版本不同：
            #   - 2.5（infer_v2_5）返回 (sample_rate, wav_tensor) 元组
            #   - 2.0（infer_v2）仅返回已写盘的输出路径 str（无元组）
            # 统一规整为 (sample_rate, wav_ndarray)，保证对外契约一致。
            if isinstance(result, tuple) and len(result) >= 2:
                sample_rate, wav = int(result[0]), np.asarray(result[1])
                logger.debug(f"[IndexTTS2] 推理完成: sample_rate={sample_rate}, wav_shape={wav.shape}")
            elif isinstance(result, (str, os.PathLike)) and os.path.exists(str(result)):
                # 2.0：infer 只回写文件路径，用 soundfile 读回波形
                import soundfile as _sf

                wav, sample_rate = _sf.read(str(result))
                wav = np.asarray(wav, dtype=np.float32)
                logger.debug(
                    f"[IndexTTS2] 2.0 从输出文件读回波形: sr={sample_rate}, shape={wav.shape}"
                )
            else:
                logger.warning(f"[IndexTTS2] 推理返回格式异常: {type(result)}")
                sample_rate, wav = 22050, np.zeros(0, dtype=np.float32)

            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                logger.info(f"[IndexTTS2] 合成完成: {output_path} ({file_size / 1024:.1f} KB)")
            else:
                logger.error(f"[IndexTTS2] 输出文件未生成: {output_path}")

            return sample_rate, wav, output_path

        except (EngineNotLoadedError, FileNotFoundError, ValueError, TTSError):
            raise
        except Exception as e:
            logger.exception(f"[IndexTTS2] 合成失败: {e}")
            if temp_created and os.path.exists(output_path):
                with contextlib.suppress(OSError):
                    os.unlink(output_path)
            raise GenerationError(
                f"IndexTTS 2.5 合成失败: {type(e).__name__}: {e}",
                engine=self._engine_name,
            ) from e

    def synthesize(
        self,
        text: str,
        spk_audio_prompt: str,
        output_path: str | None = None,
        emo_audio_prompt: str | None = None,
        emo_vector: list[float] | None = None,
        emo_text: str | None = None,
        duration_factor: float = 1.0,
        lang: str | None = None,
        **kwargs: Any,
    ) -> tuple[int, np.ndarray, str]:
        """TTSEngine Protocol 合成入口（infer 的薄封装）。

        与 :meth:`infer` 参数一一对应，仅省略不常用的 ``emo_alpha`` /
        ``use_emo_text`` / ``target_duration`` / ``seed`` 参数，
        这些参数通过 ``**kwargs`` 透传。

        Args:
            text: 待合成的正文文本。
            spk_audio_prompt: 说话人参考音频文件路径。
            output_path: 输出音频文件路径，``None`` 时创建临时文件。
            emo_audio_prompt: 情感参考音频路径（情感控制优先级 1）。
            emo_vector: 8 维情感向量（优先级 2）。
            emo_text: 自然语言情感描述（优先级 3，需配合
                ``use_emo_text=True`` 在 kwargs 中传入）。
            duration_factor: 时长缩放因子，默认 ``1.0``。
            lang: 合成语言代码。``None`` 时使用构造时传入的 ``self.lang``
                （默认 ``"Auto"`` 自动检测）。
            **kwargs: 透传给 :meth:`infer` 的其他参数（如 emo_alpha、seed 等）。

        Returns:
            tuple[int, np.ndarray, str]: 同 :meth:`infer` 返回值
            ``(sample_rate, wav, output_path)``。
        """
        return self.infer(
            text=text,
            spk_audio_prompt=spk_audio_prompt,
            output_path=output_path,
            emo_audio_prompt=emo_audio_prompt,
            emo_vector=emo_vector,
            emo_text=emo_text,
            duration_factor=duration_factor,
            lang=lang,
            **kwargs,
        )

    def get_memory_info(self) -> dict[str, Any]:
        """获取当前引擎运行设备的显存/内存使用快照。

        Returns:
            dict[str, Any]: 包含以下键（单位均为 GB）：
                - ``total_gb``: 设备总显存（CPU 模式为 0）
                - ``allocated_gb``: 当前已分配显存
                - ``reserved_gb``: 缓存分配器保留显存
                - ``free_gb``: 可用显存（total - allocated）
                - ``device``: 设备字符串（如 ``"cuda:0"``、``"cpu"``）
        """
        from ..gpu_backend import GPUBackend, GPUBackendManager

        if self.backend == GPUBackend.CPU:
            return {
                "total_gb": 0,
                "allocated_gb": 0,
                "reserved_gb": 0,
                "free_gb": 0,
                "device": "cpu",
            }

        mem_info = GPUBackendManager.get_memory_info()
        return {
            "total_gb": mem_info[0] / (1024**3),
            "allocated_gb": mem_info[1] / (1024**3),
            "reserved_gb": mem_info[2] / (1024**3),
            "free_gb": mem_info[3] / (1024**3),
            "device": self.device,
        }

    def is_ready(self) -> bool:
        """检查引擎是否已加载并准备就绪。

        Returns:
            bool: ``self.tts`` 属性存在且非 ``None`` 时返回 ``True``，
            未加载、已卸载或加载失败均返回 ``False``。
        """
        return hasattr(self, "tts") and self.tts is not None

    def load(self) -> None:
        """加载模型（本引擎的 no-op 兼容实现）。

        IndexTTS2Engine 的模型在 :meth:`__init__` 中即完成加载，
        本方法仅为满足 ``TTSEngine`` Protocol 契约而提供空实现。
        如需重新加载，请先调用 :meth:`unload` 再创建新实例。
        """
        pass

    def unload(self) -> None:
        """卸载模型并释放 GPU/CPU 内存资源。

        清理顺序（Why 注释见实现体）：
            1. 显式删除 ``self.tts`` 的所有子属性引用（gpt/s2mel/vocoder/codec…）
            2. 删除 ``self.tts`` 引用并置空
            3. ``gc.collect()`` 强制触发 Python GC
            4. GPU 后端下执行 synchronize + empty_cache + ipc_collect

        所有清理步骤均包裹在 try 中，单个步骤失败不中断整体清理流程，
        仅记录 ``logger.exception``，不重新抛出异常——卸载必须尽量成功，
        失败不应导致引擎切换中断。
        """
        logger.info("[IndexTTS2] 开始卸载模型...")

        try:
            if hasattr(self, "tts") and self.tts is not None:
                # WHY 显式删除子属性 + gc.collect：
                # IndexTTS2 内部各组件（gpt/s2mel/vocoder）持有大量 Tensor，
                # 仅 ``del self.tts`` 会使 self.tts 引用计数减 1，但组件间可能
                # 存在循环引用，Python GC 可能不会立刻回收；显式删除子属性
                # 可直接打破循环引用，再配合 gc.collect() 确保 Tensor
                # 析构函数被调用，显存能及时归还 CUDA 缓存分配器。
                for attr in ["gpt", "s2mel", "vocoder", "codec"]:
                    try:
                        if hasattr(self.tts, attr):
                            delattr(self.tts, attr)
                    except Exception as e:
                        logger.debug(f"[IndexTTS2] 删除属性 {attr} 失败: {e}")
                del self.tts
                self.tts = None

            gc.collect()
        except Exception as e:
            logger.exception(f"[IndexTTS2] 卸载主流程异常，继续清理: {e}")

        try:
            from ..gpu_backend import GPUBackend, GPUBackendManager

            if self.backend != GPUBackend.CPU:
                GPUBackendManager.synchronize(device=self.device)
                GPUBackendManager.empty_cache()
                GPUBackendManager.ipc_collect(device=self.device)
        except Exception as e:
            logger.exception(f"[IndexTTS2] GPU 缓存清理失败: {e}")

        logger.info("[IndexTTS2] 模型卸载完成")

    @staticmethod
    def build_emotion_vector(
        happy: float = 0.0,
        angry: float = 0.0,
        sad: float = 0.0,
        afraid: float = 0.0,
        disgusted: float = 0.0,
        melancholic: float = 0.0,
        surprised: float = 0.0,
        calm: float = 0.0,
    ) -> list[float]:
        """构建合法范围的 8 维情感向量。

        **8 维情感向量 Clamp 算法说明**：
            对每个维度独立执行值域截断（clamping）操作：
            ``clamped = max(0.0, min(1.0, value))``

            算法原理：
                - 情感空间在训练时被归一化到 [0.0, 1.0] 超立方体内
                - 超出此范围的值会导致情感嵌入外推，可能引发：
                  * 生成音频出现爆音、电流声等 artifacts
                  * 情感表现极端化甚至音色崩溃
                  * CUDA 张量计算 NaN/Inf 导致推理中断
                - 采用 hard clamp 而非归一化的原因：
                  * 保留各维度间的相对比例（混合情感语义正确）
                  * 避免 L1/L2 归一化将高值向量压缩导致情感变弱
                  * 计算开销极小，适合实时 API 调用

            混合情感示例：
                - 兴奋：happy=0.6, surprised=0.4 → 开心中带着惊讶
                - 温柔：calm=0.6, happy=0.3 → 平静中带着淡淡喜悦
                - 悲愤：angry=0.5, sad=0.5 → 愤怒与悲伤交织（戏剧化效果）
                - 窃喜：happy=0.4, surprised=0.2, calm=0.3 → 克制的开心

        Args:
            happy: 开心维度强度 (0.0-1.0)。
                0=无开心情绪，1=极其开心/大笑。
            angry: 愤怒维度强度。
                0=无愤怒情绪，1=暴怒/怒吼。
            sad: 悲伤维度强度。
                0=无悲伤情绪，1=极度悲伤/哭泣。
            afraid: 害怕维度强度。
                0=无恐惧情绪，1=极度恐惧/颤抖。
            disgusted: 厌恶维度强度。
                0=无厌恶情绪，1=极度反感/鄙夷。
            melancholic: 忧郁维度强度。
                0=无忧郁情绪，1=深沉忧郁/惆怅（与 sad 区别：更内敛、更文艺）。
            surprised: 惊讶维度强度。
                0=无惊讶情绪，1=极度惊讶/震惊。
            calm: 平静维度强度。
                0=情绪激动，1=极度平静/镇定（可抵消其他情感）。

        Returns:
            list[float]: 长度固定为 8 的情感向量，顺序与
            :attr:`EMOTION_DIMENSIONS` 一致，每个维度值保证在 [0.0, 1.0] 区间内。
            向量顺序：``[happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]``。
        """
        return [
            max(0.0, min(1.0, happy)),
            max(0.0, min(1.0, angry)),
            max(0.0, min(1.0, sad)),
            max(0.0, min(1.0, afraid)),
            max(0.0, min(1.0, disgusted)),
            max(0.0, min(1.0, melancholic)),
            max(0.0, min(1.0, surprised)),
            max(0.0, min(1.0, calm)),
        ]

    @staticmethod
    def get_preset_emotions() -> dict[str, list[float]]:
        """获取预设情感模板字典。

        **预设情感模板设计说明**：
            提供 10 种常用情感预设，覆盖日常对话、有声书、配音等常见场景。
            每个预设都是经过实际测试的经验值，可直接使用或作为基础微调。
            所有预设均通过 :meth:`build_emotion_vector` 构建，确保值域合法。

        Returns:
            dict[str, list[float]]: 情感模板名 → 8 维情感向量映射。
            内置模板详细说明：
            - ``neutral`` (中性)：calm=0.5，平淡无明显情感，适用于新闻播报、说明书朗读
            - ``happy`` (开心)：happy=0.8，明显愉悦，适用于祝贺、欢快场景
            - ``angry`` (愤怒)：angry=0.8，愤怒语气，适用于角色对白、戏剧冲突
            - ``sad`` (悲伤)：sad=0.8，悲伤语气，适用于抒情、告别场景
            - ``surprised`` (惊讶)：surprised=0.8，惊讶语气，适用于意外、发现场景
            - ``calm`` (平静)：calm=0.8，沉稳镇定，适用于冥想、助眠、旁白
            - ``melancholic`` (忧郁)：melancholic=0.8，忧郁惆怅，适用于文艺作品、回忆
            - ``excited`` (兴奋)：happy=0.6+surprised=0.4，激动兴奋，适用于惊喜、欢呼
            - ``gentle`` (温柔)：calm=0.6+happy=0.3，温柔亲切，适用于亲子、情感对话
            - ``whisper`` (耳语)：calm=0.9，极度平静低沉，适用于耳语、私密对话
        """
        return {
            "neutral": IndexTTS2Engine.build_emotion_vector(calm=0.5),
            "happy": IndexTTS2Engine.build_emotion_vector(happy=0.8),
            "angry": IndexTTS2Engine.build_emotion_vector(angry=0.8),
            "sad": IndexTTS2Engine.build_emotion_vector(sad=0.8),
            "surprised": IndexTTS2Engine.build_emotion_vector(surprised=0.8),
            "calm": IndexTTS2Engine.build_emotion_vector(calm=0.8),
            "melancholic": IndexTTS2Engine.build_emotion_vector(melancholic=0.8),
            "excited": IndexTTS2Engine.build_emotion_vector(happy=0.6, surprised=0.4),
            "gentle": IndexTTS2Engine.build_emotion_vector(calm=0.6, happy=0.3),
            "whisper": IndexTTS2Engine.build_emotion_vector(calm=0.9),
        }

    @property
    def version(self) -> str:
        """返回引擎版本标识字符串。

        Returns:
            str: ``"IndexTTS 2.0"`` 或 ``"IndexTTS 2.5"``（依构造时的 version 变体；
                在类上非绑定读取时按类属性 ``version_str`` 默认判定）。
        """
        _v = getattr(self, "version_str", "2.5")
        return "IndexTTS 2.0" if _v == "2.0" else "IndexTTS 2.5"

    @property
    def min_vram_gb(self) -> float:
        """返回引擎最低显存需求（GB）。

        Returns:
            float: 固定为 ``6.0``（GB）。
        """
        return 6.0

    @property
    def min_ram_gb(self) -> float:
        """返回引擎最低系统内存需求（GB）。

        Returns:
            float: 固定为 ``16.0``（GB）。
        """
        return 16.0

    def generate_voice_design(
        self,
        text: str,
        instruction: str = "",
        normalize: bool = True,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """语音设计（IndexTTS2 不支持，显式抛出 NotImplementedError）。

        Args:
            text: 待合成文本（未使用）。
            instruction: 音色设计指令（未使用）。
            normalize: 是否归一化（未使用）。
            **kwargs: 额外参数（未使用）。

        Raises:
            NotImplementedError: 始终抛出，提示用户切换到 VoxCPM2 引擎。
        """
        raise NotImplementedError(
            "Voice design is not supported by IndexTTS2 engine. "
            "Please switch to VoxCPM2 engine for voice design features."
        )

    def generate_voice_clone(
        self,
        text: str,
        reference_audio_path: str | None = None,
        instruction: str = "",
        normalize: bool = True,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """零样本语音克隆（TTSEngine Protocol 适配实现）。

        实际底层调用 :meth:`synthesize` / :meth:`infer`。
        ``reference_audio_path`` 对应 ``spk_audio_prompt``；
        ``instruction`` 若以 ``emo_text:`` 开头则解析为情感文本描述。

        Args:
            text: 待合成文本。
            reference_audio_path: 说话人参考音频路径。
            instruction: 额外指令；前缀 ``emo_text:`` 的部分会作为
                情感描述文本传入，其余部分透传 kwargs。
            normalize: 是否响度归一化（IndexTTS2 内部处理，本方法仅作占位兼容）。
            **kwargs: 透传给 :meth:`infer` 的额外参数（如 emo_vector 等）。

        Returns:
            tuple[Any, str]: 二元组 ``(output_path, status_message)``。

        Raises:
            EngineNotLoadedError: 引擎未就绪。
            ValueError: ``reference_audio_path`` 为 ``None``。
        """
        if reference_audio_path is None:
            raise ValueError("IndexTTS2 generate_voice_clone 需要 reference_audio_path。")

        emo_text_kw: str | None = None
        use_emo_text_kw: bool = False
        if instruction and instruction.startswith("emo_text:"):
            emo_text_kw = instruction[len("emo_text:") :].strip()
            use_emo_text_kw = True

        _sr, _wav, output_path = self.infer(
            text=text,
            spk_audio_prompt=reference_audio_path,
            emo_text=emo_text_kw,
            use_emo_text=use_emo_text_kw,
            **kwargs,
        )
        return output_path, f"IndexTTS2 clone 完成: {output_path}"

    def generate_script(
        self,
        text: str,
        speaker_map: dict[str, Any] | None = None,
        persona_map: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """剧本工坊模式（IndexTTS2 不支持，显式抛出 NotImplementedError）。

        Args:
            text: 剧本格式文本（未使用）。
            speaker_map: 说话人→音色映射（未使用）。
            persona_map: Persona→配置映射（未使用）。
            **kwargs: 额外参数（未使用）。

        Raises:
            NotImplementedError: 始终抛出。
        """
        raise NotImplementedError(
            "Script generation is not supported by IndexTTS2 engine. "
            "Please switch to VoxCPM2 engine for script generation features."
        )

    def generate_streaming(
        self,
        text: str,
        reference_audio_path: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """流式生成模式（IndexTTS2 不支持，显式抛出 NotImplementedError）。

        Args:
            text: 待合成长文本（未使用）。
            reference_audio_path: 参考音频路径（未使用）。
            **kwargs: 额外参数（未使用）。

        Raises:
            NotImplementedError: 始终抛出。
        """
        raise NotImplementedError(
            "Streaming generation is not supported by IndexTTS2 engine. "
            "Please switch to VoxCPM2 engine for streaming features."
        )


class IndexTTS20Engine(IndexTTS2Engine):
    """IndexTTS 2.0 引擎 —— ``IndexTTS2Engine`` 的薄子类。

    仅把 ``version`` 默认锁定为 ``"2.0"``，从而复用全部加载/推理/情感控制
    逻辑，差异（推理模块 ``indextts.infer_v2``、权重目录 ``model/IndexTTS-2.0``、
    注册名 ``indextts20``、无显式时长、仅中英）全部由基类按 version 变体处理。
    设置独立的类引用名，便于 :func:`engine_registry.register` 以
    ``.engines.indextts2_engine:IndexTTS20Engine`` 懒导入注册第三个引擎。
    """

    version_str: str = "2.0"

    def __init__(self, *args: Any, version: str = "2.0", **kwargs: Any) -> None:
        """构造 IndexTTS 2.0 引擎实例。

        Args:
            *args: 透传给 :class:`IndexTTS2Engine` 的位置参数。
            version: 固定默认 ``"2.0"``（显式传入其他值将覆盖）。
            **kwargs: 透传给 :class:`IndexTTS2Engine` 的关键字参数。
        """
        super().__init__(*args, version=version, **kwargs)
