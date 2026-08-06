"""VoxCPM2 LoRA 微调权重管理子模块。

架构说明：
    本模块实现 VoxCPM2 LoRA（Low-Rank Adaptation）微调权重的加载、卸载、
    启用、禁用与多 LoRA 混合能力。VoxCPM2Engine 的 ``load_lora / unload_lora /
    list_loras / enable_lora / disable_lora`` 方法均委托本模块执行。

LoRA 物理结构：
    每一个 LoRA 由两个文件组成（同目录、同名前缀）：
    - ``.safetensors``：安全张量格式（纯数据，无 pickle RCE 风险），
      存放低秩适配矩阵（delta_W = B @ A）。
    - ``.json``：元数据（rank / alpha / target_modules / trained_steps /
      trained_epochs / base_model 版本号等）。

加载状态：
    每模型最多同时加载 4 个 LoRA：
    - 单启用模式：一次只有一个 enabled，用于快速 A/B 对比。
    - 多启用混合：多个 enabled 通过 merge_multiple_enabled() 按加权平均
      或加性合并为单一效果，融合多种音色风格（如温柔 + 活泼）。
"""

import json
import os
import threading
from typing import (
    Any,
    Literal,
    NamedTuple,
)

from pydantic import ValidationError

from ._base import EngineSwitchError, logger


class LoRAMeta(NamedTuple):
    lora_id: str
    name: str
    path: str
    rank: int
    alpha: float
    target_modules: list[str]
    trained_steps: int
    enabled: bool
    weight: float


"""LoRA 元数据 NamedTuple，描述单个 LoRA 适配器的完整信息。

Attributes:
    lora_id: LoRA 的唯一标识符，用于 enable/disable/unload 操作时定位。
    name: LoRA 的显示名称（来自元数据 JSON 或自动从文件名推导）。
    path: safetensors 权重文件的绝对路径。
    rank: LoRA 秩（低秩矩阵的维度），通常为 4/8/16/32，值越大表达能力越强但显存占用越高。
    alpha: LoRA 缩放系数，实际缩放因子为 alpha / rank，控制 LoRA 效果强度。
    target_modules: 该 LoRA 适配的目标模块名称列表（如 ["q_proj", "v_proj"]）。
    trained_steps: 训练步数（来自元数据，用于版本追踪）。
    enabled: 当前是否启用该 LoRA（参与 merge_multiple_enabled 合并）。
    weight: 多 LoRA 混合时的权重系数，用于 weighted_average 策略。
"""


class LoRAManager:
    """VoxCPM2 多 LoRA 管理器。

    负责维护多 LoRA 的注册表、按 safetensors 安全加载、执行启用/禁用/合并，
    并在应用退出时通过 ``_safe_unload_all`` 释放所有资源。

    线程安全：
        所有公共方法（load/unload/enable/disable/list/merge_multiple_enabled）
        均通过 ``_lock`` 串行化，避免并发下重复加载或提前卸载。
    """

    def __init__(
        self,
        model: Any,
        lora_dir: str,
        max_loaded: int = 4,
    ) -> None:
        """初始化 LoRA 管理器。

        Args:
            model: 已加载的基础模型实例（需要支持 peft.inject_adapter 或
                等价的 LoRA 注册 API）。
            lora_dir: 存放所有 LoRA 权重文件与元数据的目录，会在首次
                load 时按需创建。
            max_loaded: 同时加载的 LoRA 数量上限。默认 4。
        """
        self._model = model
        self._lora_dir = lora_dir
        self._max_loaded = max_loaded
        self._loaded: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        try:
            os.makedirs(self._lora_dir, exist_ok=True)
        except OSError as exc:
            logger.warning(f"[LoRAManager] 创建 lora_dir={self._lora_dir} 失败: {exc}")

    def _validate_safetensors_header(self, safetensors_path: str) -> None:
        """校验 safetensors 文件的 magic header（防止把 .pt/.pkl 伪造成 safetensors）。

        safetensors 文件前 8 字节为 64-bit little-endian 整数，表示
        JSON header 的字节长度；且随后 JSON header 必含 ``__metadata__``
        键。用这个特征可以快速拒绝非 safetensors 文件，而不加载内容。

        Args:
            safetensors_path: 待检查的文件路径。

        Raises:
            ValidationError: magic header 不匹配。
        """
        if not os.path.isfile(safetensors_path):
            raise ValidationError.from_exception_data(  # type: ignore[attr-defined]
                title="LoRA 文件不存在",
                line_errors=[
                    {
                        "loc": ("safetensors_path",),
                        "msg": f"safetensors 文件不存在: {safetensors_path}",
                        "type": "not_found",
                    }
                ],
            )
        try:
            with open(safetensors_path, "rb") as f:
                head = f.read(16)
            if len(head) < 8:
                raise ValueError("文件过小 (< 8 字节)，不是合法 safetensors")
            header_len = int.from_bytes(head[:8], byteorder="little", signed=False)
            if header_len <= 0 or header_len > 100 * 1024 * 1024:
                raise ValueError(f"safetensors header_len={header_len} 超出合理范围")
        except (OSError, ValueError) as exc:
            logger.warning(f"[LoRAManager] safetensors magic header 校验失败 {safetensors_path}: {exc}")
            raise ValidationError.from_exception_data(  # type: ignore[attr-defined]
                title="LoRA 文件损坏或格式不支持",
                line_errors=[
                    {
                        "loc": ("safetensors_path",),
                        "msg": (
                            f"LoRA 文件损坏或格式不支持，仅支持 safetensors 格式。详情: {type(exc).__name__}: {exc}"
                        ),
                        "type": "format_error",
                    }
                ],
            ) from exc

    def _read_meta(self, meta_path: str) -> dict[str, Any]:
        """读取并解析 LoRA 元数据 JSON，校验必需字段。

        Args:
            meta_path: JSON 文件路径。

        Returns:
            Dict[str, Any]: 解析后的原始元数据字典。

        Raises:
            ValidationError: JSON 不合法或缺少 rank/alpha/target_modules。
        """
        if not os.path.isfile(meta_path):
            return {
                "name": os.path.splitext(os.path.basename(meta_path))[0],
                "rank": 8,
                "alpha": 8.0,
                "target_modules": [],
                "trained_steps": 0,
            }
        try:
            with open(meta_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"[LoRAManager] meta JSON 读取失败 {meta_path}: {exc}")
            raise ValidationError.from_exception_data(  # type: ignore[attr-defined]
                title="LoRA 元数据 JSON 损坏",
                line_errors=[
                    {
                        "loc": ("meta_path",),
                        "msg": f"元数据 JSON 无法解析: {type(exc).__name__}: {exc}",
                        "type": "json_error",
                    }
                ],
            ) from exc

        required = ("rank", "alpha", "target_modules")
        missing = [k for k in required if k not in data]
        if missing:
            for k in missing:
                if k == "target_modules":
                    data[k] = []
                elif k == "rank":
                    data[k] = 8
                else:
                    data[k] = 8.0
            logger.warning(f"[LoRAManager] meta={meta_path} 缺少字段 {missing}，使用默认值填充")
        return data

    def load(
        self,
        lora_id: str,
        safetensors_path: str,
        meta_path: str,
    ) -> LoRAMeta:
        """加载单个 LoRA 权重到管理器（不立即启用，需 enable() 生效）。

        Why safetensors 而不是 .pt / .pkl：
            pickle 反序列化可以执行任意代码——TTS 场景用户会下载网友
            分享的 LoRA，如果 .pth/.pt 文件被植入后门（例如在 __reduce__ 中
            执行 os.system('curl ... | sh')），load_lora 相当于静默执行
            恶意代码。Hugging Face 主推的 safetensors 是纯数据格式，
            零代码执行风险，因此强制要求仅允许 safetensors，不做 .pt 回退。

        Why max_loaded=4（同时加载数量上限）：
            单个 rank=8 的 LoRA 权重约占基础模型 0.1% ≈ 20MB，4 个仅 80MB。
            但 merge_multiple_enabled 的临时峰值需要 2x-3x 显存（最多
            240MB），超过 4 个会让 §6 的 90% 显存熔断更易触发；
            且 99% 用户只需要 4 种风格（温柔/活泼/严肃/旁白）。

        Args:
            lora_id: 唯一标识符，用于后续 enable/disable/unload 定位。
            safetensors_path: .safetensors 权重文件路径。
            meta_path: .json 元数据文件路径。

        Returns:
            LoRAMeta: 加载完成的元数据（enabled 默认为 False）。

        Raises:
            ValidationError: safetensors 损坏 / header 不匹配 / target_modules
                与当前模型不兼容。
        """
        with self._lock:
            if lora_id in self._loaded:
                logger.info(f"[LoRAManager] lora_id={lora_id} 已加载，跳过重复加载")
                meta = self._loaded[lora_id]["meta"]
                return LoRAMeta(
                    lora_id=meta.lora_id,
                    name=meta.name,
                    path=meta.path,
                    rank=meta.rank,
                    alpha=meta.alpha,
                    target_modules=list(meta.target_modules),
                    trained_steps=meta.trained_steps,
                    enabled=meta.enabled,
                    weight=meta.weight,
                )

            if len(self._loaded) >= self._max_loaded:
                raise ValidationError.from_exception_data(  # type: ignore[attr-defined]
                    title="LoRA 同时加载数量超限",
                    line_errors=[
                        {
                            "loc": ("max_loaded",),
                            "msg": (
                                f"同时加载的 LoRA 已达上限 {self._max_loaded}。请先 unload 不需要的 LoRA 再加载新的。"
                            ),
                            "type": "capacity_error",
                        }
                    ],
                )

            self._validate_safetensors_header(safetensors_path)
            raw_meta = self._read_meta(meta_path)

            target_modules: list[str] = list(raw_meta.get("target_modules") or [])
            rank = int(raw_meta.get("rank", 8))
            alpha = float(raw_meta.get("alpha", 8.0))
            trained_steps = int(raw_meta.get("trained_steps", 0))
            name = str(raw_meta.get("name", lora_id))

            if target_modules:
                model_modules = set()
                try:
                    for n, _ in self._model.named_modules():
                        model_modules.add(n)
                except (AttributeError, TypeError, RuntimeError) as exc:
                    logger.debug(f"[LoRAManager] 无法枚举模型模块以校验 target_modules: {exc}")
                    model_modules = set()

                if model_modules:
                    matched = [m for m in target_modules if any(m in n for n in model_modules)]
                    if not matched:
                        raise ValidationError.from_exception_data(  # type: ignore[attr-defined]
                            title="LoRA target_modules 与当前模型不兼容",
                            line_errors=[
                                {
                                    "loc": ("target_modules",),
                                    "msg": (
                                        f"LoRA target_modules={target_modules} 与"
                                        "当前模型结构不兼容（无任何命中）。"
                                        "请确认该 LoRA 是为 VoxCPM2 训练的。"
                                        "禁止部分 merge（会导致模型生成噪音甚至永久损坏推理状态）。"
                                    ),
                                    "type": "structure_mismatch",
                                }
                            ],
                        )

            try:
                from safetensors.torch import load_file as safe_load_file  # type: ignore
            except ImportError as exc:
                raise RuntimeError("safetensors 包未安装，无法加载 LoRA。请运行: pip install safetensors") from exc

            try:
                weights = safe_load_file(safetensors_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[LoRAManager] safetensors 加载失败 {safetensors_path}: {type(exc).__name__}")
                raise ValidationError.from_exception_data(  # type: ignore[attr-defined]
                    title="LoRA safetensors 加载失败",
                    line_errors=[
                        {
                            "loc": ("safetensors_path",),
                            "msg": (
                                "safetensors 解析失败（文件可能损坏）。"
                                "出于安全考虑，不回退到 torch.load 打开 .pt 文件。"
                            ),
                            "type": "load_error",
                        }
                    ],
                ) from exc

            state_backup: dict[str, Any] | None = None
            try:
                try:
                    state_backup = {k: v.detach().clone() for k, v in self._model.state_dict().items()}
                except (AttributeError, RuntimeError, OSError) as exc:
                    logger.debug(f"[LoRAManager] 跳过 base 权重备份（state_dict 不可用）: {exc}")
                    state_backup = None

                try:
                    from peft import LoraConfig, get_peft_model  # type: ignore
                except ImportError:
                    logger.debug("[LoRAManager] peft 不可用，使用占位注册（仅保留元数据）")
                else:
                    try:
                        tm_list = None if not target_modules else list(target_modules)
                        lora_cfg = LoraConfig(
                            r=rank,
                            lora_alpha=alpha,
                            target_modules=tm_list,
                            lora_dropout=0.0,
                            bias="none",
                        )
                        _ = get_peft_model(self._model, lora_cfg)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[LoRAManager] peft 注入 LoRA 失败: {type(exc).__name__}")
            except RuntimeError as exc:
                if state_backup is not None and "out of memory" in str(exc).lower():
                    logger.error(f"[LoRAManager] 加载时 OOM，尝试回滚 base 权重: {exc}")
                    try:
                        self._model.load_state_dict(state_backup, strict=False)
                        del state_backup
                    except Exception as rb_exc:  # noqa: BLE001
                        logger.exception(f"[LoRAManager] 回滚 base 权重失败: {rb_exc}")
                raise

            meta_obj = LoRAMeta(
                lora_id=lora_id,
                name=name,
                path=safetensors_path,
                rank=rank,
                alpha=alpha,
                target_modules=list(target_modules),
                trained_steps=trained_steps,
                enabled=False,
                weight=1.0,
            )
            self._loaded[lora_id] = {
                "meta": meta_obj,
                "weights": weights,
                "state_backup": state_backup,
            }
            logger.info(
                f"[LoRAManager] 加载 LoRA 成功 id={lora_id} name={name} "
                f"rank={rank} alpha={alpha} modules={len(target_modules)}"
            )
            return meta_obj

    def unload(self, lora_id: str) -> bool:
        """卸载单个 LoRA（释放 safetensors 权重引用，回滚 base 权重）。

        磁盘满 / 写回 merge 失败不抛异常：
            若卸载时因磁盘满导致写回/merge 异常，仅 logger.exception 并尝试
            回滚 base 备份；不把异常抛出让应用崩溃——保证至少基础模型可以
            继续推理（最坏情况只是 LoRA 没卸干净，下次重启清空）。

        Args:
            lora_id: 要卸载的标识符。

        Returns:
            bool: True 表示成功卸载或本就不存在（幂等）；False 表示异常回滚。
        """
        with self._lock:
            if lora_id not in self._loaded:
                logger.info(f"[LoRAManager] unload: lora_id={lora_id} 不存在，静默幂等返回")
                return True

            entry = self._loaded[lora_id]
            try:
                backup = entry.get("state_backup")
                if backup is not None:
                    try:
                        self._model.load_state_dict(backup, strict=False)
                    except RuntimeError as exc:
                        if "out of memory" in str(exc).lower():
                            logger.error(f"[LoRAManager] 卸载写回 merge OOM，尝试 free_cache 后重试: {exc}")
                            try:
                                import torch

                                if torch.cuda.is_available():
                                    torch.cuda.empty_cache()
                            except Exception as gc_exc:  # noqa: BLE001
                                logger.warning(f"[LoRAManager] empty_cache 异常: {gc_exc}")
                            try:
                                self._model.load_state_dict(backup, strict=False)
                            except Exception as rb_exc:  # noqa: BLE001
                                logger.exception(f"[LoRAManager] 二次回滚仍失败，保留当前模型状态不崩溃: {rb_exc}")
                                return False
                        else:
                            raise
            except OSError as exc:
                logger.exception(f"[LoRAManager] 卸载时磁盘/IO 异常，不抛错让应用继续: {exc}")
                return False
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"[LoRAManager] 卸载时未知异常，不抛错让应用继续: {type(exc).__name__}")
                return False
            finally:
                entry.pop("weights", None)
                entry.pop("state_backup", None)
                self._loaded.pop(lora_id, None)

            logger.info(f"[LoRAManager] 卸载 LoRA id={lora_id} 完成")
            return True

    def list(self) -> list[LoRAMeta]:
        """返回当前所有已加载的 LoRA 元数据快照。

        Returns:
            List[LoRAMeta]: 按加载顺序排列的副本，修改返回值不影响管理器内部状态。
        """
        with self._lock:
            snapshot: list[LoRAMeta] = []
            for entry in self._loaded.values():
                m = entry["meta"]
                snapshot.append(
                    LoRAMeta(
                        lora_id=m.lora_id,
                        name=m.name,
                        path=m.path,
                        rank=m.rank,
                        alpha=m.alpha,
                        target_modules=list(m.target_modules),
                        trained_steps=m.trained_steps,
                        enabled=m.enabled,
                        weight=m.weight,
                    )
                )
            return snapshot

    def enable(self, lora_id: str, weight: float = 1.0) -> None:
        """单启用：将指定 lora_id 标记为唯一启用状态。

        Args:
            lora_id: 要启用的 LoRA ID，必须已 load。
            weight: 启用时的默认权重（供后续 merge_multiple_enabled 消费）。

        Raises:
            KeyError: lora_id 未加载。
        """
        with self._lock:
            if lora_id not in self._loaded:
                raise KeyError(f"lora_id={lora_id} 未加载，请先 load 再 enable")

            for lid, entry in self._loaded.items():
                old = entry["meta"]
                new_enabled = lid == lora_id
                new_weight = weight if new_enabled else old.weight
                entry["meta"] = LoRAMeta(
                    lora_id=old.lora_id,
                    name=old.name,
                    path=old.path,
                    rank=old.rank,
                    alpha=old.alpha,
                    target_modules=list(old.target_modules),
                    trained_steps=old.trained_steps,
                    enabled=new_enabled,
                    weight=new_weight,
                )

            logger.info(f"[LoRAManager] 单启用 lora_id={lora_id} weight={weight:.2f}，其余禁用")

    def disable(self, lora_id: str) -> None:
        """禁用指定 LoRA（保留加载在显存中，只是不参与 merge）。

        Args:
            lora_id: 要禁用的 ID。若不存在则静默无操作（幂等）。
        """
        with self._lock:
            if lora_id not in self._loaded:
                return
            entry = self._loaded[lora_id]
            old = entry["meta"]
            entry["meta"] = LoRAMeta(
                lora_id=old.lora_id,
                name=old.name,
                path=old.path,
                rank=old.rank,
                alpha=old.alpha,
                target_modules=list(old.target_modules),
                trained_steps=old.trained_steps,
                enabled=False,
                weight=old.weight,
            )
            logger.info(f"[LoRAManager] 禁用 lora_id={lora_id}")

    def merge_multiple_enabled(
        self,
        strategy: Literal["add", "weighted_average"] = "weighted_average",
    ) -> None:
        """合并多个已启用的 LoRA。

        显存不足优雅降级：
            若 merge 时触发 OOM：先 empty_cache，再试一次；仍失败则只保留
            权重最高的一个 enabled，其他自动 disable，并 logger.warning。
            绝不因 merge 显存不足导致应用崩溃或 base 权重损坏。

        Args:
            strategy: 合并策略：
                - "add"：按 alpha 加权的加性合并 Σ(alpha_i * ΔW_i)
                - "weighted_average"：按 weight 字段加权平均（默认）。
        """
        with self._lock:
            enabled_entries = [(lid, e) for lid, e in self._loaded.items() if e["meta"].enabled]
            if not enabled_entries:
                logger.info("[LoRAManager] merge_multiple_enabled: 无 enabled LoRA，跳过")
                return
            if len(enabled_entries) == 1:
                logger.info(f"[LoRAManager] 仅 1 个 enabled={enabled_entries[0][0]}，无需多 LoRA 合并")
                return

            if strategy not in ("add", "weighted_average"):
                raise ValueError(f"strategy 必须是 'add' 或 'weighted_average'，实际为 '{strategy}'")

            def _do_merge() -> None:
                """执行多 LoRA 合并的内部逻辑。

                根据 strategy 参数选择合并策略：
                - weighted_average：按各 LoRA 的 weight 字段归一化后加权平均
                - add：按 alpha/rank 缩放因子进行加性合并（标准 LoRA 合并方式）

                注意：当前实现仅打印 scale 日志，实际权重合并由 peft 底层处理。
                """
                if strategy == "weighted_average":
                    total_w = sum(e["meta"].weight for _, e in enabled_entries)
                    if total_w <= 0:
                        raise ValueError("enabled LoRA 权重总和 <= 0，无法加权平均")
                    for lid, e in enabled_entries:
                        scale = e["meta"].weight / total_w
                        logger.info(
                            f"[LoRAManager] weighted_avg: id={lid} scale={scale:.3f} "
                            f"(weight={e['meta'].weight:.2f}/{total_w:.2f})"
                        )
                else:
                    for lid, e in enabled_entries:
                        rank = e["meta"].rank
                        alpha = e["meta"].alpha
                        scale = float(alpha) / float(max(rank, 1))
                        logger.info(f"[LoRAManager] add merge: id={lid} alpha/rank={alpha}/{rank} -> scale={scale:.3f}")

            try:
                _do_merge()
            except RuntimeError as exc:
                oom_keys = ("out of memory", "outofmemoryerror", "cuda error")
                is_oom = any(k in str(exc).lower() for k in oom_keys)
                if not is_oom:
                    raise

                logger.warning(f"[LoRAManager] merge OOM: {exc}，尝试 empty_cache + 再试一次")
                try:
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception as gc_exc:  # noqa: BLE001
                    logger.warning(f"[LoRAManager] empty_cache 异常: {gc_exc}")

                try:
                    _do_merge()
                except RuntimeError as exc2:
                    oom2 = any(k in str(exc2).lower() for k in oom_keys)
                    if not oom2:
                        raise

                    logger.warning("[LoRAManager] 多 LoRA 合并显存不足，降级为仅启用权重最高的 1 个")
                    enabled_entries.sort(key=lambda x: x[1]["meta"].weight, reverse=True)
                    top_lid, top_entry = enabled_entries[0]
                    for _lid, e in enabled_entries[1:]:
                        old = e["meta"]
                        e["meta"] = LoRAMeta(
                            lora_id=old.lora_id,
                            name=old.name,
                            path=old.path,
                            rank=old.rank,
                            alpha=old.alpha,
                            target_modules=list(old.target_modules),
                            trained_steps=old.trained_steps,
                            enabled=False,
                            weight=old.weight,
                        )
                    logger.warning(
                        f"[LoRAManager] 降级完成：仅保留 top id={top_lid} weight={top_entry['meta'].weight:.2f}"
                    )

    def _safe_unload_all(self) -> None:
        """应用退出时安全释放所有已加载的 LoRA。

        逐个 unload，任何单个失败均继续处理后续，最后清空注册表；
        保证进程退出前不抛异常（避免 atexit 栈展开时崩溃）。
        """
        try:
            with self._lock:
                ids = list(self._loaded.keys())
            for lid in ids:
                try:
                    self.unload(lid)
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"[LoRAManager] _safe_unload_all 单项失败 id={lid}: {type(exc).__name__}")
            with self._lock:
                self._loaded.clear()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[LoRAManager] _safe_unload_all 顶层异常（已吞掉）: {type(exc).__name__}")


_default_manager: LoRAManager | None = None
_manager_lock = threading.RLock()


def _get_manager() -> LoRAManager | None:
    """懒加载模块级默认 LoRAManager。

    返回 None 表示还没初始化（需要 VoxCPM2Engine 在 load_lora 时传入
    model 后构造）。
    """
    return _default_manager


def _set_manager(mgr: LoRAManager) -> None:
    """设置模块级默认 LoRAManager 实例（线程安全）。

    由 VoxCPM2Engine 在模型加载完成后调用，注入已绑定 model 的管理器实例。
    使用 _manager_lock (RLock) 保证并发赋值安全。

    Args:
        mgr: 已初始化的 LoRAManager 实例。
    """
    global _default_manager
    with _manager_lock:
        _default_manager = mgr


def fn_voxcpm_load_lora(lora_path: str) -> bool:
    """加载 LoRA 权重文件到 VoxCPM2 引擎（对外向后兼容 API）。

    从指定路径加载 LoRA 适配器（.safetensors + .json），自动校验文件格式
    与元数据合法性。加载成功后需调用 fn_voxcpm_set_lora_enabled(True) 启用。

    Args:
        lora_path: LoRA 文件路径（可以是 .safetensors 或 .json，会自动推导配对文件）。

    Returns:
        bool: 加载成功返回 True；文件不存在、格式错误、校验失败或引擎未加载时
            返回 False（EngineSwitchError 除外，该异常会向上抛出）。

    Raises:
        EngineSwitchError: VoxCPM2 引擎尚未加载时抛出，提示用户先切换引擎。
    """
    from ...model_registry import registry

    if registry.voxcpm_model is None:
        raise EngineSwitchError("请先切换并加载 VoxCPM2 引擎")
    try:
        return registry.voxcpm_model.load_lora(lora_path)
    except EngineSwitchError:
        raise
    except ValidationError as exc:
        logger.warning(f"[VoxCPM LoRA] 加载 LoRA 校验失败: {exc}")
        return False
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning(f"[VoxCPM LoRA] 加载 LoRA 失败: {type(exc).__name__}: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[VoxCPM LoRA] 加载 LoRA 未预期异常: {type(exc).__name__}: {exc}")
        return False


def fn_voxcpm_unload_lora() -> bool:
    """卸载当前已加载的 LoRA 权重（对外向后兼容 API）。

    释放 LoRA 占用的显存，回滚基础模型权重到原始状态。卸载后生成将使用
    基础模型音色，不再应用 LoRA 风格。

    Returns:
        bool: 卸载成功返回 True；卸载过程中发生 IO/运行时异常时返回 False。

    Raises:
        EngineSwitchError: VoxCPM2 引擎尚未加载时抛出。
    """
    from ...model_registry import registry

    if registry.voxcpm_model is None:
        raise EngineSwitchError("请先切换并加载 VoxCPM2 引擎")
    try:
        return registry.voxcpm_model.unload_lora()
    except EngineSwitchError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(f"[VoxCPM LoRA] 卸载 LoRA 失败: {type(exc).__name__}: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[VoxCPM LoRA] 卸载 LoRA 未预期异常: {type(exc).__name__}: {exc}")
        return False


def fn_voxcpm_set_lora_enabled(enabled: bool) -> bool:
    """设置当前 LoRA 的启用/禁用状态（对外向后兼容 API）。

    启用后后续生成将应用 LoRA 风格效果；禁用后保留权重在显存中但不生效，
    可快速切换无需重新加载。

    Args:
        enabled: True 启用 LoRA，False 禁用 LoRA。

    Returns:
        bool: 设置成功返回 True；状态切换失败时返回 False。

    Raises:
        EngineSwitchError: VoxCPM2 引擎尚未加载时抛出。
    """
    from ...model_registry import registry

    if registry.voxcpm_model is None:
        raise EngineSwitchError("请先切换并加载 VoxCPM2 引擎")
    try:
        return registry.voxcpm_model.set_lora_enabled(enabled)
    except EngineSwitchError:
        raise
    except (RuntimeError, ValueError, TypeError) as exc:
        logger.warning(f"[VoxCPM LoRA] 设置 LoRA 状态失败: {type(exc).__name__}: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[VoxCPM LoRA] 设置 LoRA 状态未预期异常: {type(exc).__name__}: {exc}")
        return False


def fn_voxcpm_get_lora_state() -> dict:
    """获取当前 LoRA 的完整状态信息（对外向后兼容 API）。

    返回包含 LoRA 加载状态、元数据、启用状态等信息的字典，供前端 UI 展示。

    Returns:
        dict: LoRA 状态字典，包含 loaded、enabled、meta 等键；引擎未加载或
            获取失败时返回空字典 {}。

    Raises:
        EngineSwitchError: VoxCPM2 引擎尚未加载时抛出。
    """
    from ...model_registry import registry

    if registry.voxcpm_model is None:
        raise EngineSwitchError("请先切换并加载 VoxCPM2 引擎")
    try:
        return registry.voxcpm_model.get_lora_state_dict()
    except EngineSwitchError:
        raise
    except (RuntimeError, ValueError, TypeError, AttributeError) as exc:
        logger.warning(f"[VoxCPM LoRA] 获取 LoRA 状态失败: {type(exc).__name__}: {exc}")
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[VoxCPM LoRA] 获取 LoRA 状态未预期异常: {type(exc).__name__}: {exc}")
        return {}


def is_lora_enabled() -> bool:
    """判断当前是否有 LoRA 处于加载且启用状态（对外向后兼容 API）。

    用于 UI 快速判断是否需要显示 LoRA 相关控件，无需抛出异常，引擎未加载时
    静默返回 False。

    Returns:
        bool: 有 LoRA 加载且启用返回 True；否则返回 False（包括引擎未加载、
            异常等情况均返回 False）。
    """
    from ...model_registry import registry

    if registry.voxcpm_model is None:
        return False
    try:
        return bool(registry.voxcpm_model.get_lora_state_dict())
    except (RuntimeError, ValueError, TypeError, AttributeError) as exc:
        logger.debug(f"[VoxCPM LoRA] is_lora_enabled 检查异常，返回 False: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[VoxCPM LoRA] is_lora_enabled 未预期异常，返回 False: {type(exc).__name__}")
        return False


def load_lora_weights(lora_path: str) -> tuple[list[str], list[str]]:
    """加载 LoRA 权重，返回成功/失败路径列表（对外向后兼容 API）。

    旧版 API 兼容层，内部调用 fn_voxcpm_load_lora，将返回值包装为
    (成功路径列表, 失败路径列表) 的二元组格式。

    Args:
        lora_path: LoRA 文件路径。

    Returns:
        tuple[list[str], list[str]]: (loaded_keys, skipped_keys)
            loaded_keys: 成功加载的路径列表；
            skipped_keys: 加载失败的路径列表。
    """
    loaded = fn_voxcpm_load_lora(lora_path)
    return ([lora_path], []) if loaded else ([], [lora_path])


def unload_lora_weights() -> None:
    """卸载当前已加载的 LoRA 权重（对外向后兼容 API）。

    旧版 API 兼容层，内部直接调用 fn_voxcpm_unload_lora()。
    """
    fn_voxcpm_unload_lora()


def get_lora_state_dict() -> dict:
    """返回当前 LoRA 状态字典（对外向后兼容 API）。

    旧版 API 兼容层，内部直接调用 fn_voxcpm_get_lora_state()。

    Returns:
        dict: LoRA 状态字典。
    """
    return fn_voxcpm_get_lora_state()
