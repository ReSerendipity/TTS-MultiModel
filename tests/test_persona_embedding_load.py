"""覆盖率预算门禁补测：``persona_manager.load_persona_embedding`` 的加载与降级路径。

背景：CI 覆盖率预算 `"persona_manager.py" = 30` 实测 28.2%（74/262），差约 2.8pp。
本文件覆盖此前未覆盖的 `.pt` 预计算嵌入加载链路——新格式（含 `_meta.origin`）、
旧格式（无 `_meta` 向后兼容）、origin 不匹配告警、`.pt` 损坏降级删除、缓存命中，
以及缺 `.wav` 时的早退返回 None。
"""

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))


@pytest.fixture
def persona_env(tmp_path, monkeypatch):
    """把 PERSONA_DIR 指向临时目录，并保证 VoxCPM2 未就绪（走降级分支）。"""
    from integrated_app import persona_manager as pm

    monkeypatch.setattr(pm, "PERSONA_DIR", str(tmp_path))
    monkeypatch.setattr(pm.registry, "is_voxcpm_ready", lambda: False)
    return tmp_path, pm


def _write_persona_files(root: Path, name: str, *, wav: bool = True, txt: bool = True) -> None:
    if wav:
        (root / f"{name}.wav").write_bytes(b"RIFF....WAVEfmt ")
    if txt:
        (root / f"{name}.txt").write_text("参考文本", encoding="utf-8")


def _save_pt(path: Path, payload, *, origin: str | None, old_format: bool = False) -> None:
    import torch

    if old_format:
        torch.save(payload, str(path))
    else:
        torch.save(
            {"data": payload, "_meta": {"origin": origin, "format_version": 1}},
            str(path),
        )


class TestLoadPersonaEmbedding:
    """load_persona_embedding 的磁盘/缓存/降级路径。"""

    def test_returns_none_when_wav_missing(self, persona_env):
        """缺 .wav → 直接返回 None（不进入在线计算分支）。"""
        root, pm = persona_env
        assert pm.load_persona_embedding("no_such_persona") is None

    def test_loads_new_format_pt(self, persona_env):
        """新格式 .pt（_meta.origin 匹配）→ 返回 data。"""
        root, pm = persona_env
        _write_persona_files(root, "alice")
        payload = ("alice.wav", "参考文本")
        _save_pt(root / "alice.pt", payload, origin=pm.PERSONA_PT_ORIGIN)

        assert pm.load_persona_embedding("alice") == payload

    def test_origin_mismatch_still_loads_data(self, persona_env):
        """origin 不匹配 → 告警但仍返回 data（外部导入文件不阻断加载）。"""
        root, pm = persona_env
        _write_persona_files(root, "bob")
        payload = ("bob.wav", "外部导入")
        _save_pt(root / "bob.pt", payload, origin="some-other-tool v0")

        assert pm.load_persona_embedding("bob") == payload

    def test_old_format_pt_backward_compatible(self, persona_env):
        """旧格式 .pt（无 _meta）→ 原样返回（向后兼容）。"""
        root, pm = persona_env
        _write_persona_files(root, "carol")
        payload = ("carol.wav", "旧格式")
        _save_pt(root / "carol.pt", payload, origin=None, old_format=True)

        assert pm.load_persona_embedding("carol") == payload

    def test_cache_hit_returns_same_object(self, persona_env):
        """第二次调用命中内存缓存，返回同一对象。"""
        root, pm = persona_env
        _write_persona_files(root, "dave")
        payload = ("dave.wav", "缓存")
        _save_pt(root / "dave.pt", payload, origin=pm.PERSONA_PT_ORIGIN)

        first = pm.load_persona_embedding("dave")
        second = pm.load_persona_embedding("dave")
        assert first == second

    def test_corrupt_pt_deleted_and_engine_not_loaded(self, persona_env):
        """损坏 .pt → 删除该文件并降级在线计算；引擎未就绪时抛 EngineNotLoadedError。"""
        from integrated_app.exceptions import EngineNotLoadedError

        root, pm = persona_env
        _write_persona_files(root, "erin")
        pt = root / "erin.pt"
        pt.write_bytes(b"\x00\x01\x02 not-a-pickle")

        with pytest.raises(EngineNotLoadedError):
            pm.load_persona_embedding("erin")

        assert not pt.exists(), "损坏的 .pt 应被删除以降级在线计算"

    def test_missing_txt_still_uses_pt(self, persona_env):
        """缺 .txt 不影响 .pt 加载（ref_text 为空即可）。"""
        root, pm = persona_env
        _write_persona_files(root, "frank", txt=False)
        payload = ("frank.wav", "")
        _save_pt(root / "frank.pt", payload, origin=pm.PERSONA_PT_ORIGIN)

        assert pm.load_persona_embedding("frank") == payload
