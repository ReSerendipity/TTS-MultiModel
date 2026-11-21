#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""P4 系统域适配层的单元测试（ADR-0005 P4）。

不启动服务：验证脱敏逻辑、只读视图结构与参数校验。
"""

from __future__ import annotations

import json

from integrated_app.cli_service_system import (
    _mask_secrets,
    cmd_system_gpu,
    cmd_system_health,
    cmd_system_logs,
    cmd_system_settings,
)


class _Args:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _roundtrip(result) -> dict:
    return json.loads(json.dumps(result.to_dict(), ensure_ascii=False))


class TestMaskSecrets:
    def test_masks_sensitive_keys(self):
        raw = {
            "api_token": "abcd1234",
            "client_secret": "s3cr3t",
            "db_password": "hunter2",
            "model_api_key": "sk-xxx",
            "hmac_key": "deadbeef",
            "safe_field": "keep-me",
            "nested": {"auth_token": "nope", "value": 1},
        }
        masked = _mask_secrets(raw)
        assert masked["api_token"] == "***"
        assert masked["client_secret"] == "***"
        assert masked["db_password"] == "***"
        assert masked["model_api_key"] == "***"
        assert masked["hmac_key"] == "***"
        assert masked["safe_field"] == "keep-me"
        assert masked["nested"]["auth_token"] == "***"
        assert masked["nested"]["value"] == 1

    def test_empty_secret_stays_empty(self):
        assert _mask_secrets({"token": None})["token"] is None
        assert _mask_secrets({"token": ""})["token"] == ""

    def test_list_truncated_and_masked(self):
        payload = [{"secret": "x"} for _ in range(80)]
        out = _mask_secrets(payload)
        assert len(out) == 50  # 截断到 50，避免超长输出
        assert all(item["secret"] == "***" for item in out)

    def test_deep_nesting_guard(self):
        deep: dict = {}
        node = deep
        for _ in range(12):
            node["child"] = {}
            node = node["child"]
        # 不应递归爆栈
        assert isinstance(_mask_secrets(deep), dict)


class TestSystemViews:
    def test_health_shape(self):
        result = cmd_system_health(_Args())
        payload = _roundtrip(result)
        assert payload["ok"] is True
        data = payload["data"]
        for key in ("platform", "python", "memory", "gpu", "models"):
            assert key in data
        assert "available" in data["gpu"]

    def test_gpu_shape(self):
        result = cmd_system_gpu(_Args())
        payload = _roundtrip(result)
        assert payload["ok"] is True
        assert "available" in payload["data"]

    def test_logs_missing_file(self, tmp_path):
        result = cmd_system_logs(_Args(file=str(tmp_path / "nope.log"), tail=5))
        assert result.ok is False
        assert result.error_code == "BUSINESS_ERROR"

    def test_logs_tail(self, tmp_path):
        log_file = tmp_path / "x.log"
        log_file.write_text("\n".join(f"line {i}" for i in range(20)), encoding="utf-8")
        result = cmd_system_logs(_Args(file=str(log_file), tail=3))
        assert result.ok is True
        assert result.data["lines"] == ["line 17", "line 18", "line 19"]
        assert result.data["total_lines"] == 20

    def test_settings_masked_and_serializable(self):
        result = cmd_system_settings(_Args())
        payload = _roundtrip(result)
        assert payload["ok"] is True
        # 配置里不应出现明文密钥
        text = json.dumps(payload, ensure_ascii=False)
        assert '"***"' in text or "token" not in text.lower()
