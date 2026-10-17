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

import contextlib
import hashlib
import hmac as _hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger("tts_multimodel")

_AUDIT_MAX_MEM = 500
_audit_ring: deque[dict[str, Any]] = deque(maxlen=_AUDIT_MAX_MEM)
_audit_lock = threading.Lock()

# P1 安全整改：默认开启审计后需防止日志无限增长，按大小轮转
_AUDIT_MAX_BYTES = 10 * 1024 * 1024  # 10MB
_AUDIT_MAX_BACKUPS = 3

# 安全合规评估 P1-1：审计链 HMAC（复用 history_db record_hmac 模式）。
# 每条记录追加 prev_hash + record_hmac 字段；密钥与业务数据密钥分离
# （密钥按用途分离，见 security/secret_key.py 注释的命名决策）。
_audit_last_hmac: str = ""


def _resolve_audit_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "audit.log")


def _get_audit_hmac_secret() -> bytes:
    """获取或生成审计日志 HMAC 密钥（安全合规 P1-1）。

    与 history_db 的 .history_hmac_key 分离（密钥按用途分离）：
    - Windows: ``%APPDATA%/tts-multimodel/.audit_hmac_key``
    - Linux/macOS: ``~/.config/tts-multimodel/.audit_hmac_key``
    - 环境变量 ``TTS_AUDIT_HMAC_KEY`` 优先级最高
    文件系统不可用时回退固定密钥（同 history_db 的降级口径）。
    """
    env_key = os.environ.get("TTS_AUDIT_HMAC_KEY")
    if env_key:
        return env_key.encode("utf-8")
    import platform

    if platform.system() == "Windows":
        config_base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        config_base = os.path.join(os.path.expanduser("~"), ".config")
    key_dir = os.path.join(config_base, "tts-multimodel")
    key_path = os.path.join(key_dir, ".audit_hmac_key")
    try:
        os.makedirs(key_dir, exist_ok=True)
        if not os.path.isfile(key_path):
            new_key = secrets.token_urlsafe(48)
            with open(key_path, "w", encoding="utf-8") as f:
                f.write(new_key)
            with contextlib.suppress(OSError):
                os.chmod(key_path, 0o600)
        with open(key_path, encoding="utf-8") as f:
            return f.read().strip().encode("utf-8")
    except OSError:
        # 仅文件系统不可用时的降级（同 history_db 口径）
        return hashlib.sha256(b"tts-multimodel-audit-fallback").digest()


def _chain_entry(entry: dict[str, Any], prev_hash: str, secret: bytes) -> dict[str, Any]:
    """计算审计记录的 HMAC 链字段（安全合规 P1-1）。

    模式与 history_db.record_hmac 一致：
      record_hash = sha256(内容字段规范化串)
      record_hmac = HMAC-SHA256(secret, prev_hash + record_hash)

    Args:
        entry: 审计事件字典（不含 prev_hash/record_hmac）。
        prev_hash: 上一条记录的 record_hmac（链头；无则为 ""）。
        secret: HMAC 密钥。

    Returns:
        带 prev_hash/record_hmac 的 entry 副本。
    """
    content_fields = ("ts", "iso", "action", "actor", "severity", "outcome", "detail", "request_id")
    record_str = "|".join(str(entry.get(f, "")) for f in content_fields)
    record_hash = hashlib.sha256(record_str.encode("utf-8")).hexdigest()
    hmac_value = _hmac.new(secret, f"{prev_hash}{record_hash}".encode(), hashlib.sha256).hexdigest()
    chained = dict(entry)
    chained["prev_hash"] = prev_hash
    chained["record_hmac"] = hmac_value
    return chained


def _rotate_audit_log_if_needed(path: str) -> None:
    """按大小轮转审计日志（P1 安全整改：默认开启后防止无限增长）。

    超过 _AUDIT_MAX_BYTES 时执行轮转：删除最旧备份 .{MAX_BACKUPS}，
    其余备份依次后移，当前日志重命名为 .1。
    """
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < _AUDIT_MAX_BYTES:
            return
    except OSError:
        return
    oldest = f"{path}.{_AUDIT_MAX_BACKUPS}"
    if os.path.isfile(oldest):
        with contextlib.suppress(OSError):
            os.remove(oldest)
    for i in range(_AUDIT_MAX_BACKUPS - 1, 0, -1):
        src = f"{path}.{i}"
        dst = f"{path}.{i + 1}"
        if os.path.isfile(src):
            with contextlib.suppress(OSError):
                os.rename(src, dst)
    with contextlib.suppress(OSError):
        os.rename(path, f"{path}.1")


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
        global _audit_last_hmac
        try:
            secret = _get_audit_hmac_secret()
            entry = _chain_entry(entry, _audit_last_hmac, secret)
            _audit_last_hmac = entry["record_hmac"]
        except Exception as exc:  # noqa: BLE001
            # 链计算失败不阻断审计落盘（降级为无链记录，verify 按 legacy 跳过）
            logger.warning("[AUDIT] HMAC 链计算失败（降级为无链记录）: %s", exc)
        _audit_ring.append(entry)
        if _audit_enabled():
            try:
                audit_path = _resolve_audit_path()
                _rotate_audit_log_if_needed(audit_path)
                with open(audit_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError as exc:
                logger.warning("[AUDIT] 写入审计日志失败: %s", exc)


def verify_audit_chain(path: str | None = None) -> dict[str, Any]:
    """校验审计日志的 HMAC 链（安全合规 P1-1）。

    Args:
        path: 审计日志路径；None 时用默认 ``data/audit.log``。

    Returns:
        ``{"ok", "checked", "tampered", "legacy_skipped", "first_error"}``：
        - 无 ``record_hmac`` 字段的历史记录（HMAC 落地前写入）按 legacy 跳过并计数；
        - 链头重置（轮转/清理导致首条断链）会体现为 ``tampered``，由调用方
          结合 ``checked`` 计数判断是轮转边界还是真实篡改。
    """
    if path is None:
        path = _resolve_audit_path()
    if not os.path.isfile(path):
        return {"ok": True, "checked": 0, "tampered": 0, "legacy_skipped": 0, "first_error": None}
    try:
        secret = _get_audit_hmac_secret()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "checked": 0, "tampered": 0, "legacy_skipped": 0, "first_error": f"密钥获取失败: {exc}"}
    prev_hash = ""
    checked = 0
    tampered = 0
    legacy = 0
    first_error: str | None = None
    line_no = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            line_no += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                tampered += 1
                if first_error is None:
                    first_error = f"第 {line_no} 行 JSON 解析失败: {exc}"
                continue
            stored = entry.get("record_hmac")
            if not stored:
                legacy += 1
                continue
            content = {k: v for k, v in entry.items() if k not in ("prev_hash", "record_hmac")}
            expected = _chain_entry(content, prev_hash, secret)["record_hmac"]
            if expected != stored:
                tampered += 1
                if first_error is None:
                    first_error = f"第 {line_no} 行 HMAC 不匹配（prev_hash 前缀 {prev_hash[:8] or '(空)'}）"
                continue
            checked += 1
            prev_hash = stored
    return {
        "ok": tampered == 0,
        "checked": checked,
        "tampered": tampered,
        "legacy_skipped": legacy,
        "first_error": first_error,
    }


def get_recent_audit(limit: int = 100) -> list[dict[str, Any]]:
    """返回最近 limit 条审计事件（内存环）。"""
    with _audit_lock:
        return list(_audit_ring)[-max(1, limit) :]
