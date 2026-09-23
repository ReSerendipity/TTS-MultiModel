"""`error_surface.safe_error_message` 的脱敏覆盖面。

回归动机（2026-09-20 复核 py/stack-trace-exposure 时发现）：S-R6 的
`SENSITIVE_PATH_PATTERN` 脱敏只用在 `OSError` 与兜底分支上，而
`InsufficientVRAMError` / `EngineSwitchError` / `ModelLoadError` / `TTSError`
四条分支直接返回未脱敏的 `str(exc)` —— 这几个类恰恰是最常把 `model/` 下的
真实路径写进消息的。这里锁住「每一条分支都过脱敏」，防止以后新增分支时又漏。
"""

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))

from integrated_app.error_surface import safe_error_message  # noqa: E402
from integrated_app.exceptions import (  # noqa: E402
    EngineSwitchError,
    InsufficientVRAMError,
    ModelLoadError,
    TTSError,
)

POSIX_PATH = "/app/model/VoxCPM2/config.json"
WIN_PATH = "C:\\Users\\dev\\model\\IndexTTS-2.5\\config.json"


def _assert_redacted(out: str, secret: str) -> None:
    assert secret not in out, f"路径未脱敏: {out}"
    assert "[PATH]" in out, f"没有脱敏占位符，可能是整段被吞了: {out}"


class TestDomainErrorsAreRedacted:
    @pytest.mark.parametrize(
        "exc_cls,label",
        [
            (InsufficientVRAMError, "显存不足"),
            (EngineSwitchError, "引擎切换失败"),
            (ModelLoadError, "模型加载失败"),
            (TTSError, ""),
        ],
    )
    def test_posix_path_never_escapes(self, exc_cls, label):
        out = safe_error_message(exc_cls(f"读取 {POSIX_PATH} 失败"))
        assert out.startswith(label) or not label
        _assert_redacted(out, POSIX_PATH)

    @pytest.mark.parametrize("exc_cls", [InsufficientVRAMError, EngineSwitchError, ModelLoadError, TTSError])
    def test_windows_path_never_escapes(self, exc_cls):
        out = safe_error_message(exc_cls(f"加载失败：{WIN_PATH} 缺失"))
        _assert_redacted(out, WIN_PATH)


class TestOtherBranchesUnchanged:
    def test_file_not_found_is_generic_message(self):
        assert safe_error_message(FileNotFoundError(POSIX_PATH)) == "文件不存在或已被删除"

    def test_permission_error_is_generic_message(self):
        assert "权限不足" in safe_error_message(PermissionError(POSIX_PATH))

    def test_oserror_still_redacts(self):
        _assert_redacted(safe_error_message(OSError(f"IO 失败 {POSIX_PATH}")), POSIX_PATH)

    def test_unknown_exception_still_redacts(self):
        _assert_redacted(safe_error_message(RuntimeError(f"boom {POSIX_PATH}")), POSIX_PATH)

    def test_none_returns_unknown(self):
        assert safe_error_message(None) == "未知错误"

    def test_long_message_truncated(self):
        out = safe_error_message(RuntimeError("x" * 900))
        assert len(out) <= 203 and out.endswith("...")
