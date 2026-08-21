"""通用 UI/文件 工具函数模块。

提供三个能力域的辅助功能：
1. 临时文件清理（cleanup_temp_files）：匹配多种临时文件模式，
   在 routes 启动/关闭流程和生成上下文中调用，保障临时目录整洁。
2. 剧本角色颜色映射（get_role_color）：为剧本工坊 script.py 中的多角色对话
   分配一致的颜色主题，返回 (color_key, css_hex) 二元组。
3. 文本标签包装（add_tag）：为剧本文本添加 [说话人] 或 [uv_break] 等控制标签，
   同样由 script.py 剧本工坊调用。
"""

import contextlib
import glob
import logging
import os
import tempfile
import time
from collections.abc import Iterable

from .config import _ROLE_COLOR_MAP, SAVE_DIR

logger = logging.getLogger("tts_multimodel")

# 临时文件模式（SAVE_DIR 目录下）
_TEMP_GLOB_PATTERNS: tuple[str, ...] = (
    "temp_*.wav",
    "temp_*.mp3",
    "temp_*.ogg",
    "temp_*.flac",
    "indextts2_*.wav",
    "*.wav.tmp",
    "*_ref.wav",
    "*_denoised.wav",
    "*_full.wav",
    "*_seg.wav",
    ".tmp_*.json",
    ".tmp_config_*.yaml",
)

# 系统临时目录中需要清理的文件前缀（超过1小时未修改的）
_SYSTEM_TEMP_PREFIXES: tuple[str, ...] = (
    "tmp",  # mkstemp 默认前缀
    "indextts2_",
)

# 临时文件最大年龄（秒）：1 小时
_MAX_TEMP_AGE_SECONDS: int = 3600


def cleanup_temp_files(files: Iterable[str] | None = None) -> int:
    """清理临时音频/配置文件。

    支持两种调用模式：
    1. 传入 ``files`` 可迭代对象：仅清理指定路径列表（生成上下文 finally 中
       清理本次请求产生的临时文件，精确无副作用）；
    2. 不传参数：清理 SAVE_DIR 和系统临时目录中匹配预定义模式的过期临时文件
       （启动/关闭时调用，批量清理历史遗留临时文件）。

    单文件删除时使用 ``contextlib.suppress(OSError)`` 容忍并发场景下
    被其他线程先删的竞态。清理失败仅记录 debug 日志，不重抛异常——
    临时文件清理是"尽力而为"的操作，主流程不应因个别文件清理失败而中断。

    Args:
        files: 可选，要删除的具体文件路径 iterable。None 表示执行全局模式清理。

    Returns:
        int: 实际成功删除的文件数量。
    """
    removed_count = 0

    # 模式 1：精确删除指定文件列表
    if files is not None:
        for f in files:
            if not f:
                continue
            with contextlib.suppress(OSError):
                if os.path.exists(f):
                    os.remove(f)
                    removed_count += 1
        return removed_count

    # 模式 2：全局模式清理（SAVE_DIR）
    now = time.time()
    try:
        for pattern in _TEMP_GLOB_PATTERNS:
            for f in glob.glob(os.path.join(SAVE_DIR, pattern)):
                try:
                    # 检查文件年龄，避免误删正在写入的文件
                    mtime = os.path.getmtime(f)
                    if now - mtime > _MAX_TEMP_AGE_SECONDS:
                        with contextlib.suppress(OSError):
                            os.remove(f)
                            removed_count += 1
                except OSError:
                    pass
    except (OSError, PermissionError, glob.error) as e:
        logger.debug(f"清理 SAVE_DIR 临时文件失败（忽略）: {type(e).__name__}: {e}")

    # 模式 2 补充：清理系统临时目录中超过 1 小时的相关临时文件
    try:
        temp_dir = tempfile.gettempdir()
        for prefix in _SYSTEM_TEMP_PREFIXES:
            for f in glob.glob(os.path.join(temp_dir, f"{prefix}*")):
                try:
                    # 只清理 .wav/.tmp 等特定后缀，避免误删其他程序文件
                    if not (f.endswith(".wav") or f.endswith(".tmp") or ".tmp" in os.path.basename(f)):
                        continue
                    mtime = os.path.getmtime(f)
                    if now - mtime > _MAX_TEMP_AGE_SECONDS:
                        with contextlib.suppress(OSError):
                            os.remove(f)
                            removed_count += 1
                except OSError:
                    pass
    except (OSError, PermissionError, glob.error) as e:
        logger.debug(f"清理系统临时目录失败（忽略）: {type(e).__name__}: {e}")

    if removed_count > 0:
        logger.debug(f"[cleanup_temp_files] 已清理 {removed_count} 个临时文件")
    return removed_count


def get_role_color(role_name: str) -> tuple[str, str]:
    """获取角色对应的颜色标识。

    先对 role_name 执行 strip("[]）") 去除剧本方括号，再在
    _ROLE_COLOR_MAP 中查找。未命中时返回默认蓝色。

    Args:
        role_name: 角色名称字符串，可能包含剧本格式的方括号。

    Returns:
        tuple[str, str]: (color_key, css_hex) 二元组，
        例如 ("blue", "#3B82F6")。
    """
    try:
        clean_name = role_name.strip("[]）")
        return _ROLE_COLOR_MAP.get(clean_name, ("blue", "#3B82F6"))
    except AttributeError:
        return ("blue", "#3B82F6")


def add_tag(text: str, tag: str, is_speaker: bool = True) -> str:
    """在文本中添加角色标签。

    Args:
        text: 原始文本内容。
        tag: 标签名称；若为空或 "(暂无音色)" 则不包装，直接返回原文本。
        is_speaker: 是否为说话人标签。True 时标签前加换行前缀，避免
            [说话人] 与上一行尾字视觉粘连；False（如 [uv_break] 控制
            标签）时不加换行，确保紧贴文字生效。

    Returns:
        str: 包装标签后的文本。
    """
    # 用户对象可能实现了一个会抛 TypeError/ValueError/AttributeError 的
    # __str__；这里作为 UI 包装函数走"能转就转，不能转就放过"，
    # 后续 rstrip 分支再兜底做二次 str()（下方 AttributeError 分支同样处理）。
    # 禁止裸 except，避免把 MemoryError / KeyboardInterrupt / SystemExit 吃掉。
    with contextlib.suppress(TypeError, ValueError, AttributeError):
        text = str(text)

    if not tag or tag == "(暂无音色)":
        return text

    # is_speaker 语义差异：说话人标签是视觉换行分隔符，需要换行分隔；
    # 非 speaker 标签（如 [uv_break] 控制标签）需要紧贴文字生效，因此不加换行。
    prefix = "\n" if text.strip() and is_speaker else ""

    try:
        stripped_text = text.rstrip()
    except AttributeError:
        stripped_text = str(text).rstrip()

    result = f"{stripped_text}{prefix}[{tag}] "
    return result
