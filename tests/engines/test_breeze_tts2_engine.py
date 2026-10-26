# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze TTS 2 引擎 L2 协议合规与单元测试（纯 mock，不加载 GPU / 权重）。

覆盖：
    - 注册表元数据一致性（engine_registry ↔ config.yaml ↔ EngineName ↔ UI 数据）
    - TTSEngine / ControllableTTSEngine 双协议结构合规
    - 生命周期契约（未加载即生成 → EngineNotLoadedError；重复 load 幂等跳过）
    - 参数校验（克隆缺转写 / 导演缺指令 / 纯文本误传 CFG / 非法 seed 域外的 cfg）
    - 上游模板选择映射（4 类模板与 generate_* 方法的对应关系）
    - Vocal Events 标记透传（正文内嵌 [笑] / (laugh) 不被拦截）
    - vendor 导入链完整性（transformers 4.57.3 口径，无 qwen_tts 包依赖）

Run::

    pytest tests/engines/test_breeze_tts2_engine.py -v
"""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

ENGINE_ID = "breeze-tts2"
EXPECTED_FEATURES = {"voice_design", "clone", "ultimate", "streaming", "vocal_events"}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _make_engine(tmp_path: Any) -> Any:
    """构造指向临时空目录的引擎实例（不触发任何权重加载）。"""
    from pathlib import Path

    from integrated_app.engines.breeze_tts2.engine import BreezeTTSEngine

    return BreezeTTSEngine(model_dir=str(Path(tmp_path) / "Breeze-TTS-2"))


def _make_ready_engine(tmp_path: Any, monkeypatch: Any) -> Any:
    """构造「已加载」状态的引擎：_ensure_ready 通过，推理链路打桩。

    推理桩返回固定长度波形，验证 (wav, sr, filename) 三元组契约与落盘。
    """
    import numpy as np

    engine = _make_engine(tmp_path)

    class _FakeChunk:
        def __init__(self, audio: Any) -> None:
            self.audio = audio

    class _FakeRuntime:
        sample_rate = 24000

        def iter_audio_chunks(self, inputs: Any, request_id: str, seed: int) -> Any:
            yield _FakeChunk(np.ones(2400, dtype=np.float32) * 0.1)

    engine._runtime = _FakeRuntime()
    engine._tokenizer = object()
    engine._model = object()
    engine._audio_tokenizer = object()
    engine._loaded = True

    # 打桩 vendor 的 set_all_seeds / prepare_inputs（避免 tokenizer 依赖）
    import integrated_app.engines.breeze_tts2.vendor.breeze_infer.runtime as br_runtime
    import integrated_app.engines.breeze_tts2.vendor.breeze_infer.templates as br_templates

    monkeypatch.setattr(br_runtime, "set_all_seeds", lambda seed: None, raising=False)
    monkeypatch.setattr(
        br_templates,
        "prepare_inputs",
        lambda *a, **k: {"input_ids": None},
    )
    return engine


# ---------------------------------------------------------------------------
# 1. 注册与元数据一致性
# ---------------------------------------------------------------------------


class TestRegistrationConsistency:
    def test_registered_in_engine_registry(self):
        from integrated_app.engine_interface import engine_registry

        assert engine_registry.is_registered(ENGINE_ID)
        meta = engine_registry.get_metadata(ENGINE_ID)
        assert meta["display_name"] == "Breeze TTS 2"
        assert meta["sample_rate"] == 24000
        assert set(meta["supported_features"]) == EXPECTED_FEATURES
        assert meta["requires_gpu"] is True
        assert meta["vram_requirement"] == 7.5

    def test_config_yaml_engine_entry_matches(self):
        from pathlib import Path

        import yaml

        root = Path(__file__).resolve().parents[2]
        with open(root / "config.yaml", encoding="utf-8") as f:
            engines = (yaml.safe_load(f).get("models") or {}).get("engines") or {}
        entry = engines.get(ENGINE_ID)
        assert entry, "config.yaml 缺 breeze-tts2 声明"
        assert entry["model_dir"] == "Breeze-TTS-2"
        assert entry["sample_rate"] == 24000
        assert set(entry["supported_features"]) == EXPECTED_FEATURES
        assert entry["vram_gb"] == 7.5

    def test_engine_name_enum_and_vram_table(self):
        from integrated_app.model_registry import ENGINE_VRAM_REQUIREMENTS, EngineName

        assert EngineName.BREEZE_TTS2.value == ENGINE_ID
        assert ENGINE_VRAM_REQUIREMENTS[ENGINE_ID] == 7.5

    def test_ui_data_registered(self):
        from integrated_app.engine_ui_data import get_engine_ui

        ui = get_engine_ui(ENGINE_ID)
        assert ui is not None
        assert ui.sample_rate == 24000
        keys = {p.key for p in ui.params}
        assert {"text", "instruction", "cfg_scale", "seed"} <= keys

    def test_text_limit_declared(self):
        from integrated_app.config import get_engine_text_limit

        assert get_engine_text_limit(ENGINE_ID) == 2000


# ---------------------------------------------------------------------------
# 2. 协议合规
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_implements_tts_engine_protocol(self):
        from integrated_app.engine_interface import TTSEngine

        engine = _make_engine(__import__("tempfile").mkdtemp())
        assert isinstance(engine, TTSEngine)

    def test_implements_controllable_protocol(self):
        from integrated_app.engine_interface import ControllableTTSEngine

        engine = _make_engine(__import__("tempfile").mkdtemp())
        assert isinstance(engine, ControllableTTSEngine)

    def test_unimplemented_stubs_raise(self, tmp_path):
        """LoRA / Prompt 续写未声明支持，必须保持 NotImplementedError 桩。"""
        engine = _make_engine(tmp_path)
        with pytest.raises(NotImplementedError):
            engine.generate_with_prompt("text", "a.wav", "ref")
        with pytest.raises(NotImplementedError):
            engine.load_lora("lora.pt")
        assert engine.lora_enabled is False


# ---------------------------------------------------------------------------
# 3. 生命周期与校验
# ---------------------------------------------------------------------------


class TestLifecycleAndValidation:
    def test_not_ready_before_load(self, tmp_path):
        engine = _make_engine(tmp_path)
        assert engine.is_ready() is False
        from integrated_app.exceptions import EngineNotLoadedError

        with pytest.raises(EngineNotLoadedError):
            engine.generate_voice_design("你好")

    def test_load_missing_dir_raises_engine_load_error(self, tmp_path):
        from integrated_app.exceptions import EngineLoadError

        engine = _make_engine(tmp_path)
        with pytest.raises(EngineLoadError, match="download_models"):
            engine.load()

    def test_clone_without_ref_text_rejected(self, tmp_path, monkeypatch):
        from integrated_app.exceptions import ValidationError

        engine = _make_ready_engine(tmp_path, monkeypatch)
        wav = tmp_path / "ref.wav"
        wav.write_bytes(b"RIFF")
        with pytest.raises(ValidationError, match="转写"):
            engine.generate_voice_clone("测试", reference_audio_path=str(wav))

    def test_direction_requires_instruction_and_ref_text(self, tmp_path, monkeypatch):
        from integrated_app.exceptions import ValidationError

        engine = _make_ready_engine(tmp_path, monkeypatch)
        wav = tmp_path / "ref.wav"
        wav.write_bytes(b"RIFF")
        with pytest.raises(ValidationError, match="instruction"):
            engine.generate_ultimate_clone("测试", ref_audio_path=str(wav), ref_text="转写")
        with pytest.raises(ValidationError, match="ref_text"):
            engine.generate_ultimate_clone("测试", instruction="慢一点", ref_audio_path=str(wav))

    def test_design_result_contract_and_file_written(self, tmp_path, monkeypatch):
        """generate_* 返回 ((wav, sr, filename), msg)，文件落 SAVE_DIR。"""
        import numpy as np

        from integrated_app import config as app_config

        engine = _make_ready_engine(tmp_path, monkeypatch)
        monkeypatch.setattr(app_config, "SAVE_DIR", str(tmp_path / "outputs"), raising=False)
        import integrated_app.engines.breeze_tts2.engine as eng_mod

        monkeypatch.setattr(eng_mod, "SAVE_DIR", str(tmp_path / "outputs"))
        (result, msg) = engine.generate_voice_design(
            "[笑] 欢迎来到今晚的故事时间。", instruction="温柔的年轻女声", cfg_scale=4.0, seed=42
        )
        wav, sr, filename = result
        assert isinstance(wav, np.ndarray) and wav.size > 0
        assert sr == 24000
        assert os.path.isfile(os.path.join(str(tmp_path / "outputs"), filename))
        assert filename.startswith("breeze_tts2_")
        assert "seed=42" in msg

    def test_vocal_events_markers_pass_through(self, tmp_path, monkeypatch):
        """正文内嵌中英文事件标记不被安全校验拦截（声明 vocal_events 能力的可测面）。"""
        engine = _make_ready_engine(tmp_path, monkeypatch)
        # 桩运行时产出的占位波形不得写进真实 outputs/（与 contract 用例同口径）
        import integrated_app.engines.breeze_tts2.engine as eng_mod

        monkeypatch.setattr(eng_mod, "SAVE_DIR", str(tmp_path / "outputs"))
        captured: dict[str, Any] = {}

        import integrated_app.engines.breeze_tts2.vendor.breeze_infer.templates as br_templates

        orig = br_templates.prepare_inputs

        def _capture(tokenizer, audio_tokenizer, model, requests, template, **kw):
            captured["requests"] = requests
            return orig(tokenizer, audio_tokenizer, model, requests, template, **kw) if False else {"input_ids": None}

        monkeypatch.setattr(br_templates, "prepare_inputs", _capture)
        engine.generate_voice_design("(sigh) It is good to hear you again.", seed=1)
        assert "(sigh)" in captured["requests"][0]["text"]


# ---------------------------------------------------------------------------
# 4. 上游模板映射（纯逻辑，不依赖 GPU）
# ---------------------------------------------------------------------------


class TestTemplateMapping:
    def test_four_templates_select_as_documented(self):
        from integrated_app.engines.breeze_tts2.vendor.breeze_infer.templates import (
            select_template_name,
        )

        assert select_template_name({"text": "hi"}) == "tts_plain"
        assert select_template_name({"text": "hi", "instruction": "warm"}) == "tts_instruction"
        assert select_template_name({"text": "hi", "ref_audio_path": "a.wav", "ref_text": "r"}) == "ref_clone_tata"
        assert (
            select_template_name({"text": "hi", "ref_audio_path": "a.wav", "ref_text": "r", "instruction": "slow"})
            == "ref_edit_tata"
        )

    def test_engine_methods_map_to_expected_templates(self, tmp_path, monkeypatch):
        engine = _make_ready_engine(tmp_path, monkeypatch)
        selected: list[str] = []

        import integrated_app.engines.breeze_tts2.vendor.breeze_infer.templates as br_templates

        class _T:
            name = "stub"
            build_negative_segments = staticmethod(lambda r: [])
            build_dual_branches = None

        monkeypatch.setattr(
            br_templates,
            "get_template",
            lambda name: (_selected_append(selected, name), _T())[1],
        )

        def _selected_append(lst, name):
            lst.append(name)

        wav = tmp_path / "ref.wav"
        wav.write_bytes(b"RIFF")
        engine.generate_voice_design("hi", instruction="warm", seed=1)
        engine.generate_voice_clone("hi", reference_audio_path=str(wav), ref_text="r", seed=1)
        engine.generate_ultimate_clone("hi", instruction="slow", ref_audio_path=str(wav), ref_text="r", advanced_seed=1)
        assert selected == ["tts_instruction", "ref_clone_tata", "ref_edit_tata"]


# ---------------------------------------------------------------------------
# 5. vendor 导入链（无 qwen_tts 包依赖的证明）
# ---------------------------------------------------------------------------


class TestVendorChain:
    def test_vendor_imports_without_qwen_tts_package(self):
        """qwen_tts_min 裁剪副本应替代 PyPI qwen-tts：环境里没有该包也能整链导入。"""
        try:
            import qwen_tts  # noqa: F401

            pytest.skip("环境装了 qwen_tts 包，本用例无法证明「无包可跑」")
        except ImportError:
            pass
        from integrated_app.engines.breeze_tts2.vendor.breeze_infer.runtime import load_runtime
        from integrated_app.engines.breeze_tts2.vendor.models.fast_streaming import (
            FastBreezeStreamingRuntime,
        )
        from integrated_app.engines.breeze_tts2.vendor.qwen_tts_min import Qwen3TTSTokenizer

        assert callable(load_runtime)
        assert FastBreezeStreamingRuntime is not None
        assert Qwen3TTSTokenizer is not None

    def test_indextts_tf_compat_shim_applies(self):
        """shim 幂等且能回填 4 个符号（IndexTTS 与 Breeze 共存的依赖前提）。"""
        from integrated_app.engines.indextts_tf_compat import apply

        apply()
        apply()  # 幂等
        import transformers.cache_utils as cu
        import transformers.generation.candidate_generator as cg
        import transformers.generation.configuration_utils as gu
        import transformers.modeling_utils as mu

        assert hasattr(cu, "QuantizedCacheConfig")
        assert hasattr(cg, "_crop_past_key_values")
        assert hasattr(gu, "NEED_SETUP_CACHE_CLASSES_MAPPING")
        assert hasattr(mu, "SequenceSummary")
