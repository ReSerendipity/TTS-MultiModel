"""service_layer 模块单元测试 — 覆盖请求生命周期编排。

覆盖目标模块: app/integrated_app/service_layer.py
覆盖率目标: >=70%

覆盖范围:
- 数据类: GenerationResult / LoadResult / SwitchResult / ModelStatus / PersonaInfo
- 辅助函数: _check_vram_circuit_breaker / _get_vram_usage_percent
- TTSGenerationService: _ensure_engine_ready / _extract_generation_result /
  _estimate_duration / generate_voice_design / generate_voice_clone /
  generate_ultimate_clone / generate_script / generate_streaming /
  _save_version_record
- ModelService: load_model / unload_model / switch_engine / get_model_status
- PersonaService: list_personas / get_persona / create_persona / delete_persona /
  cache invalidation
- 单例获取: get_generation_service / get_model_service / get_persona_service
"""

import time
from unittest.mock import MagicMock, patch

import pytest

# =====================================================================
# 数据类测试
# =====================================================================


class TestGenerationResult:
    """GenerationResult 数据类测试。"""

    def test_defaults(self):
        from integrated_app.service_layer import GenerationResult

        r = GenerationResult()
        assert r.audio_path == ""
        assert r.message == ""
        assert r.duration == 0.0
        assert r.engine == ""
        assert r.params == {}

    def test_to_dict(self):
        from integrated_app.service_layer import GenerationResult

        r = GenerationResult(
            audio_path="/tmp/test.wav",
            message="test",
            duration=3.5,
            engine="voxcpm2",
            params={"cfg": 2.0},
        )
        d = r.to_dict()
        assert d["audio_path"] == "/tmp/test.wav"
        assert d["message"] == "test"
        assert d["duration"] == 3.5
        assert d["engine"] == "voxcpm2"
        assert d["params"]["cfg"] == 2.0

    def test_to_dict_returns_new_dict(self):
        from integrated_app.service_layer import GenerationResult

        r = GenerationResult(params={"a": 1})
        d = r.to_dict()
        d["params"]["a"] = 2
        assert r.params["a"] == 1  # Original not modified


class TestLoadResult:
    """LoadResult 数据类测试。"""

    def test_defaults(self):
        from integrated_app.service_layer import LoadResult

        r = LoadResult()
        assert r.success is False
        assert r.message == ""
        assert r.engine == ""
        assert r.load_time == 0.0

    def test_success(self):
        from integrated_app.service_layer import LoadResult

        r = LoadResult(success=True, message="OK", engine="voxcpm2", load_time=3.2)
        assert r.success is True
        assert r.message == "OK"
        assert r.engine == "voxcpm2"
        assert r.load_time == 3.2


class TestSwitchResult:
    """SwitchResult 数据类测试。"""

    def test_defaults(self):
        from integrated_app.service_layer import SwitchResult

        r = SwitchResult()
        assert r.success is False
        assert r.from_engine == ""
        assert r.to_engine == ""
        assert r.switch_time == 0.0

    def test_success(self):
        from integrated_app.service_layer import SwitchResult

        r = SwitchResult(success=True, from_engine="voxcpm2", to_engine="indextts2", switch_time=5.0)
        assert r.success is True
        assert r.from_engine == "voxcpm2"
        assert r.to_engine == "indextts2"


class TestModelStatus:
    """ModelStatus 数据类测试。"""

    def test_defaults(self):
        from integrated_app.service_layer import ModelStatus

        s = ModelStatus()
        assert s.engine is None
        assert s.loaded is False
        assert s.ready is False
        assert s.vram_usage_percent == -1.0
        assert s.info == {}

    def test_with_values(self):
        from integrated_app.service_layer import ModelStatus

        s = ModelStatus(
            engine="voxcpm2",
            loaded=True,
            ready=True,
            vram_usage_percent=45.3,
            info={"version": "2.0"},
        )
        assert s.engine == "voxcpm2"
        assert s.loaded is True
        assert s.ready is True
        assert s.vram_usage_percent == 45.3
        assert s.info["version"] == "2.0"


class TestPersonaInfo:
    """PersonaInfo 数据类测试。"""

    def test_defaults(self):
        from integrated_app.service_layer import PersonaInfo

        info = PersonaInfo()
        assert info.name == ""
        assert info.description == ""
        assert info.wav_path == ""
        assert info.exists is False
        assert info.wav_size_kb == 0.0
        assert info.created_at == ""

    def test_with_values(self):
        from integrated_app.service_layer import PersonaInfo

        info = PersonaInfo(
            name="test_voice",
            description="test desc",
            wav_path="/tmp/test.wav",
            exists=True,
            wav_size_kb=512.0,
            created_at="2026-01-01 00:00",
        )
        assert info.name == "test_voice"
        assert info.exists is True
        assert info.wav_size_kb == 512.0


# =====================================================================
# VRAM 辅助函数测试
# =====================================================================


class TestVRAMChecks:
    """VRAM 熔断检查函数测试。"""

    def test_vram_check_returns_false_on_cpu(self):
        from integrated_app.service_layer import _check_vram_circuit_breaker

        with patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend") as mock:
            from integrated_app.gpu_backend import GPUBackend

            mock.return_value = GPUBackend.CPU
            assert _check_vram_circuit_breaker() is False

    def test_vram_usage_percent_on_cpu(self):
        from integrated_app.service_layer import _get_vram_usage_percent

        with patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend") as mock:
            from integrated_app.gpu_backend import GPUBackend

            mock.return_value = GPUBackend.CPU
            assert _get_vram_usage_percent() == 0.0

    def test_vram_check_returns_false_on_exception(self):
        from integrated_app.service_layer import _check_vram_circuit_breaker

        with patch(
            "integrated_app.gpu_backend.GPUBackendManager.detect_backend",
            side_effect=Exception("test"),
        ):
            assert _check_vram_circuit_breaker() is False

    def test_vram_usage_percent_returns_negative_on_exception(self):
        from integrated_app.service_layer import _get_vram_usage_percent

        with patch(
            "integrated_app.gpu_backend.GPUBackendManager.detect_backend",
            side_effect=Exception("test"),
        ):
            assert _get_vram_usage_percent() == -1.0

    def test_vram_check_returns_false_when_no_device(self):
        from integrated_app.service_layer import _check_vram_circuit_breaker

        with (
            patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend") as mock_be,
            patch("integrated_app.gpu_utils.get_gpu_device", return_value=None),
        ):
            from integrated_app.gpu_backend import GPUBackend

            mock_be.return_value = GPUBackend.CUDA
            assert _check_vram_circuit_breaker() is False

    def test_vram_check_triggers_when_over_threshold(self):
        from integrated_app.service_layer import _check_vram_circuit_breaker

        mock_device = MagicMock()
        with (
            patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend") as mock_be,
            patch("integrated_app.gpu_utils.get_gpu_device", return_value=mock_device),
            patch("integrated_app.gpu_backend.GPUBackendManager.get_device_properties") as mock_props,
            patch("integrated_app.gpu_backend.GPUBackendManager.memory_allocated") as mock_alloc,
        ):
            from integrated_app.gpu_backend import GPUBackend

            mock_be.return_value = GPUBackend.CUDA
            mock_props.return_value = {"total_memory": 10000}
            mock_alloc.return_value = 9500  # 95%
            assert _check_vram_circuit_breaker() is True

    def test_vram_usage_percent_with_device(self):
        from integrated_app.service_layer import _get_vram_usage_percent

        mock_device = MagicMock()
        with (
            patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend") as mock_be,
            patch("integrated_app.gpu_utils.get_gpu_device", return_value=mock_device),
            patch("integrated_app.gpu_backend.GPUBackendManager.get_device_properties") as mock_props,
            patch("integrated_app.gpu_backend.GPUBackendManager.memory_allocated") as mock_alloc,
        ):
            from integrated_app.gpu_backend import GPUBackend

            mock_be.return_value = GPUBackend.CUDA
            mock_props.return_value = {"total_memory": 10000}
            mock_alloc.return_value = 4000  # 40%
            assert _get_vram_usage_percent() == 40.0


# =====================================================================
# TTSGenerationService 测试
# =====================================================================


class TestTTSGenerationServiceHelpers:
    """TTSGenerationService 内部辅助方法测试。"""

    def test_extract_generation_result_tuple_with_audio_info(self):
        from integrated_app.service_layer import TTSGenerationService

        result = ((24000, b"wav_data", "output.wav"), "success")
        path, msg = TTSGenerationService._extract_generation_result(result)
        assert "output.wav" in path
        assert msg == "success"

    def test_extract_generation_result_string_path(self):
        from integrated_app.service_layer import TTSGenerationService

        result = ("/tmp/audio.wav", "done")
        path, msg = TTSGenerationService._extract_generation_result(result)
        assert path == "/tmp/audio.wav"
        assert msg == "done"

    def test_extract_generation_result_tuple_audio_info_str(self):
        from integrated_app.service_layer import TTSGenerationService

        result = (("/tmp/audio.wav",), "done")
        path, msg = TTSGenerationService._extract_generation_result(result)
        assert "/tmp/audio.wav" in path
        assert msg == "done"

    def test_extract_generation_result_non_tuple(self):
        from integrated_app.service_layer import TTSGenerationService

        path, msg = TTSGenerationService._extract_generation_result("just a string")
        assert path == "just a string"
        assert msg == "生成完成"

    def test_extract_generation_result_empty(self):
        from integrated_app.service_layer import TTSGenerationService

        path, msg = TTSGenerationService._extract_generation_result(None)
        assert path == ""
        assert msg == "生成完成"

    def test_estimate_duration_nonexistent_file(self):
        from integrated_app.service_layer import TTSGenerationService

        assert TTSGenerationService._estimate_duration("") == 0.0
        assert TTSGenerationService._estimate_duration("/nonexistent.wav") == 0.0

    def test_estimate_duration_with_filesize(self, tmp_path):
        from integrated_app.service_layer import TTSGenerationService

        # Create a fake wav file
        wav_file = tmp_path / "test.wav"
        wav_file.write_bytes(b"\x00" * 48000)  # ~1s at 48KB/s
        duration = TTSGenerationService._estimate_duration(str(wav_file))
        assert duration > 0
        assert abs(duration - 1.0) < 0.1


class TestTTSGenerationServiceGeneration:
    """TTSGenerationService 生成方法测试（使用 mock 引擎）。"""

    def _make_mock_engine(self, result=None):
        """创建 mock 引擎。"""
        engine = MagicMock()
        if result is None:
            result = (("/tmp/output.wav",), "生成完成")
        engine.generate_voice_design.return_value = result
        engine.generate_voice_clone.return_value = result
        engine.generate_ultimate_clone.return_value = result
        engine.generate_script.return_value = result
        return engine

    def _make_mock_registry(self, engine=None, ready=True):
        """创建 mock registry。"""
        registry = MagicMock()
        registry.is_engine_ready.return_value = ready
        registry.current_engine = "voxcpm2"
        registry.get_current_engine.return_value = engine
        return registry

    def test_generate_voice_design_success(self):
        from integrated_app.service_layer import TTSGenerationService

        engine = self._make_mock_engine()
        registry = self._make_mock_registry(engine)

        with (
            patch("integrated_app.model_registry.registry", registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
            patch("integrated_app.service_layer.TTSGenerationService._save_version_record", return_value=None),
        ):
            svc = TTSGenerationService()
            result = svc.generate_voice_design(text="你好", instruction="温柔")
            # generate_voice_design returns a GenerationResult
            from integrated_app.service_layer import GenerationResult

            assert isinstance(result, GenerationResult)
            engine.generate_voice_design.assert_called_once()

    def test_generate_voice_design_engine_not_ready(self):
        from integrated_app.service_layer import TTSGenerationService

        registry = MagicMock()
        registry.is_engine_ready.return_value = False

        with (
            patch("integrated_app.model_registry.registry", registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
        ):
            svc = TTSGenerationService()
            from integrated_app.exceptions import EngineNotLoadedError

            with pytest.raises(EngineNotLoadedError):
                svc.generate_voice_design(text="你好")

    def test_generate_voice_design_vram_circuit_breaker(self):
        from integrated_app.service_layer import TTSGenerationService

        registry = MagicMock()
        registry.is_engine_ready.return_value = True

        with (
            patch("integrated_app.model_registry.registry", registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=True),
        ):
            svc = TTSGenerationService()
            from integrated_app.exceptions import InsufficientVRAMError

            with pytest.raises(InsufficientVRAMError):
                svc.generate_voice_design(text="你好")

    def test_generate_voice_clone_success(self):
        from integrated_app.service_layer import TTSGenerationService

        engine = self._make_mock_engine()
        registry = self._make_mock_registry(engine)

        with (
            patch("integrated_app.model_registry.registry", registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
            patch("integrated_app.service_layer.TTSGenerationService._save_version_record", return_value=None),
        ):
            svc = TTSGenerationService()
            result = svc.generate_voice_clone(text="你好", reference_audio="/tmp/ref.wav")
            assert result is not None
            engine.generate_voice_clone.assert_called_once()

    def test_generate_ultimate_clone_requires_voxcpm2(self):
        from integrated_app.service_layer import TTSGenerationService

        registry = MagicMock()
        registry.is_engine_ready.return_value = True
        registry.current_engine = "indextts2"  # Wrong engine

        with (
            patch("integrated_app.model_registry.registry", registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
        ):
            svc = TTSGenerationService()
            from integrated_app.exceptions import EngineSwitchError

            with pytest.raises(EngineSwitchError):
                svc.generate_ultimate_clone(text="你好")

    def test_generate_ultimate_clone_success(self):
        from integrated_app.service_layer import TTSGenerationService

        engine = self._make_mock_engine()
        registry = self._make_mock_registry(engine)
        registry.current_engine = "voxcpm2"

        with (
            patch("integrated_app.model_registry.registry", registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
            patch("integrated_app.service_layer.TTSGenerationService._save_version_record", return_value=None),
        ):
            svc = TTSGenerationService()
            result = svc.generate_ultimate_clone(text="你好")
            assert result is not None
            engine.generate_ultimate_clone.assert_called_once()

    def test_generate_script_success(self):
        from integrated_app.service_layer import TTSGenerationService

        engine = self._make_mock_engine()
        registry = self._make_mock_registry(engine)

        with (
            patch("integrated_app.model_registry.registry", registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
            patch("integrated_app.service_layer.TTSGenerationService._save_version_record", return_value=None),
        ):
            svc = TTSGenerationService()
            result = svc.generate_script(text="你好", speaker_map={"A": "vox1"})
            assert result is not None
            engine.generate_script.assert_called_once()

    def test_generate_voice_design_exception_propagates(self):
        from integrated_app.service_layer import TTSGenerationService

        engine = MagicMock()
        engine.generate_voice_design.side_effect = RuntimeError("engine error")
        registry = self._make_mock_registry(engine)

        with (
            patch("integrated_app.model_registry.registry", registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
        ):
            svc = TTSGenerationService()
            with pytest.raises(RuntimeError, match="engine error"):
                svc.generate_voice_design(text="你好")

    def test_save_version_record_failure_is_safe(self):
        from integrated_app.service_layer import TTSGenerationService

        with patch("integrated_app.generation_versioning.get_version_manager") as mock:
            mock.side_effect = Exception("vm error")
            svc = TTSGenerationService()
            result = svc._save_version_record("/tmp/x.wav", "text", {}, "voxcpm2")
            assert result is None


class TestTTSGenerationServiceStreaming:
    """TTSGenerationService 流式生成测试。"""

    def test_generate_streaming_success(self):
        from integrated_app.service_layer import TTSGenerationService

        engine = MagicMock()

        def fake_streaming(text, reference_audio_path=None, **kwargs):
            yield b"chunk1"
            yield b"chunk2"

        engine.generate_streaming = fake_streaming

        registry = MagicMock()
        registry.is_engine_ready.return_value = True
        registry.current_engine = "voxcpm2"
        registry.get_current_engine.return_value = engine

        with (
            patch("integrated_app.model_registry.registry", registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
        ):
            import asyncio

            svc = TTSGenerationService()

            async def run():
                chunks = []
                async for chunk in svc.generate_streaming(text="你好"):
                    chunks.append(chunk)
                return chunks

            loop = asyncio.new_event_loop()
            try:
                chunks = loop.run_until_complete(run())
                assert len(chunks) == 2
                assert chunks[0] == b"chunk1"
                assert chunks[1] == b"chunk2"
            finally:
                loop.close()

    def test_generate_streaming_tobytes(self):
        import numpy as np

        from integrated_app.service_layer import TTSGenerationService

        engine = MagicMock()

        def fake_streaming(text, reference_audio_path=None, **kwargs):
            yield np.array([1, 2, 3], dtype=np.float32)

        engine.generate_streaming = fake_streaming

        registry = MagicMock()
        registry.is_engine_ready.return_value = True
        registry.current_engine = "voxcpm2"
        registry.get_current_engine.return_value = engine

        with (
            patch("integrated_app.model_registry.registry", registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
        ):
            import asyncio

            svc = TTSGenerationService()

            async def run():
                chunks = []
                async for chunk in svc.generate_streaming(text="你好"):
                    chunks.append(chunk)
                return chunks

            loop = asyncio.new_event_loop()
            try:
                chunks = loop.run_until_complete(run())
                assert len(chunks) == 1
                assert isinstance(chunks[0], bytes)
            finally:
                loop.close()

    def test_generate_streaming_tuple_format(self):
        import numpy as np

        from integrated_app.service_layer import TTSGenerationService

        engine = MagicMock()

        def fake_streaming(text, reference_audio_path=None, **kwargs):
            yield (24000, np.array([1, 2, 3], dtype=np.float32))

        engine.generate_streaming = fake_streaming

        registry = MagicMock()
        registry.is_engine_ready.return_value = True
        registry.current_engine = "voxcpm2"
        registry.get_current_engine.return_value = engine

        with (
            patch("integrated_app.model_registry.registry", registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
        ):
            import asyncio

            svc = TTSGenerationService()

            async def run():
                chunks = []
                async for chunk in svc.generate_streaming(text="你好"):
                    chunks.append(chunk)
                return chunks

            loop = asyncio.new_event_loop()
            try:
                chunks = loop.run_until_complete(run())
                assert len(chunks) == 1
            finally:
                loop.close()


# =====================================================================
# ModelService 测试
# =====================================================================


class TestModelService:
    """ModelService 测试。"""

    def test_load_model_voxcpm2_success(self):
        from integrated_app.service_layer import ModelService

        svc = ModelService()

        def fake_load_voxcpm2():
            yield ("loading...",)
            yield ("done",)

        with (
            patch("integrated_app.model_manager.load_voxcpm2", fake_load_voxcpm2),
            patch("integrated_app.model_registry.registry") as reg,
        ):
            reg.is_voxcpm_ready.return_value = True
            result = svc.load_model("voxcpm2")
            assert result.success is True
            assert result.engine == "voxcpm2"

    def test_load_model_voxcpm2_failure(self):
        from integrated_app.service_layer import ModelService

        svc = ModelService()

        def fake_load_voxcpm2():
            yield ("error",)

        with (
            patch("integrated_app.model_manager.load_voxcpm2", fake_load_voxcpm2),
            patch("integrated_app.model_registry.registry") as reg,
        ):
            reg.is_voxcpm_ready.return_value = False
            result = svc.load_model("voxcpm2")
            assert result.success is False

    def test_load_model_indextts2_success(self):
        from integrated_app.service_layer import ModelService

        svc = ModelService()

        def fake_load_indextts2():
            yield ("loading...",)
            yield ("done",)

        with (
            patch("integrated_app.model_manager.load_indextts2", fake_load_indextts2),
            patch("integrated_app.model_registry.registry") as reg,
        ):
            reg.is_indextts2_ready.return_value = True
            result = svc.load_model("indextts2")
            assert result.success is True
            assert result.engine == "indextts2"

    def test_load_model_unknown_engine(self):
        from integrated_app.service_layer import ModelService

        svc = ModelService()
        result = svc.load_model("unknown_engine")
        assert result.success is False
        assert "不支持" in result.message

    def test_load_model_exception(self):
        from integrated_app.service_layer import ModelService

        svc = ModelService()

        with patch("integrated_app.model_manager.load_voxcpm2", side_effect=Exception("crash")):
            result = svc.load_model("voxcpm2")
            assert result.success is False
            assert "crash" in result.message

    def test_unload_model(self):
        from integrated_app.service_layer import ModelService

        svc = ModelService()
        with patch("integrated_app.model_manager.unload_model") as mock:
            svc.unload_model()
            mock.assert_called_once()

    def test_unload_model_raises(self):
        from integrated_app.service_layer import ModelService

        svc = ModelService()
        with (
            patch("integrated_app.model_manager.unload_model", side_effect=Exception("fail")),
            pytest.raises(Exception, match="fail"),
        ):
            svc.unload_model()

    def test_switch_engine_success(self):
        from integrated_app.service_layer import ModelService

        svc = ModelService()

        def fake_switch(engine):
            yield ("switching...",)
            yield ("done",)

        with (
            patch("integrated_app.model_manager.switch_engine", fake_switch),
            patch("integrated_app.model_registry.registry") as reg,
        ):
            reg.current_engine = "voxcpm2"
            result = svc.switch_engine("indextts2")
            assert result.success is True
            assert result.from_engine == "voxcpm2"
            assert result.to_engine == "indextts2"

    def test_switch_engine_exception(self):
        from integrated_app.service_layer import ModelService

        svc = ModelService()

        with (
            patch("integrated_app.model_manager.switch_engine", side_effect=Exception("fail")),
            patch("integrated_app.model_registry.registry") as reg,
        ):
            reg.current_engine = "voxcpm2"
            from integrated_app.exceptions import EngineSwitchError

            with pytest.raises(EngineSwitchError):
                svc.switch_engine("indextts2")

    def test_get_model_status(self):
        from integrated_app.service_layer import ModelService

        svc = ModelService()
        with (
            patch("integrated_app.model_registry.registry") as reg,
            patch("integrated_app.service_layer._get_vram_usage_percent", return_value=42.5),
        ):
            reg.current_engine = "voxcpm2"
            reg.model_loaded = True
            reg.is_engine_ready.return_value = True
            reg.get_current_model_info.return_value = {"ready": True}
            status = svc.get_model_status()
            assert status.engine == "voxcpm2"
            assert status.loaded is True
            assert status.ready is True
            assert status.vram_usage_percent == 42.5


# =====================================================================
# PersonaService 测试
# =====================================================================


class TestPersonaService:
    """PersonaService 测试。"""

    def test_cache_validation(self):
        from integrated_app.service_layer import PersonaService

        svc = PersonaService()
        # Initial cache invalid
        assert svc._is_cache_valid() is False
        # Set timestamp
        svc._cache_timestamp = time.time()
        assert svc._is_cache_valid() is True
        # Expire
        svc._cache_timestamp = time.time() - 100
        assert svc._is_cache_valid() is False
        # Invalidate
        svc._cache_timestamp = time.time()
        svc._invalidate_cache()
        assert svc._is_cache_valid() is False
        assert len(svc._cache) == 0

    def test_list_personas_empty(self):
        from integrated_app.service_layer import PersonaService

        svc = PersonaService()
        with patch("integrated_app.persona_manager.get_persona_list", return_value=[]):
            result = svc.list_personas()
            assert result == []

    def test_list_personas_with_no_persona_marker(self):
        from integrated_app.service_layer import PersonaService

        svc = PersonaService()
        with patch("integrated_app.persona_manager.get_persona_list", return_value=["(暂无音色)"]):
            result = svc.list_personas()
            assert result == []

    def test_get_persona_not_found(self):
        from integrated_app.service_layer import PersonaService

        svc = PersonaService()
        with patch("integrated_app.persona_manager.load_persona_embedding", return_value=None):
            result = svc.get_persona("nonexistent")
            assert result is None

    def test_get_persona_invalid_name(self):
        from integrated_app.service_layer import PersonaService

        svc = PersonaService()
        # Invalid name (special chars)
        result = svc.get_persona("!!invalid!!")
        assert result is None

    def test_delete_persona_success(self):
        from integrated_app.service_layer import PersonaService

        svc = PersonaService()
        with patch("integrated_app.persona_manager.delete_persona", return_value=(True, "OK")):
            result = svc.delete_persona("test")
            assert result is True

    def test_delete_persona_failure(self):
        from integrated_app.service_layer import PersonaService

        svc = PersonaService()
        with patch("integrated_app.persona_manager.delete_persona", return_value=(False, "not found")):
            result = svc.delete_persona("test")
            assert result is False


# =====================================================================
# 单例获取测试
# =====================================================================


class TestSingletons:
    """单例获取测试。"""

    def test_get_generation_service_singleton(self):
        from integrated_app.service_layer import get_generation_service

        svc1 = get_generation_service()
        svc2 = get_generation_service()
        assert svc1 is svc2

    def test_get_model_service_singleton(self):
        from integrated_app.service_layer import get_model_service

        svc1 = get_model_service()
        svc2 = get_model_service()
        assert svc1 is svc2

    def test_get_persona_service_singleton(self):
        from integrated_app.service_layer import get_persona_service

        svc1 = get_persona_service()
        svc2 = get_persona_service()
        assert svc1 is svc2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
