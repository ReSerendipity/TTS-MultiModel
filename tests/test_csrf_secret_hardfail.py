"""CSRF 密钥必须硬失败的前置条件测试（不启动服务、不加载模型）。

守卫的是同一件事的两个面：
- `app_server._load_or_create_csrf_secret`：读不出来 / 写不进去就**拒绝启动**，
  不再 `logger.warning("回退到无签名模式")` 之后照常接请求；
- `CSRFMiddleware.__init__`：拿到空 `secret_key` 直接 `ValueError`，
  防止别的装配点把这道防护悄悄关掉。

对应 PR #81 提过、一直没进 main 的那半边（`app_server.py` 里"回退到无签名模式"的静默降级）。
#81 的另一半（把 transformers 下界抬到 4.53）已被实测推翻并按 #103 回退到 4.52.1。
"""

from __future__ import annotations

import os

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


@pytest.mark.skipif(os.name != "posix", reason="Windows 的 st_mode 不表达 POSIX 权限位")
class TestCsrfSecretFileMode:
    """CodeQL #1（py/clear-text-storage-sensitive-data）对应的真加固那半边。

    抑制注释只解释"为什么必须落盘"；"落下来只能是主人可读"这件事由这里守着。
    """

    def test_generated_secret_file_is_owner_only(self, tmp_path) -> None:
        decoy = tmp_path / "decoy"  # 反空验证：这台机器上普通文件的默认权限确实带着 other 位
        decoy.write_text("x", encoding="utf-8")
        assert decoy.stat().st_mode & 0o077, "默认权限已经是 0600，这条断言在该文件系统上没有区分度"

        path = tmp_path / ".csrf_secret"
        _load_or_create_csrf_secret(str(path))
        assert path.stat().st_mode & 0o077 == 0, f"密钥文件带着 group/other 权限位：{oct(path.stat().st_mode)}"

    def test_preexisting_broad_mode_is_tightened_without_losing_the_secret(self, tmp_path) -> None:
        """老版本（0644）建出来的密钥：读出来的值必须原样保留，权限必须被收紧。"""
        path = tmp_path / ".csrf_secret"
        path.write_text("k" * 48, encoding="utf-8")
        os.chmod(str(path), 0o644)
        assert path.stat().st_mode & 0o077

        assert _load_or_create_csrf_secret(str(path)) == "k" * 48
        assert path.stat().st_mode & 0o077 == 0, f"没收紧：{oct(path.stat().st_mode)}"

    def test_empty_preexisting_file_is_rewritten_not_fatal(self, tmp_path) -> None:
        """空文件走的是 O_TRUNC 分支：不能因为用了 O_EXCL 而把"启动"变成"文件已存在"失败。"""
        path = tmp_path / ".csrf_secret"
        path.write_text("", encoding="utf-8")
        os.chmod(str(path), 0o666)
        secret = _load_or_create_csrf_secret(str(path))
        assert secret and path.stat().st_mode & 0o077 == 0
