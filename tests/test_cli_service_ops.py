#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""P3 运维域（模型/引擎/下载/LoRA/训练）适配层的单元测试（ADR-0005 P3）。

不加载任何模型、不启动训练进程：只验证结构、纯函数与参数校验。
"""

from __future__ import annotations

import json
import os

from integrated_app.cli_service_ops import (
    _resolve_save_path,
    cmd_download_catalog,
    cmd_download_start,
    cmd_download_uninstall,
    cmd_engines_info,
    cmd_engines_list,
    cmd_lora_list,
    cmd_lora_toggle,
    cmd_train_log,
    cmd_train_start,
)


class _Args:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _roundtrip(result) -> dict:
    return json.loads(json.dumps(result.to_dict(), ensure_ascii=False))


class TestEngines:
    def test_list_has_engines(self):
        result = cmd_engines_list(_Args())
        payload = _roundtrip(result)
        assert payload["ok"] is True
        engines = payload["data"]["engines"]
        assert isinstance(engines, list) and engines
        names = {e["name"] for e in engines}
        # 项目声明的 7 个引擎至少包含这些核心项
        assert {"voxcpm2", "edge-tts"}.issubset(names)
        for e in engines:
            assert "capabilities" in e and "metadata" in e

    def test_info_requires_engine_id(self):
        assert cmd_engines_info(_Args(engine_id="")).error_code == "USAGE_ERROR"

    def test_info_known_engine(self):
        result = cmd_engines_info(_Args(engine_id="voxcpm2"))
        assert result.ok is True
        assert result.data["name"] == "voxcpm2"


class TestDownload:
    def test_catalog_shape(self):
        result = cmd_download_catalog(_Args())
        payload = _roundtrip(result)
        assert payload["ok"] is True
        assert "models" in payload["data"]

    def test_start_requires_valid_kind(self):
        assert cmd_download_start(_Args(kind="bogus", target="")).error_code == "USAGE_ERROR"

    def test_start_model_requires_target(self):
        assert cmd_download_start(_Args(kind="model", target="")).error_code == "USAGE_ERROR"

    def test_start_model_rejects_unknown_target(self):
        """探测阶段就拒绝未知模型，避免进入下载后才失败。"""
        result = cmd_download_start(_Args(kind="model", target="__not_a_model__"))
        assert result.ok is False
        assert result.error_code == "USAGE_ERROR"

    def test_uninstall_requires_valid_kind(self):
        assert cmd_download_uninstall(_Args(kind="bogus", target="")).error_code == "USAGE_ERROR"
        assert cmd_download_uninstall(_Args(kind="model", target="")).error_code == "USAGE_ERROR"


class TestLora:
    def test_list_shape(self):
        result = cmd_lora_list(_Args())
        payload = _roundtrip(result)
        assert payload["ok"] is True
        assert "checkpoints" in payload["data"]
        assert payload["data"]["count"] == len(payload["data"]["checkpoints"])

    def test_toggle_requires_flag(self):
        result = cmd_lora_toggle(_Args(enabled=None))
        assert result.ok is False
        assert result.error_code == "USAGE_ERROR"


class TestTrainPaths:
    def test_default_save_path(self):
        from integrated_app.config import PROJECT_ROOT

        assert _resolve_save_path(_Args(save_path=None)) == os.path.join(PROJECT_ROOT, "lora", "finetune")
        # 空串也必须落到默认值，不能被 abspath 解析成 cwd
        assert _resolve_save_path(_Args(save_path="")) == os.path.join(PROJECT_ROOT, "lora", "finetune")

    def test_explicit_save_path(self, tmp_path):
        assert _resolve_save_path(_Args(save_path=str(tmp_path))) == os.path.abspath(str(tmp_path))

    def test_train_log_missing_dir(self, tmp_path):
        result = cmd_train_log(_Args(save_path=str(tmp_path), tail=10))
        assert result.ok is False
        assert result.error_code == "BUSINESS_ERROR"

    def test_train_start_requires_manifest(self, tmp_path):
        result = cmd_train_start(_Args(save_path=str(tmp_path), train_manifest=""))
        assert result.ok is False
        assert result.error_code == "USAGE_ERROR"

    def test_train_start_missing_manifest_file(self, tmp_path):
        result = cmd_train_start(_Args(save_path=str(tmp_path), train_manifest=str(tmp_path / "nope.jsonl")))
        assert result.ok is False
        assert result.error_code == "USAGE_ERROR"
