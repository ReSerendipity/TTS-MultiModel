"""Progress tracking module.

Manages generation progress tracking and renders HTML progress bars
for frontend rendering via HTMX partial updates.

中文架构说明：
    本模块通过 ProgressManager 单例实现生成进度追踪：
    - 段级完成计数（advance_segment 每完成一段 +1）→ 百分比估算
    - 输出 HTMX 局部 HTML 更新片段（替换 #progress-container 元素 innerHTML）
    - 同时通过 SSE 事件总线（/api/sse/events）推送状态变化，前端无需轮询

两种进度模式：
    1. 单段动画模式（total_segments == 1）：基于耗时做 5% → 95% 线性动画，
       适合无显式分段的短任务，避免进度卡死在 0。
    2. 多段显式计数模式（total_segments > 1）：advance_segment 每段 +1，
       百分比 = _current_segment / _total_segments，附段信息与 ETA。
"""

import logging
import threading
import time
from typing import Any


class ProgressManager:
    """Manages generation progress tracking and renders HTML progress bars.

    Tracks segment-by-segment progress with phase labels, timing,
    byte throughput, and character throughput. Generates HTML for
    frontend rendering via HTMX partial updates.

    Supports single-segment mode (animated progress 5%->95%) and
    multi-segment mode (explicit segment counting).

    Attributes:
        _phase: str，当前阶段标签（如 "准备中"、"推理中"、"完成"）。
        _current_segment: int，已完成段数。
        _total_segments: int，总段数。
        _start_time: float，进度开始时间戳（time.time()）。
        _segment_times: list[float]，每段耗时的滚动历史，用于滑动平均 ETA。
        _max_history: int，_segment_times 保留的最大历史条数（滑动窗口大小）。
        _lock: threading.RLock，保护所有状态字段的可重入锁。
        _total_bytes_processed: int，累计处理字节数。
        _last_segment_bytes: int，最近一段处理的字节数。
        _is_complete: bool，是否已全部完成。
        _is_cancelled: bool，是否已取消。
        _is_error: bool，是否发生错误。
        _total_chars_processed: int，累计处理字符数。
    """

    # Why _EARLY_DISPLAY_THRESHOLD_SECONDS = 0.5s：
    #   任务刚开始 <0.5s 时不显示进度条——短任务（1s 内能生成完）会出现
    #   "进度条还没出来就结束"的闪屏，0.5s 内完成直接跳过进度条显示
    #   结果卡片更干净，避免 UI 抖动。
    _EARLY_DISPLAY_THRESHOLD_SECONDS: float = 0.5

    def __init__(self, max_history: int = 5) -> None:
        """初始化进度管理器。

        Args:
            max_history: _segment_times 滑动窗口保留的最大历史段数，
                用于 ETA 估算的滑动平均，默认 5。
        """
        self._phase: str = ""
        self._current_segment: int = 0
        self._total_segments: int = 1
        self._start_time: float = 0
        self._segment_times: list[float] = []
        self._max_history: int = max_history
        self._lock: threading.RLock = threading.RLock()
        self._total_bytes_processed: int = 0
        self._last_segment_bytes: int = 0
        self._is_complete: bool = False
        self._is_cancelled: bool = False
        self._is_error: bool = False
        self._total_chars_processed: int = 0

    def _notify_sse(self) -> None:
        """通知 SSE 事件总线状态已变化。

        拆分异常处理：
        - ImportError（SSE 模块未启用或路由未加载）：仅 debug 级别静默记录。
        - 其他 Exception：同样 debug 级别，progress 通知失败不影响主流程。
        """
        try:
            from .routes.sse import event_bus

            event_bus.notify()
        except ImportError as e:
            logging.getLogger("tts_multimodel").debug(f"[ProgressManager] SSE 模块未加载 (ImportError, 可忽略): {e}")
        except Exception as e:
            logging.getLogger("tts_multimodel").debug(f"[ProgressManager] SSE 通知失败 (可忽略): {e}")

    def start(self, total_segments: int = 1, phase: str = "准备中") -> None:
        """初始化并启动新的生成任务进度追踪。

        重置所有字段到干净状态（确保新任务不会受上一个任务残留影响），
        随后通过 SSE 推送初始状态。

        Args:
            total_segments: 预期需要处理的总段数，=1 启用单段动画模式，>1 启用多段显式计数模式。
            phase: 初始阶段显示标签。
        """
        with self._lock:
            self._phase = phase
            self._current_segment = 0
            self._total_segments = total_segments
            self._start_time = time.time()
            self._segment_times = []
            self._total_bytes_processed = 0
            self._last_segment_bytes = 0
            self._is_complete = False
            self._is_cancelled = False
            self._is_error = False
            self._total_chars_processed = 0
        self._notify_sse()

    def update_phase(self, phase: str) -> None:
        """更新当前阶段显示标签。

        Args:
            phase: 新的阶段标签字符串（如 "准备中"、"推理中"、"合并音频" 等）。
        """
        with self._lock:
            self._phase = phase
        self._notify_sse()

    def cancel(self) -> None:
        """取消当前生成任务。

        设置 _is_cancelled = True，并将 phase 标记为 "已取消"。
        前端进度条收到取消状态后渲染为红色样式。
        """
        with self._lock:
            self._is_cancelled = True
            self._phase = "已取消"
        self._notify_sse()

    def advance_segment(self, phase: str = "推理中", segment_bytes: int = 0) -> None:
        """标记一段已完成，记录耗时与吞吐数据并推进进度。

        ETA 估算：_segment_times 使用 rolling average（滑动窗口，大小 = _max_history），
        丢弃过旧数据以适配推理速度在不同阶段的波动。
        segment_bytes 用于累计 _total_bytes_processed，供吞吐量统计展示。

        Why _current_segment / max(1, _total_segments)：
          用户误传 total_segments=0 时不抛 ZeroDivisionError，优雅降级为 0%。

        Args:
            phase: 下一段的阶段显示标签。
            segment_bytes: 刚刚完成的这一段处理的字节数，用于累计吞吐量。
        """
        with self._lock:
            self._is_complete = False
            if self._current_segment > 0:
                elapsed = time.time() - self._start_time
                self._segment_times.append(elapsed / self._current_segment)
                if len(self._segment_times) > self._max_history:
                    self._segment_times.pop(0)
            if segment_bytes > 0:
                self._total_bytes_processed += segment_bytes
                self._last_segment_bytes = segment_bytes
            self._current_segment += 1
            self._phase = phase
        self._notify_sse()

    def mark_error(self, error_msg: str = "") -> None:
        """标记任务发生错误。

        等同 set_error 的别名，error_msg 非空时作为 phase 显示。

        Args:
            error_msg: 错误信息（作为 phase 显示），为空默认 "生成失败"。
        """
        phase = error_msg if error_msg else "生成失败"
        with self._lock:
            self._phase = phase
            self._is_error = True
        self._notify_sse()

    def set_error(self, phase: str = "生成失败") -> None:
        """标记当前操作发生错误，进度条渲染为红色错误状态。

        Args:
            phase: 错误阶段标签，默认 "生成失败"。
        """
        with self._lock:
            self._phase = phase
            self._is_error = True
        self._notify_sse()

    def complete(self) -> None:
        """标记所有段已完成，进度锁定到 100%。

        设置 _is_complete = True，百分比 = 100，phase = "完成"。
        E2 保障：complete 之后 render_html_progress_bar 不再显示动画进度，
        直接渲染 100% 完成态绿色进度条。
        """
        with self._lock:
            self._current_segment = self._total_segments
            self._phase = "完成"
            self._is_complete = True
        self._notify_sse()

    def get_percentage(self) -> float:
        """获取当前进度百分比（0.0 ~ 100.0）。

        - 单段模式：按 5%~95% 动画估算
        - 多段模式：按段计数比例
        - 完成/错误：返回 100.0 / 0.0

        Returns:
            float，百分比值 0.0 ~ 100.0。
        """
        with self._lock:
            if self._is_complete:
                return 100.0
            if self._is_error or self._is_cancelled:
                return 0.0
            if self._total_segments <= 1:
                elapsed = time.time() - self._start_time if self._start_time > 0 else 0
                if self._start_time > 0 and elapsed < self._EARLY_DISPLAY_THRESHOLD_SECONDS:
                    return 0.0
                estimated_total = 20.0
                raw_progress = elapsed / estimated_total
                return max(5.0, min(95.0, 5.0 + raw_progress * 90.0))
            # Why max(1, ...)：total_segments=0 时不抛 ZeroDivisionError
            progress = self._current_segment / max(1, self._total_segments)
            return progress * 100.0

    def get_eta_seconds(self) -> float:
        """估算剩余时间（秒）。

        - 除零保护：_current_segment == 0 或无历史数据 → 返回 0.0
        - 多段模式：优先使用 _segment_times 滑动平均，其次整体平均
        - 单段模式：基于估计总时长 20s 线性差值

        Returns:
            float，剩余秒数，无数据或异常时返回 0.0。
        """
        with self._lock:
            if self._current_segment == 0:
                return 0.0
            try:
                if self._total_segments <= 1:
                    elapsed = time.time() - self._start_time if self._start_time > 0 else 0
                    estimated_total = 20.0
                    remaining = max(0.0, estimated_total - elapsed)
                    return remaining
                if not self._segment_times:
                    if self._current_segment > 0 and self._start_time > 0:
                        avg = (time.time() - self._start_time) / self._current_segment
                    else:
                        return 0.0
                else:
                    avg = sum(self._segment_times) / len(self._segment_times)
                remaining_segments = self._total_segments - self._current_segment
                return max(0.0, avg * remaining_segments)
            except ZeroDivisionError:
                return 0.0

    def render_html_progress_bar(self) -> str:
        """渲染 HTMX 进度条 HTML 片段。

        返回的 HTML 用于前端替换 HTMX 目标元素 #progress-container 的 innerHTML。
        f-string 拼接任何变量失败时 try/except 兜底返回最小进度条 <div>，
        避免页面空白。

        Returns:
            str，进度条 HTML；早期（<0.5s）或异常返回最小/空 HTML。
        """
        try:
            with self._lock:
                if self._is_error:
                    phase_display = self._phase or "生成失败"
                    return (
                        '<div class="tts-progress-bar">'
                        '<div class="tts-progress-fill tts-progress-error" style="width:100%"></div>'
                        "</div>"
                        '<div class="tts-progress-info tts-progress-error-info">'
                        f'<span class="tts-progress-phase">{phase_display}</span>'
                        '<span class="tts-progress-percentage">失败</span>'
                        "</div>"
                    )
                if self._is_complete:
                    return (
                        '<div class="tts-progress-bar">'
                        '<div class="tts-progress-fill tts-progress-complete" style="width:100%"></div>'
                        "</div>"
                        '<div class="tts-progress-info tts-progress-complete-info">'
                        '<span class="tts-progress-phase">生成完成</span>'
                        '<span class="tts-progress-percentage">100%</span>'
                        "</div>"
                    )
                if self._total_segments <= 0:
                    return ""
                if self._total_segments == 1:
                    elapsed = time.time() - self._start_time if self._start_time > 0 else 0
                    # E2/F1: 生成刚开始（<0.5s）时返回空字符串，避免显示无意义的 5% 进度条
                    if self._start_time > 0 and elapsed < self._EARLY_DISPLAY_THRESHOLD_SECONDS:
                        return ""
                    estimated_total = 20.0
                    raw_progress = elapsed / estimated_total
                    pct = max(5, min(95, int(5 + raw_progress * 90)))
                    remaining = max(0, estimated_total - elapsed)
                    speed_items = self._get_speed_info(elapsed)
                    phase_display = self._phase
                    return (
                        f'<div class="tts-progress-bar">'
                        f'<div class="tts-progress-fill" style="width:{pct}%"></div>'
                        f"</div>"
                        f'<div class="tts-progress-info">'
                        f'<span class="tts-progress-phase">{phase_display}</span>'
                        f'<span class="tts-progress-percentage">{pct}%</span>'
                        f'<span class="tts-progress-speed">{speed_items}</span>'
                        f"</div>"
                    )
                # Why max(1, ...)：用户误传 total_segments=0 时不抛 ZeroDivisionError，优雅降级为 0%
                progress = self._current_segment / max(1, self._total_segments)
                pct = int(progress * 100)
                elapsed = time.time() - self._start_time if self._start_time > 0 else 0
                remaining = self._estimate_remaining()
                speed_items = self._get_speed_info(elapsed)
                phase_display = self._phase
                segment_info = f"第 {self._current_segment}/{self._total_segments} 段"
                remaining_text = f"预计剩余 {self._format_duration(remaining)}" if remaining > 0 else ""
                return (
                    f'<div class="tts-progress-bar">'
                    f'<div class="tts-progress-fill" style="width:{pct}%"></div>'
                    f"</div>"
                    f'<div class="tts-progress-info">'
                    f'<span class="tts-progress-phase">{phase_display}</span>'
                    f'<span class="tts-progress-segment">{segment_info}</span>'
                    f'<span class="tts-progress-percentage">{pct}%</span>'
                    f'<span class="tts-progress-speed">{speed_items}</span>'
                    f'<span class="tts-progress-remaining">{remaining_text}</span>'
                    f"</div>"
                )
        except (ValueError, KeyError, AttributeError):
            return '<div class="tts-progress-bar"><div class="tts-progress-fill" style="width:50%"></div></div>'

    def get_progress_html(self) -> str:
        """渲染 HTMX 进度条 HTML 片段（render_html_progress_bar 的别名）。

        Returns:
            str: 进度条 HTML 字符串；任务启动 <0.5s 时返回空字符串避免闪屏。
        """
        return self.render_html_progress_bar()

    def get_state(self) -> dict[str, Any]:
        """获取当前完整进度状态字典。

        等同 get_status 的别名，对外暴露统一命名。

        Returns:
            dict[str, Any]，包含 phase、current_segment、total_segments、
            is_complete、is_cancelled、is_error、is_active、percentage、eta_seconds 等字段。
        """
        with self._lock:
            return {
                "phase": self._phase,
                "current_segment": self._current_segment,
                "total_segments": self._total_segments,
                "is_complete": self._is_complete,
                "is_cancelled": self._is_cancelled,
                "is_error": self._is_error,
                "is_active": self._phase != "",
                "percentage": self.get_percentage(),
                "eta_seconds": self.get_eta_seconds(),
            }

    def get_status(self) -> dict[str, Any]:
        """获取当前进度状态字典（精简版，不含百分比和 ETA）。

        为 SSE 和其他消费者提供公共读取接口，避免直接访问私有属性。

        Returns:
            dict[str, Any]: 包含以下键的字典：
                - phase (str): 当前阶段标签
                - current_segment (int): 已完成段数
                - total_segments (int): 总段数
                - is_complete (bool): 是否已完成
                - is_cancelled (bool): 是否已取消
                - is_error (bool): 是否发生错误
                - is_active (bool): 是否有活动任务（phase 非空）
        """
        with self._lock:
            return {
                "phase": self._phase,
                "current_segment": self._current_segment,
                "total_segments": self._total_segments,
                "is_complete": self._is_complete,
                "is_cancelled": self._is_cancelled,
                "is_error": self._is_error,
                "is_active": self._phase != "",
            }

    def _get_speed_info(self, elapsed: float) -> str:
        """计算并格式化吞吐量信息用于进度条显示。

        Args:
            elapsed: 已耗时（秒）。

        Returns:
            str: 格式化的速度字符串，如 "2.3秒/段 | ~1.5MB 待处理"；
                 数据不足时返回空字符串。
        """
        if elapsed <= 0 or self._current_segment <= 0:
            return ""
        avg_per_segment = elapsed / self._current_segment
        remaining_segments = self._total_segments - self._current_segment
        if remaining_segments <= 0:
            return ""
        speed_text = f"{avg_per_segment:.1f}秒/段"
        if self._total_bytes_processed > 0 and self._current_segment > 0:
            avg_bytes = self._total_bytes_processed / self._current_segment
            remaining_bytes = avg_bytes * remaining_segments
            if remaining_bytes > 1024 * 1024:
                speed_text += f" | ~{remaining_bytes / (1024 * 1024):.1f}MB 待处理"
        return speed_text

    def _format_duration(self, seconds: float) -> str:
        """将秒数格式化为人类可读的时长字符串。

        Args:
            seconds: 时长（秒）。

        Returns:
            str: 格式化字符串，如 "35秒"、"2分10秒"、"0秒"。
        """
        if seconds <= 0:
            return "0秒"
        if seconds < 60:
            return f"{int(seconds)}秒"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}分{secs}秒"

    def _estimate_remaining(self) -> float:
        """基于历史段耗时估算剩余时间。

        优先使用 _segment_times 滑动窗口平均，若无历史数据则使用整体平均。

        Returns:
            float: 估算剩余时间（秒），无数据时返回 0。
        """
        if not self._segment_times:
            if self._current_segment > 0 and self._start_time > 0:
                avg = (time.time() - self._start_time) / self._current_segment
            else:
                return 0
        else:
            avg = sum(self._segment_times) / len(self._segment_times)
        remaining_segments = self._total_segments - self._current_segment
        return avg * remaining_segments

    def reset(self) -> None:
        """重置所有进度状态到初始值，并通过 SSE 通知前端清空。"""
        with self._lock:
            self._phase = ""
            self._current_segment = 0
            self._total_segments = 1
            self._start_time = 0
            self._segment_times = []
            self._total_bytes_processed = 0
            self._last_segment_bytes = 0
            self._is_complete = False
            self._is_cancelled = False
            self._is_error = False
            self._total_chars_processed = 0
        self._notify_sse()

    def set_total_bytes(self, total_bytes: int) -> None:
        """设置累计处理字节数（覆盖式）。

        Args:
            total_bytes: 新的总处理字节数。
        """
        with self._lock:
            self._total_bytes_processed = total_bytes

    def is_cancelled(self) -> bool:
        """检查当前操作是否已被取消。

        Returns:
            bool: True 表示已取消，False 表示继续执行。
        """
        with self._lock:
            return self._is_cancelled

    def should_stop(self) -> bool:
        """检查操作是否应该停止（取消、完成或出错任一条件满足即停止）。

        生成循环中应周期性调用此方法，及时响应用户取消请求。

        Returns:
            bool: True 表示应立即停止生成，False 表示继续执行。
        """
        with self._lock:
            return self._is_cancelled or self._is_complete or self._is_error

    def add_chars_processed(self, char_count: int) -> None:
        """累加已处理字符数，用于字符吞吐量统计。

        Args:
            char_count: 本段处理的字符数量。
        """
        with self._lock:
            self._total_chars_processed += char_count

    def get_speed_stats(self) -> dict[str, Any]:
        """计算字符吞吐量统计信息。

        Returns:
            dict[str, Any]: 包含以下键的字典：
                - total_chars (int): 累计处理字符数
                - elapsed (float): 已耗时（秒）
                - chars_per_sec (float): 每秒处理字符数
        """
        with self._lock:
            elapsed = time.time() - self._start_time if self._start_time > 0 else 0
            chars_per_sec = (self._total_chars_processed / elapsed) if elapsed > 0 else 0
            return {
                "total_chars": self._total_chars_processed,
                "elapsed": elapsed,
                "chars_per_sec": round(chars_per_sec, 1),
            }

    def schedule_reset(self, delay_seconds: int = 3) -> None:
        """在后台线程延迟重置进度状态。

        生成完成后延迟几秒再清空进度条，让用户有时间看到 100% 完成态。

        Args:
            delay_seconds: 延迟秒数，默认 3 秒。
        """

        def _delayed_reset() -> None:
            time.sleep(delay_seconds)
            self.reset()

        t = threading.Thread(target=_delayed_reset, daemon=True)
        t.start()
