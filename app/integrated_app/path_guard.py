"""把外部传入的名字解析为受控目录内的裸文件路径。

Security [D6] / py/path-injection：
    仓库里此前有两种"看似安全"的写法，各自都有洞：

    1. ``os.path.basename(user_input)`` —— 挡得住 ``../`` 遍历，挡不住同目录内的
       命名混淆（覆盖 ``metadata.json``、写出隐藏文件），而且一旦忘记调用就没有任何防线；
    2. ``realpath(p).startswith(realpath(base))`` 且少了 ``+ os.sep`` ——
       ``base`` 为 ``personas`` 时，兄弟目录 ``personas_evil/x.wav`` 同样以
       ``personas`` 开头，判定直接通过。这类前缀比对是假包含。

    本模块把"裸文件名 + 真实路径严格前缀"两件事一次做对，供音色/输出目录共用，
    避免每处再写一遍 basename。
"""

from __future__ import annotations

import os
import re

#: 单个文件名的长度上限（各主流文件系统的单段上限均为 255）
MAX_FILENAME_LENGTH = 255

#: 平台分隔符集合：POSIX 下 ``os.sep`` 是 ``/``、Windows 下是 ``\\``，
#: 但两种平台都可能收到另一种分隔符，所以四个都要显式排除。
_SEPARATORS: frozenset[str] = frozenset(s for s in (os.sep, os.altsep, "/", "\\") if s)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def is_bare_filename(value: str) -> bool:
    """判断 ``value`` 是否是单段裸文件名（不含目录成分、非隐藏、非 ``.``/``..``）。

    Args:
        value: 待判定的名字（来自请求体、表单或配置文件）。

    Returns:
        是裸文件名返回 True。
    """
    if not value or len(value) > MAX_FILENAME_LENGTH:
        return False
    if _CONTROL_CHARS.search(value):
        return False
    if any(sep in value for sep in _SEPARATORS):
        return False
    # 以点开头的既包括 "." 与 ".."，也包括 `.metadata.json` 这类隐藏文件。
    return not value.startswith(".")


def ensure_within_dir(base_dir: str, candidate: str) -> str | None:
    """校验 ``candidate`` 解析后确实落在 ``base_dir`` 之内。

    接受任意（相对或绝对）路径，返回规范化真实路径或 ``None``。
    判定用 ``base + os.sep`` 前缀，因此兄弟目录同前缀的情况会被拒绝。

    Args:
        base_dir:  允许的根目录。
        candidate: 待校验路径（可以是绝对路径）。

    Returns:
        命中时返回真实绝对路径，越界或解析失败返回 ``None``。
    """
    if not candidate:
        return None
    try:
        root = os.path.realpath(base_dir)
        real = os.path.realpath(candidate)
    except OSError:
        return None
    if real == root or not real.startswith(root + os.sep):
        return None
    return real


def resolve_bare_in_dir(
    base_dir: str,
    name: str,
    *,
    allowed_extensions: frozenset[str] | set[str] | None = None,
) -> str | None:
    """把裸文件名解析为 ``base_dir`` 内的真实路径；非法或越界返回 ``None``。

    Args:
        base_dir:           目标目录。
        name:               外部传入的文件名（含扩展名）。
        allowed_extensions: 扩展名白名单（小写含点），``None`` 表示不校验扩展名。

    Returns:
        合法时返回真实绝对路径，否则 ``None``。
    """
    if not is_bare_filename(name):
        return None
    if allowed_extensions is not None and os.path.splitext(name)[1].lower() not in allowed_extensions:
        return None
    return ensure_within_dir(base_dir, os.path.join(base_dir, name))
