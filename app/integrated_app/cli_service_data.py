#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 数据域适配层：音色库 + 历史记录（ADR-0005 P2）。

覆盖 Web UI 的音色管理与历史管理能力，全部直调纯函数/DB 层，无需起服务：

    - persona：list / show / save / delete
    - history：list / stats / hide / clear / recycle / restore / purge / export / sync

返回值统一为 ``cli_output.Result``；``--json`` 时可直接被 agent 解析。
"""

from __future__ import annotations

import os
import zipfile
from typing import Any

from integrated_app.cli_output import Result, log


def _history_db() -> Any:
    """惰性获取 HistoryDatabase 单例（首次调用会建表/迁移/留存清理）。"""
    from integrated_app.history_db import get_history_db

    return get_history_db()


# ---------------------------------------------------------------------------
# 音色库（persona）
# ---------------------------------------------------------------------------


def cmd_persona_list(args: Any) -> Result:
    """列出音色库（支持关键字过滤）。

    ``get_persona_detail_table`` 的列顺序（见其 docstring）：
    0=name / 1=status / 2=file_size / 3=created_at / 4=description
    """
    from integrated_app.persona_manager import get_persona_detail_table

    keyword = getattr(args, "keyword", "") or ""
    rows = get_persona_detail_table(keyword)
    items = [
        {
            "name": row[0],
            "status": row[1],
            "file_size": row[2],
            "created_at": row[3],
            "description": row[4],
        }
        for row in rows
        if len(row) >= 5 and row[0] != "暂无音色"
    ]
    return Result.success(data={"count": len(items), "personas": items}, command="persona-list")


def cmd_persona_show(args: Any) -> Result:
    """查看单个音色详情（含授权状态与参考文本全文）。

    不经过 ``PersonaService.get_persona``：该实现存在既有解包 bug
    （``wav_path, ref_text = embedding`` 会抛 ValueError），这里直接读
    persona_manager 的表格数据与磁盘文件。
    """
    from integrated_app.config import PERSONA_DIR
    from integrated_app.persona_manager import get_persona_consent_state, get_persona_detail_table

    name = getattr(args, "name", "")
    if not name:
        return Result.failure("需要 --name", "USAGE_ERROR", command="persona-show")

    matched = [row for row in get_persona_detail_table(name) if len(row) >= 5 and row[0] == name]
    if not matched:
        return Result.failure(f"音色不存在: {name}", "BUSINESS_ERROR", command="persona-show")

    row = matched[0]
    wav = os.path.join(PERSONA_DIR, f"{name}.wav")
    txt = os.path.join(PERSONA_DIR, f"{name}.txt")
    ref_text = ""
    if os.path.isfile(txt):
        from pathlib import Path as _Path

        ref_text = _Path(txt).read_text(encoding="utf-8").strip()

    data = {
        "name": row[0],
        "status": row[1],
        "file_size": row[2],
        "created_at": row[3],
        "description": row[4],
        "wav_path": os.path.abspath(wav) if os.path.isfile(wav) else "",
        "ref_text": ref_text,
        "consent_state": get_persona_consent_state(name),
    }
    return Result.success(data=data, command="persona-show")


def cmd_persona_save(args: Any) -> Result:
    """保存音色到音色库（参考音频 + 转写 + 授权声明）。"""
    from integrated_app.persona_manager import fn_save_persona

    name = getattr(args, "name", "")
    audio = getattr(args, "audio", "")
    if not name or not audio:
        return Result.failure("需要 --name 与 --audio", "USAGE_ERROR", command="persona-save")
    if not os.path.isfile(audio):
        return Result.failure(f"参考音频不存在: {audio}", "USAGE_ERROR", command="persona-save")

    consent = getattr(args, "consent", "self") or "self"
    if consent not in ("self", "granted", "unverified"):
        return Result.failure("--consent 只能是 self / granted / unverified", "USAGE_ERROR", command="persona-save")

    message, needs_confirm = fn_save_persona(
        name,
        audio,
        getattr(args, "ref_text", "") or "",
        bool(getattr(args, "overwrite", False)),
        consent_state=consent,
        consent_at="",
    )
    if needs_confirm:
        return Result.failure(f"{message}（加 --overwrite 可覆盖）", "CONFIRM_REQUIRED", command="persona-save")
    ok = message.startswith("✅")
    if not ok:
        return Result.failure(message, "BUSINESS_ERROR", command="persona-save")
    return Result.success(
        data={"name": name, "message": message, "consent_state": consent, "audio": os.path.abspath(audio)},
        command="persona-save",
    )


def cmd_persona_delete(args: Any) -> Result:
    """删除音色及其关联文件。"""
    from integrated_app.persona_manager import fn_delete_persona

    name = getattr(args, "name", "")
    if not name:
        return Result.failure("需要 --name", "USAGE_ERROR", command="persona-delete")
    message, ok = fn_delete_persona(name)
    if not ok:
        return Result.failure(message, "BUSINESS_ERROR", command="persona-delete")
    return Result.success(data={"name": name, "message": message}, command="persona-delete")


# ---------------------------------------------------------------------------
# 历史记录（history）
# ---------------------------------------------------------------------------


def cmd_history_list(args: Any) -> Result:
    """分页列出历史记录（支持关键词/时间/时长过滤）。"""
    db = _history_db()
    page = db.get_paginated_records(
        limit=int(getattr(args, "limit", 20) or 20),
        offset=int(getattr(args, "offset", 0) or 0),
        search_keyword=getattr(args, "keyword", "") or "",
        time_filter=getattr(args, "time_filter", "all") or "all",
        duration_filter=getattr(args, "duration_filter", "all") or "all",
        include_hidden=bool(getattr(args, "include_hidden", False)),
        include_missing=bool(getattr(args, "include_missing", False)),
    )
    return Result.success(data=page, command="history-list")


def cmd_history_stats(args: Any) -> Result:
    """历史聚合统计（总数/引擎数/平均时长等）。"""
    db = _history_db()
    return Result.success(data=db.get_stats(), command="history-stats")


def cmd_history_hide(args: Any) -> Result:
    """隐藏指定历史记录（按文件名，可恢复）。"""
    db = _history_db()
    files = [x.strip() for x in str(getattr(args, "files", "") or "").split(",") if x.strip()]
    if not files:
        return Result.failure("需要 --files（逗号分隔的文件名）", "USAGE_ERROR", command="history-hide")
    affected = db.hide_multiple_records(files)
    return Result.success(data={"requested": len(files), "hidden": affected}, command="history-hide")


def cmd_history_clear(args: Any) -> Result:
    """清空历史（默认软删除可恢复；--hard 物理删除）。"""
    db = _history_db()
    hard = bool(getattr(args, "hard", False))
    affected = db.clear_all_records(hide_only=not hard)
    return Result.success(data={"mode": "hard" if hard else "soft", "affected": affected}, command="history-clear")


def cmd_history_recycle(args: Any) -> Result:
    """列出回收站（已隐藏记录）。"""
    db = _history_db()
    records, total = db.list_deleted_records(
        limit=int(getattr(args, "limit", 50) or 50),
        offset=int(getattr(args, "offset", 0) or 0),
    )
    return Result.success(data={"total": total, "records": records}, command="history-recycle")


def cmd_history_restore(args: Any) -> Result:
    """从回收站恢复记录（按记录 ID）。"""
    db = _history_db()
    raw = str(getattr(args, "ids", "") or "")
    try:
        ids = [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        return Result.failure("--ids 需为逗号分隔的整数记录 ID", "USAGE_ERROR", command="history-restore")
    if not ids:
        return Result.failure("需要 --ids", "USAGE_ERROR", command="history-restore")
    restored = db.restore_records(ids)
    return Result.success(data={"requested": len(ids), "restored": restored}, command="history-restore")


def cmd_history_purge(args: Any) -> Result:
    """物理清理回收站中超过保留期的记录与文件。"""
    db = _history_db()
    keep_days = int(getattr(args, "keep_days", 30) or 30)
    purged, failed = db.purge_deleted_records(keep_days=keep_days)
    return Result.success(
        data={"keep_days": keep_days, "purged": purged, "failed_files": failed}, command="history-purge"
    )


def cmd_history_export(args: Any) -> Result:
    """把指定历史音频打包为 ZIP。"""
    raw = str(getattr(args, "files", "") or "")
    files = [x.strip() for x in raw.split(",") if x.strip()]
    if not files:
        return Result.failure("需要 --files（逗号分隔的文件名）", "USAGE_ERROR", command="history-export")

    from integrated_app.config import SAVE_DIR

    output = getattr(args, "output", None)
    if not output:
        import time

        output = os.path.join(SAVE_DIR, f"history_export_{int(time.time())}.zip")
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)

    added, missing = 0, []
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in files:
            # 只接受裸文件名，防 ZIP 路径遍历
            safe = os.path.basename(name)
            src = os.path.join(SAVE_DIR, safe)
            if os.path.isfile(src):
                zf.write(src, safe)
                added += 1
            else:
                missing.append(safe)

    if added == 0:
        return Result.failure(
            f"没有可导出的音频（缺失: {', '.join(missing)}）", "BUSINESS_ERROR", command="history-export"
        )
    result = Result.success(
        data={"zip_path": os.path.abspath(output), "exported": added, "missing": missing},
        command="history-export",
    )
    if missing:
        result.warnings.append(f"{len(missing)} 个文件缺失未导出: {', '.join(missing)}")
    return result


def cmd_history_sync(args: Any) -> Result:
    """扫描 outputs 目录，把未入库的音频同步进历史库。"""
    from integrated_app.config import SAVE_DIR

    db = _history_db()
    output_dir = getattr(args, "output_dir", None) or SAVE_DIR
    since = float(getattr(args, "since", 0.0) or 0.0)
    log(f"同步历史库（目录: {output_dir}）...")
    latest_mtime = db.sync_from_filesystem(output_dir=output_dir, since_mtime=since)
    return Result.success(
        data={"output_dir": os.path.abspath(output_dir), "latest_mtime": latest_mtime},
        command="history-sync",
    )
