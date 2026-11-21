#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""P2 数据域（音色库 / 历史记录）适配层的单元测试（ADR-0005 P2）。

只测纯逻辑与只读路径，不做任何破坏性历史操作（不隐藏/清空真实记录）。
"""

from __future__ import annotations

import json
import os
import zipfile

import pytest

from integrated_app.cli_output import Result
from integrated_app.cli_service_data import (
    cmd_history_export,
    cmd_history_hide,
    cmd_history_list,
    cmd_history_restore,
    cmd_history_stats,
    cmd_persona_delete,
    cmd_persona_list,
    cmd_persona_save,
    cmd_persona_show,
)


class _Args:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _ok_roundtrip(result: Result) -> dict:
    """Result → JSON → dict，验证结构可被 agent 解析。"""
    return json.loads(json.dumps(result.to_dict(), ensure_ascii=False))


class TestPersona:
    def test_list_returns_structured_items(self):
        result = cmd_persona_list(_Args(keyword=""))
        assert result.command == "persona-list"
        payload = _ok_roundtrip(result)
        assert payload["ok"] is True
        assert isinstance(payload["data"]["personas"], list)
        # 每条记录字段必须固定，agent 才能稳定消费
        for item in payload["data"]["personas"]:
            assert set(item) == {"name", "status", "file_size", "created_at", "description"}

    def test_list_keyword_filters(self):
        all_result = cmd_persona_list(_Args(keyword=""))
        names = [p["name"] for p in all_result.data["personas"]]
        if not names:
            pytest.skip("本机没有音色，跳过过滤断言")
        target = names[0]
        filtered = cmd_persona_list(_Args(keyword=target))
        assert all(target in p["name"] for p in filtered.data["personas"])

    def test_show_missing_name(self):
        result = cmd_persona_show(_Args(name=""))
        assert result.ok is False
        assert result.error_code == "USAGE_ERROR"

    def test_show_unknown_persona(self):
        result = cmd_persona_show(_Args(name="__不存在__"))
        assert result.ok is False
        assert result.error_code == "BUSINESS_ERROR"

    def test_save_requires_args(self):
        assert cmd_persona_save(_Args(name="", audio="")).error_code == "USAGE_ERROR"
        assert cmd_persona_save(_Args(name="x", audio="")).error_code == "USAGE_ERROR"

    def test_save_missing_audio_file(self, tmp_path):
        result = cmd_persona_save(_Args(name="unit_test", audio=str(tmp_path / "nope.wav"), ref_text=""))
        assert result.ok is False
        assert result.error_code == "USAGE_ERROR"

    def test_save_invalid_consent(self, tmp_path):
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        result = cmd_persona_save(_Args(name="unit_test", audio=str(audio), ref_text="", consent="bogus"))
        assert result.ok is False
        assert result.error_code == "USAGE_ERROR"

    def test_delete_missing_name(self):
        assert cmd_persona_delete(_Args(name="")).error_code == "USAGE_ERROR"


class TestHistory:
    def test_list_returns_paginated_payload(self):
        result = cmd_history_list(_Args(limit=2, offset=0, keyword=""))
        assert result.command == "history-list"
        payload = _ok_roundtrip(result)
        assert payload["ok"] is True
        assert "items" in payload["data"]

    def test_stats_shape(self):
        result = cmd_history_stats(_Args())
        payload = _ok_roundtrip(result)
        assert payload["ok"] is True
        assert "total_records" in payload["data"]

    def test_hide_requires_files(self):
        assert cmd_history_hide(_Args(files="")).error_code == "USAGE_ERROR"

    def test_hide_unknown_file_is_noop(self):
        """只对不存在的文件名调用，确保不误伤真实记录。"""
        result = cmd_history_hide(_Args(files="__nonexistent_unit_test__.wav"))
        assert result.ok is True
        assert result.data["hidden"] == 0

    def test_restore_bad_ids(self):
        assert cmd_history_restore(_Args(ids="a,b")).error_code == "USAGE_ERROR"
        assert cmd_history_restore(_Args(ids="")).error_code == "USAGE_ERROR"

    def test_export_requires_files(self):
        assert cmd_history_export(_Args(files="")).error_code == "USAGE_ERROR"

    def test_export_rejects_path_traversal(self, tmp_path):
        """ZIP 内只允许裸文件名，../ 不得越界。"""
        out = tmp_path / "out.zip"
        result = cmd_history_export(_Args(files="../../etc/passwd", output=str(out)))
        assert result.ok is False

    def test_export_existing_file(self, tmp_path):
        from integrated_app.config import SAVE_DIR

        # 找一个真实存在的输出音频（不写入、只读取）
        candidate = None
        for name in os.listdir(SAVE_DIR):
            if name.endswith(".wav"):
                candidate = name
                break
        if not candidate:
            pytest.skip("outputs 下没有 wav，跳过导出断言")

        out = tmp_path / "export.zip"
        result = cmd_history_export(_Args(files=candidate, output=str(out)))
        assert result.ok is True
        assert os.path.isfile(out)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        assert candidate in names
        assert all("/" not in n and ".." not in n for n in names)
