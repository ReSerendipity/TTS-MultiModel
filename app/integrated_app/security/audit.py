"""结构化审计日志模块（M7 整改）。

设计要点：
- 以 JSONL 追加方式持久化到 ``data/audit.log``（仅在 ``security.audit_enabled`` 时落盘）。
- 内存环形缓冲（最近 500 条）供 ``/api/system/audit`` 端点读取，不依赖磁盘即可回溯。
- 即便 ``audit_enabled=False``，仍写入内存环 + DEBUG 日志，避免未配置时静默丢事件；
  仅"落盘持久化"受开关控制。
- ``detail`` 只存必要元信息（如引擎/模式/分类），**不写完整 PII 文本**，满足最小采集。
- 模块级函数懒加载 ``config``，避免与 ``config`` 形成导入环。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger("tts_multimodel")

_AUDIT_MAX_MEM = 500
_audit_ring: deque[dict[str, Any]] = deque(maxlen=_AUDIT_MAX_MEM)
_audit_lock = threading.Lock()


def _resolve_audit_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "audit.log")


def _audit_enabled() -> bool:
    try:
        from ..config import get_config

        return get_config().pydantic_config.security.audit_enabled
    except Exception:  # noqa: BLE001
        return False


def log_audit(
    action: str,
    actor: str = "system",
    detail: str = "",
    severity: str = "info",
    outcome: str = "success",
    request_id: str | None = None,
) -> None:
    """记录一条审计事件。

    Args:
        action: 事件类型（auth_failure / generation / model_load / model_unload /
                lora_load / persona_save / pii_export / content_blocked / config_change）。
        actor: 操作者（用户名 / system / anonymous）。
        detail: 简短描述（不要写入完整 PII 文本）。
        severity: info / warning / critical。
        outcome: success / blocked / failure。
        request_id: 关联请求 ID（可选）。
    """
    entry = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": action,
        "actor": actor,
        "severity": severity,
        "outcome": outcome,
        "detail": detail,
        "request_id": request_id,
    }
    logger.info("[AUDIT] %s actor=%s severity=%s outcome=%s detail=%s", action, actor, severity, outcome, detail)
    with _audit_lock:
        _audit_ring.append(entry)
        if _audit_enabled():
            try:
                with open(_resolve_audit_path(), "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError as exc:
                logger.warning("[AUDIT] 写入审计日志失败: %s", exc)


def get_recent_audit(limit: int = 100) -> list[dict[str, Any]]:
    """返回最近 limit 条审计事件（内存环）。"""
    with _audit_lock:
        return list(_audit_ring)[-max(1, limit):]
