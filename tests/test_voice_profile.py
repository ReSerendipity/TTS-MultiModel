"""Voice Profile（P1-2）测试：资产层解析、跨引擎隔离、派生缓存命中与失效。

覆盖点：
1. ``load_voice_profile`` 读取参考音频 / 转写 / 标签 / 来源，并生成指纹；
2. 同一音色在不同引擎下解析出各自载荷，互不串味；
3. 派生缓存命中（compute 只调用一次）与源文件变化后的自动失效；
4. 显式 ``invalidate_voice_profile`` 按音色/引擎收窄；
5. VoxCPM2 兼容：仍走既有 ``.pt`` 路径，origin 校验语义不变。
"""

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    """临时音色目录 + 清空两级缓存，保证用例之间互不污染。"""
    from integrated_app import persona_manager as pm

    monkeypatch.setattr(pm, "PERSONA_DIR", str(tmp_path))
    monkeypatch.setattr(pm.registry, "is_voxcpm_ready", lambda: False)
    pm.VOICE_DERIVED_CACHE.clear()
    pm._persona_embedding_cache.clear()
    yield tmp_path, pm
    pm.VOICE_DERIVED_CACHE.clear()
    pm._persona_embedding_cache.clear()


def _make_persona(
    root: Path,
    name: str,
    *,
    text: str = "这是参考文本。",
    tags: tuple[str, ...] = ("女声", "温柔"),
    source: str = "upload/demo.wav",
    wav_bytes: bytes = b"RIFF....WAVEfmt ",
) -> None:
    """写入 .wav/.txt/metadata.json 三件套（不写 .pt）。"""
    from integrated_app.persona_metadata import PersonaMetadata

    (root / f"{name}.wav").write_bytes(wav_bytes)
    (root / f"{name}.txt").write_text(text, encoding="utf-8")
    meta = PersonaMetadata(
        name=name,
        tags=list(tags),
        category="自定义克隆",
        voice_type="青年",
        language="zh",
        source_audio=source,
        consent_state="granted",
    )
    (root / f"{name}.metadata.json").write_text(json.dumps(meta.to_dict(), ensure_ascii=False), encoding="utf-8")


def _save_pt(root: Path, name: str, payload, *, origin: str) -> None:
    import torch

    torch.save(
        {"data": payload, "_meta": {"origin": origin, "format_version": 1}},
        str(root / f"{name}.pt"),
    )


class TestVoiceProfileAsset:
    """资产层：参考音频、转写、标签、来源、指纹。"""

    def test_profile_reads_assets(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "alice", text="你好，世界。", tags=("少女", "甜美"), source="record/mic.wav")

        profile = pm.load_voice_profile("alice")

        assert profile is not None
        assert profile.reference_wav == str(root / "alice.wav")
        assert profile.reference_text == "你好，世界。"
        assert profile.tags == ("少女", "甜美")
        assert profile.category == "自定义克隆"
        assert profile.voice_type == "青年"
        assert profile.source == "record/mic.wav"
        assert profile.consent_state == "granted"
        assert profile.fingerprint, "有参考音频时指纹不应为空"

    def test_source_defaults_to_local_without_metadata(self, profile_env):
        root, pm = profile_env
        (root / "bare.wav").write_bytes(b"RIFF....WAVEfmt ")
        (root / "bare.txt").write_text("无元数据的转写", encoding="utf-8")

        profile = pm.load_voice_profile("bare")

        assert profile is not None
        assert profile.source == "local"
        assert profile.tags == ()
        assert profile.reference_text == "无元数据的转写"

    def test_missing_wav_returns_none(self, profile_env):
        _root, pm = profile_env
        assert pm.load_voice_profile("nobody") is None

    def test_traversal_name_rejected(self, profile_env):
        _root, pm = profile_env
        assert pm.load_voice_profile("../victim") is None

    def test_fingerprint_changes_when_wav_rewritten(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "fp")
        before = pm.load_voice_profile("fp").fingerprint

        (root / "fp.wav").write_bytes(b"RIFF....WAVEfmt " + b"x" * 64)
        after = pm.load_voice_profile("fp").fingerprint

        assert before != after, "覆盖写入后指纹必须变化"

    def test_to_dict_roundtrip(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "dict")
        profile = pm.load_voice_profile("dict")

        data = profile.to_dict()

        assert data["tags"] == ["女声", "温柔"]
        assert data["reference_wav"].endswith("dict.wav")
        json.dumps(data)  # 必须可 JSON 序列化


class TestCrossEngineResolution:
    """跨引擎解析：各引擎拿到自己的载荷，不共享派生结果。"""

    def test_reference_payload_without_compute(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "ref", text="参考转写")

        res = pm.resolve_voice_profile("ref", "breeze_tts2")

        assert res is not None
        assert res.cache_source == "reference"
        assert res.payload.wav_path == str(root / "ref.wav")
        assert res.payload.ref_text == "参考转写"
        assert res.payload.tags == ("女声", "温柔")

    def test_same_asset_two_engines_isolated(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "multi")
        calls = {"indextts2": 0, "breeze_tts2": 0}

        def make_compute(engine):
            def _compute(profile):
                calls[engine] += 1
                return {"engine": engine, "text": profile.reference_text}

            return _compute

        a = pm.resolve_voice_profile("multi", "indextts2", compute=make_compute("indextts2"))
        b = pm.resolve_voice_profile("multi", "breeze_tts2", compute=make_compute("breeze_tts2"))

        assert a.payload == {"engine": "indextts2", "text": "这是参考文本。"}
        assert b.payload == {"engine": "breeze_tts2", "text": "这是参考文本。"}
        assert calls == {"indextts2": 1, "breeze_tts2": 1}

    def test_compute_receives_profile(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "arg")
        seen = []

        pm.resolve_voice_profile("arg", "indextts2", compute=lambda p: seen.append(p) or "payload")

        assert seen and seen[0].name == "arg"
        assert seen[0].reference_text == "这是参考文本。"

    def test_empty_engine_rejected(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "eng")
        with pytest.raises(ValueError):
            pm.resolve_voice_profile("eng", "")


class TestDerivedCache:
    """派生缓存：命中、指纹失效、显式失效、LRU 容量。"""

    def test_cache_hit_skips_recompute(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "hit")
        sentinel = object()
        calls = []

        def compute(_profile):
            calls.append(1)
            return sentinel

        first = pm.resolve_voice_profile("hit", "indextts2", compute=compute)
        second = pm.resolve_voice_profile("hit", "indextts2", compute=compute)

        assert first.payload is sentinel
        assert second.payload is sentinel
        assert second.cache_source == "memory"
        assert len(calls) == 1, "命中缓存时不应重复派生"

    def test_source_change_invalidates_cache(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "stale")
        calls = []

        def compute(profile):
            calls.append(profile.reference_text)
            return f"payload-{len(calls)}"

        assert pm.resolve_voice_profile("stale", "indextts2", compute=compute).payload == "payload-1"

        # 覆盖参考音频：大小变化 → 指纹变化 → 旧条目失效
        (root / "stale.wav").write_bytes(b"RIFF....WAVEfmt " + b"y" * 128)
        second = pm.resolve_voice_profile("stale", "indextts2", compute=compute)

        assert second.cache_source == "computed"
        assert second.payload == "payload-2"
        assert len(calls) == 2

    def test_reference_payload_also_cached(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "refhit")

        first = pm.resolve_voice_profile("refhit", "openvoice")
        second = pm.resolve_voice_profile("refhit", "openvoice")

        assert first.cache_source == "reference"
        assert second.cache_source == "memory"
        assert second.payload is first.payload

    def test_invalidate_single_engine(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "narrow")
        calls = {"a": 0, "b": 0}

        pm.resolve_voice_profile("narrow", "engine_a", compute=lambda p: calls.__setitem__("a", calls["a"] + 1))
        pm.resolve_voice_profile("narrow", "engine_b", compute=lambda p: calls.__setitem__("b", calls["b"] + 1))

        removed = pm.invalidate_voice_profile("narrow", engine="engine_a")
        pm.resolve_voice_profile("narrow", "engine_a", compute=lambda p: calls.__setitem__("a", calls["a"] + 1))
        pm.resolve_voice_profile("narrow", "engine_b", compute=lambda p: calls.__setitem__("b", calls["b"] + 1))

        assert removed == 1
        assert calls == {"a": 2, "b": 1}, "只应失效指定引擎，另一个引擎仍命中"

    def test_invalidate_all_engines_for_name(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "wide")

        pm.resolve_voice_profile("wide", "engine_a")
        pm.resolve_voice_profile("wide", "engine_b")

        assert pm.invalidate_voice_profile("wide") == 2
        assert len(pm.VOICE_DERIVED_CACHE) == 0

    def test_deleted_persona_clears_cache(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "gone")
        pm.resolve_voice_profile("gone", "engine_a")
        pm.resolve_voice_profile("gone", "engine_b")

        ok, _msg = pm.delete_persona("gone")

        assert ok
        assert len(pm.VOICE_DERIVED_CACHE) == 0
        assert "gone" not in pm._persona_embedding_cache

    def test_lru_eviction(self):
        from integrated_app.voice_profile import VoiceDerivedCache

        cache = VoiceDerivedCache(maxsize=2)
        cache.put("n", "e1", "fp", "one")
        cache.put("n", "e2", "fp", "two")
        assert cache.lookup("n", "e1", "fp")[0]

        cache.put("n", "e3", "fp", "three")  # e2 为最久未访问，应被淘汰

        assert cache.lookup("n", "e2", "fp")[0] is False
        assert cache.lookup("n", "e1", "fp")[0] is True
        assert cache.lookup("n", "e3", "fp")[0] is True

    def test_invalid_maxsize(self):
        from integrated_app.voice_profile import VoiceDerivedCache

        with pytest.raises(ValueError):
            VoiceDerivedCache(maxsize=0)


class TestVoxcpm2Compatibility:
    """VoxCPM2 行为保持：仍走 .pt，origin 校验与 EngineNotLoadedError 不变。"""

    def test_resolution_matches_embedding_loader(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "vox")
        payload = (str(root / "vox.wav"), "这是参考文本。")
        _save_pt(root, "vox", payload, origin=pm.PERSONA_PT_ORIGIN)

        res = pm.resolve_voice_profile("vox", "voxcpm2")

        assert res.cache_source == "voxcpm2"
        assert res.payload == payload
        assert res.payload == pm.load_persona_embedding("vox")

    def test_origin_mismatch_still_loads(self, profile_env):
        root, pm = profile_env
        _make_persona(root, "extern")
        payload = (str(root / "extern.wav"), "外部导入")
        _save_pt(root, "extern", payload, origin="another-tool v1")

        res = pm.resolve_voice_profile("extern", "voxcpm2")

        assert res.payload == payload, "origin 不匹配只告警，不阻断加载"

    def test_engine_not_loaded_still_raises(self, profile_env):
        from integrated_app.exceptions import EngineNotLoadedError

        root, pm = profile_env
        _make_persona(root, "nopt")  # 无 .pt，且模型未就绪

        with pytest.raises(EngineNotLoadedError):
            pm.resolve_voice_profile("nopt", "voxcpm2")
