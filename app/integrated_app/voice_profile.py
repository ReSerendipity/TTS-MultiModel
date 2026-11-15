"""Voice Profile：把音色资产从「VoxCPM2 专属嵌入缓存」抽象为引擎无关的画像。

## 为什么需要这一层

改动前，``persona_manager.load_persona_embedding`` 的缓存键只有音色名，语义上
等价于“VoxCPM2 的嵌入”；音色库因此与单一引擎耦合（``engine`` 固定
``voxcpm2``）。同一份参考音频要在 IndexTTS / Breeze 等引擎复用时，只能各自
重新读盘、重新转写、重新编码，既重复又无法判断缓存是否还对应磁盘上的文件。

本模块把音色拆成两层：

1. **Voice Profile（资产层）** —— 参考音频、转写文本、标签、来源与授权状态，
   外加一个由磁盘文件状态推导出的 ``fingerprint``。这一层与引擎无关，可直接
   被任意引擎复用。
2. **每引擎派生缓存（派生层）** —— ``VoiceDerivedCache`` 以 ``(engine, name)``
   为键、以 ``fingerprint`` 判新鲜度；同一音色在不同引擎下的派生结果互不覆盖，
   源文件变化时旧条目自动失效。

派生结果**不可跨引擎互换**：VoxCPM2 的嵌入不能喂给 IndexTTS。因此缓存只保证
“同一引擎 + 同一份源文件”复用，跨引擎复用发生在资产层（参考音频 + 转写）。

## 磁盘 .pt 的定位

VoxCPM2 的 ``{name}.pt`` 仍由 ``persona_manager.load_persona_embedding`` 负责，
并保留原有的 ``_meta.origin`` 来源校验；本模块不读写该文件，也不删除它——
``.pt`` 的淘汰策略属于既有嵌入缓存，改动它会波及存量音色。派生层只做进程内
LRU，重启后回落到引擎自己的磁盘缓存或重新计算。
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

#: 派生缓存默认容量（条目数）。按标签页常见“每引擎十来个音色”估算；
#: 超出后按 LRU 淘汰，避免长期运行累积。
DEFAULT_DERIVED_CACHE_SIZE = 64


#: 引擎无关的参考音频载荷，供直接接收 wav + 参考文本的引擎使用。
@dataclass(frozen=True, slots=True)
class VoiceReference:
    """引擎无关的参考音频载荷。

    Attributes:
        wav_path: 参考音频绝对路径。
        ref_text: 参考音频对应的转写文本，可为空。
        tags: 音色标签（来自 metadata.json），供提示词/风格路由使用。
    """

    wav_path: str
    ref_text: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    """音色资产画像（与引擎无关）。

    Attributes:
        name: 音色名称（``PERSONA_DIR`` 下的裸文件名，不含扩展名）。
        reference_wav: 参考音频绝对路径。
        reference_text: 参考音频转写文本。
        tags: 标签列表。
        category: 分类（见 ``persona_metadata.VOICE_CATEGORIES``）。
        voice_type: 音色类型描述。
        language: 主要语言代码。
        source: 来源说明（元数据 ``source_audio``，缺省为 ``"local"``）。
        consent_state: 授权声明状态（granted / self / unverified）。
        created_at: 创建时间（ISO 8601）。
        fingerprint: 由源文件（wav/txt/metadata）状态推导的新鲜度指纹。
    """

    name: str
    reference_wav: str
    reference_text: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    category: str = ""
    voice_type: str = ""
    language: str = "zh"
    source: str = "local"
    consent_state: str = "unverified"
    created_at: str = ""
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可 JSON 序列化的字典（供 API/UI 展示）。"""
        return {
            "name": self.name,
            "reference_wav": self.reference_wav,
            "reference_text": self.reference_text,
            "tags": list(self.tags),
            "category": self.category,
            "voice_type": self.voice_type,
            "language": self.language,
            "source": self.source,
            "consent_state": self.consent_state,
            "created_at": self.created_at,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class VoiceProfileResolution:
    """某一引擎解析到的音色载荷。

    Attributes:
        profile: 资产层画像。
        engine: 解析目标引擎标识（如 ``voxcpm2`` / ``indextts2``）。
        payload: 该引擎可直接使用的载荷；无专用编码器时为 ``VoiceReference``。
        cache_source: 载荷来源，``memory`` 表示派生缓存命中；
            ``voxcpm2`` 表示走既有嵌入路径；``computed`` / ``reference``
            表示本次现算或直接打包参考音频。
    """

    profile: VoiceProfile
    engine: str
    payload: Any
    cache_source: str


def compute_voice_fingerprint(
    wav_path: str,
    txt_path: str | None = None,
    meta_path: str | None = None,
) -> str:
    """由源文件状态推导新鲜度指纹。

    使用 ``size`` + ``mtime_ns`` 而非内容哈希：音色音频常为数百 KB 到数 MB，
    每次解析都做全量哈希会拖慢生成入口；而“保存音色”走 ``os.replace`` 原子
    替换，mtime/大小必然变化，足以识别覆盖写入。

    Args:
        wav_path: 参考音频路径（必须存在，否则指纹为空串）。
        txt_path: 转写文本路径，可为 None 或不存在。
        meta_path: 元数据路径，可为 None 或不存在。

    Returns:
        str: 16 位十六进制指纹；参考音频不存在时返回空串。
    """
    parts: list[str] = []
    for path in (wav_path, txt_path, meta_path):
        if not path:
            continue
        try:
            stat = os.stat(path)
        except OSError:
            parts.append(f"{os.path.basename(path)}:-")
            continue
        parts.append(f"{os.path.basename(path)}:{stat.st_size}:{stat.st_mtime_ns}")
    if not parts:
        return ""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


class VoiceDerivedCache:
    """按 ``(engine, name)`` 隔离的派生载荷缓存，带指纹新鲜度判定。

    线程安全；容量满时淘汰最久未访问条目。与 ``LRUCache`` 的差别在于值里必须
    携带 ``fingerprint``：源文件一变，旧条目即使还在也不能命中。
    """

    def __init__(self, maxsize: int = DEFAULT_DERIVED_CACHE_SIZE) -> None:
        """初始化派生缓存。

        Args:
            maxsize: 最大条目数，必须为正数。
        """
        if maxsize <= 0:
            raise ValueError(f"maxsize 必须为正数，得到: {maxsize}")
        self._maxsize = maxsize
        self._entries: OrderedDict[tuple[str, str], tuple[str, Any]] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def maxsize(self) -> int:
        """最大条目数。"""
        return self._maxsize

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def lookup(
        self,
        name: str,
        engine: str,
        fingerprint: str,
    ) -> tuple[bool, Any | None, bool]:
        """查询派生载荷并判定新鲜度。

        Args:
            name: 音色名称。
            engine: 引擎标识。
            fingerprint: 当前源文件指纹。

        Returns:
            ``(hit, payload, stale)``：``hit`` 为 True 时 ``payload`` 可直接使用；
            ``stale`` 为 True 表示曾有条目但指纹已过期（条目已被清除）。
        """
        key = (engine, name)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False, None, False
            cached_fingerprint, payload = entry
            if cached_fingerprint != fingerprint:
                # Why 直接删除而不是留在原地：过期载荷继续占容量没有意义，
                # 且留着会让后续 __len__ / 统计误判为可用缓存。
                del self._entries[key]
                return False, None, True
            self._entries.move_to_end(key)
            return True, payload, False

    def put(self, name: str, engine: str, fingerprint: str, payload: Any) -> None:
        """写入派生载荷（同名同引擎覆盖旧条目）。"""
        key = (engine, name)
        with self._lock:
            self._entries[key] = (fingerprint, payload)
            self._entries.move_to_end(key)
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)

    def invalidate(self, name: str | None = None, engine: str | None = None) -> int:
        """使条目失效。

        Args:
            name: 目标音色名；None 表示不按名过滤。
            engine: 目标引擎；None 表示不按引擎过滤（即该音色的全部引擎）。

        Returns:
            int: 实际清除的条目数。
        """
        with self._lock:
            keys = [
                key
                for key in self._entries
                if (name is None or key[1] == name) and (engine is None or key[0] == engine)
            ]
            for key in keys:
                del self._entries[key]
            return len(keys)

    def clear(self) -> None:
        """清空全部条目（测试与模型卸载场景使用）。"""
        with self._lock:
            self._entries.clear()
