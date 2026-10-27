# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""IndexTTS 代码包引导（reference_repos sys.path 注入）单元测试。

不依赖 reference_repos/index-tts 是否真实存在（CI 无克隆）：
目录形态用 tmp_path 伪造，importable 状态用 monkeypatch 模拟。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")

from integrated_app.engines.indextts2_engine import IndexTTS2Engine  # noqa: E402


@pytest.fixture()
def _restore_sys_path() -> Any:
    """保住 sys.path 与已导入模块，测试后回滚。"""
    saved_path = list(sys.path)
    saved_modules = {k: v for k, v in sys.modules.items() if k == "indextts" or k.startswith("indextts.")}
    yield
    sys.path[:] = saved_path
    for name in list(sys.modules):
        if (name == "indextts" or name.startswith("indextts.")) and name not in saved_modules:
            del sys.modules[name]


@pytest.mark.usefixtures("_restore_sys_path")
class TestEnsureIndexttsImportable:
    def test_missing_repo_raises_with_download_guidance(self, tmp_path: Any, monkeypatch: Any) -> None:
        """引导目录缺失 → ImportError，且文案指向下载管理器。"""
        monkeypatch.setattr(IndexTTS2Engine, "REFERENCE_REPO_DIR", str(tmp_path / "index-tts"))
        with pytest.raises(ImportError, match="download_models.py --model index-tts"):
            IndexTTS2Engine._ensure_indextts_importable()

    def test_present_repo_inserts_sys_path_and_imports(self, tmp_path: Any, monkeypatch: Any) -> None:
        """引导目录存在（含 indextts/ 包）→ sys.path 注入且可导入。"""
        fake_repo = tmp_path / "index-tts"
        pkg = fake_repo / "indextts"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("BOOTSTRAPPED = True" + chr(10), encoding="utf-8")
        monkeypatch.setattr(IndexTTS2Engine, "REFERENCE_REPO_DIR", str(fake_repo))

        IndexTTS2Engine._ensure_indextts_importable()

        assert str(fake_repo) in sys.path
        import indextts  # noqa: F401 — 引导后应解析到伪造包

        assert indextts.BOOTSTRAPPED is True

    def test_noop_when_already_importable(self, tmp_path: Any, monkeypatch: Any) -> None:
        """indextts 已可导入（用户 pip install -e / 重复引导）→ 不动 sys.path。"""
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name: object() if name == "indextts" else importlib.util.find_spec(name),
        )
        before = list(sys.path)
        monkeypatch.setattr(IndexTTS2Engine, "REFERENCE_REPO_DIR", str(tmp_path / "nope"))
        IndexTTS2Engine._ensure_indextts_importable()
        assert sys.path == before

    def test_prefers_vendored_guide_over_pip_message(self, tmp_path: Any, monkeypatch: Any) -> None:
        """错误文案必须包含下载管理器路径，不再指引用户 pip install -e。"""
        monkeypatch.setattr(IndexTTS2Engine, "REFERENCE_REPO_DIR", str(tmp_path / "index-tts"))
        with pytest.raises(ImportError) as ei:
            IndexTTS2Engine._ensure_indextts_importable()
        msg = str(ei.value)
        assert "reference_repos" in msg
        assert "pip install -e" not in msg
