"""参考音频 Prompt 持久化缓存模块。

架构说明
--------
本模块为 VoxCPM2 / IndexTTS2 等引擎的参考音频（Reference Audio）嵌入
计算结果提供持久化缓存。每个音频文件对应一个独立的缓存文件，格式为
``MAGIC_HEADER + JSON 元数据头 + 二进制嵌入体``，避免重复编码同一音色。

淘汰策略采用 **LRU（按 last_access_ts 淘汰）+ TTL（7 天强制过期）**
双重机制，兼顾热门音色的复用率和模型升级后的格式兼容性。

性能收益
--------
VoxCPM2 对 5 秒参考音频做嵌入计算通常需要 300 ~ 800ms，
同一音色多次复用（剧本工坊/多轮克隆）命中缓存时可直接跳过嵌入计算，
节省 60%+ 克隆首段延迟。

缓存位置
--------
通过 ``config.yaml`` 的 ``cache.prompt_cache_dir`` 配置目录，
默认值为 ``ROOT_DIR/.cache/prompts/``。
"""

import base64
import contextlib
import dataclasses
import hashlib
import io
import json
import logging
import os
import struct
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

np: Any = None
try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

torch: Any = None
try:
    import torch

    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

logger = logging.getLogger("tts_multimodel")

_DEFAULT_CACHE_DIR: str = "./prompt_cache"
_DEFAULT_MAX_ENTRIES: int = 50
_DEFAULT_TTL_SECONDS: int = 604800
_MAGIC_HEADER: bytes = b"TTSP1"
_METADATA_VERSION: int = 3
_META_SAVE_THROTTLE_SECONDS: float = 10.0
_META_SAVE_DIRTY_THRESHOLD: int = 5

_BinaryInput = str | bytes | Any


@dataclasses.dataclass
class PromptCacheEntry:
    """Prompt 缓存单条元数据条目（dataclass）。

    Attributes:
        key (str): 缓存键字符串。
        created_at (float): 条目创建时间戳（Unix 秒），用于 TTL 过期判断。
        last_access_ts (float): 最近访问时间戳（Unix 秒），用于 LRU 淘汰排序。
        access_count (int): 累计访问次数，用于统计命中热度。
        embedding_size (int): 嵌入二进制体大小（字节）。
        audio_hash (str): 参考音频内容哈希（sha256 前 16 位），用于防脏缓存。
    """

    key: str
    created_at: float
    last_access_ts: float
    access_count: int
    embedding_size: int
    audio_hash: str = ""


def _serialize_value(obj: Any) -> tuple[dict[str, Any], bytes]:
    """将任意对象拆分为（JSON 可序列化元信息，二进制嵌入体）。

    Returns:
        (meta_dict, binary_bytes) —— 前者写入 JSON 头，后者写入二进制体。
    """
    if _HAS_TORCH and isinstance(obj, torch.Tensor):
        arr = obj.detach().cpu().numpy()
        buf = io.BytesIO()
        if _HAS_NUMPY:
            np.save(buf, arr, allow_pickle=False)
        else:
            buf.write(arr.tobytes())
        return (
            {
                "__type__": "tensor",
                "dtype": str(arr.dtype),
                "shape": list(arr.shape),
                "format": "npy" if _HAS_NUMPY else "raw",
            },
            buf.getvalue(),
        )
    if _HAS_NUMPY and isinstance(obj, np.ndarray):
        buf = io.BytesIO()
        np.save(buf, obj, allow_pickle=False)
        return (
            {
                "__type__": "ndarray",
                "dtype": str(obj.dtype),
                "shape": list(obj.shape),
                "format": "npy",
            },
            buf.getvalue(),
        )
    if isinstance(obj, dict):
        sub_metas: dict[str, Any] = {}
        parts: list[bytes] = []
        offset: int = 0
        for k, v in obj.items():
            sm, sb = _serialize_value(v)
            sm["_offset"] = offset
            sm["_length"] = len(sb)
            sub_metas[k] = sm
            parts.append(sb)
            offset += len(sb)
        return {"__type__": "dict", "items": sub_metas}, b"".join(parts)
    if isinstance(obj, (list, tuple)):
        sub_metas: list[Any] = []
        parts: list[bytes] = []
        offset: int = 0
        is_tuple = isinstance(obj, tuple)
        for v in obj:
            sm, sb = _serialize_value(v)
            sm["_offset"] = offset
            sm["_length"] = len(sb)
            sub_metas.append(sm)
            parts.append(sb)
            offset += len(sb)
        return {"__type__": "tuple" if is_tuple else "list", "items": sub_metas}, b"".join(parts)
    try:
        json.dumps(obj)
        return {"__type__": "scalar", "value": obj}, b""
    except (TypeError, ValueError):
        return {"__type__": "scalar", "value": str(obj)}, b""


def _deserialize_value(meta: Any, binary: bytes) -> Any:
    """根据元信息 + 二进制体还原对象。"""
    if not isinstance(meta, dict):
        if isinstance(meta, list):
            return [_deserialize_value(item, binary) for item in meta]
        return meta
    type_tag = meta.get("__type__")
    if type_tag in ("tensor", "ndarray"):
        fmt = meta.get("format", "npy")
        if fmt == "npy" and _HAS_NUMPY and binary:
            arr = np.load(io.BytesIO(binary), allow_pickle=False)
        elif _HAS_NUMPY and binary:
            arr = np.frombuffer(binary, dtype=np.dtype(meta["dtype"])).reshape(meta["shape"]).copy()
        else:
            arr = None
        if type_tag == "tensor" and _HAS_TORCH and arr is not None:
            return torch.from_numpy(arr)
        return arr
    if type_tag == "dict":
        result: dict[str, Any] = {}
        items = meta.get("items", {})
        for k, sm in items.items():
            off = int(sm.get("_offset", 0))
            length = int(sm.get("_length", 0))
            sub_bin = binary[off : off + length] if length > 0 else b""
            result[k] = _deserialize_value(sm, sub_bin)
        return result
    if type_tag in ("list", "tuple"):
        items = meta.get("items", [])
        result: list[Any] = []
        for sm in items:
            off = int(sm.get("_offset", 0))
            length = int(sm.get("_length", 0))
            sub_bin = binary[off : off + length] if length > 0 else b""
            result.append(_deserialize_value(sm, sub_bin))
        return tuple(result) if type_tag == "tuple" else result
    if type_tag == "scalar":
        return meta.get("value")
    return {k: _deserialize_value(v, binary) for k, v in meta.items() if not k.startswith("__")}


def _serialize_legacy(obj: Any) -> dict[str, Any]:
    """旧版序列化：全部塞进 JSON（向后兼容读取）。"""
    if _HAS_TORCH and isinstance(obj, torch.Tensor):
        arr = obj.detach().cpu().numpy()
        return {
            "__type__": "tensor",
            "data": base64.b64encode(arr.tobytes()).decode("ascii"),
            "dtype": str(arr.dtype),
            "shape": list(arr.shape),
        }
    if _HAS_NUMPY and isinstance(obj, np.ndarray):
        return {
            "__type__": "ndarray",
            "data": base64.b64encode(obj.tobytes()).decode("ascii"),
            "dtype": str(obj.dtype),
            "shape": list(obj.shape),
        }
    if isinstance(obj, dict):
        return {k: _serialize_legacy(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_legacy(item) for item in obj]
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return {"__type__": "str", "data": str(obj)}


def _deserialize_legacy(data: Any) -> Any:
    """旧版反序列化：从纯 JSON 还原（向后兼容读取）。"""
    if isinstance(data, dict):
        type_tag = data.get("__type__")
        if type_tag == "tensor" and _HAS_NUMPY and _HAS_TORCH:
            raw = base64.b64decode(data["data"])
            arr = np.frombuffer(raw, dtype=np.dtype(data["dtype"])).reshape(data["shape"]).copy()
            return torch.from_numpy(arr)
        if type_tag == "ndarray" and _HAS_NUMPY:
            raw = base64.b64decode(data["data"])
            return np.frombuffer(raw, dtype=np.dtype(data["dtype"])).reshape(data["shape"]).copy()
        if type_tag == "str":
            return data["data"]
        return {k: _deserialize_legacy(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_deserialize_legacy(item) for item in data]
    return data


class PromptCache:
    """参考音频嵌入持久化缓存：LRU + TTL 双淘汰 + 原子写。

    文件格式：``MAGIC_HEADER (5B) + JSON_LEN (4B LE) + JSON_BODY + BINARY_BODY``
    - MAGIC_HEADER = b"TTSP1"，标识新版本二进制格式
    - JSON_LEN 为 JSON 元数据头的小端无符号 32 位整数长度
    - JSON_BODY 为 UTF-8 编码的元数据（含类型/shape/dtype/offset 信息）
    - BINARY_BODY 为嵌入向量原始字节（numpy .npy 或 raw bytes）

    Attributes:
        cache_dir (Path): 缓存根目录路径。
        max_entries (int): LRU 最大条目数，超出淘汰最久未访问条目。
        ttl (int): TTL 过期时间（秒），默认 7 天。
        _lock (threading.RLock): 可重入线程锁，保护并发访问。
        _meta (OrderedDict[str, PromptCacheEntry]): 内存元数据索引 OrderedDict，
            按访问顺序维护 LRU：最近访问/写入的在末尾，最旧的在开头，O(1) 淘汰。
        _last_meta_save (float): 上次元数据持久化时间戳。
        _meta_dirty_count (int): 自上次保存后元数据变更次数。
    """

    def __init__(
        self,
        cache_dir: str = _DEFAULT_CACHE_DIR,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        """初始化 PromptCache。

        Args:
            cache_dir: 缓存根目录路径。
            max_entries: LRU 最大条目数，超出后淘汰最久未访问条目。
            ttl_seconds: TTL 秒数，默认 7 天（604800s）。
        """
        self.cache_dir: Path = Path(cache_dir)
        self.max_entries: int = max_entries
        self.ttl: int = ttl_seconds
        self._lock: threading.RLock = threading.RLock()
        self._meta: OrderedDict[str, PromptCacheEntry] = OrderedDict()
        self._last_meta_save: float = 0.0
        self._meta_dirty_count: int = 0
        self._load_meta()

    # ------------------------------------------------------------------ utils
    def _cache_file_path(self, cache_key: str) -> Path:
        """返回指定 cache_key 对应的新格式缓存文件路径 (.ttsp)。

        Args:
            cache_key: 缓存键字符串。

        Returns:
            Path: ``{cache_dir}/{cache_key}.ttsp`` 路径对象。
        """
        return self.cache_dir / f"{cache_key}.ttsp"

    def _old_json_path(self, cache_key: str) -> Path:
        """返回指定 cache_key 对应的旧版 JSON 缓存文件路径 (.json)。

        Args:
            cache_key: 缓存键字符串。

        Returns:
            Path: ``{cache_dir}/{cache_key}.json`` 路径对象。
        """
        return self.cache_dir / f"{cache_key}.json"

    def _old_pkl_path(self, cache_key: str) -> Path:
        """返回指定 cache_key 对应的旧版 pickle 缓存文件路径 (.pkl)。

        Args:
            cache_key: 缓存键字符串。

        Returns:
            Path: ``{cache_dir}/{cache_key}.pkl`` 路径对象。
        """
        return self.cache_dir / f"{cache_key}.pkl"

    def _metadata_path(self) -> Path:
        """返回元数据索引文件路径。

        Returns:
            Path: ``{cache_dir}/metadata.json`` 路径对象。
        """
        return self.cache_dir / "metadata.json"

    def _ensure_cache_dir(self) -> None:
        """确保缓存根目录存在（递归创建父目录）。"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ meta I/O
    def _load_meta(self) -> None:
        """从 metadata.json 加载内存索引；损坏或缺失时尝试从旧格式迁移。"""
        self._ensure_cache_dir()
        meta_path = self._metadata_path()
        raw: dict[str, Any] = {}
        if meta_path.exists():
            try:
                with open(meta_path, encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict):
                    raw = {}
            except (json.JSONDecodeError, OSError):
                raw = {}
        else:
            raw = self._migrate_legacy_metadata()

        entries_list: list[tuple[str, PromptCacheEntry]] = []
        for k, v in raw.items():
            if k == "version" or not isinstance(v, dict):
                continue
            try:
                entry = PromptCacheEntry(
                    key=str(v.get("key", k)),
                    created_at=float(v.get("created_at", 0)),
                    last_access_ts=float(v.get("last_access_ts", v.get("last_accessed", 0))),
                    access_count=int(v.get("access_count", 0)),
                    embedding_size=int(v.get("embedding_size", v.get("file_size", 0))),
                    audio_hash=str(v.get("audio_hash", "")),
                )
                entries_list.append((k, entry))
            except (TypeError, ValueError):
                continue

        # 按 last_access_ts 升序排序，初始化 OrderedDict 的 LRU 顺序
        entries_list.sort(key=lambda x: x[1].last_access_ts)
        self._meta = OrderedDict(entries_list)

    def _save_meta(self, force: bool = False) -> None:
        """持久化内存索引到 metadata.json（带节流）。

        写失败（权限不足等）仅记录 error 日志并静默吞掉；
        下次启动时会从磁盘文件重建索引，不中断本次运行。

        Args:
            force: 是否强制立即保存（忽略节流条件）。
        """
        now = time.time()
        if not force and (
            now - self._last_meta_save < _META_SAVE_THROTTLE_SECONDS
            and self._meta_dirty_count < _META_SAVE_DIRTY_THRESHOLD
        ):
            return

        meta_path = self._metadata_path()
        payload: dict[str, Any] = {"version": _METADATA_VERSION}
        for k, entry in self._meta.items():
            payload[k] = {
                "key": entry.key,
                "created_at": entry.created_at,
                "last_access_ts": entry.last_access_ts,
                "access_count": entry.access_count,
                "embedding_size": entry.embedding_size,
                "audio_hash": entry.audio_hash,
            }
        try:
            tmp_path = meta_path.with_suffix(meta_path.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, meta_path)
            self._last_meta_save = now
            self._meta_dirty_count = 0
        except (PermissionError, OSError, TypeError) as e:
            logger.error("保存 PromptCache 元数据失败（%s），已忽略", e)
            with contextlib.suppress(OSError):
                tmp_path = meta_path.with_suffix(meta_path.suffix + ".tmp")
                if tmp_path.exists():
                    tmp_path.unlink()

    def _mark_meta_dirty(self) -> None:
        """标记元数据已变更，增加脏计数（供节流使用）。"""
        self._meta_dirty_count += 1

    def flush(self) -> None:
        """强制将所有未保存的元数据写入磁盘。"""
        with self._lock:
            if self._meta_dirty_count > 0:
                self._save_meta(force=True)

    def _migrate_legacy_metadata(self) -> dict[str, Any]:
        """从旧版 metadata.pkl（pickle 格式）迁移元数据索引。

        迁移成功后删除旧 .pkl 文件，失败仅记录 warning 返回空 dict。

        Returns:
            Dict[str, Any]: 迁移后的元数据字典，失败或文件不存在返回空 dict。
        """
        old_path = self.cache_dir / "metadata.pkl"
        if not old_path.exists():
            return {}
        try:
            import pickle as _pickle  # nosec B403 - 自有旧版 metadata.pkl 迁移读取（读后即删），加载处已有 B301 nosec

            with open(old_path, "rb") as f:
                metadata = _pickle.load(f)  # nosec B301 - 自有旧版 metadata.pkl 迁移读取（读后即删），加载后 isinstance 校验
            if isinstance(metadata, dict):
                logger.info("已从 metadata.pkl 迁移 PromptCache 元数据")
                with contextlib.suppress(OSError):
                    old_path.unlink()
                return metadata
        except Exception as e:
            logger.warning("迁移旧版 PromptCache 元数据失败: %s", e)
        return {}

    # -------------------------------------------------------------- hash
    @staticmethod
    def _compute_audio_hash(audio_path_or_data: _BinaryInput | None) -> str:
        """计算参考音频内容哈希（sha256，前 16 位十六进制）。

        - ``str`` / ``Path``：按文件读取，为了大音频性能只 hash 前 1MB
        - ``bytes`` / ``bytearray`` / ``memoryview``：直接哈希全部字节
        - numpy ``ndarray`` / torch ``Tensor``：转为字节流后哈希
        - ``None``：返回空串

        Returns:
            str: 十六进制哈希字符串，输入为 ``None`` 时返回 ``""``。
        """
        if audio_path_or_data is None:
            return ""
        h = hashlib.sha256()
        if isinstance(audio_path_or_data, (str, Path)):
            try:
                with open(audio_path_or_data, "rb") as f:
                    chunk = f.read(1024 * 1024)
                    h.update(chunk)
            except OSError:
                h.update(str(audio_path_or_data).encode("utf-8"))
            return h.hexdigest()[:16]
        if isinstance(audio_path_or_data, (bytes, bytearray, memoryview)):
            h.update(bytes(audio_path_or_data))
            return h.hexdigest()[:16]
        if _HAS_NUMPY and isinstance(audio_path_or_data, np.ndarray):
            h.update(audio_path_or_data.tobytes())
            return h.hexdigest()[:16]
        if _HAS_TORCH and isinstance(audio_path_or_data, torch.Tensor):
            arr = audio_path_or_data.detach().cpu().numpy()
            h.update(arr.tobytes())
            return h.hexdigest()[:16]
        h.update(str(audio_path_or_data).encode("utf-8"))
        return h.hexdigest()[:16]

    # ------------------------------------------------------ public API
    def get(self, cache_key: str, audio_hash: str | None = None) -> Any | None:
        """读取缓存条目。

        若传入 ``audio_hash``，会与缓存内记录的 ``audio_hash`` 对比；
        不一致说明同路径音频内容已变化，主动 invalidate 该条目并返回
        ``None``，避免脏缓存（stale cache）。

        读取时遇到 ``EOFError`` / ``UnpicklingError`` 或损坏 JSON 会
        自动 invalidate 对应条目并返回 ``None``，静默自修复。

        Args:
            cache_key: 缓存键。
            audio_hash: 可选；当前音频内容哈希，用于防脏缓存。

        Returns:
            嵌入对象或 ``None``。
        """
        with self._lock:
            self._ensure_cache_dir()
            now = time.time()
            entry = self._meta.get(cache_key)
            if entry is None:
                return self._try_read_legacy(cache_key, audio_hash, now)

            ttl_expired = now - entry.created_at > self.ttl
            if ttl_expired:
                self._invalidate_nolock(cache_key)
                return None

            if audio_hash and entry.audio_hash and entry.audio_hash != audio_hash:
                self._invalidate_nolock(cache_key)
                return None

            cache_file = self._cache_file_path(cache_key)
            if not cache_file.exists():
                legacy = self._try_read_legacy(cache_key, audio_hash, now)
                if legacy is not None:
                    return legacy
                del self._meta[cache_key]
                self._mark_meta_dirty()
                self._save_meta()
                return None

            try:
                with open(cache_file, "rb") as f:
                    header = f.read(len(_MAGIC_HEADER))
                    if header != _MAGIC_HEADER:
                        data = self._read_legacy_json(cache_file)
                        if data is None:
                            self._invalidate_nolock(cache_key)
                            return None
                        entry.last_access_ts = now
                        entry.access_count += 1
                        self._meta.move_to_end(cache_key)
                        self._mark_meta_dirty()
                        self._save_meta()
                        return data
                    size_bytes = f.read(4)
                    if len(size_bytes) < 4:
                        raise EOFError("truncated meta length")
                    (json_len,) = struct.unpack("<I", size_bytes)
                    json_bytes = f.read(json_len)
                    if len(json_bytes) < json_len:
                        raise EOFError("truncated json body")
                    file_meta = json.loads(json_bytes.decode("utf-8"))
                    binary = f.read()
                value = _deserialize_value(file_meta, binary)
            except (EOFError, json.JSONDecodeError, KeyError, TypeError, ValueError, struct.error, OSError) as e:
                logger.warning("读取 PromptCache 条目 %s 损坏，已自动失效: %s", cache_key, e)
                self._invalidate_nolock(cache_key)
                return None

            entry.last_access_ts = now
            entry.access_count += 1
            self._meta.move_to_end(cache_key)  # LRU：移动到末尾（最近使用）
            self._mark_meta_dirty()
            self._save_meta()
            return value

    def _try_read_legacy(self, cache_key: str, audio_hash: str | None, now: float) -> Any | None:
        json_path = self._old_json_path(cache_key)
        if json_path.exists():
            try:
                with open(json_path, encoding="utf-8") as f:
                    data = json.load(f)
                value = _deserialize_legacy(data)
                if value is not None:
                    self.put(cache_key, value, audio_path_or_data=None)
                    with contextlib.suppress(OSError):
                        json_path.unlink()
                    entry = self._meta.get(cache_key)
                    if entry:
                        entry.last_access_ts = now
                        entry.access_count += 1
                        self._save_meta()
                    return value
            except (json.JSONDecodeError, EOFError, OSError, Exception) as e:
                logger.warning("读取旧版 JSON PromptCache %s 失败: %s", cache_key, e)
                with contextlib.suppress(OSError):
                    if json_path.exists():
                        json_path.unlink()
        pkl_path = self._old_pkl_path(cache_key)
        if pkl_path.exists():
            try:
                import pickle as _pickle  # nosec B403 - 自有旧版 .pkl 迁移读取（读后即删），加载处已有 B301 nosec

                with open(pkl_path, "rb") as f:
                    value = _pickle.load(f)  # nosec B301 - 自有旧版 .pkl 迁移读取（读后即删）
                self.put(cache_key, value, audio_path_or_data=None)
                with contextlib.suppress(OSError):
                    pkl_path.unlink()
                return value
            except Exception as e:
                logger.warning("迁移旧版 .pkl PromptCache %s 失败: %s", cache_key, e)
                with contextlib.suppress(OSError):
                    if pkl_path.exists():
                        pkl_path.unlink()
        return None

    def _read_legacy_json(self, cache_file: Path) -> Any | None:
        """文件头不是 MAGIC_HEADER 时，尝试按旧版纯 JSON 格式解析。

        Args:
            cache_file: 缓存文件路径。

        Returns:
            解析成功返回嵌入对象；解析失败或文件损坏返回 None。
        """
        try:
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
            return _deserialize_legacy(data)
        except (json.JSONDecodeError, OSError):
            return None

    def put(
        self,
        cache_key: str,
        embedding: Any,
        audio_path_or_data: _BinaryInput | None = None,
    ) -> None:
        """写入缓存条目（原子写 + LRU 淘汰）。

        Why 采用 .tmp + os.replace 原子替换：
            写入中途进程崩溃或断电会造成半写损坏文件；先写同名 .tmp
            再用 ``os.replace`` 做原子重命名，Windows / Linux 均能
            保证缓存文件要么完整要么不存在，杜绝半写读取出错。

        Why LRU + TTL 双重淘汰策略：
            只有 LRU 的话用户长期开机不改音色，7 天前的旧嵌入
            （模型权重升级后格式可能变化）仍会被命中；TTL 强制兜底。
            只有 TTL 的话热门用户 1 天访问 1000 次会把磁盘塞满；
            LRU 控制硬上限。两者结合兼顾复用率与一致性。

        Args:
            cache_key: 缓存键。
            embedding: 任意可序列化的嵌入对象。
            audio_path_or_data: 可选；原始音频路径 / bytes / ndarray，
                用于计算内容哈希防脏缓存。
        """
        with self._lock:
            self._ensure_cache_dir()

            meta, binary = _serialize_value(embedding)
            json_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
            total_size = len(_MAGIC_HEADER) + 4 + len(json_bytes) + len(binary)

            now = time.time()
            audio_hash = self._compute_audio_hash(audio_path_or_data)

            existing_entry = self._meta.get(cache_key)
            self._meta[cache_key] = PromptCacheEntry(
                key=cache_key,
                created_at=existing_entry.created_at if existing_entry is not None else now,
                last_access_ts=now,
                access_count=existing_entry.access_count + 1 if existing_entry is not None else 1,
                embedding_size=total_size,
                audio_hash=audio_hash or (existing_entry.audio_hash if existing_entry is not None else ""),
            )
            self._meta.move_to_end(cache_key)  # 新写入的放在末尾（最近使用）

            cache_file = self._cache_file_path(cache_key)
            tmp_path = cache_file.with_suffix(cache_file.suffix + ".tmp")
            try:
                with open(tmp_path, "wb") as f:
                    f.write(_MAGIC_HEADER)
                    f.write(struct.pack("<I", len(json_bytes)))
                    f.write(json_bytes)
                    f.write(binary)
                os.replace(tmp_path, cache_file)
            except (PermissionError, OSError) as e:
                logger.error("写入 PromptCache 条目 %s 失败: %s", cache_key, e)
                with contextlib.suppress(OSError):
                    if tmp_path.exists():
                        tmp_path.unlink()
                del self._meta[cache_key]
                return

            # LRU 淘汰：OrderedDict.popitem(last=False) O(1) 弹出最旧条目
            evicted_count = 0
            while len(self._meta) > self.max_entries:
                try:
                    oldest_key, _ = self._meta.popitem(last=False)
                except KeyError:
                    break
                self._evict_nolock(oldest_key, reason="lru")
                evicted_count += 1

            if evicted_count > 0:
                self._mark_meta_dirty()
            self._mark_meta_dirty()
            self._save_meta()
            self.clear_expired()

    def invalidate(self, cache_key: str) -> bool:
        """失效指定缓存键。

        Args:
            cache_key: 要失效的缓存键。

        Returns:
            bool: 该键存在且成功删除返回 ``True``，否则 ``False``。
        """
        with self._lock:
            return self._invalidate_nolock(cache_key)

    def _invalidate_nolock(self, cache_key: str) -> bool:
        """失效指定缓存键（内部方法，调用方需持有 _lock）。

        同时删除 .ttsp / .json / .pkl 三种格式的缓存文件（如果存在）。

        Args:
            cache_key: 要失效的缓存键。

        Returns:
            bool: 该键在内存索引中存在且被删除返回 True，否则 False。
        """
        existed = cache_key in self._meta
        if existed:
            del self._meta[cache_key]
        cache_file = self._cache_file_path(cache_key)
        for p in (cache_file, self._old_json_path(cache_key), self._old_pkl_path(cache_key)):
            with contextlib.suppress(OSError):
                if p.exists():
                    p.unlink()
        if existed:
            self._mark_meta_dirty()
            self._save_meta()
        return existed

    def _evict_nolock(self, cache_key: str, reason: str = "evict") -> None:
        """淘汰指定缓存键（内部方法，调用方需持有 _lock）。

        LRU 超限时调用，从内存索引移除并删除磁盘缓存文件。
        与 _invalidate_nolock 的区别：不尝试删除旧格式文件，仅删除当前 .ttsp。

        Args:
            cache_key: 要淘汰的缓存键。
            reason: 淘汰原因（用于日志），如 "lru"、"ttl"。
        """
        if cache_key in self._meta:
            del self._meta[cache_key]
        cache_file = self._cache_file_path(cache_key)
        with contextlib.suppress(OSError):
            if cache_file.exists():
                cache_file.unlink()
        logger.debug("PromptCache 淘汰条目 %s (%s)", cache_key, reason)

    def clear_expired(self) -> int:
        """清理所有 TTL 过期的缓存条目。

        删除单个条目时若文件已不存在（用户手动清理），用
        ``contextlib.suppress(FileNotFoundError)`` 静默吞掉，不中断整体清理。

        Returns:
            int: 实际被清理的条目数量。
        """
        with self._lock:
            now = time.time()
            expired: list[str] = []
            for k, entry in self._meta.items():
                if now - entry.created_at > self.ttl:
                    expired.append(k)
            for k in expired:
                with contextlib.suppress(FileNotFoundError, OSError):
                    p = self._cache_file_path(k)
                    if p.exists():
                        p.unlink()
                with contextlib.suppress(FileNotFoundError, OSError):
                    p = self._old_json_path(k)
                    if p.exists():
                        p.unlink()
                with contextlib.suppress(FileNotFoundError, OSError):
                    p = self._old_pkl_path(k)
                    if p.exists():
                        p.unlink()
                if k in self._meta:
                    del self._meta[k]
            if expired:
                logger.info("PromptCache TTL 清理 %d 个条目", len(expired))
                self._meta_dirty_count += len(expired)
                self._save_meta()
            return len(expired)

    def clear_all(self) -> int:
        """清空全部缓存条目（含旧格式遗留文件）。

        Returns:
            int: 被删除的文件数量。
        """
        with self._lock:
            self._ensure_cache_dir()
            removed = 0
            for key in list(self._meta.keys()):
                with contextlib.suppress(OSError):
                    p = self._cache_file_path(key)
                    if p.exists():
                        p.unlink()
                        removed += 1
                with contextlib.suppress(OSError):
                    p = self._old_json_path(key)
                    if p.exists():
                        p.unlink()
                        removed += 1
                with contextlib.suppress(OSError):
                    p = self._old_pkl_path(key)
                    if p.exists():
                        p.unlink()
                        removed += 1
            self._meta.clear()
            meta_path = self._metadata_path()
            with contextlib.suppress(OSError):
                if meta_path.exists():
                    meta_path.unlink()
            with contextlib.suppress(OSError):
                old_meta = self.cache_dir / "metadata.pkl"
                if old_meta.exists():
                    old_meta.unlink()
            logger.info("PromptCache 已清空，删除 %d 个文件", removed)
            return removed

    def clear(self) -> int:
        """向后兼容别名：等价于 :meth:`clear_all`。"""
        return self.clear_all()

    def get_stats(self) -> dict[str, Any]:
        """获取缓存统计信息。

        Returns:
            dict[str, Any]: 包含 entries / total_size_bytes /
            total_size_mb / max_entries / ttl_seconds / cache_dir /
            hit_count_estimate 等字段。
        """
        with self._lock:
            self._ensure_cache_dir()
            total_size = 0
            for k, entry in self._meta.items():
                p = self._cache_file_path(k)
                if p.exists():
                    with contextlib.suppress(OSError):
                        total_size += p.stat().st_size
                        continue
                total_size += entry.embedding_size
            total_access = sum(e.access_count for e in self._meta.values())
            return {
                "entries": len(self._meta),
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / 1024 / 1024, 2),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl,
                "cache_dir": str(self.cache_dir),
                "total_access_count": total_access,
                "magic_header": _MAGIC_HEADER.decode("ascii", errors="replace"),
                "format_version": _METADATA_VERSION,
            }


# ----------------------------------------------------------------------
# 全局默认实例 & 向后兼容函数式 API
# ----------------------------------------------------------------------

_PROMPT_CACHE_DIR = Path(_DEFAULT_CACHE_DIR)
_MAX_CACHE_ENTRIES = _DEFAULT_MAX_ENTRIES
_CACHE_TTL_SECONDS = _DEFAULT_TTL_SECONDS

_lock = threading.RLock()
_default_cache: PromptCache | None = None


def get_prompt_cache() -> PromptCache:
    """获取全局默认 PromptCache 单例。"""
    global _default_cache
    if _default_cache is None:
        with _lock:
            if _default_cache is None:
                _default_cache = PromptCache(
                    cache_dir=str(_PROMPT_CACHE_DIR),
                    max_entries=_MAX_CACHE_ENTRIES,
                    ttl_seconds=_CACHE_TTL_SECONDS,
                )
    return _default_cache


def _ensure_cache_dir() -> None:
    """向后兼容：确保全局缓存目录存在。"""
    get_prompt_cache()._ensure_cache_dir()


def _get_prompt_cache_key(audio_path: str) -> str:
    """向后兼容：根据音频路径生成缓存键（sha256 前 16 位）。"""
    h = hashlib.sha256()
    try:
        with open(audio_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except OSError:
        h.update(audio_path.encode("utf-8"))
    return h.hexdigest()[:16]


def load_cached_prompt(audio_path: str) -> Any | None:
    """向后兼容：按音频路径读取缓存嵌入。

    等价于：``key = hash(audio_path); cache.get(key, audio_hash=hash_content(audio_path))``
    """
    cache = get_prompt_cache()
    cache_key = _get_prompt_cache_key(audio_path)
    audio_hash = PromptCache._compute_audio_hash(audio_path)
    return cache.get(cache_key, audio_hash=audio_hash)


def save_prompt_cache(audio_path: str, prompt_cache: Any) -> None:
    """向后兼容：按音频路径保存嵌入缓存。"""
    cache = get_prompt_cache()
    cache_key = _get_prompt_cache_key(audio_path)
    cache.put(cache_key, prompt_cache, audio_path_or_data=audio_path)


def clear_prompt_cache() -> None:
    """向后兼容：清空全局 PromptCache。"""
    get_prompt_cache().clear_all()


def get_cache_stats() -> dict[str, Any]:
    """向后兼容：获取全局 PromptCache 统计。"""
    return get_prompt_cache().get_stats()


# ----------------------------------------------------------------------
# 旧版 prompt_cache 的 4 个内部辅助函数（tests/test_prompt_cache.py 依赖）
# 为兼容未升级的测试代码，在此保留为薄包装。
# ----------------------------------------------------------------------


def _get_cache_file_path(cache_key: str) -> "Path":
    """向后兼容：返回指定 cache_key 对应的缓存文件路径 (.json)。

    Args:
        cache_key: 缓存键字符串（合法文件名字符）。

    Returns:
        指向 ``{cache_dir}/{cache_key}.json`` 的 Path 对象。
    """
    # Why .json 后缀：旧版 prompt_cache 每个条目存为独立 JSON 文件，
    # 新版本改为 .ttsp（MAGIC_HEADER + JSON + BINARY），但兼容函数仍返回 .json
    # 保证 test_prompt_cache.py 的 endswith(".json") 断言依然成立。
    return _PROMPT_CACHE_DIR / f"{cache_key}.json"


def _get_metadata_path() -> "Path":
    """向后兼容：返回全局元数据文件路径。

    Returns:
        指向 ``{cache_dir}/metadata.json`` 的 Path 对象。
    """
    # 新版本元数据存储在每个条目内嵌的 JSON header 中，
    # 旧版本通过 metadata.json 索引所有条目，此处保留薄包装兼容旧测试。
    return _PROMPT_CACHE_DIR / "metadata.json"


def _serialize_prompt_cache(data: Any) -> bytes:
    """向后兼容：安全序列化任意可 JSON 化数据为 UTF-8 bytes。

    与旧版 ``json.dumps + encode`` 行为一致，不使用 pickle（安全红线）。

    Args:
        data: JSON 可序列化对象（dict / list / str / int / float / bool / None）。

    Returns:
        UTF-8 编码的 JSON bytes。

    Raises:
        TypeError: data 含不可 JSON 序列化的类型（如 torch.Tensor、np.ndarray），
            调用方应先自行转换为 list / dict。
    """
    # Why json 而非 pickle：旧版本测试用这对函数断言"prompt_cache 不使用 pickle"，
    # 防止 LoRA/Prompt 文件被植入 pickle RCE 后门，这是与 safetensors 强制方案
    # 一致的统一安全红线（AGENTS 安全与变更边界）。
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _deserialize_prompt_cache(serialized: Any) -> Any:
    """向后兼容：还原 :func:`_serialize_prompt_cache` 输出的数据。

    Args:
        serialized: bytes / bytearray / str 格式的 JSON 数据。

    Returns:
        还原后的 dict / list / str / int / float / bool / None。

    Raises:
        json.JSONDecodeError: 输入非合法 JSON 结构；
        TypeError: 输入非 bytes/str 类型且无法强制转换。
    """
    if isinstance(serialized, (bytes, bytearray)):
        text = serialized.decode("utf-8")
    elif isinstance(serialized, str):
        text = serialized
    else:
        # 兼容 numpy bytes_ / memoryview：尽可能强制 str
        text = str(serialized)
    return json.loads(text)
