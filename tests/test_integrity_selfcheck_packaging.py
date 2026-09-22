"""完整性清单在**打包**与 **enforce 语义**上的两条守卫（2026-09-22 由 v2.2.2 的产物核对引出）。

背景：`config.yaml` 默认 `security.integrity_selfcheck.enforce: true`，而 v2.2.2 的 wheel 里
`integrated_app/security/` 只有 `.py` —— 清单、Ed25519 签名、验签公钥三件都没打进包。
当时 `run_startup_selfcheck()` 在"清单不存在"分支只 `logger.info("跳过自检")` 就返回，
于是**纯 pip 安装的那条部署路径上 P0 完整性保护一条都没执行，而配置声称它在强制运行**。

本文件守住修法两侧：
- 行为侧：enforce 开着却没清单 → 拒绝启动（不许静默降级）；enforce 关着仍按跳过处理；
- 产物侧：`[tool.setuptools.package-data]` 必须逐条点名那三件，且 CI 的构建作业必须真的
  去解开 wheel 核对（只查配置不查产物，setuptools 行为变了也不会红）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from integrated_app.security import integrity_selfcheck as isc
from integrated_app.security.integrity_selfcheck import _CORE_MODULES, run_startup_selfcheck

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / "pyproject.toml"
_CI_WF = _ROOT / ".github" / "workflows" / "ci.yml"

#: 必须随包分发的完整性三件套（相对 integrated_app/）
INTEGRITY_FILES = (
    "security/integrity_manifest.json",
    "security/integrity_manifest.json.sig.ed25519",
    "security/manifest_signing_public_key.pem",
)


def _package_data_patterns() -> list[str]:
    """取 [tool.setuptools.package-data] 里 integrated_app 那一串的条目（不依赖 tomllib，CI 有 3.10）。"""
    text = _PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r"^\[tool\.setuptools\.package-data\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    assert m, "pyproject.toml 里没有 [tool.setuptools.package-data] 段"
    block = m.group(1)
    key = re.search(r'"integrated_app"\s*=\s*\[(.*?)\]', block, re.S)
    assert key, "package-data 里没有 integrated_app 这个键（templates/static 会一起丢）"
    return re.findall(r'"([^"]+)"', key.group(1))


class TestMissingManifestUnderEnforce:
    def test_enforce_refuses_to_start_without_the_manifest(self, tmp_path, monkeypatch) -> None:
        missing = tmp_path / "integrity_manifest.json"
        monkeypatch.setattr(isc, "_get_manifest_path", lambda: missing)
        with pytest.raises(RuntimeError) as exc:
            run_startup_selfcheck(enforce=True)
        msg = str(exc.value)
        assert "拒绝以「无校验模式」启动" in msg, msg
        # 报错必须给得出路，而不只是"失败了"
        assert "generate_integrity_manifest.py" in msg, "没告诉源码检出的人怎么补清单"
        assert "package-data" in msg, "没告诉装包的人这是 wheel 漏打文件"
        assert "enforce=false" in msg, "没说明要关闭保护必须显式改配置"

    def test_without_enforce_it_still_skips(self, tmp_path, monkeypatch) -> None:
        """非强制模式保持原语义：跳过并返回 skipped，不抛。"""
        monkeypatch.setattr(isc, "_get_manifest_path", lambda: tmp_path / "nope.json")
        out = run_startup_selfcheck(enforce=False)
        assert out["skipped"] == len(_CORE_MODULES)
        assert out["manifest_signed"] is False

    def test_repository_checkout_has_the_trio_so_enforce_passes(self) -> None:
        """防空转：上面那条"没清单就拒启动"必须只在**真没清单**时触发。

        仓库自带三件套，所以 enforce=True 在源码检出下应正常跑完 16 个模块且 0 失败。
        若这条红了，说明清单/签名与代码不同步（GOTCHAS #77/#142 那一族），不是本测试太严。
        """
        out = run_startup_selfcheck(enforce=True)
        assert out["total"] == len(_CORE_MODULES), out
        assert out["failed"] == 0, f"清单与当前代码不同步：{out['failed_files']}"


class TestIntegrityFilesArePackaged:
    @pytest.mark.parametrize("rel", INTEGRITY_FILES)
    def test_named_explicitly_in_package_data(self, rel: str) -> None:
        pats = _package_data_patterns()
        assert rel in pats, f"package-data 没点名 {rel}（当前条目：{pats}）→ 装出来的包里没有它"

    @pytest.mark.parametrize("rel", INTEGRITY_FILES)
    def test_the_file_actually_exists_in_tree(self, rel: str) -> None:
        path = _ROOT / "app" / "integrated_app" / rel
        assert path.is_file(), f"{rel} 在 package-data 里被点名，但仓库里没有这个文件"

    def test_ci_builds_and_inspects_the_wheel(self) -> None:
        """只查配置不够：setuptools 的打包行为变了，配置看着对、产物里却没有（本次就是这么发现的）。"""
        text = _CI_WF.read_text(encoding="utf-8")
        assert "python -m build" in text, "CI 不再构建 wheel，这条守卫就失去了对象"
        for rel in INTEGRITY_FILES:
            assert rel in text, f"CI 的构建作业没有核对 wheel 里的 {rel}"
        # 反空验证：判据不是恒真 —— 把清单文件名从 workflow 里去掉后必须不再匹配
        mutated = text.replace(INTEGRITY_FILES[0], "")
        assert INTEGRITY_FILES[0] not in mutated
