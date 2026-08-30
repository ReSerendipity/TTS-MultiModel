"""Smoke tests - minimal set to verify core functionality works.

These tests should:
1. Run quickly (<30s total)
2. Not require GPU or model loading
3. Cover critical paths that would indicate a broken build

Usage:
    pytest -m smoke  # Run only smoke tests
"""

import os
import sys

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


pytestmark = pytest.mark.smoke


class TestConfigLoading:
    """Verify configuration loads without errors."""

    def test_config_loads_successfully(self):
        """Config should load from config.yaml without exceptions."""
        from integrated_app.config import get_config

        config = get_config()
        assert config is not None

    def test_config_has_api_auth_section(self):
        """Config should have api_auth section."""
        from integrated_app.config import get_config

        config = get_config()
        assert hasattr(config, "api_auth")


class TestCoreUtils:
    """Verify core utility functions work."""

    def test_progress_manager_create(self):
        """ProgressManager should instantiate and track progress."""
        from integrated_app.progress import ProgressManager

        pm = ProgressManager()
        pm.start(total_segments=5, phase="测试")
        state = pm.get_state()
        assert state["phase"] == "测试"
        assert state["total_segments"] == 5
        pm.complete()
        assert pm.get_state()["is_complete"] is True

    def test_text_segmenter_can_be_imported(self):
        """Text segmenter module should be importable."""
        from integrated_app.text_segmenter import TextSegmenter

        # Create with valid params (min_chars cannot exceed max_chars)
        segmenter = TextSegmenter(max_chars=100, min_chars=50)
        assert segmenter is not None


class TestModels:
    """Test config models."""

    def test_generation_config_defaults(self):
        """GenerationConfig should have sensible defaults."""
        from integrated_app.config_models import GenerationConfig

        config = GenerationConfig()
        assert config.max_chars_per_segment == 200
        assert config.default_sample_rate == 24000


class TestRegistryPattern:
    """Verify registry pattern exists."""

    def test_engine_registry_class_exists(self):
        """EngineRegistry class should be importable."""
        from integrated_app.engine_interface import EngineRegistry

        assert EngineRegistry is not None


class TestImportSanity:
    """Basic import sanity checks for critical modules."""

    def test_task_queue_module(self):
        """Task queue module should be importable."""
        from integrated_app.task_queue import init_queue, shutdown_queue

        assert init_queue is not None
        assert shutdown_queue is not None

    def test_cache_utils(self):
        """Cache utilities should be importable."""
        from integrated_app.cache import LRUCache

        cache = LRUCache(maxsize=10)
        cache.put("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_audio_processing_module(self):
        """Audio processing module should be importable."""
        import integrated_app.audio_processing as ap

        assert ap is not None


class TestNoPhantomImports:
    """防止「调用点存在、定义已丢失」的幻影引用（本仓已两次因此静默故障）。

    背景：model_manager 拆分为 model_manager_core 时，re-export 名单与实际函数
    失去同步。第一次是 ruff --fix 的 F401 删掉整段再导出，导致 /api/model/* 全量
    404；第二次是 unload_all_models 在 core 里根本不存在，而 app_server 的
    lifespan shutdown 仍在 import 它 —— 被 except 吞成一条 error 日志，
    于是每次优雅关闭都不卸载模型、不释放显存，且没有任何测试报警。

    这类问题的共同点是**被 try/except 或运行时懒导入掩盖**，只有静态对账能发现，
    所以这里直接扫 AST 而不是逐个手写断言。
    """

    @staticmethod
    def _app_server_path() -> str:
        return os.path.join(_APP_DIR, "integrated_app", "app_server.py")

    def test_app_server_relative_imports_all_resolve(self):
        """app_server.py 里每一处 `from .X import a, b` 的名字都必须真实存在。"""
        import ast
        import importlib

        with open(self._app_server_path(), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="app_server.py")

        targets: list[tuple[str, list[str]]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                targets.append((node.module, [alias.name for alias in node.names]))

        assert targets, "未扫到任何相对导入，说明本测试的解析方式已失效"

        missing: list[str] = []
        for module_name, names in targets:
            try:
                mod = importlib.import_module(f"integrated_app.{module_name}")
            except Exception as exc:  # noqa: BLE001 - 记录后继续检查其余模块
                missing.append(f"integrated_app.{module_name}: <import failed: {exc}>")
                continue
            for name in names:
                if name == "*":
                    continue
                if not hasattr(mod, name):
                    missing.append(f"integrated_app.{module_name}.{name}")

        assert missing == [], f"app_server.py 引用了不存在的名字（幻影引用）：{missing}"

    def test_shutdown_can_actually_unload_models(self):
        """显式钉住 unload_all_models —— 它曾丢失并被 except 静默吞掉。"""
        from integrated_app.model_manager import unload_all_models

        assert callable(unload_all_models)
        # 无模型加载时必须安全返回（关闭路径不得抛异常中断进程退出）
        unload_all_models()

    def test_no_wrong_depth_relative_imports(self):
        """扫描 app/integrated_app 下所有相对导入，确认目标模块真实存在。

        WHY：本仓已三次栽在相对导入深度上（``..engines``、``.middleware`` 等），
        共同特征是**只在特定运行路径才触发**（后台线程、懒导入），因此单测跑不到、
        CI 全绿也发现不了，线上只留下一条 debug 日志或线程 stderr traceback。
        静态对账是唯一可靠的检测手段。vendor/ 是上游代码，不参与本仓包结构，跳过。
        """
        import ast
        import os
        from pathlib import Path

        root = Path(_APP_DIR) / "integrated_app"
        offenders: list[str] = []
        checked = 0

        for path in sorted(root.rglob("*.py")):
            rel = os.path.relpath(path, _APP_DIR).replace("\\", "/")
            if "/vendor/" in f"/{rel}/":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError) as exc:
                offenders.append(f"{rel}: <unparsable: {exc}>")
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.level or not node.module:
                    continue
                checked += 1
                # level=1 → 当前包目录；level=2 → 上一级包目录，依此类推
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                target = base.joinpath(*node.module.split("."))
                if not (target.with_suffix(".py").exists() or target.is_dir()):
                    offenders.append(f"{rel}:{node.lineno}: from {'.' * node.level}{node.module} -> 目标不存在")

        assert checked > 50, f"仅检查了 {checked} 处相对导入，扫描逻辑可能已失效"
        assert offenders == [], "发现失效的相对导入：\n" + "\n".join(offenders)
