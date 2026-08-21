# -*- coding: utf-8 -*-
"""引擎接口契约测试"""
import os
import sys
from abc import ABC

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")


class TestEngineInterface:
    """测试引擎接口契约"""

    def test_engine_interface_exists(self):
        """测试引擎接口类存在"""
        from integrated_app.engine_interface import TTSEngine
        assert TTSEngine is not None

    def test_engine_interface_is_protocol(self):
        """测试引擎接口是 Protocol"""
        from integrated_app.engine_interface import TTSEngine
        from typing import Protocol
        # TTSEngine 是 runtime_checkable Protocol
        assert hasattr(TTSEngine, '__protocol_attrs__') or issubclass(type(TTSEngine), type(Protocol))

    def test_engine_interface_has_required_methods(self):
        """测试引擎接口定义了必要的方法"""
        from integrated_app.engine_interface import TTSEngine
        required_methods = ['is_ready', 'load', 'unload', 'generate_voice_design',
                            'generate_voice_clone', 'generate_script', 'generate_streaming']
        for method_name in required_methods:
            assert hasattr(TTSEngine, method_name), f"TTSEngine missing method: {method_name}"

    def test_engine_registry_exists(self):
        """测试引擎注册表存在"""
        from integrated_app.engine_interface import engine_registry
        assert engine_registry is not None

    def test_engine_registry_has_list_engines(self):
        """测试引擎注册表有 list_engines 方法"""
        from integrated_app.engine_interface import engine_registry
        engines = engine_registry.list_engines()
        assert isinstance(engines, list)
