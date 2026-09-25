"""极致克隆 prompt_text（ref_text）全链路测试（UPSTREAM_SYNC A5：#9/#10/#11）。

上游契约：极致克隆 = ``prompt_wav_path`` + ``prompt_text``（参考音频的转写文本），
两者必须成对出现（``vendor/voxcpm/core.py:243-244``）。落地前的实测状态是：

* 前端 ``ultimate_clone.html:91`` 有 ``name="ref_text"`` 的 textarea，
  但极致克隆路由没有对应 Form 字段 —— 用户填了被 FastAPI **静默丢弃**；
* 引擎侧永远跑 SenseVoice ASR 猜转写，用户无法纠正；
* ``prompt_cache`` 的键只含音频字节。
"""

from __future__ import annotations

import hashlib
import inspect
from types import SimpleNamespace

import pytest

from integrated_app import prompt_cache as pc
from integrated_app.routes.generate.voxcpm2 import clone as vc_clone


def _legacy_key(audio_path: str) -> str:
    """改动前的键算法，用来证明"不传 prompt_text 时键值不变"。"""
    h = hashlib.sha256()
    try:
        with open(audio_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except OSError:
        h.update(audio_path.encode("utf-8"))
    return h.hexdigest()[:16]


class TestHttpFieldExists:
    def test_ultimate_route_declares_ref_text(self) -> None:
        params = inspect.signature(vc_clone.generate_voxcpm_ultimate).parameters
        assert "ref_text" in params, "Form 字段没加 -> 前端 textarea 的值仍会被静默丢弃"

    def test_controllable_route_untouched(self) -> None:
        """可控克隆走 reference_wav_path，上游没有 prompt_text 的位置，不应假装支持。"""
        params = inspect.signature(vc_clone.generate_voxcpm_clone).parameters
        assert "ref_text" not in params


class TestEngineChainAcceptsRefText:
    def test_wrapper_and_impl_have_the_param(self) -> None:
        from integrated_app.engines.voxcpm2.ultimate import (
            _fn_voxcpm_ultimate_clone_impl,
            fn_voxcpm_ultimate_clone,
        )

        for fn in (fn_voxcpm_ultimate_clone, _fn_voxcpm_ultimate_clone_impl):
            p = inspect.signature(fn).parameters
            assert "ref_text" in p, f"{fn.__name__} 缺 ref_text，链路上会 TypeError"
            assert p["ref_text"].default == "", f"{fn.__name__} 的 ref_text 默认值应为空串"

    def test_engine_forwards_kwargs_to_wrapper(self) -> None:
        """VoxCPM2Engine.generate_ultimate_clone 是 **kwargs 转发；包装函数必须收得住。"""
        from integrated_app.engines.voxcpm2.engine import VoxCPM2Engine

        assert any(
            p.kind is inspect.Parameter.VAR_KEYWORD
            for p in inspect.signature(VoxCPM2Engine.generate_ultimate_clone).parameters.values()
        ), "引擎不再透传 kwargs，route 传的 ref_text 到不了 ultimate.py"


class TestRefTextReachesEngine:
    @staticmethod
    async def _no_upload(_request, _file):
        return None, None

    @staticmethod
    async def _persona_ref(_request, _name):
        return "C:/fake/ref.wav", None

    @staticmethod
    async def _fake_execute(_request, **kwargs):
        return kwargs["run_fn"]()

    @pytest.fixture
    def captured(self, monkeypatch: pytest.MonkeyPatch):
        calls: dict = {}

        class _FakeEngine:
            def generate_ultimate_clone(self, *args, **kwargs):
                calls["args"] = args
                calls.update(kwargs)
                return ("out.wav", "ok")

        monkeypatch.setattr(vc_clone, "_check_clone_consent", lambda *a, **k: None)
        monkeypatch.setattr(vc_clone, "pre_validate", lambda *a, **k: None)
        monkeypatch.setattr(vc_clone, "save_uploaded_audio", self._no_upload)
        monkeypatch.setattr(vc_clone, "resolve_persona_ref", self._persona_ref)
        monkeypatch.setattr(vc_clone, "_execute_generation", self._fake_execute)
        monkeypatch.setattr(vc_clone.registry, "get_current_engine", lambda: _FakeEngine())
        return calls

    def _post(self, **overrides):
        import asyncio

        args = {
            "request": SimpleNamespace(state=SimpleNamespace(request_id="r")),
            "text": "你好世界",
            "instruction": "",
            "ref_audio_path": "C:/fake/ref.wav",
            "persona_name": "",
            "cfg": 2.0,
            "norm": "true",
            "denoise": "true",
            "steps": 10,
            "seed": -1,
            "denoise_strength": None,
            "ref_audio_upload": None,
            "ref_text": "这是参考音频的转写文本",
            "lang": "Auto",
            "tempo_factor": 1.0,
            "voice_enhancement": "false",
            "target_lufs": -16.0,
            "has_consent": True,
        }
        args.update(overrides)
        return asyncio.run(vc_clone.generate_voxcpm_ultimate(**args))

    def test_normal_and_degraded_paths_both_relay(self, captured) -> None:
        self._post()
        assert captured["ref_text"] == "这是参考音频的转写文本"

    def test_empty_ref_text_still_sent_as_empty(self, captured) -> None:
        """留空必须显式传空串，让引擎走 ASR 回退，而不是靠 KeyError 兜。"""
        self._post(ref_text="")
        assert captured["ref_text"] == ""


class TestPromptCacheKey:
    def test_different_prompt_text_different_key(self, tmp_path) -> None:
        audio = tmp_path / "ref.wav"
        audio.write_bytes(b"fake-audio-bytes" * 64)
        k1 = pc._get_prompt_cache_key(str(audio), "第一版转写")
        k2 = pc._get_prompt_cache_key(str(audio), "第二版转写")
        assert k1 != k2, "不同 prompt_text 命中同一缓存 → 会拿错的音素对齐结果出音"

    def test_empty_prompt_text_keeps_legacy_key(self, tmp_path) -> None:
        """向后兼容：可控克隆不传 prompt_text，键值必须与改动前逐位一致，
        否则既有缓存全部失效（每次多花 300~800ms 重算嵌入）。"""
        audio = tmp_path / "ref.wav"
        audio.write_bytes(b"fake-audio-bytes" * 64)
        assert pc._get_prompt_cache_key(str(audio)) == _legacy_key(str(audio))
        assert pc._get_prompt_cache_key(str(audio), "") == _legacy_key(str(audio))

    def test_save_and_load_use_the_same_key(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        seen: list[str] = []

        class _Cache:
            def get(self, key, audio_hash=None):
                seen.append(("get", key))
                return None

            def put(self, key, value, audio_path_or_data=None):
                seen.append(("put", key))

        audio = tmp_path / "ref.wav"
        audio.write_bytes(b"x" * 100)
        monkeypatch.setattr(pc, "get_prompt_cache", lambda: _Cache())
        pc.save_prompt_cache(str(audio), object(), prompt_text="转写A")
        pc.load_cached_prompt(str(audio), prompt_text="转写A")
        pc.load_cached_prompt(str(audio), prompt_text="转写B")
        ops = [op for op, _ in seen]
        keys = [k for _, k in seen]
        assert ops == ["put", "get", "get"]
        assert keys[0] == keys[1], "同一段音频+同一转写，save 与 load 却落在两条键上 → 缓存永远命中不了"
        assert keys[2] != keys[1], "换了转写文本仍命中同一键 → 会取到按旧转写编码的 prompt_cache"


class TestPromptTextAlignmentGuard:
    """护栏阈值直接钉在 2026-09-23 实测的六个内置音色分布上。

    配套转写的字/秒实测落在 3.32–5.57；唯一那份残缺转写（personas/gf1.txt 只有
    47.84s 音频的第一句，63 字）是 **1.32**，产物从 5.7s 塌成 0.29s、相似度从
    0.89 掉到 0.30、耗时 41s→321s。分界取 1.5/2.5，两侧都留了余量。
    """

    @pytest.mark.parametrize(
        ("chars", "duration_s", "expected"),
        [
            (63, 47.84, "reject"),  # gf1.txt vs gf1.wav：真实事故样本
            (159, 47.84, "ok"),  # 同一段音频的完整转写
            (17, 5.12, "ok"),  # 小林
            (15, 3.20, "ok"),  # 御姐
            (43, 10.00, "ok"),  # 李老师
            (41, 7.68, "ok"),  # 韩立
            (37, 6.64, "ok"),  # 南宫婉
            (120, 60.0, "suspect"),  # 2.0 字/秒：介于两阈值之间，保留但提示
        ],
    )
    def test_verdicts_on_measured_distribution(self, chars: int, duration_s: float, expected: str) -> None:
        from integrated_app.engines.voxcpm2.ultimate import _classify_prompt_text_alignment

        verdict, cps = _classify_prompt_text_alignment("词" * chars, duration_s)
        assert verdict == expected, f"{chars}字/{duration_s}s = {cps} 字/秒，判成 {verdict} 而非 {expected}"

    def test_english_is_counted_by_words_not_letters(self) -> None:
        """英文按词折算，否则 12 词的 5 秒转写会被误判成残缺（字母数除以秒数虚高）。"""
        from integrated_app.engines.voxcpm2.ultimate import _classify_prompt_text_alignment, _prompt_text_units

        sentence = " ".join(["hello"] * 12)
        assert _prompt_text_units(sentence) == 24  # 12 词 × 2
        verdict, _ = _classify_prompt_text_alignment(sentence, 5.0)
        assert verdict == "ok"

    def test_missing_duration_degrades_to_unknown(self) -> None:
        from integrated_app.engines.voxcpm2.ultimate import _classify_prompt_text_alignment

        assert _classify_prompt_text_alignment("任意文本", 0.0)[0] == "unknown"
        assert _classify_prompt_text_alignment("", 10.0)[0] == "unknown"

    def test_audio_duration_helper_never_raises(self, tmp_path) -> None:
        """时长只服务启发式，坏文件必须退化成 0（跳过检查）而不是打断合成。"""
        from integrated_app.engines.voxcpm2.ultimate import _audio_duration_seconds

        assert _audio_duration_seconds(None) == 0.0
        bad = tmp_path / "not-audio.wav"
        bad.write_bytes(b"garbage")
        assert _audio_duration_seconds(str(bad)) == 0.0

    def test_reject_path_falls_back_to_asr_and_tells_the_user(self) -> None:
        """被丢弃时不能静默：必须把"改用自动转写"写进用户可见消息。"""
        import inspect

        from integrated_app.engines.voxcpm2.ultimate import _fn_voxcpm_ultimate_clone_impl

        src = inspect.getsource(_fn_voxcpm_ultimate_clone_impl)
        assert "已改用自动转写" in src, "reject 分支静默替换用户输入"
        assert 'ref_text, ref_text_source = "", ""' in src
        assert "alignment_notice" in src
        # notice 必须拼进 message_builder 的返回值，否则前端看不到
        msg = src[src.index("def message_builder") : src.index("def message_builder") + 900]
        assert "alignment_notice" in msg, "提示没进用户可见消息"


class TestAsrFallbackDisclosure:
    """#11：留空走 ASR 可以，但必须让用户知道那段文本是机器猜的。"""

    def test_source_distinguishes_user_and_asr(self) -> None:
        from integrated_app.engines.voxcpm2 import ultimate

        src = inspect.getsource(ultimate._fn_voxcpm_ultimate_clone_impl)
        assert 'ref_text_source = "user"' in src
        assert 'ref_text_source = "asr"' in src
        assert "由 ASR 自动转写" in src, "成功消息未披露转写来源"
        assert "按你填写的内容" in src, "用户自填时也被当成 ASR 结果"

    def test_denoise_temp_file_cleanup_is_outside_the_asr_branch(self) -> None:
        """跳过 ASR 时降噪临时文件仍要被清掉，否则每次极致克隆漏一个文件。"""
        from integrated_app.engines.voxcpm2 import ultimate

        src = inspect.getsource(ultimate._fn_voxcpm_ultimate_clone_impl)
        cleanup = src.index("os.remove(processed_ref_path_for_asr)")
        elif_skip = src.index("使用用户提供的参考文本")
        assert cleanup > elif_skip, "清理又回到了 ASR 分支内部"
