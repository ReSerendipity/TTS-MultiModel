"""音色（Persona）管理核心模块。

本模块负责自定义音色的完整生命周期管理，主要功能域包括：

**1. CRUD 操作**
    - 保存（fn_save_persona）：支持音频上传 / 文件路径 / bytes 三种输入形态，自动固化为
      .wav + .txt + metadata.json 三件套，写入前执行路径防御校验。
    - 删除（delete_persona / fn_delete_persona）：独立删除 .wav / .txt / .pt / metadata 四个
      文件，单个失败不影响其余文件清理，汇总失败信息返回。
    - 查询（get_persona_list / get_persona_detail_table / get_total_persona_count）：
      支持关键字搜索，详情表返回二维数组供 HTMX 前端直接渲染。
    - 导入 / 导出：委托 persona_metadata.PersonaExporter 执行 zip 打包与解包。

**2. 嵌入缓存机制**
    - 三层命中顺序：_persona_embedding_cache（内存 LRU）→ .pt 文件（磁盘预计算）
      → 在线重新计算 + 写回 .pt + 回填缓存，降低重复推理开销。
    - torch.load 显式指定 map_location="cpu"，避免嵌入占用 GPU 显存。

**3. 路径安全防御（三段式）**
    - 第一段：_validate_persona_name + _PERSONA_NAME_RE 正则限制文件名字符集
      （中/英/数/下划线/连字符，1~50 字符），从源头阻断路径遍历字符（/ \\ ..）。
    - 第二段：os.path.realpath 解析最终绝对路径，通过 startswith 前缀比对确保
      路径仍落在 PERSONA_DIR 白名单目录内，拦截符号链接跳转与 Unicode 同形字绕过。
    - 第三段：所有文件操作均以 PERSONA_DIR 为根目录进行 os.path.join 拼接，
      禁止直接拼接用户原始输入。

**4. 后台固化验证（_verify_persona_sync）**
    - 音色写入磁盘后，启动 daemon 线程调用 VoxCPM2 官方 generate 接口进行一次
      最小参数推理（cfg=2.0, steps=10, 文本取参考文本前 100 字）。
    - 目的：提前发现音频格式损坏、采样率不兼容、编码器不支持等问题，避免用户
      在数小时/数天后实际使用时才报错，将故障检测左移到固化瞬间。
    - 验证失败仅记录日志，不阻塞固化成功返回（防止偶发 OOM 影响正常保存流程）。
"""

import contextlib
import logging
import os
import threading
from datetime import datetime
from typing import Any

from .config import (
    _PERSONA_NAME_RE,
    PERSONA_DIR,
)
from .exceptions import EngineNotLoadedError
from .generation import preprocess_and_save_temp
from .model_manager import _model_lock, _persona_embedding_cache
from .model_registry import registry
from .persona_metadata import (
    PersonaMetadata,
    save_persona_metadata,
)

logger: logging.Logger = logging.getLogger("tts_multimodel")

# P2 安全修复：.pt 嵌入文件来源追溯元数据
# 保存 .pt 文件时自动写入 origin 字段，加载时校验来源，防止嵌入被批量导出后无法追溯。
PERSONA_PT_ORIGIN = "TTS_MultiModel v2.1.0"
PERSONA_PT_FORMAT_VERSION = 1


def _validate_persona_name(name: str) -> tuple[bool, str]:
    """验证音色名称合法性，防止路径遍历与同形字攻击。

    采用正则 ``_PERSONA_NAME_RE`` 进行白名单校验，规则如下：
    - 长度范围 1~50 字符；
    - 允许字符集：英文大小写 a-zA-Z、数字 0-9、下划线 _、连字符 -、
      中文 Unicode 区间 U+4E00~U+9FFF。

    **仅允许上述字符的原因**：
    1. **文件系统兼容**：排除 Windows / macOS / Linux 三平台文件系统保留字符
       （``/ \\ : * ? " < > |`` 等），避免跨平台保存失败。
    2. **防止路径遍历**：直接在名字层面禁止 ``..``、``/``、``\\``、``~`` 等
       路径构造字符，从源头切断 Path Traversal 攻击面。
    3. **防 Unicode 同形字（Homoglyph）攻击**：收窄到 CJK 统一表意文字基本区，
       避免用户使用 ``а``（西里尔）伪装 ``a``（拉丁）创建两个视觉相同但
       实际不同的音色文件，导致混淆或覆盖风险。

    Args:
        name: 用户提交的音色名称原始字符串。

    Returns:
        Tuple[bool, str]: 第一项为是否合法（True/False），第二项为不合法时的
        人类可读错误提示，合法时返回空串。
    """
    if not name:
        return False, "名称不能为空"
    if not _PERSONA_NAME_RE.match(name):
        return False, "名称格式不合法（仅支持字母、数字、下划线、连字符、中文，1-50字符）"
    return True, ""


def _verify_persona_sync(name: str, wav_path: str, ref_text: str) -> None:
    """后台线程：固化后调用 VoxCPM2 generate 验证音色可读性。

    在持有 ``_model_lock`` 前后各做一次 ``is_voxcpm_ready`` 双重检查，
    确保验证期间模型未被并发卸载。验证失败仅记录日志，不抛出异常、不影响
    已完成的固化结果。

    Args:
        name: 音色名称，用于日志定位。
        wav_path: 已固化 .wav 文件的绝对路径。
        ref_text: 关联参考文本，供 generate 取短句执行最小推理。
    """
    if not registry.is_voxcpm_ready():
        logger.warning(f"[音色固化] 跳过音色 [{name}] 验证：VoxCPM2 模型未就绪")
        return

    try:
        with _model_lock:
            # Why 双重检查：典型 TOCTOU（Time-Of-Check to Time-Of-Use）竞态窗口——
            # 上面 lock 外的 is_voxcpm_ready 通过之后、acquire 成功之前，
            # 另一个线程可能执行 unload_model 把 voxcpm_model 置空；
            # lock 内再做一次检查是最低成本的竞态规避手段，无需引入复杂条件变量。
            if not registry.is_voxcpm_ready():
                logger.warning(f"[音色固化] 跳过音色 [{name}] 验证：模型在获取锁后被卸载")
                return
            logger.info(f"[音色固化] 后台验证音色 [{name}] 通过官方 generate(reference_wav_path=...)")
            _ = registry.voxcpm_model.generate(
                text=ref_text.strip(),
                reference_wav_path=wav_path,
                normalize=True,
                cfg_value=2.0,
                inference_timesteps=10,
                denoise=True,
                min_len=2,
                max_len=100,
            )
            logger.info(f"[音色固化] 音色 [{name}] 后台验证成功")
    except Exception as e:
        logger.error(f"[音色固化] 音色 [{name}] 后台验证失败: {e}")


def fn_save_persona(name: str, audio_input: Any, ref_text: str, overwrite: bool = False) -> tuple[str, bool]:
    """保存音色到音色库（固化）- 使用官方 VoxCPM2 API。

    Args:
        name: 目标音色名称，将作为 .wav/.txt/.pt 文件名前缀（不含扩展名）。
        audio_input: 音频输入，与 ``generation.preprocess_and_save_temp`` 接受形态一致，
            共三种：
            1. ``str`` —— 本地音频文件绝对/相对路径（.wav / .mp3 / .flac 等）；
            2. ``bytes`` —— 音频文件原始二进制内容；
            3. ``UploadedFile / tuple`` —— Gradio/HTTP 文件上传对象或
               ``(filename, bytes_content)`` 二元组。
        ref_text: 音色参考文本描述，保存到同名 .txt 供后续生成时复用。
        overwrite: 是否覆盖已存在的同名音色。当同名音色已存在且 overwrite=False
            时将返回 needs_confirm=True 提示前端弹二次确认。

    Returns:
        Tuple[str, bool]: 二元组 ``(message, needs_confirm)``。
        - message: 面向用户的中文提示文本，含成功 / 失败 / 确认前缀符号
          （✅ / ❌ / ⚠️）。
        - needs_confirm: True 时表示当前请求触发"是否覆盖"二次确认流程，
          前端应弹确认框并以 overwrite=True 再次调用；False 表示流程结束。
    """
    if not name or audio_input is None:
        return "❌ 失败：需输入名称及音频", False

    valid, err_msg = _validate_persona_name(name)
    if not valid:
        return f"❌ {err_msg}", False

    tmp_p: str | None = None
    try:
        wav_path = os.path.join(PERSONA_DIR, f"{name}.wav")
        txt_path = os.path.join(PERSONA_DIR, f"{name}.txt")
        wav_real = os.path.realpath(wav_path)
        if not wav_real.startswith(os.path.realpath(PERSONA_DIR)):
            return "❌ 非法路径", False

        existing = os.path.exists(wav_path) or os.path.exists(txt_path)
        if existing and not overwrite:
            return f"⚠️ 音色 [{name}] 已存在，再次点击保存将覆盖原有音色", True

        try:
            tmp_p, sr_p, wav_p = preprocess_and_save_temp(audio_input, f"{name}.wav")
        except FileNotFoundError:
            if not os.path.isdir(PERSONA_DIR):
                os.makedirs(PERSONA_DIR, exist_ok=True)
            tmp_p, sr_p, wav_p = preprocess_and_save_temp(audio_input, f"{name}.wav")

        # Why os.replace 而非 shutil.move / os.rename：
        # 1. Windows 跨卷场景：当临时目录（%TEMP%）与 PERSONA_DIR 不在同一卷时，
        #    os.rename 会抛出 OSError(errno=EXDEV: cross-device link)，而
        #    os.replace 在 Python 3.3+ 内部统一了跨卷行为（等价 copy+unlink）；
        # 2. 原子写入语义：若目标文件已存在，os.replace 会在单个系统调用内完成
        #    新旧替换，避免写入中途崩溃导致 wav 文件半写损坏（半截文件无法被
        #    VoxCPM2 解码，但文件存在会欺骗后续检查逻辑）。
        try:
            os.replace(tmp_p, wav_path)
            tmp_p = None
        except Exception:
            if tmp_p and os.path.exists(tmp_p):
                with contextlib.suppress(Exception):
                    os.unlink(tmp_p)
            raise

        meta = PersonaMetadata(
            name=name,
            description=ref_text if ref_text else "",
            voice_type="",
            traits="",
            created_at=datetime.now().isoformat(),
        )
        save_persona_metadata(PERSONA_DIR, name, meta)

        if ref_text:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(ref_text)

        if ref_text and ref_text.strip():
            threading.Thread(
                target=_verify_persona_sync,
                args=(name, wav_path, ref_text),
                daemon=True,
            ).start()

        if name in _persona_embedding_cache:
            del _persona_embedding_cache[name]

        return f"✅ 音色 [{name}] 已成功固化！", False

    except PermissionError:
        if tmp_p and os.path.exists(tmp_p):
            with contextlib.suppress(Exception):
                os.unlink(tmp_p)
        logger.exception("[音色固化] 写入失败：无 PERSONA_DIR 写权限")
        return (
            "❌ 写入失败：无 PERSONA_DIR 写权限，请检查杀毒软件或文件夹权限",
            False,
        )
    except Exception as e:
        if tmp_p and os.path.exists(tmp_p):
            with contextlib.suppress(Exception):
                os.unlink(tmp_p)
        logger.exception(f"[音色固化] 音色 [{name}] 固化失败")
        return f"❌ 固化失败: {str(e)}", False


def get_persona_list(search_keyword: str = "") -> list[str]:
    """获取自定义音色名称列表，支持关键字不区分大小写搜索过滤。

    Args:
        search_keyword: 过滤关键字，为空则返回全部。匹配逻辑为
            名称小写 contains 关键字小写。

    Returns:
        List[str]: 按字典序升序排列的音色名称列表。若当前无任何音色则返回
        ``["(暂无音色)"]`` 单元素占位列表，**而不是空列表**。
    """
    try:
        wav_files = [f[:-4] for f in os.listdir(PERSONA_DIR) if f.endswith(".wav")]
    except FileNotFoundError:
        os.makedirs(PERSONA_DIR, exist_ok=True)
        wav_files = []
    except PermissionError:
        logger.error(f"[音色列表] PERSONA_DIR 目录不可读: {PERSONA_DIR}")
        return ["(音色目录不可读)"]

    custom: list[str] = sorted(wav_files) if wav_files else []

    if search_keyword:
        kw = search_keyword.lower()
        custom = [c for c in custom if kw in c.lower()]

    # Why 返回 ['(暂无音色)'] 而非空列表 []：
    # 1. UI 层表格渲染逻辑直接访问 custom[0] 取首项展示，空列表会触发 IndexError，
    #    用占位字符串免除前端额外判空分支；
    # 2. "(暂无音色)" 同时作为前端"是否展示空状态引导页（添加音色按钮）"的
    #    判断条件——一值两用，减少前后端契约字段数量。
    return custom if custom else ["(暂无音色)"]


def get_total_persona_count() -> int:
    """获取当前自定义音色总数量。

    Returns:
        int: PERSONA_DIR 下后缀为 ``.wav`` 的文件总数。若目录不存在或
        不可读则返回 0。
    """
    try:
        files = [f for f in os.listdir(PERSONA_DIR) if f.endswith(".wav")]
    except FileNotFoundError:
        os.makedirs(PERSONA_DIR, exist_ok=True)
        return 0
    except PermissionError:
        logger.error(f"[音色计数] PERSONA_DIR 目录不可读: {PERSONA_DIR}")
        return 0
    return len(files)


def get_persona_detail_table(search_keyword: str = "") -> list[list[str]]:
    """获取自定义音色详情二维表格数据，供 HTMX <table> 直接渲染。

    Args:
        search_keyword: 名称关键字过滤，大小写不敏感，为空返回全部。

    Returns:
        List[List[str]]: 行数组，每行 5 列，列含义依次为：

        =====  ==========  ==========================
        索引    列名        说明
        =====  ==========  ==========================
        0      name        音色名称（.wav 文件名去扩展名）
        1      status      固化状态（固定 "✅ 已固化"）
        2      file_size   .wav 文件大小，"xxx.x KB" 格式化，失败 "-"
        3      created_at  文件修改时间，"%Y-%m-%d %H:%M" 格式化，失败 "-"
        4      description 同名 .txt 前 50 字预览，超 50 字追加 "..."，无则 "-"
        =====  ==========  ==========================

        若当前无音色则返回单行：``[["暂无音色", "-", "-", "-", "-"]]``。
    """
    table: list[list[str]] = []

    try:
        files = [f.replace(".wav", "") for f in os.listdir(PERSONA_DIR) if f.endswith(".wav")]
    except FileNotFoundError:
        os.makedirs(PERSONA_DIR, exist_ok=True)
        files = []
    except PermissionError:
        logger.error(f"[音色表格] PERSONA_DIR 目录不可读: {PERSONA_DIR}")
        return [["音色目录不可读", "-", "-", "-", "-"]]

    files = sorted(files)

    if search_keyword:
        kw = search_keyword.lower()
        files = [f for f in files if kw in f.lower()]

    for name in files:
        wav_path = os.path.join(PERSONA_DIR, f"{name}.wav")
        txt_path = os.path.join(PERSONA_DIR, f"{name}.txt")

        ref_text = ""
        if os.path.exists(txt_path):
            try:
                with open(txt_path, encoding="utf-8") as f:
                    ref_text = f.read()
                if len(ref_text) > 50:
                    ref_text = ref_text[:50] + "..."
            except OSError:
                ref_text = ""

        stat = None
        if os.path.exists(wav_path):
            with contextlib.suppress(OSError):
                stat = os.stat(wav_path)

        wav_size = f"{stat.st_size / 1024:.1f} KB" if stat else "-"
        wav_time = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M") if stat else "-"

        table.append([name, "✅ 已固化", wav_size, wav_time, ref_text if ref_text else "-"])

    if not table:
        table = [["暂无音色", "-", "-", "-", "-"]]
    return table


def get_persona_desc(name: str) -> str:
    """获取音色 Markdown 富文本描述，供前端 tooltip / 选择面板展示。

    Args:
        name: 目标音色名称。

    Returns:
        str: 若对应 .wav 存在则返回带粗体名称的 Markdown 描述串；否则返回空串。
    """
    wav_path = os.path.join(PERSONA_DIR, f"{name}.wav")
    if os.path.exists(wav_path):
        return f"**{name}**（自定义音色）\n\n自定义音色，适用于个性化语音合成。"
    return ""


def load_persona_embedding(name: str) -> Any | None:
    """加载指定音色的嵌入表示或 (wav 路径, 参考文本) 元组。

    **缓存命中顺序（三层）**：
    1. **内存 LRU 缓存** —— 先查 ``_persona_embedding_cache``，命中直接返回，
       零 I/O 开销；
    2. **磁盘 .pt 预计算** —— 读取 ``{PERSONA_DIR}/{name}.pt``，使用
       ``torch.load(..., map_location="cpu")`` 加载到 CPU，避免占用 GPU 显存；
       若文件损坏（``UnpicklingError``）则删除坏文件并降级到第 3 层；
    3. **在线计算 + 持久化** —— 调用 VoxCPM2 模型推理计算嵌入。若此时模型未加载，
       抛出 ``EngineNotLoadedError(engine="voxcpm2")``，不静默返回 None。计算完成
       后：(a) 写回 .pt 供下次命中，(b) 回填内存 LRU 缓存。

    当前实现实际返回 ``(wav_path: str, ref_text: str)`` 二元组供上层
    ``engines/voxcpm2/engine.py`` 在 generate 时内部计算嵌入，.pt 与在线计算
    路径为兼容扩展点。

    Args:
        name: 目标音色名称（去扩展名）。

    Returns:
        Optional[Any]: 音色嵌入对象 / (spk, cond) 元组 / (wav_path, ref_text) 元组。
        若 .wav 文件不存在则返回 None。

    Raises:
        EngineNotLoadedError: 走到在线计算分支但 VoxCPM2 模型尚未加载时抛出，
            ``engine`` 属性固定为 ``"voxcpm2"``。
    """
    cached = _persona_embedding_cache.get(name)
    if cached is not None:
        return cached

    wav_path = os.path.join(PERSONA_DIR, f"{name}.wav")
    txt_path = os.path.join(PERSONA_DIR, f"{name}.txt")
    pt_path = os.path.join(PERSONA_DIR, f"{name}.pt")

    wav_exists = os.path.exists(wav_path)
    txt_exists = os.path.exists(txt_path)
    pt_exists = os.path.exists(pt_path)

    ref_text = ""
    if txt_exists:
        with contextlib.suppress(OSError), open(txt_path, encoding="utf-8") as f:
            ref_text = f.read()

    if not wav_exists:
        return None

    if pt_exists:
        try:
            import torch

            raw = torch.load(pt_path, map_location="cpu", weights_only=True)
            # P2 安全修复：校验 .pt 文件来源元数据
            # 新格式: {"data": payload, "_meta": {"origin": "TTS_MultiModel v2.x", ...}}
            # 旧格式: 直接存储 (wav_path, ref_text) 元组，无 _meta 键
            if isinstance(raw, dict) and "_meta" in raw:
                meta = raw.get("_meta", {})
                origin = meta.get("origin", "")
                if origin and origin != PERSONA_PT_ORIGIN:
                    logger.warning(
                        f"[嵌入加载] 音色 [{name}] .pt 文件 origin 不匹配: "
                        f"期望 '{PERSONA_PT_ORIGIN}'，实际 '{origin}'，可能为外部导入文件"
                    )
                embedding = raw.get("data")
            else:
                # 向后兼容：旧格式 .pt 文件直接存储原始数据
                logger.debug(f"[嵌入加载] 音色 [{name}] .pt 文件为旧格式（无 origin 元数据）")
                embedding = raw
            _persona_embedding_cache.put(name, embedding)
            return embedding
        except Exception as e:
            if isinstance(e, (EOFError, ValueError, pickle_error_cls())):
                logger.warning(f"[嵌入加载] 音色 [{name}] 的 .pt 文件损坏，已删除并降级在线计算: {e}")
                with contextlib.suppress(OSError):
                    os.unlink(pt_path)
            else:
                logger.warning(f"[嵌入加载] 读取 {name}.pt 失败，降级在线计算: {e}")

    if not registry.is_voxcpm_ready():
        raise EngineNotLoadedError(
            f"计算音色 [{name}] 嵌入需要 VoxCPM2 模型，请先加载模型",
            engine="voxcpm2",
        )

    with _model_lock:
        if not registry.is_voxcpm_ready():
            raise EngineNotLoadedError(
                f"计算音色 [{name}] 嵌入需要 VoxCPM2 模型，请先加载模型",
                engine="voxcpm2",
            )
        result = (wav_path, ref_text)

    # P2 安全修复：保存 .pt 文件时写入 origin 元数据，用于来源追溯
    # 格式: {"data": original_payload, "_meta": {"origin": ..., "version": ..., "created_at": ...}}
    # 向后兼容：加载时检测旧格式（非 dict 或无 _meta 键）自动降级
    with contextlib.suppress(Exception):
        import torch

        torch.save(
            {
                "data": result,
                "_meta": {
                    "origin": PERSONA_PT_ORIGIN,
                    "format_version": PERSONA_PT_FORMAT_VERSION,
                    "created_at": datetime.now().isoformat(),
                },
            },
            pt_path,
        )

    _persona_embedding_cache.put(name, result)
    return result


def pickle_error_cls() -> type:
    """延迟获取 pickle.UnpicklingError，避免顶层 import 开销。"""
    import pickle  # nosec B403 - 仅用于获取 UnpicklingError 异常类做 isinstance 判断，不反序列化不可信数据

    return pickle.UnpicklingError


def get_persona_map(selected_names: list[str] | None = None) -> dict[str, Any]:
    """获取音色名称到音频路径 / 嵌入的映射字典，供剧本工坊（script.py）批量使用。

    Args:
        selected_names: 可选的音色名称白名单。若为 None 则返回 PERSONA_DIR 中
            全部 .wav 音色；若为列表则仅保留名称在该列表内的条目（去重 + 保序）。

    Returns:
        Dict[str, Any]: 键为音色名称（去扩展名），值为字典结构：
        ``{"wav": 绝对路径 str, "embedding": 嵌入或 None}``。
        当前实现仅填充 ``wav`` 字段，``embedding`` 字段由上层按需调用
        ``load_persona_embedding`` 填充。目录不存在时返回空 ``{}``。
    """
    persona_map: dict[str, Any] = {}
    if not os.path.exists(PERSONA_DIR):
        return persona_map

    try:
        all_files = os.listdir(PERSONA_DIR)
    except PermissionError:
        logger.error(f"[音色映射] PERSONA_DIR 目录不可读: {PERSONA_DIR}")
        return persona_map

    for f in all_files:
        if f.endswith(".wav"):
            name = f[:-4]
            wav_path = os.path.join(PERSONA_DIR, f)
            persona_map[name] = {"wav": wav_path}

    if selected_names is not None:
        filtered: dict[str, Any] = {}
        for n in selected_names:
            if n in persona_map:
                filtered[n] = persona_map[n]
        return filtered
    return persona_map


def delete_persona(name: str) -> tuple[bool, str]:
    """删除指定音色及其关联文件（.wav / .txt / .pt / {name}.metadata.json）。

    四个关联文件使用**独立 try/except (OSError)** 分别删除：单个失败仅记录到
    错误列表，不中断其余文件删除；最终汇总所有失败文件名返回给用户。

    Args:
        name: 目标音色名称。

    Returns:
        Tuple[bool, str]: ``(success, message)`` 二元组。
        - success: True 表示至少删除了一个关联文件且无任何删除失败；
          False 表示名称非法 / 路径非法 / 音色不存在 / 有文件删除失败。
        - message: 人类可读中文提示。
    """
    if not name:
        return False, "名称不能为空"

    valid, err_msg = _validate_persona_name(name)
    if not valid:
        return False, err_msg

    wav_path = os.path.join(PERSONA_DIR, f"{name}.wav")
    txt_path = os.path.join(PERSONA_DIR, f"{name}.txt")
    pt_path = os.path.join(PERSONA_DIR, f"{name}.pt")
    meta_path = os.path.join(PERSONA_DIR, f"{name}.metadata.json")

    real_wav = os.path.realpath(wav_path)
    if not real_wav.startswith(os.path.realpath(PERSONA_DIR)):
        return False, "非法路径"

    deleted_any = False
    errors: list[str] = []

    for path in (wav_path, txt_path, pt_path, meta_path):
        if os.path.exists(path):
            try:
                os.remove(path)
                deleted_any = True
            except OSError as e:
                errors.append(f"删除 {os.path.basename(path)} 失败: {e}")

    if name in _persona_embedding_cache:
        with contextlib.suppress(Exception):
            del _persona_embedding_cache[name]

    if errors:
        return False, "; ".join(errors)
    if not deleted_any:
        return False, f"音色 [{name}] 不存在"
    return True, f"音色 [{name}] 已删除"


def fn_delete_persona(name: str) -> tuple[str, bool]:
    """删除音色（fn_ 前缀版本，返回顺序与 fn_save_persona 对齐）。

    内部调用 :func:`delete_persona`，仅将返回值从 ``(bool, str)`` 转换为
    ``(str, bool)`` 顺序，保持与 ``fn_save_persona`` 一致的 API 风格。

    Args:
        name: 目标音色名称。

    Returns:
        Tuple[str, bool]: ``(message, success_flag)`` 二元组，首项为中文提示，
        第二项 True 表示全部删除成功。
    """
    success, message = delete_persona(name)
    return message, success
