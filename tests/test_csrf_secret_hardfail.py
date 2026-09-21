"""CSRF 密钥必须硬失败的前置条件测试（不启动服务、不加载模型）。

守卫的是同一件事的两个面：
- `app_server._load_or_create_csrf_secret`：读不出来 / 写不进去就**拒绝启动**，
  不再 `logger.warning("回退到无签名模式")` 之后照常接请求；
- `CSRFMiddleware.__init__`：拿到空 `secret_key` 直接 `ValueError`，
  防止别的装配点把这道防护悄悄关掉。

对应 PR #81 里那条一直没能进 main 的内容（`app_server.py:818-819` 的静默降级至今还在）。
"""

from __future__ import annotations

import pytest

from integrated_app.app_server import _load_or_create_csrf_secret
from integrated_app.middleware.csrf import CSRFMiddleware


def _noop_app() -> object:
    async def app(scope: object, receive: object, send: object) -> None:  # noqa: ARG001
        return None

    return app


class TestCsrfSecretHardFail:
    def test_creates_and_persists_the_secret(self, tmp_path) -> None:
        path = tmp_path / "data" / ".csrf_secret"
        secret = _load_or_create_csrf_secret(str(path))
        assert len(secret) >= 32, "密钥长度不足以做 HMAC"
        assert path.read_text(encoding="utf-8").strip() == secret, "没落盘 = 每次重启换一把钥匙"
        assert _load_or_create_csrf_secret(str(path)) == secret, "第二次读应当复用同一把"

    def test_empty_file_is_replaced_by_a_real_secret(self, tmp_path) -> None:
        path = tmp_path / ".csrf_secret"
        path.write_text("   \n", encoding="utf-8")
        assert _load_or_create_csrf_secret(str(path)).strip(), "空白内容必须被重新生成，不能当有效密钥"

    def test_unwritable_location_raises_with_actionable_advice(self, tmp_path) -> None:
        blocker = tmp_path / "blocked"
        blocker.write_text("我是一个文件，不是目录", encoding="utf-8")
        with pytest.raises(RuntimeError) as exc:
            _load_or_create_csrf_secret(str(blocker / "sub" / ".csrf_secret"))
        msg = str(exc.value)
        assert "拒绝以「无签名模式」启动" in msg, msg
        assert "可写" in msg and "docker-compose" in msg, f"报错没给出可操作出路：{msg[:160]}"

    def test_middleware_refuses_an_empty_secret(self) -> None:
        with pytest.raises(ValueError, match="非空 secret_key") as exc:
            CSRFMiddleware(_noop_app(), secret_key="")  # type: ignore[arg-type]
        assert "data/.csrf_secret" in str(exc.value), "报错要点名去哪修"

    def test_middleware_still_mounts_with_a_real_secret(self) -> None:
        """防空转：只有"正常路径仍然可装配"成立，上一条拒绝才有意义。"""
        mw = CSRFMiddleware(_noop_app(), secret_key="x" * 48)  # type: ignore[arg-type]
        assert mw._secret_key == "x" * 48
