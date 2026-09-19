"""IndexTTS2 预热路径回归（2026-09-18）。

预热长期硬编码 ``spk_audio_prompt=""`` —— 而引擎对空路径必抛
``FileNotFoundError``，所以这条链路从来没有真正执行过一次推理，也就一直没有
测试覆盖；补真实参考音频后又暴露出 ``synthesize()`` 返回三元组、调用方只解
两个值的契约错配。以下用例把这两点钉住。
"""

import os

import numpy as np
import pytest

from integrated_app.model_optimizer import _find_warmup_reference, warmup_indextts2


class _FakeEngine:
    """按 IndexTTS2Engine.synthesize() 的真实契约返回三元组。"""

    def __init__(self, output_path: str):
        self._output_path = output_path
        self.calls: list[dict] = []

    def synthesize(self, **kwargs):
        self.calls.append(kwargs)
        return 22050, np.zeros(2205, dtype=np.float32), self._output_path


class TestWarmupIndextts2:
    def test_accepts_three_tuple_and_reclaims_temp_wav(self, tmp_path):
        """三元组要能解开；成功路径留下的临时 wav 必须被预热自己回收。"""
        out = tmp_path / "warmup.wav"
        out.write_bytes(b"RIFF" + b"\x00" * 200)  # 必须 > 44 字节才会被视为可用参考
        engine = _FakeEngine(str(out))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("integrated_app.persona_manager.PERSONA_DIR", str(tmp_path))
            assert warmup_indextts2(engine) is True

        assert len(engine.calls) == 1
        assert engine.calls[0]["spk_audio_prompt"] == str(tmp_path / "warmup.wav")
        assert not out.exists(), "预热产物未被回收，每次切换引擎都会攒一个孤儿文件"

    def test_skips_without_reference_and_never_calls_engine(self, tmp_path):
        """没有可用音色时明确跳过，而不是拿空路径去撞 FileNotFoundError。"""
        engine = _FakeEngine(str(tmp_path / "never.wav"))
        empty_personas = tmp_path / "personas"
        empty_personas.mkdir()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("integrated_app.persona_manager.PERSONA_DIR", str(empty_personas))
            assert warmup_indextts2(engine) is False

        assert engine.calls == []

    def test_reference_lookup_ignores_empty_wav(self, tmp_path):
        """只认非空 wav（≤44 字节只是裸 WAV 头，喂给模型必然出故障）。"""
        (tmp_path / "a_empty.wav").write_bytes(b"\x00" * 44)
        good = tmp_path / "b_real.wav"
        good.write_bytes(b"\x00" * 1024)
        (tmp_path / "c_readme.txt").write_text("not audio", encoding="utf-8")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("integrated_app.persona_manager.PERSONA_DIR", str(tmp_path))
            found = _find_warmup_reference()

        assert found == str(good)
        assert os.path.isabs(found)

    def test_reference_lookup_returns_none_when_dir_missing(self, tmp_path):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("integrated_app.persona_manager.PERSONA_DIR", str(tmp_path / "nope"))
            assert _find_warmup_reference() is None
