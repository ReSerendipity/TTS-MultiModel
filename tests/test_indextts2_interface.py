"""IndexTTS2 引擎接口级单元测试（不加载模型）。

覆盖目标模块: app/integrated_app/engines/indextts2_engine.py
"""

import contextlib
import inspect

import pytest

from integrated_app.engines.indextts2_engine import IndexTTS2Engine


class TestEmotionConstants:
    def test_emotion_dimensions(self):
        dims = IndexTTS2Engine.EMOTION_DIMENSIONS
        assert isinstance(dims, list)
        assert "happy" in dims
        assert "sad" in dims
        # 与 emotion_control 的 8 维对齐
        from integrated_app.emotion_control import EMOTION_DIMENSION_NAMES

        assert set(dims) == set(EMOTION_DIMENSION_NAMES)


class TestBuildEmotionVector:
    def test_preset_vector(self):
        vec = IndexTTS2Engine.build_emotion_vector(happy=0.8)
        assert len(vec) == 8
        assert all(isinstance(v, (int, float)) for v in vec)
        assert vec[0] == pytest.approx(0.8)  # happy 是第一个维度

    def test_values_clamped(self):
        vec = IndexTTS2Engine.build_emotion_vector(happy=2.0)
        assert all(0.0 <= v <= 1.0 for v in vec)


class TestGetPresetEmotions:
    def test_returns_dict(self):
        presets = IndexTTS2Engine.get_preset_emotions()
        assert isinstance(presets, dict)
        assert "happy" in presets
        assert "neutral" in presets
        assert len(presets["happy"]) == 8


class TestIndexTTS2EngineInterface:
    def test_class_implements_protocol(self):
        from integrated_app.engine_interface import TTSEngine

        assert issubclass(IndexTTS2Engine, TTSEngine)

    def test_static_properties(self):
        # 类层面访问 @property 得到描述符对象，验证属性存在且类型正确
        assert isinstance(IndexTTS2Engine.__dict__["version"], property)
        assert isinstance(IndexTTS2Engine.__dict__["min_vram_gb"], property)
        assert isinstance(IndexTTS2Engine.__dict__["min_ram_gb"], property)

    def test_init_without_model_dir(self):
        # 初始化时即校验模型文件，缺失则抛 EngineLoadError（引导用户下载模型）
        from integrated_app.exceptions import EngineLoadError

        with pytest.raises(EngineLoadError):
            IndexTTS2Engine(model_dir="/nonexistent/path")


class TestVersion:
    """IndexTTS 2.5 升级相关断言。

    注意：``version`` 是 @property，通过实例访问会触发模型加载（__init__ 中
    即加载模型），因此这里只做类层面（描述符）断言，不实例化。
    """

    def test_version_is_property(self):
        # 类层面访问 @property 得到描述符对象，验证 version 是 property
        assert isinstance(IndexTTS2Engine.__dict__["version"], property)

    def test_version_is_2_5(self):
        # 通过 property 的 fget 获取返回值，避免实例化触发模型加载
        fget = IndexTTS2Engine.__dict__["version"].fget
        assert fget is not None
        assert fget(IndexTTS2Engine) == "IndexTTS 2.5"


class TestIndexTTS2LangCapability:
    """IndexTTS 2.5 新增 lang 语言能力。

    通过 inspect.signature 检查方法签名（不实例化、不触发模型加载）。
    """

    def test_infer_has_lang_param(self):
        params = inspect.signature(IndexTTS2Engine.infer).parameters
        assert "lang" in params

    def test_synthesize_has_lang_param(self):
        params = inspect.signature(IndexTTS2Engine.synthesize).parameters
        assert "lang" in params


class TestIndexTTS2InitSignature:
    """IndexTTS 2.5 __init__ 参数检查（不实例化）。"""

    def test_init_uses_bf16_not_fp16(self):
        params = inspect.signature(IndexTTS2Engine.__init__).parameters
        assert "use_bf16" in params
        assert "use_fp16" not in params

    def test_init_has_lang_param(self):
        assert "lang" in inspect.signature(IndexTTS2Engine.__init__).parameters

    def test_init_has_use_qwen_emo_param(self):
        assert "use_qwen_emo" in inspect.signature(IndexTTS2Engine.__init__).parameters


class TestDurationControlGuard:
    """时长控制参数守卫：底层库只认 duration_factor，绝对秒数必须显式拒绝。

    ``indextts.infer_v2_5.infer()`` 的形参表里没有 ``target_duration``，透传会落进
    ``**generation_kwargs`` 并被 ``model.generate`` 以 "model_kwargs not used" 拒收
    （400），与 GOTCHAS #90 记录的 seed 同型；2.0 走 ``infer_v2``，连 duration_factor
    都没有，旧行为是 debug 一句然后静默忽略——用户拿到与请求无关的音频却以为生效。
    """

    @staticmethod
    def _bare_engine(version: str, supports_duration: bool):
        from unittest.mock import MagicMock

        eng = object.__new__(IndexTTS2Engine)
        eng.tts = MagicMock()
        eng.tts.infer.return_value = (22050, MagicMock())
        eng.lang = "Auto"
        eng.version_str = version  # version 是只读 property，由 version_str 推导
        eng.supports_duration = supports_duration
        eng._engine_name = "indextts2" if version == "2.5" else "indextts20"
        eng.supported_langs = {"Auto", "ZH", "EN", "JA", "ES", "AR"}
        eng.device = "cpu"
        eng.use_bf16 = False
        return eng

    @staticmethod
    def _ref_audio(tmp_path):
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"RIFF" + b"\x00" * 200)
        return str(ref)

    def test_target_duration_rejected_on_2_5(self, tmp_path):
        from integrated_app.exceptions import ValidationError

        eng = self._bare_engine("2.5", supports_duration=True)
        with pytest.raises(ValidationError, match="不支持按绝对秒数") as exc:
            eng.infer(
                text="你好",
                spk_audio_prompt=self._ref_audio(tmp_path),
                output_path=str(tmp_path / "out.wav"),
                target_duration=8.0,
            )
        assert exc.value.field == "target_duration"
        assert exc.value.status_code == 400
        # self.version 已经带 "IndexTTS " 前缀，文案里再拼一次会变成
        # "IndexTTS IndexTTS 2.5 …"（真机点击时看到过）
        assert str(exc.value).startswith("IndexTTS 2.5 ")
        eng.tts.infer.assert_not_called()

    def test_duration_factor_rejected_on_2_0(self, tmp_path):
        from integrated_app.exceptions import ValidationError

        eng = self._bare_engine("2.0", supports_duration=False)
        with pytest.raises(ValidationError, match="不支持显式时长控制") as exc:
            eng.infer(
                text="你好",
                spk_audio_prompt=self._ref_audio(tmp_path),
                output_path=str(tmp_path / "out.wav"),
                duration_factor=1.25,
            )
        assert exc.value.field == "duration_scale"
        assert str(exc.value).startswith("IndexTTS 2.0 ")
        eng.tts.infer.assert_not_called()

    # ── 以下两条挂在 TestDurationControlGuard 是因为要复用它的 _bare_engine / _ref_audio
    # 两个桩（单独成类会把 fixture 甩在后面）；语义与时长无关：一条管并发串行，一条管报错文案。
    def test_infer_serializes_concurrent_callers(self, tmp_path):
        """同一实例上的并发 infer 必须串行 —— 这是预热与用户请求抢占的真机故障守卫。

        WHY：预热（model_optimizer.warmup_indextts2）从后台线程直接调 `engine.infer`，
        绕开了用户侧的 per-engine asyncio.Semaphore(1)。2026-09-21 真机实测：预热开始 2 秒后
        插入一条用户合成，两条并发进同一个 IndexTTS 模型 → `CUDA error: device-side assert
        triggered`，整个 CUDA context 被毒化，之后同进程所有推理连带失败。
        引擎层的 RLock 是"任何入口都串行"的兜底，所以这里直接压并发，不测上游用了哪把锁。
        """
        import threading
        import time
        from unittest.mock import MagicMock

        eng = self._bare_engine("2.5", supports_duration=True)
        live: list[int] = []
        seen: list[int] = []
        guard = threading.Lock()

        def _fake_infer(*args: object, **kwargs: object) -> tuple:
            with guard:
                live.append(1)
                seen.append(len(live))
            time.sleep(0.2)
            with guard:
                live.pop()
            return (22050, MagicMock())

        eng.tts.infer.side_effect = _fake_infer

        def _call(i: int) -> None:
            with contextlib.suppress(Exception):  # 后续步骤缺 stub 不重要，看重叠没有
                eng.infer(
                    text=f"并发第 {i} 条",
                    spk_audio_prompt=self._ref_audio(tmp_path),
                    output_path=str(tmp_path / f"out{i}.wav"),
                )

        threads = [threading.Thread(target=_call, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert seen, "一次都没进到模型层，说明桩没接上（本守卫会空转）"
        assert max(seen) == 1, f"并发进入了模型层 {max(seen)} 次 —— device-side assert 的形状回来了"

    @pytest.mark.parametrize("version", ["2.5", "2.0"])
    def test_generation_failure_names_the_running_variant(self, tmp_path, version):
        """推理内部抛异常时，GenerationError 的文案必须点名**当前实例**的变体。

        WHY：2.5 与 2.0 共用 IndexTTS2Engine，原先文案硬编码 "IndexTTS 2.5"，
        2026-09-21 真机跑冒烟时在 indextts20 上报出 "IndexTTS 2.5 合成失败"，
        同一函数里的加载错误也曾说 "IndexTTS 2.5 模型文件不可读 + 请跑
        download_indextts2.py"，会把人引去下载另一套权重目录。
        """
        from integrated_app.exceptions import GenerationError

        eng = self._bare_engine(version, supports_duration=True)
        eng.tts.infer.side_effect = RuntimeError("CUDA error: device-side assert triggered")
        with pytest.raises(GenerationError) as exc:
            eng.infer(
                text="你好",
                spk_audio_prompt=self._ref_audio(tmp_path),
                output_path=str(tmp_path / "out.wav"),
            )
        assert str(exc.value).startswith(f"IndexTTS {version} 合成失败"), str(exc.value)
        assert f"IndexTTS {'2.0' if version == '2.5' else '2.5'}" not in str(exc.value)
