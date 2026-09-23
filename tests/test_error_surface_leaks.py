"""对外错误面不得带出服务端路径（py/stack-trace-exposure 的整改回归）。

覆盖 2026-09-24 分诊确认的两类真实外泄：

1. ``routes/system/settings.py`` —— 7 处把 ``str(exc)`` 原样放进响应
   （CodeQL 标了 725/769/802/819/852 五处，另有 639/702 两处
   ``HTTPException(detail=str(exc))`` 同类）；
2. ``persona_manager`` —— 固化失败与逐文件删除失败把 ``OSError`` 原文
   拼进用户可见消息（Windows 下该原文含绝对路径）。

统一走 ``error_surface.safe_error_message`` 后，这里断言的是**行为**：
异常消息里的路径在响应中不再出现，同时可读提示仍在。
"""

import sys
from pathlib import Path
from typing import Any

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))

from integrated_app.exceptions import (  # noqa: E402
    EngineSwitchError,
    InsufficientVRAMError,
    ModelLoadError,
    TTSError,
)

WIN_PATH = r"C:\Users\dev\TTS_MultiModel\config\general_settings.json"
POSIX_PATH = "/opt/tts/config/general_settings.json"


def _assert_no_path(out: str, secret: str) -> None:
    assert secret not in out, out
    assert "[PATH]" in out or "不存在" in out or "权限" in out, out


class TestSettingsErrorSurface:
    @pytest.fixture
    def settings_mod(self):
        from integrated_app.routes.system import settings

        return settings

    async def test_get_generation_defaults_hides_path(self, settings_mod, monkeypatch):
        async def _boom(*_a: Any, **_k: Any) -> None:
            raise OSError(f"[Errno 13] Permission denied: '{POSIX_PATH}'")

        monkeypatch.setattr(settings_mod, "_load_json_file", _boom)
        out = await settings_mod.get_generation_defaults()

        assert out["status"] == "error"
        _assert_no_path(out["message"], POSIX_PATH)

    async def test_save_general_settings_hides_path(self, settings_mod, monkeypatch):
        async def _boom(*_a: Any, **_k: Any) -> None:
            raise OSError(f"[Errno 28] No space left on device: '{WIN_PATH}'")

        class _Req:
            async def json(self) -> dict[str, Any]:
                return {"language": "zh", "theme": "dark"}

        monkeypatch.setattr(settings_mod, "_save_json_file", _boom)
        out = await settings_mod.save_general_settings(_Req())  # type: ignore[arg-type]

        assert out["status"] == "error"
        _assert_no_path(out["message"], WIN_PATH)

    def test_reset_settings_http_detail_hides_path(self, settings_mod, monkeypatch):
        from fastapi import HTTPException

        def _boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError(f"写入 config.yaml 失败: {WIN_PATH}")

        monkeypatch.setattr(settings_mod, "_save_yaml_raw", _boom)
        with pytest.raises(HTTPException) as ei:
            settings_mod.reset_settings()

        _assert_no_path(str(ei.value.detail), WIN_PATH)


class TestPersonaManagerMessageRedaction:
    @pytest.fixture
    def persona_env(self, tmp_path, monkeypatch):
        from integrated_app import persona_manager as pm

        monkeypatch.setattr(pm, "PERSONA_DIR", str(tmp_path))
        return tmp_path, pm

    def test_delete_failure_message_has_no_path(self, persona_env, monkeypatch):
        root, pm = persona_env
        (root / "alice.wav").write_bytes(b"RIFF....WAVEfmt ")

        def _locked(path: str) -> None:
            raise OSError(32, f"另一个程序正在使用此文件: '{path}'")

        monkeypatch.setattr(pm.os, "remove", _locked)
        ok, msg = pm.delete_persona("alice")

        assert ok is False
        assert "alice.wav" in msg, "文件名要留下，否则用户不知道该关哪个程序"
        _assert_no_path(msg, str(root))

    def test_save_failure_message_has_no_path(self, persona_env, monkeypatch):
        root, pm = persona_env

        def _boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError(f"ffmpeg 退出码 1，输出目录 {POSIX_PATH}")

        monkeypatch.setattr(pm, "preprocess_and_save_temp", _boom)
        msg, needs_confirm = pm.fn_save_persona("alice", b"RIFF", "参考文本")

        assert needs_confirm is False
        assert "❌" in msg
        _assert_no_path(msg, POSIX_PATH)


class TestGenerateSideErrorSurface:
    """生成侧的 ``_safe_error_msg`` 与 SSE / 错误片段共用同一把尺子。"""

    @pytest.mark.parametrize(
        "exc",
        [
            TTSError(f"读取 {POSIX_PATH} 失败"),
            ModelLoadError(f"权重缺失 {WIN_PATH}"),
            InsufficientVRAMError(f"需要 6.2GB，{POSIX_PATH} 所在盘空闲不足"),
            EngineSwitchError(f"切换到 voxcpm2 失败：{WIN_PATH}"),
            RuntimeError(f"CUDA 初始化失败 at {POSIX_PATH}"),
            ValueError(f"参数非法 {WIN_PATH}"),
        ],
    )
    def test_safe_error_msg_redacts(self, exc: BaseException) -> None:
        from integrated_app.routes.generate.utils import _safe_error_msg

        out = _safe_error_msg(exc)

        assert POSIX_PATH not in out
        assert WIN_PATH not in out

    def test_error_html_redacts_at_render(self):
        """``_error_html`` 是错误文本的统一出口，脱敏挂在渲染入口而非各调用方。"""
        from integrated_app.routes.generate import utils as gu

        class _Req:
            """只够走通 ``get_lang``；没有 ``app`` 属性，因此渲染落到内联降级分支。"""

            query_params: dict[str, str] = {}
            cookies: dict[str, str] = {}

        resp = gu._error_html(_Req(), f"生成失败: 无法读取 {POSIX_PATH}")
        body = resp.body.decode("utf-8")

        assert POSIX_PATH not in body
        assert "[PATH]" in body
        assert POSIX_PATH not in resp.headers.get("HX-Trigger", "")
