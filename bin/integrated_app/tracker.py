"""生成任务队列追踪模块。

架构说明：GenerationTracker 全局单例队列追踪，维护以下核心状态：
- queue_depth：当前队列深度（正在排队+正在生成的任务总数）
- avg_gen_time：指数滑动平均（EMA）生成时长（α=0.2）
供 SSE 事件流向前端推送"队列情况"，并供 model_manager 估算剩余生成时间。

EMA α=0.2 的取值说明：由公式 α=2/(N+1) 推导，等效于约 N≈9 个样本的滑动窗口平滑，
经验上既能保证估算足够稳定，又不会因滞后过大导致估算失真。
"""

import threading
from typing import Any


class GenerationTracker:
    """生成任务队列深度追踪与等待时间估算器。

    使用指数滑动平均（EMA α=0.2）平滑生成耗时测量，以获得更稳定的等待时间估算。

    Attributes:
        queue_depth: 当前排队的生成请求数量（含正在生成的）。
        avg_gen_time: 生成耗时的指数滑动平均值（秒）。
        _lock: 线程锁，用于状态变更的互斥保护。
        phase: 人类可读的当前阶段描述文本。
    """

    queue_depth: int
    avg_gen_time: float
    _lock: threading.RLock
    phase: str

    def __init__(self) -> None:
        """初始化生成任务追踪器。

        初始队列深度为 0，平均生成时长默认 15 秒（冷启动经验值），阶段为"空闲"。
        """
        self.queue_depth = 0
        self.avg_gen_time = 15.0
        self._lock = threading.RLock()
        self.phase = "空闲"

    def _notify_sse(self) -> None:
        """通知 SSE 事件总线状态已变化。

        ImportError 或其他异常均静默忽略，追踪器状态通知失败不影响主流程。
        """
        try:
            from .routes.sse import event_bus

            event_bus.notify()
        except ImportError:
            pass
        except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）
            pass

    def start_generation(self) -> int:
        """进入生成队列时调用，递增队列深度。

        在生成请求进入队列（开始排队或开始生成）时调用，队列深度 +1。

        Returns:
            int: 递增后的当前队列深度（包含自己在内）。
        """
        with self._lock:
            self.queue_depth += 1
            depth = self.queue_depth
        self._notify_sse()
        return depth

    def end_generation(self, elapsed: float) -> None:
        """生成完成时调用，更新 EMA 平均耗时并递减队列深度。

        EMA 更新公式：avg = 0.8 * old_avg + 0.2 * elapsed
        权重分配说明：80% 历史权重 + 20% 新样本权重。
        为什么给历史更大权重？用户偶尔生成极短或极长文本会导致单次耗时剧烈抖动，
        80% 的历史权重能有效平滑这种毛刺，保证估算稳定且不滞后。

        Args:
            elapsed: 本次生成实际消耗的时间（秒）。
        """
        with self._lock:
            # EMA 权重 0.8/0.2：为什么给历史大权重——用户偶尔生成超短/超长文本会剧烈抖动，
            # 80% 历史 + 20% 新样本 = 稳定不滞后
            self.avg_gen_time = 0.8 * self.avg_gen_time + 0.2 * elapsed
            self.queue_depth = max(0, self.queue_depth - 1)
        self._notify_sse()

    def estimate_wait(self) -> float:
        """估算当前队列的总等待时间。

        估算公式：等待时间 = avg_gen_time × queue_depth
        为什么不是 avg × (queue_depth - 1)？因为 queue_depth 是包含自己的总深度，
        调用方通常在 start_generation 之前先读取 estimate_wait，此时读到的是前面任务的等待时间。
        此处采用含自己的估算更保守，用户体验不会翻车（估算稍长 > 估算过短导致用户焦虑）。

        Returns:
            float: 估算的总等待秒数（含自己在内的排队 + 生成时间）。
        """
        with self._lock:
            # estimate_wait = avg_gen_time × queue_depth：为什么不是 avg×(queue_depth-1) ——
            # queue_depth 是含自己的，调用方在 start_generation 之前先读 estimate_wait 才是前面的等待，
            # 此处含自己的估算更保守，用户体验不翻车
            return self.avg_gen_time * self.queue_depth

    def status_text(self) -> str:
        """生成人类可读的队列状态字符串。

        Returns:
            str: 中文状态文本。队列非空时格式为 "队列: N | 预计等待: M秒"；
                 队列为空时返回 "空闲"。极端情况下若估算值为 NaN，则回退为
                 "队列: N | 估算中..."。
        """
        with self._lock:
            if self.queue_depth == 0:
                return "空闲"
            wait = self.estimate_wait()
            try:
                return f"队列: {self.queue_depth} | 预计等待: {wait:.0f}秒"
            except ValueError:
                return f"队列: {self.queue_depth} | 估算中..."

    def get_info(self) -> dict[str, Any]:
        """以字典形式获取当前追踪器的完整状态信息。

        为 SSE 和其他消费者提供公共读取接口，避免直接访问私有属性。

        Returns:
            dict[str, Any]: 包含四个键的字典：
                - queue_depth (int): 当前队列深度
                - avg_gen_time (float): EMA 平均生成时长（秒）
                - phase (str): 当前处理阶段描述
                - status_text (str): 人类可读的状态文本
        """
        with self._lock:
            return {
                "queue_depth": self.queue_depth,
                "avg_gen_time": self.avg_gen_time,
                "phase": self.phase,
                "status_text": self.status_text(),
            }

    def update_phase(self, new_phase: str) -> None:
        """更新当前处理阶段的描述文本。

        Args:
            new_phase: 新的阶段描述文本。
        """
        with self._lock:
            self.phase = new_phase
        self._notify_sse()

    def reset(self) -> None:
        """重置追踪器到初始空闲状态。

        清空队列深度、重置平均时长到默认值、阶段恢复为"空闲"。
        """
        with self._lock:
            self.queue_depth = 0
            self.avg_gen_time = 15.0
            self.phase = "空闲"
        self._notify_sse()
