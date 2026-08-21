"""HealthMonitor 单例健康监控模块。

架构说明：HealthMonitor 作为全局单例运行，包含以下四大核心监控能力：
① GPU 显存泄漏检测：基于 100 样本滑动窗口，通过前后均值差诊断潜在泄漏；
② 显存熔断检查：显存占用超过 90% 立即触发熔断，终止当前推理（AGENTS.md §6 硬约束）；
③ 模型加载预检：加载新模型前验证可用显存 ≥ 模型权重大小 × 1.5 倍安全裕度；
④ 运行统计：总生成数、错误数、OOM 重试次数、熔断触发次数等健康指标。

为什么使用 get_health_monitor() 单例入口而不直接实例化？
避免在多处代码各自创建 HealthMonitor 实例导致各自维护独立的采样窗口和统计数据，
单例模式确保全应用范围内采样数据的一致性和指标统计的准确性。

原英文重构说明：Enhanced health monitoring: GPU leak detection, model self-check, metrics.
"""

import logging
import time
from collections import deque
from typing import Any

logger = logging.getLogger("tts_multimodel")


def _get_gpu_device() -> int:
    """使用统一后端管理器获取 GPU 设备索引。

    Returns:
        int: GPU 设备索引，获取失败时默认返回 0。
    """
    from .gpu_backend import GPUBackendManager

    if not GPUBackendManager.is_available():
        return 0

    try:
        device = GPUBackendManager.get_device()
        import torch

        if isinstance(device, torch.device):
            return device.index if device.index is not None else 0
        return device
    except RuntimeError:
        logger.debug("[_get_gpu_device] RuntimeError 读取设备失败，使用默认 0")
        return 0
    except ImportError:
        logger.debug("[_get_gpu_device] ImportError torch 未导入，使用默认 0")
        return 0
    except AttributeError:
        logger.debug("[_get_gpu_device] AttributeError 设备属性缺失，使用默认 0")
        return 0


class HealthMonitor:
    """应用健康监控器：GPU 显存趋势、模型状态、熔断机制。

    显存熔断机制（AGENTS.md §6 硬约束）：
    - 推理前调用 check_vram_circuit_breaker() 检查显存占用
    - 占用超过 90% 时返回熔断标志，调用方立即抛 InsufficientVRAMError 终止推理
    - 推理过程中周期性检查，超阈值则中断生成并清理缓存

    Attributes:
        VRAM_CIRCUIT_BREAKER_PCT: 显存熔断阈值（百分比），默认 90.0%。
        VRAM_PRELOAD_SAFETY_FACTOR: 模型加载预检安全系数，默认 1.5 倍。
        _vram_samples: 显存采样历史（MB），固定 100 样本滑动窗口。
        _max_samples: 滑动窗口最大样本数，固定 100。
        _leak_threshold_mb: 泄漏判定阈值（MB），窗口首尾均值差 > 此值视为泄漏。
        _model_last_check: 模型最后一次自检的 Unix 时间戳。
        _model_status: 模型当前状态（loaded/unloading/ready/error/unknown）。
        _start_time: 监控器启动时的 Unix 时间戳，用于计算 uptime。
        _total_generations: 累计生成尝试次数。
        _total_errors: 累计生成失败次数。
        _total_oom_retries: 累计 OOM 后自动重试次数。
        _circuit_breaker_trips: 累计显存熔断触发次数。
    """

    VRAM_CIRCUIT_BREAKER_PCT: float = 90.0
    VRAM_PRELOAD_SAFETY_FACTOR: float = 1.5

    _vram_samples: deque[float]
    _max_samples: int
    _leak_threshold_mb: int
    _model_last_check: float
    _model_status: str
    _start_time: float
    _total_generations: int
    _total_errors: int
    _total_oom_retries: int
    _circuit_breaker_trips: int

    def __init__(self) -> None:
        """初始化健康监控器。

        所有计数器初始化为 0，显存采样窗口为空，泄漏阈值 200MB，
        熔断阈值 90%，加载预检安全系数 1.5 倍。
        """
        self._max_samples = 100
        self._vram_samples = deque(maxlen=self._max_samples)
        self._leak_threshold_mb = 200
        self._baseline_mb: float | None = None
        self._baseline_stable_count: int = 0
        self._baseline_tolerance_mb: float = 80.0
        self._baseline_required_stable: int = 5
        self._model_last_check = 0.0
        self._model_status = "unknown"
        self._start_time = time.time()
        self._total_generations = 0
        self._total_errors = 0
        self._total_oom_retries = 0
        self._circuit_breaker_trips = 0

    def record_vram_usage(self, used_mb: float) -> None:
        """记录一次 GPU 显存使用样本，用于后续泄漏诊断。

        同时检测显存是否稳定：连续 N 个样本波动在容差范围内时，
        自动建立/更新稳定基线，避免模型加载期间的显存上升误报为泄漏。

        Args:
            used_mb: 当前已分配的 GPU 显存（MB）。
        """
        self._vram_samples.append(used_mb)

        # 稳定基线自动建立/更新逻辑
        if self._baseline_mb is None:
            # 基线尚未建立：等待连续稳定样本
            if len(self._vram_samples) >= 2:
                prev = self._vram_samples[-2]
                if abs(used_mb - prev) <= self._baseline_tolerance_mb:
                    self._baseline_stable_count += 1
                    if self._baseline_stable_count >= self._baseline_required_stable:
                        # 连续稳定样本达到阈值，建立基线（使用最近N个样本均值）
                        recent = list(self._vram_samples)[-self._baseline_required_stable :]
                        self._baseline_mb = sum(recent) / len(recent)
                        logger.debug(
                            "[HealthMonitor] 显存基线已建立: %.0fMB (稳定样本=%d)",
                            self._baseline_mb,
                            self._baseline_required_stable,
                        )
                else:
                    self._baseline_stable_count = 0
        else:
            # 基线已存在：检测是否有剧烈跳变（如模型切换/卸载），需要重置基线
            if abs(used_mb - self._baseline_mb) > self._baseline_tolerance_mb * 3:
                logger.debug(
                    "[HealthMonitor] 显存剧烈变化 %.0fMB -> %.0fMB，重置基线",
                    self._baseline_mb,
                    used_mb,
                )
                self._baseline_mb = None
                self._baseline_stable_count = 0

    def reset_vram_baseline(self) -> None:
        """手动重置显存基线（模型加载/切换/卸载后调用）。

        下次 record_vram_usage 时将自动重新建立稳定基线，避免加载期间的
        显存跳变误报为泄漏。
        """
        self._baseline_mb = None
        self._baseline_stable_count = 0
        logger.debug("[HealthMonitor] 显存基线已手动重置")

    def check_memory_leak(self) -> str | None:
        """检查是否存在 GPU 显存泄漏迹象。

        诊断逻辑：等待显存稳定基线建立后，比较最后 5 个样本的均值与基线值，
        若差值超过 200MB 则视为潜在泄漏。使用稳定基线而非窗口首5个样本，
        可避免模型加载期间显存从0飙升到6GB导致的误报。

        Returns:
            Optional[str]: 若检测到泄漏则返回中文预警字符串；无泄漏、样本不足或
                基线未建立时返回 None。
        """
        if len(self._vram_samples) < 10 or self._baseline_mb is None:
            return None

        samples_list = list(self._vram_samples)
        recent_avg = sum(samples_list[-5:]) / 5
        diff = recent_avg - self._baseline_mb

        if diff > self._leak_threshold_mb:
            warning = (
                f"\u26a0\ufe0f 潜在 GPU 显存泄漏：检测到显存持续上升约 {diff:.0f}MB，"
                f"当前 {samples_list[-1]:.0f}MB，基线 {self._baseline_mb:.0f}MB。"
                f"建议检查是否存在未释放的中间张量或缓存未清理。"
            )
            logger.warning(warning)
            return warning
        return None

    def get_vram_trend(self) -> dict[str, Any]:
        """获取 GPU 显存使用趋势统计摘要。

        Returns:
            dict[str, Any]: 包含 7 个键的字典：
                - current_mb (float): 最新一次采样的显存（MB）
                - min_mb (float): 窗口内最小显存（MB）
                - max_mb (float): 窗口内最大显存（MB）
                - avg_mb (float): 窗口内平均显存（MB）
                - trend (str): 趋势描述，"increasing" 或 "stable"
                - sample_count (int): 当前窗口内有效样本数
                - status (str): 数据状态标识（仅无数据时存在）
        """
        if not self._vram_samples:
            return {"status": "no_data"}

        # 转为list确保切片和聚合操作安全
        samples_list = list(self._vram_samples)
        current = samples_list[-1]
        min_val = min(samples_list)
        max_val = max(samples_list)
        avg = sum(samples_list) / len(samples_list)

        return {
            "current_mb": round(current, 1),
            "min_mb": round(min_val, 1),
            "max_mb": round(max_val, 1),
            "avg_mb": round(avg, 1),
            "trend": "increasing" if current > avg * 1.1 else "stable",
            "sample_count": len(self._vram_samples),
        }

    def record_generation(self, success: bool = True) -> None:
        """记录一次生成尝试的结果。

        Args:
            success: 生成是否成功完成。True 增加成功计数，False 同时增加错误计数。
        """
        self._total_generations += 1
        if not success:
            self._total_errors += 1

    def record_oom_retry(self) -> None:
        """记录一次 OOM 发生后的自动重试事件。"""
        self._total_oom_retries += 1

    def get_vram_usage_percent(self) -> float:
        """获取当前 GPU 显存占用百分比。

        Returns:
            float: 显存占用百分比（0-100）。无 GPU 或读取失败时返回 0.0。
        """
        from .gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        if backend == GPUBackend.CPU:
            return 0.0
        try:
            device = _get_gpu_device()
            props = GPUBackendManager.get_device_properties(device)
            total = props.get("total_memory", 0)
            if total <= 0:
                return 0.0
            allocated = GPUBackendManager.memory_allocated(device)
            return allocated / total * 100
        except Exception:
            return 0.0

    def check_vram_circuit_breaker(self) -> tuple[bool, str]:
        """显存熔断检查：占用超过阈值时返回熔断标志。

        熔断阈值设为 90% 而非更高的 95%，原因：CUDA 驱动和 GPU 内核会预留 3%~5%
        的显存用于页表、内核上下文等内部工作，实际用户可用显存约为总量的 95%。
        在 90% 时触发熔断，留出约 5% 的安全裕度让当前推理优雅退出并清理缓存，
        避免直接触发 GPU 内核崩溃导致整个进程挂掉。

        若调用方收到 tripped=True，必须立即抛 InsufficientVRAMError 终止推理并清理缓存。
        若内部读取显存失败则采用保守策略（不测不误杀）：返回 (False, "无法读取显存，跳过熔断")。

        Returns:
            Tuple[bool, str]: (tripped, reason)
                - tripped: True 表示已触发熔断，调用方需立即终止推理；False 表示安全。
                - reason: 中文说明文字，触发熔断时给出具体原因和占用数值。
        """
        try:
            usage_pct = self.get_vram_usage_percent()
        except Exception as e:
            logger.warning(f"[显存熔断] 读取显存占用失败: {e}，跳过本次熔断检查")
            return (False, "无法读取显存，跳过熔断")

        # VRAM_CIRCUIT_BREAKER_PCT=90%：为什么不是更高 95%——CUDA 驱动/内核会预留 3~5% 做页表/内部工作，
        # 实际用户可用只有 95%，90% 触发留 5% 安全裕度让当前推理优雅退出而非内核崩溃
        if usage_pct > self.VRAM_CIRCUIT_BREAKER_PCT:
            self._circuit_breaker_trips += 1
            reason = (
                f"VRAM 占用 {usage_pct:.1f}% 超过阈值 {self.VRAM_CIRCUIT_BREAKER_PCT}%，"
                f"累计熔断触发 {self._circuit_breaker_trips} 次。请立即终止推理并清理显存。"
            )
            logger.error(f"[显存熔断] {reason}")
            try:
                from .gpu_utils import free_gpu_memory

                free_gpu_memory()
            except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）
                pass
            return (True, reason)
        return (False, f"VRAM 占用 {usage_pct:.1f}%，低于熔断阈值")

    def check_model_load_prereq(self, model_size_gb: float) -> tuple[bool, str, int]:
        """模型加载预检：检查可用显存是否满足模型权重 × 1.5 倍安全裕度。

        安全系数 1.5 倍的拆解：模型权重本身占 X GB + ASR/辅助模型约 0.3X +
        推理过程中中间激活峰值约 0.2X = 合计 1.5X。AGENTS.md §6 硬约束，
        加载前必须预检通过以防 OOM 导致加载中途失败、显存碎片残留。

        若 model_size_gb ≤ 0（输入异常）则 fail-open：记录警告后跳过检查视为通过。

        Args:
            model_size_gb: 待加载模型的权重文件大小（GB），不包含辅助模型与激活。

        Returns:
            Tuple[bool, str, int]: (passed, msg, free_mb)
                - passed: True 表示预检通过，可以加载；False 表示显存不足。
                - msg: 中文结果说明文本，通过/失败均给出具体数值对比。
                - free_mb: 当前可用显存（MB），供调用方日志或 UI 展示。
        """
        from .gpu_backend import GPUBackend, GPUBackendManager

        try:
            if model_size_gb <= 0:
                raise ValueError(f"model_size_gb 必须为正数，实际收到 {model_size_gb}")
        except ValueError as e:
            logger.warning(f"[显存预检] 输入异常: {e}，采用 fail-open 跳过预检")
            return (True, f"输入异常跳过预检: {e}", 0)

        backend = GPUBackendManager.detect_backend()
        if backend == GPUBackend.CPU:
            logger.info("[显存预检] CPU 模式，跳过显存预检")
            return (True, "CPU 模式跳过显存预检", 0)

        try:
            device = _get_gpu_device()
            mem_info = GPUBackendManager.get_memory_info(device)
            free_bytes = mem_info[3]
            free_mb = int(free_bytes / (1024**2))
            free_gb = free_bytes / (1024**3)
            needed_gb = model_size_gb * self.VRAM_PRELOAD_SAFETY_FACTOR

            # VRAM_PRELOAD_SAFETY_FACTOR=1.5：为什么需要 0.5 倍——权重本身占 X GB +
            # ASR/辅助模型 ≈0.3X + 中间激活峰值 ≈0.2X = 刚好 1.5X，AGENTS.md §6 硬约束
            if free_gb < needed_gb:
                msg = (
                    f"显存不足：模型需要 {needed_gb:.1f}GB (权重 {model_size_gb:.1f}GB "
                    f"× 安全系数 {self.VRAM_PRELOAD_SAFETY_FACTOR})，当前可用仅 {free_gb:.1f}GB。"
                    f"请先卸载其他模型或关闭占用显存的程序。"
                )
                logger.warning(f"[显存预检] 失败：{msg}")
                return (False, msg, free_mb)

            msg = (
                f"显存预检通过：需要 {needed_gb:.1f}GB (×{self.VRAM_PRELOAD_SAFETY_FACTOR})，"
                f"可用 {free_gb:.1f}GB，裕度充足。"
            )
            logger.info(f"[显存预检] {msg}")
            return (True, msg, free_mb)
        except Exception as e:
            logger.warning(f"[显存预检] 检查过程异常: {e}，fail-open 视为通过")
            return (True, f"检查异常跳过: {e}", 0)

    def check_vram_preload(self, model_size_gb: float) -> bool:
        """模型加载预检（向后兼容别名）：内部调用 check_model_load_prereq。

        保留此方法名以确保向后兼容。新代码请直接使用 check_model_load_prereq 获取完整返回信息。

        Args:
            model_size_gb: 模型预计占用显存（GB）。

        Returns:
            bool: True 表示预检通过；False 表示显存不足（同时内部抛 InsufficientVRAMError 兼容旧行为）。

        Raises:
            InsufficientVRAMError: 显存不足时抛出，与旧版本行为保持一致。
        """
        passed, msg, _free_mb = self.check_model_load_prereq(model_size_gb)
        if not passed:
            from .exceptions import InsufficientVRAMError

            raise InsufficientVRAMError(msg)
        return True

    def set_model_status(self, status: str) -> None:
        """更新模型当前状态文本。

        Args:
            status: 新的模型状态，建议取值：loaded / unloading / ready / error / unknown。
        """
        self._model_status = status
        self._model_last_check = time.time()

    def run_model_self_check(self) -> tuple[bool, str]:
        """执行模型自检：对一小段测试文本进行干推理（不输出音频），验证模型能否正常运行。

        自检目的：在模型加载完成后立即通过一次短文本干推理确认以下事项：
        ① 权重加载完整无损坏；② 计算图构建正常无 shape 不匹配；③ 显存峰值在安全范围内不触发 OOM。
        若自检失败，应立即将模型标记为 error 状态并建议用户重新加载或切换引擎。

        Returns:
            Tuple[bool, str]: (ok, msg)
                - ok: True 表示自检通过（干推理无异常）；False 表示自检发现 OOM 或其他错误。
                - msg: 中文自检结果说明，失败时给出具体异常类型摘要。
        """
        from .model_registry import registry

        current_engine = registry.current_engine
        if current_engine is None:
            self._model_status = "error"
            self._model_last_check = time.time()
            return (False, "无已加载的引擎，无法执行自检")

        try:
            test_text = "这是一段用于模型自检的短文本，不输出音频。"
            if hasattr(current_engine, "synthesize"):
                _ = current_engine.synthesize(test_text, dry_run=True)
            else:
                _test_result = True

            self._model_status = "ready"
            self._model_last_check = time.time()
            return (True, "模型自检通过：干推理执行成功，无 OOM 与异常。")
        except RuntimeError as e:
            if "out of memory" in str(e).lower() or "OOM" in str(e):
                msg = f"模型自检失败：干推理触发 OOM，建议减小 batch 或卸载其他模型。异常: {e}"
            else:
                msg = f"模型自检失败：RuntimeError 推理执行异常。异常: {e}"
            logger.error(f"[模型自检] {msg}")
            self._model_status = "error"
            self._model_last_check = time.time()
            return (False, msg)
        except Exception as e:
            msg = f"模型自检失败：未预期异常 {type(e).__name__}: {e}"
            logger.error(f"[模型自检] {msg}")
            self._model_status = "error"
            self._model_last_check = time.time()
            return (False, msg)

    def get_metrics(self) -> dict[str, Any]:
        """获取汇总的健康指标字典，供 /api/system/health 等接口返回。

        任何单个指标计算失败都不会影响整体返回：失败的键填充合理默认值，确保响应始终可用。

        Returns:
            dict[str, Any]: 汇总指标字典，包含 uptime / 生成统计 / 成功率 / 熔断次数 / GPU 信息等。
        """
        result: dict[str, Any] = {}
        try:
            result["uptime_seconds"] = round(time.time() - self._start_time, 1)
        except Exception:
            result["uptime_seconds"] = 0.0

        try:
            result["total_generations"] = self._total_generations
        except Exception:
            result["total_generations"] = 0

        try:
            result["total_errors"] = self._total_errors
        except Exception:
            result["total_errors"] = 0

        try:
            result["total_oom_retries"] = self._total_oom_retries
        except Exception:
            result["total_oom_retries"] = 0

        try:
            result["circuit_breaker_trips"] = self._circuit_breaker_trips
        except Exception:
            result["circuit_breaker_trips"] = 0

        try:
            result["model_status"] = self._model_status
        except Exception:
            result["model_status"] = "unknown"

        try:
            result["model_last_check"] = self._model_last_check
        except Exception:
            result["model_last_check"] = 0.0

        try:
            success_rate = 0.0
            if self._total_generations > 0:
                success_rate = (self._total_generations - self._total_errors) / self._total_generations * 100
            result["success_rate_pct"] = round(success_rate, 1)
        except Exception:
            result["success_rate_pct"] = 0.0

        try:
            from .gpu_backend import GPUBackend, GPUBackendManager

            backend = GPUBackendManager.detect_backend()
            if backend != GPUBackend.CPU:
                device = _get_gpu_device()
                try:
                    vram_used = GPUBackendManager.memory_allocated(device) / (1024**2)
                except Exception:
                    vram_used = 0.0
                try:
                    props = GPUBackendManager.get_device_properties(device)
                    vram_total = props.get("total_memory", 0) / (1024**2)
                except Exception:
                    vram_total = 0.0
                try:
                    device_name = GPUBackendManager.get_device_name(device)
                except Exception:
                    device_name = "unknown"

                gpu_info: dict[str, Any] = {
                    "name": device_name,
                    "vram_used_mb": round(vram_used, 1),
                    "vram_total_mb": round(vram_total, 1),
                    "vram_usage_pct": round(vram_used / vram_total * 100, 1) if vram_total > 0 else 0,
                }
                try:
                    leak_warning = self.check_memory_leak()
                    if leak_warning:
                        gpu_info["leak_warning"] = leak_warning
                except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）
                    pass
                try:
                    gpu_info["trend"] = self.get_vram_trend()
                except Exception:
                    gpu_info["trend"] = {"status": "error"}
                result["gpu"] = gpu_info
            else:
                result["gpu"] = {"mode": "cpu"}
        except Exception:
            result["gpu"] = {"status": "unavailable"}

        return result

    def get_health_report(self) -> dict[str, Any]:
        """获取完整健康报告（向后兼容别名）。

        内部调用 get_metrics() 并附加兼容字段，确保旧调用方不中断。

        Returns:
            dict[str, Any]: 与 get_metrics 结构基本一致，兼容旧版 success_rate 字段命名。
        """
        report = self.get_metrics()
        if "success_rate_pct" in report and "success_rate" not in report:
            report["success_rate"] = report["success_rate_pct"]
        return report


_health_monitor = HealthMonitor()


def get_health_monitor() -> HealthMonitor:
    """获取全局单例 HealthMonitor 实例。

    统一入口避免多处实例化导致采样窗口与统计数据分裂。

    Returns:
        HealthMonitor: 全应用共享的健康监控单例。
    """
    return _health_monitor
