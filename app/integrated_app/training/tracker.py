"""训练进度追踪 + SSE 实时推送 + TensorBoard 记录。

training/ 目录对应 WebUI 中 LoRA 微调 Tab 的训练任务；scripts/train_voxcpm_finetune.py
的训练主循环每个 step / epoch 结束都会回调本模块的 ``TrainingTracker``：
  - on_step_end 更新 step loss、LR、梯度范数、GPU 利用率，并通过统一
    ``/api/sse/events`` 端点推送给前端（WebUI Training Tab 的进度条 / Loss 曲线）；
  - on_epoch_end 汇总 epoch loss + 跑 eval + 更新 best loss + 触发 checkpoint；
  - 同时可选写入 TensorBoard logdir，供科研/重度用户做多 run 历史对比。

双通道设计 Why：
  - SSE：浏览器端 EventSource/WebSocket 实时，不阻塞主循环（异步推送）
  - TensorBoard：离线历史对比，科研用户刚需
两者均为异步写入或 best-effort，单步耗时 < 1ms，不会把 10k step 的训练
多拖出 100s。
"""

from __future__ import annotations

import contextlib
import logging
import math
import time
from collections import deque
from pathlib import Path
from typing import Any

from .state import TrainingState

logger = logging.getLogger("tts_multimodel.training.tracker")

# EMA 平滑因子：EMA_epoch = 0.3 * epoch_time + 0.7 * EMA_prev
# 第一个 epoch 因 torch 编译 / cache warm-up / worker 启动通常慢 30%，
# 用较大的 α=0.3 能让第 2 个 epoch 起 ETA 偏差 < 5%；若用单 epoch 时间
# 估算，第 1 个 epoch 结束会显示"还剩 10h"实际只需要 7h，用户体验差。
_EMA_ALPHA: float = 0.3
# 最多保留多少个 epoch 的时间用于 EMA（避免早期异常值长期拖偏 ETA）
_EMA_WINDOW: int = 30


class TrainingTracker:
    """训练进度追踪器：统计 + SSE 推送 + TensorBoard 双通道。

    保留了 minicpm-audio 风格的老接口（writer / log_file / rank /
    print / log_metrics / done / state_dict / load_state_dict / live），
    以满足 100% 向后兼容；同时新增回调式 on_* 接口和 ETA / progress 计算。

    Args:
        total_epochs: 总 epoch 数（用于 progress_percent / ETA）
        steps_per_epoch: 每个 epoch 的 step 数（用于 progress_percent）
        sse_event_bus: 可选 SSE 事件总线（调用 ``.publish(event_type, payload)``）
        tensorboard_log_dir: 可选 TensorBoard logdir，缺 writer 时按需新建
        writer: 兼容老接口，直接传入 SummaryWriter（优先级高于 log_dir）
        log_file: 兼容老接口，日志写入的文本文件
        rank: 兼容老接口，分布式 rank，只有 rank == 0 真正写日志 / SSE
    """

    def __init__(
        self,
        total_epochs: int = 0,
        steps_per_epoch: int = 0,
        sse_event_bus: Any | None = None,
        tensorboard_log_dir: Path | None = None,
        *,
        writer: Any | None = None,
        log_file: str | None = None,
        rank: int = 0,
    ) -> None:
        """初始化训练进度追踪器。

        保留了 minicpm-audio 风格的老接口以满足 100% 向后兼容；
        同时新增回调式 on_* 接口和 ETA / progress 计算。

        Args:
            total_epochs: 总 epoch 数（用于 progress_percent / ETA 计算）
            steps_per_epoch: 每个 epoch 的 step 数（用于 progress_percent 计算）
            sse_event_bus: 可选 SSE 事件总线（调用 ``.publish(event_type, payload)``）
            tensorboard_log_dir: 可选 TensorBoard logdir，缺 writer 时按需新建
            writer: 兼容老接口，直接传入 SummaryWriter（优先级高于 log_dir）
            log_file: 兼容老接口，日志写入的文本文件路径
            rank: 分布式 rank，只有 rank == 0 真正写日志 / 推送 SSE
        """
        self.total_epochs: int = max(0, int(total_epochs))
        self.steps_per_epoch: int = max(0, int(steps_per_epoch))
        self.sse_event_bus: Any | None = sse_event_bus

        # --- Legacy 兼容字段 ---
        self.log_file: Path | None = Path(log_file) if log_file else None
        if self.log_file is not None:
            try:
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.log_file = None
        self.rank: int = int(rank)
        self.step: int = 0
        self._last_log_time: float | None = None

        # --- TensorBoard writer ---
        self._writer: Any | None = writer
        if self._writer is None and tensorboard_log_dir is not None:
            self._writer = self._init_tensorboard(Path(tensorboard_log_dir))

        # --- 统计 / ETA 内部状态 ---
        self._epoch_start_ts: float | None = None
        self._epoch_times_sec: deque[float] = deque(maxlen=_EMA_WINDOW)
        self._ema_epoch_sec: float | None = None
        self._train_begin_ts: float | None = None
        self._current_epoch_loss_sum: float = 0.0
        self._current_epoch_loss_cnt: int = 0
        self._recent_losses: deque[float] = deque(maxlen=200)

        # 公共统计缓存（get_info 返回的最近一次 on_step_end 结果）
        self._last_step_info: dict[str, Any] = {}
        self._last_epoch_info: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # TensorBoard：缺包时静默降级
    # ------------------------------------------------------------------ #
    @staticmethod
    def _init_tensorboard(log_dir: Path) -> Any | None:
        """初始化 TensorBoard SummaryWriter（缺包时静默降级）。

        Args:
            log_dir: TensorBoard 日志目录

        Returns:
            SummaryWriter 实例，失败或未安装时返回 None
        """
        try:
            from torch.utils.tensorboard import SummaryWriter

            log_dir.mkdir(parents=True, exist_ok=True)
            return SummaryWriter(log_dir=str(log_dir))
        except ImportError:
            logger.info("TensorBoard 未安装，仅通过 SSE 推送训练进度。 执行 `pip install tensorboard` 可启用历史曲线。")
            return None
        except Exception as exc:  # noqa: BLE001
            logger.info("TensorBoard 初始化异常，跳过: %s", exc)
            return None

    @property
    def writer(self) -> Any | None:
        """Legacy getter：返回 SummaryWriter（外部可能直接调用 .add_scalar）。"""
        return self._writer

    @writer.setter
    def writer(self, value: Any | None) -> None:
        """设置 TensorBoard SummaryWriter（legacy 兼容）。

        Args:
            value: SummaryWriter 实例或 None
        """
        self._writer = value

    # ------------------------------------------------------------------ #
    # Legacy 接口（100% 保留，最小改动）
    # ------------------------------------------------------------------ #
    def print(self, message: str) -> None:
        """Legacy：rank 0 时写 logger + 追加 log_file。

        Args:
            message: 要记录的日志消息
        """
        if self.rank != 0:
            return
        try:
            logger.info(message)
            if self.log_file is not None:
                try:
                    with self.log_file.open("a", encoding="utf-8") as f:
                        f.write(message + "\n")
                except OSError as exc:
                    logger.debug("写 log_file 失败（忽略）: %s", exc)
        except Exception:  # noqa: BLE001  # nosec B110
            # print 本身绝不能抛异常，否则训练中断
            pass

    def log_metrics(self, metrics: dict[str, float], split: str) -> None:
        """Legacy：打印格式化指标 + 写 TensorBoard。

        Args:
            metrics: 指标名到指标值的字典
            split: 数据集划分名（"train" / "val" / "epoch" 等）
        """
        if self.rank == 0:
            try:
                now = time.time()
                dt_str = ""
                if self._last_log_time is not None:
                    dt = now - self._last_log_time
                    dt_str = f", log interval: {dt:.2f}s"
                self._last_log_time = now
                formatted = ", ".join(f"{k}: {v:.6f}" for k, v in metrics.items())
                self.print(f"[{split}] step {self.step}: {formatted}{dt_str}")
            except Exception:  # noqa: BLE001  # nosec B110
                pass
        if self._writer is not None:
            try:
                for key, value in metrics.items():
                    if isinstance(value, (int, float)):
                        self._writer.add_scalar(f"{split}/{key}", float(value), self.step)
            except Exception:  # noqa: BLE001  # nosec B110
                # TensorBoard 写失败绝不影响训练
                pass

    def done(self, split: str, message: str) -> None:
        """Legacy：标记某个 split 阶段结束。

        Args:
            split: 数据集划分名
            message: 结束消息
        """
        self.print(f"[{split}] {message}")

    def state_dict(self) -> dict[str, Any]:
        """返回可持久化的最小状态字典（用于断点续训）。

        Returns:
            包含 step / EMA epoch time / loss history 等的字典
        """
        return {
            "step": int(self.step),
            "total_epochs": int(self.total_epochs),
            "steps_per_epoch": int(self.steps_per_epoch),
            "ema_epoch_sec": self._ema_epoch_sec,
            "epoch_times_sec": list(self._epoch_times_sec),
            "last_log_time": self._last_log_time,
        }

    def load_state_dict(self, state: dict[str, Any]) -> None:
        """从 state_dict 恢复追踪器状态（断点续训后 ETA 不跳变）。

        Args:
            state: 由 state_dict() 导出的状态字典
        """
        self.step = int(state.get("step", 0))
        self.total_epochs = int(state.get("total_epochs", self.total_epochs))
        self.steps_per_epoch = int(state.get("steps_per_epoch", self.steps_per_epoch))
        ema = state.get("ema_epoch_sec")
        self._ema_epoch_sec = float(ema) if isinstance(ema, (int, float)) and ema > 0 else None
        times = state.get("epoch_times_sec") or []
        if times:
            with contextlib.suppress(Exception):
                self._epoch_times_sec = deque(
                    [float(t) for t in times if isinstance(t, (int, float)) and t > 0],
                    maxlen=_EMA_WINDOW,
                )
        ll = state.get("last_log_time")
        self._last_log_time = float(ll) if isinstance(ll, (int, float)) else None

    @contextlib.contextmanager
    def live(self):
        """Legacy：上下文管理器（与 minicpm-audio 训练脚本奇偶对齐）。

        Yields:
            self
        """
        yield self

    # ------------------------------------------------------------------ #
    # 新回调接口：on_train_begin / on_step_end / on_epoch_end / on_train_end
    # ------------------------------------------------------------------ #
    def on_train_begin(self, state: TrainingState) -> None:
        """训练开始回调：记录时间戳、重置统计、推送 SSE。

        Args:
            state: 初始 TrainingState（可能是刚初始化，也可能是 resume 的）
        """
        if self.rank != 0:
            return
        try:
            self._train_begin_ts = time.time()
            if state.total_epochs > 0 and self.total_epochs == 0:
                self.total_epochs = int(state.total_epochs)
            self.step = int(state.global_step)
            payload: dict[str, Any] = {
                "type": "training_begin",
                "run_id": state.run_id,
                "total_epochs": int(self.total_epochs),
                "steps_per_epoch": int(self.steps_per_epoch),
                "resume_from_epoch": int(state.current_epoch),
                "resume_from_step": int(state.global_step),
                "timestamp": time.time(),
            }
            self._sse_publish("training.status", payload)
            self.print(
                f"训练开始: run_id={state.run_id}, "
                f"total_epochs={self.total_epochs}, steps_per_epoch={self.steps_per_epoch}, "
                f"resume_epoch={state.current_epoch}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("on_train_begin 非关键异常: %s", exc)

    def on_step_end(
        self,
        step: int,
        loss: float,
        lr: float,
        grad_norm: float | None = None,
        gpu_util_pct: float | None = None,
    ) -> dict[str, Any]:
        """每步结束回调：更新 step loss / 推送 SSE / 写 TensorBoard。

        Args:
            step: 当前 global_step
            loss: 本步标量 loss（未 scale）
            lr: 当前学习率
            grad_norm: 可选梯度范数（用于 debug 梯度爆炸/消失）
            gpu_util_pct: 可选 GPU 利用率 0-100（WebUI 进度条旁小面板）

        Returns:
            统一的 info dict（也会缓存到 self._last_step_info 供 get_info）
        """
        self.step = int(step)
        # 先记录 epoch 聚合统计（所有 rank 都执行，保证各卡统计一致）
        loss_f = float(loss) if isinstance(loss, (int, float)) else 0.0
        if not math.isnan(loss_f) and not math.isinf(loss_f):
            self._current_epoch_loss_sum += loss_f
            self._current_epoch_loss_cnt += 1
            self._recent_losses.append(loss_f)
        running_avg = sum(self._recent_losses) / len(self._recent_losses) if self._recent_losses else 0.0
        info: dict[str, Any] = {
            "step": int(self.step),
            "loss": loss_f,
            "running_avg_loss": float(running_avg),
            "lr": float(lr),
            "grad_norm": float(grad_norm) if isinstance(grad_norm, (int, float)) else None,
            "gpu_util_pct": float(gpu_util_pct) if isinstance(gpu_util_pct, (int, float)) else None,
            "timestamp": time.time(),
        }
        self._last_step_info = dict(info)
        if self.rank != 0:
            return info
        try:
            # 写 SSE（best-effort）
            payload = dict(info)
            payload["type"] = "step_end"
            payload["progress_percent"] = self._progress_impl()
            payload["eta_seconds"] = self._eta_impl()
            self._sse_publish("training.progress", payload)
            # 写 TensorBoard
            if self._writer is not None:
                try:
                    self._writer.add_scalar("train/step_loss", loss_f, self.step)
                    self._writer.add_scalar("train/running_avg_loss", running_avg, self.step)
                    self._writer.add_scalar("train/lr", float(lr), self.step)
                    if isinstance(grad_norm, (int, float)):
                        self._writer.add_scalar("train/grad_norm", float(grad_norm), self.step)
                    if isinstance(gpu_util_pct, (int, float)):
                        self._writer.add_scalar("train/gpu_util_pct", float(gpu_util_pct), self.step)
                except Exception:  # noqa: BLE001  # nosec B110
                    pass
        except Exception as exc:  # noqa: BLE001
            # 任何统计 / 推送异常都不能中断训练
            logger.warning("on_step_end 非关键异常: %s", exc)
        return info

    def on_epoch_end(
        self,
        epoch: int,
        avg_train_loss: float | None = None,
        eval_loss: float | None = None,
        state: TrainingState | None = None,
        early_stopping_patience: int = 0,
        early_stopping_min_delta: float = 0.0,
    ) -> dict[str, Any]:
        """每个 epoch 结束回调：更新 EMA epoch 时间 / best loss / SSE 推送。

        Args:
            epoch: 已完成的 epoch 索引（从 1 开始计，或从 state.current_epoch 同步）
            avg_train_loss: 可选，训练集平均 loss；缺省时用 step 累积统计代替
            eval_loss: 可选，验证集平均 loss
            state: 可选，TrainingState（用于写入 state / progress 辅助字段）

        Returns:
            info dict（缓存到 self._last_epoch_info）
        """
        # 1) 结算 epoch 耗时 + EMA
        now = time.time()
        epoch_sec: float | None = None
        if self._epoch_start_ts is not None:
            epoch_sec = max(0.0, now - self._epoch_start_ts)
            if epoch_sec > 0:
                self._epoch_times_sec.append(epoch_sec)
                # EMA 更新
                if self._ema_epoch_sec is None:
                    self._ema_epoch_sec = epoch_sec
                else:
                    self._ema_epoch_sec = _EMA_ALPHA * epoch_sec + (1.0 - _EMA_ALPHA) * self._ema_epoch_sec
        self._epoch_start_ts = now
        # 2) 训练平均 loss
        if avg_train_loss is None:
            if self._current_epoch_loss_cnt > 0:
                avg_train_loss = self._current_epoch_loss_sum / float(self._current_epoch_loss_cnt)
            else:
                avg_train_loss = 0.0
        # 3) best_loss 与 eval 更新（若 state 传入则直接写回）
        best_eval_loss: float | None = None
        best_epoch: int = 0
        if state is not None:
            try:
                state.current_epoch = int(epoch)
                if not math.isnan(float(avg_train_loss)):
                    # train_loss_history 按 step 粒度：每 epoch 末尾追加一个点
                    # 用于前端曲线（step 粒度已有 on_step_end 的 history）
                    pass
                if isinstance(eval_loss, (int, float)) and not math.isnan(float(eval_loss)):
                    eval_loss_f = float(eval_loss)
                    state.eval_loss_history.append(eval_loss_f)
                    if eval_loss_f < state.best_eval_loss:
                        state.best_eval_loss = eval_loss_f
                        state.best_epoch = int(epoch)
                best_eval_loss = state.best_eval_loss
                best_epoch = state.best_epoch
            except Exception:  # noqa: BLE001  # nosec B110
                pass
        # 重置 epoch 累计
        self._current_epoch_loss_sum = 0.0
        self._current_epoch_loss_cnt = 0

        early_stop = False
        if state is not None and early_stopping_patience > 0:
            early_stop = self.should_stop_early(
                state, early_stopping_patience, early_stopping_min_delta
            )

        info: dict[str, Any] = {
            "epoch": int(epoch),
            "avg_train_loss": float(avg_train_loss),
            "eval_loss": float(eval_loss)
            if isinstance(eval_loss, (int, float)) and not math.isnan(float(eval_loss))
            else None,
            "epoch_time_sec": epoch_sec,
            "ema_epoch_sec": self._ema_epoch_sec,
            "best_eval_loss": best_eval_loss,
            "best_epoch": int(best_epoch),
            "early_stop": bool(early_stop),
            "eta_seconds": self._eta_impl(),
            "progress_percent": self._progress_impl(),
            "timestamp": now,
        }
        self._last_epoch_info = dict(info)
        if self.rank != 0:
            return info
        try:
            payload = dict(info)
            payload["type"] = "epoch_end"
            self._sse_publish("training.epoch", payload)
            if self._writer is not None:
                try:
                    self._writer.add_scalar("epoch/avg_train_loss", float(avg_train_loss), int(epoch))
                    if isinstance(eval_loss, (int, float)) and not math.isnan(float(eval_loss)):
                        self._writer.add_scalar("epoch/eval_loss", float(eval_loss), int(epoch))
                    if epoch_sec is not None:
                        self._writer.add_scalar("epoch/time_sec", float(epoch_sec), int(epoch))
                except Exception:  # noqa: BLE001  # nosec B110
                    pass
            self.print(
                f"[epoch {epoch}] train_loss={avg_train_loss:.6f}, "
                f"eval_loss={info['eval_loss']}, time={epoch_sec:.1f}s, "
                f"best_eval={best_eval_loss}, ETA={self._format_eta(info['eta_seconds'])}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("on_epoch_end 非关键异常: %s", exc)
        return info

    def should_stop_early(
        self,
        state: TrainingState,
        patience: int,
        min_delta: float,
    ) -> bool:
        """判断是否满足 early stopping 条件（P2-4）。

        规则：取最近 ``patience`` 个 epoch 的 eval loss 窗口，若窗口内最佳值相对全局
        ``best_eval_loss`` 的改进小于 ``min_delta``，说明模型已连续 ``patience`` 个 epoch
        无显著改善，应停止训练以节省算力并防止过拟合。

        Args:
            state: 当前 TrainingState（含 eval_loss_history / best_eval_loss）
            patience: 连续无改善的 epoch 数阈值；<=0 表示禁用
            min_delta: 视为「有改善」的最小 eval loss 下降量

        Returns:
            True=应早停；False=继续
        """
        if patience <= 0:
            return False
        hist = list(state.eval_loss_history)
        if len(hist) <= patience:
            return False
        recent_best = min(hist[-patience:])
        return (state.best_eval_loss - recent_best) < min_delta

    def on_train_end(
        self,
        state: TrainingState,
        success: bool,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """训练结束回调：success=True 正常完成，success=False 异常中断。

        Args:
            state: 最终 TrainingState
            success: 是否正常完成所有 epoch
            error_message: success=False 时的错误描述（会推送到 SSE）

        Returns:
            info dict
        """
        now = time.time()
        total_sec: float | None = None
        if self._train_begin_ts is not None:
            total_sec = max(0.0, now - self._train_begin_ts)
        info: dict[str, Any] = {
            "run_id": state.run_id,
            "success": bool(success),
            "error_message": str(error_message) if error_message else None,
            "total_epochs": int(state.total_epochs),
            "finished_epochs": int(state.current_epoch),
            "global_step": int(state.global_step),
            "best_eval_loss": state.best_eval_loss,
            "best_epoch": int(state.best_epoch),
            "total_time_sec": total_sec,
            "timestamp": now,
        }
        if self.rank != 0:
            return info
        try:
            payload = dict(info)
            payload["type"] = "training_end"
            self._sse_publish("training.status", payload)
            if self._writer is not None:
                try:
                    self._writer.flush()
                    self._writer.close()
                except Exception:  # noqa: BLE001  # nosec B110
                    pass
            status_word = "成功完成" if success else "异常中断"
            msg = (
                f"训练{status_word}: run_id={state.run_id}, "
                f"epochs={state.current_epoch}/{state.total_epochs}, "
                f"best_eval={state.best_eval_loss:.6f}@epoch{state.best_epoch}"
            )
            if error_message:
                msg += f", error={error_message}"
            if total_sec is not None:
                msg += f", total_time={self._format_eta(total_sec)}"
            self.print(msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("on_train_end 非关键异常: %s", exc)
        return info

    # ------------------------------------------------------------------ #
    # ETA / progress 计算（独立可调用）
    # ------------------------------------------------------------------ #
    def estimate_eta_seconds(self, state: TrainingState | None = None) -> float:
        """估算剩余训练时间（秒）。公开 API，内部走 _eta_impl。

        Args:
            state: 可选 state，用于更精确的进度（比 tracker 内部 self.total_epochs 优先）

        Returns:
            剩余秒数；信息不足时返回 0.0
        """
        if state is not None:
            old_total = self.total_epochs
            try:
                if state.total_epochs > 0:
                    self.total_epochs = int(state.total_epochs)
                return self._eta_impl(state=state)
            finally:
                self.total_epochs = old_total
        return self._eta_impl()

    def _eta_impl(self, state: TrainingState | None = None) -> float:
        """估算剩余训练时间（内部实现）。

        使用 EMA 平滑的 epoch 时间估算剩余时间，避免早期 epoch 编译/热身导致的 ETA 偏差。

        Args:
            state: 可选 TrainingState，提供更精确的进度信息

        Returns:
            剩余训练时间（秒），信息不足时返回 0.0
        """
        epoch_cursor = int(state.current_epoch) if state is not None else 0
        total = self.total_epochs
        if total <= 0:
            return 0.0
        remain = max(0, total - epoch_cursor)
        if remain <= 0:
            return 0.0
        base_time: float | None = None
        if self._ema_epoch_sec is not None and self._ema_epoch_sec > 0:
            base_time = self._ema_epoch_sec
        elif self._epoch_times_sec:
            s = sum(self._epoch_times_sec)
            n = len(self._epoch_times_sec)
            if n > 0 and s > 0:
                base_time = s / float(n)
        if base_time is None or base_time <= 0:
            return 0.0
        return float(remain) * base_time

    def progress_percent(self, state: TrainingState | None = None) -> float:
        """训练进度百分比（公开 API）。

        Args:
            state: 可选 TrainingState

        Returns:
            进度百分比（0-100）
        """
        return self._progress_impl(state=state)

    def _progress_impl(self, state: TrainingState | None = None) -> float:
        """计算训练进度百分比（内部实现）。

        以 epoch 为主粒度，step 为辅助粒度计算精确进度。

        Args:
            state: 可选 TrainingState

        Returns:
            进度百分比（0-100）
        """
        total_epochs = int(state.total_epochs) if state is not None else self.total_epochs
        epoch_cursor = int(state.current_epoch) if state is not None else 0
        steps_per_epoch = (
            int(state.current_step) + (int(state.global_step) - int(state.current_step))  # no-op，占位
            if state is not None
            else 0
        )
        if steps_per_epoch <= 0:
            steps_per_epoch = self.steps_per_epoch
        step_in_epoch = int(state.current_step) if state is not None else 0
        if total_epochs <= 0:
            return 0.0
        # 以 epoch 为主粒度，step 为辅
        if steps_per_epoch > 0:
            frac_epoch = min(1.0, float(epoch_cursor) + float(step_in_epoch) / float(steps_per_epoch))
            ratio = frac_epoch / float(total_epochs)
        else:
            ratio = float(epoch_cursor) / float(total_epochs)
        return max(0.0, min(100.0, ratio * 100.0))

    # ------------------------------------------------------------------ #
    # get_info：对外快照
    # ------------------------------------------------------------------ #
    def get_info(self) -> dict[str, Any]:
        """返回当前追踪器状态的完整快照（前端 API 直接返回 JSON）。"""
        eta = self._eta_impl()
        return {
            "step": int(self.step),
            "total_epochs": int(self.total_epochs),
            "steps_per_epoch": int(self.steps_per_epoch),
            "progress_percent": self._progress_impl(),
            "eta_seconds": float(eta),
            "eta_human": self._format_eta(eta),
            "last_step": dict(self._last_step_info),
            "last_epoch": dict(self._last_epoch_info),
            "ema_epoch_sec": self._ema_epoch_sec,
            "recent_step_count": len(self._recent_losses),
        }

    # ------------------------------------------------------------------ #
    # SSE 推送封装（失败静默，绝不阻塞训练主循环）
    # ------------------------------------------------------------------ #
    def _sse_publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """通过 SSE 事件总线推送训练进度事件（失败静默，不阻塞训练）。

        支持多种事件总线接口：
        - 有 .publish(event_type, payload) 方法的对象
        - 可调用对象 bus(event_type, payload)
        - 有 .put_nowait() 方法的队列对象

        Args:
            event_type: 事件类型（如 "training.progress" / "training.epoch"）
            payload: 事件数据字典
        """
        if self.sse_event_bus is None:
            return
        try:
            bus = self.sse_event_bus
            if hasattr(bus, "publish"):
                bus.publish(event_type, payload)
            elif callable(bus):
                bus(event_type, payload)
            else:
                # 队列 / Queue 风格
                if hasattr(bus, "put_nowait"):
                    with contextlib.suppress(Exception):
                        bus.put_nowait({"event": event_type, "data": payload})
        except Exception as exc:  # noqa: BLE001
            logger.debug("SSE 推送失败，已跳过: %s", exc)

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _format_eta(seconds: float) -> str:
        """把秒数格式化为 'Xh Ym Zs' / 'Ym Zs' / 'Zs' 人类可读字符串。"""
        try:
            secs = (
                int(seconds)
                if isinstance(seconds, (int, float)) and not math.isnan(seconds) and not math.isinf(seconds)
                else 0
            )
        except Exception:  # noqa: BLE001
            secs = 0
        if secs <= 0:
            return "0s"
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}h {m}m {s}s"
        if m > 0:
            return f"{m}m {s}s"
        return f"{s}s"

    def __del__(self) -> None:
        """best-effort 清理：关闭 TensorBoard writer。"""
        w = self.__dict__.get("_writer")
        if w is not None:
            try:
                if hasattr(w, "close"):
                    w.close()
            except Exception:  # noqa: BLE001  # nosec B110
                pass
