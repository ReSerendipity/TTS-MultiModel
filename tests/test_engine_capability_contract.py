"""引擎能力契约矩阵测试。

验证每个已注册引擎的：
1. supported_features 与基线契约一致（防止静默增删能力）
2. 声明的能力对应方法真实存在于引擎类
3. 未声明的能力不冒充实现（缺方法或显式 NotImplementedError）
4. 元数据完整：languages 非空、sample_rate > 0、vram_requirement >= 0、display_name 非空
"""

import contextlib

import pytest

from integrated_app.engine_interface import engine_registry

# 基线契约：引擎名 → 期望的 supported_features 集合
EXPECTED_FEATURES: dict[str, set[str]] = {
    "voxcpm2": {
        "voice_design",
        "clone",
        "ultimate",
        "script",
        "streaming",
        "prompt",
        "lora",
    },
    "indextts2": {
        "clone",
        "emotion_control",
        "duration_control",
    },
    "indextts20": {
        "clone",
        "emotion_control",
    },
}

# 能力 → 对应方法名映射（用于验证声明的能力有真实实现）
FEATURE_METHOD_MAP: dict[str, str] = {
    "voice_design": "generate_voice_design",
    "clone": "generate_voice_clone",
    "script": "generate_script",
    "streaming": "generate_streaming",
    "ultimate": "generate_ultimate_clone",
    "prompt": "generate_with_prompt",
    "lora": "load_lora",
}

# 已知全部能力集合（用于反向验证：未声明的能力不应被实现）
ALL_KNOWN_FEATURES = set(FEATURE_METHOD_MAP.keys()) | {"emotion_control", "duration_control"}


class TestEngineCapabilityContract:
    """引擎能力契约矩阵测试。"""

    def test_all_expected_engines_registered(self):
        """基线中声明的引擎必须全部已注册。"""
        registered = set(engine_registry.list_engines())
        for name in EXPECTED_FEATURES:
            assert name in registered, f"引擎 {name} 未注册"

    def test_no_unexpected_engines(self):
        """注册的引擎必须都在基线中（新增引擎需同步更新本测试基线）。"""
        registered = set(engine_registry.list_engines())
        unexpected = registered - set(EXPECTED_FEATURES.keys())
        assert not unexpected, f"发现未在基线中的引擎: {unexpected}，请更新 EXPECTED_FEATURES"

    @pytest.mark.parametrize("engine_name", list(EXPECTED_FEATURES.keys()))
    def test_supported_features_match_baseline(self, engine_name):
        """每个引擎的 supported_features 必须与基线完全一致。"""
        meta = engine_registry.get_metadata(engine_name)
        actual = set(meta.get("supported_features", []))
        expected = EXPECTED_FEATURES[engine_name]
        assert actual == expected, (
            f"{engine_name} 能力声明不匹配:\n  缺失: {expected - actual}\n  多余: {actual - expected}"
        )

    @pytest.mark.parametrize("engine_name", list(EXPECTED_FEATURES.keys()))
    def test_declared_features_have_methods(self, engine_name):
        """声明的能力必须在引擎类上有对应方法（懒导入引擎需能解析到类）。"""
        meta = engine_registry.get_metadata(engine_name)
        features = meta.get("supported_features", [])
        engine_cls = engine_registry.get(engine_name)
        assert engine_cls is not None, f"无法获取引擎类: {engine_name}"

        for feature in features:
            method_name = FEATURE_METHOD_MAP.get(feature)
            if method_name is None:
                # emotion_control / duration_control 是参数级能力，无独立方法
                continue
            assert hasattr(engine_cls, method_name), f"{engine_name} 声明了 {feature} 但缺少方法 {method_name}"

    @pytest.mark.parametrize("engine_name", list(EXPECTED_FEATURES.keys()))
    def test_undeclared_features_not_implemented(self, engine_name):
        """未声明的能力不应在引擎类上有对应方法（防止能力声明与实现脱节）。"""
        meta = engine_registry.get_metadata(engine_name)
        declared = set(meta.get("supported_features", []))
        engine_cls = engine_registry.get(engine_name)
        assert engine_cls is not None

        undeclared = ALL_KNOWN_FEATURES - declared
        for feature in undeclared:
            method_name = FEATURE_METHOD_MAP.get(feature)
            if method_name is None:
                continue
            # 未声明的能力：要么没有方法，要么方法显式 raise NotImplementedError
            if hasattr(engine_cls, method_name):
                method = getattr(engine_cls, method_name)
                # 检查是否是抽象方法或显式 NotImplementedError
                import inspect

                src = ""
                with contextlib.suppress(OSError, TypeError):
                    src = inspect.getsource(method)
                is_stub = (
                    "NotImplementedError" in src
                    or "raise NotImplementedError" in src
                    or getattr(method, "__isabstractmethod__", False)
                )
                assert is_stub, (
                    f"{engine_name} 未声明 {feature} 但实现了 {method_name}，请更新 supported_features 或移除实现"
                )

    @pytest.mark.parametrize("engine_name", list(EXPECTED_FEATURES.keys()))
    def test_metadata_complete(self, engine_name):
        """元数据必须完整：display_name、languages、sample_rate、vram_requirement。"""
        meta = engine_registry.get_metadata(engine_name)
        assert meta.get("display_name"), f"{engine_name} 缺少 display_name"
        langs = meta.get("languages", [])
        assert isinstance(langs, list) and len(langs) > 0, f"{engine_name} languages 为空"
        sr = meta.get("sample_rate", 0)
        assert sr > 0, f"{engine_name} sample_rate 无效: {sr}"
        vram = meta.get("vram_requirement", -1)
        assert vram >= 0, f"{engine_name} vram_requirement 无效: {vram}"

    def test_voxcpm2_has_all_core_features(self):
        """VoxCPM2 作为核心引擎必须具备全部 5 项核心能力。"""
        meta = engine_registry.get_metadata("voxcpm2")
        features = set(meta.get("supported_features", []))
        core = {"voice_design", "clone", "script", "streaming"}
        assert core.issubset(features), f"VoxCPM2 缺少核心能力: {core - features}"

    def test_indextts20_is_subset_of_indextts2(self):
        """IndexTTS 2.0 的能力必须是 2.5 的子集（旧版本不应有新版本没有的能力）。"""
        meta2 = engine_registry.get_metadata("indextts2")
        meta20 = engine_registry.get_metadata("indextts20")
        f2 = set(meta2.get("supported_features", []))
        f20 = set(meta20.get("supported_features", []))
        assert f20.issubset(f2), f"IndexTTS 2.0 有 2.5 没有的能力: {f20 - f2}"

    def test_feature_method_map_covers_all_method_features(self):
        """FEATURE_METHOD_MAP 必须覆盖所有有对应方法的能力。"""
        method_features = {"voice_design", "clone", "script", "streaming", "ultimate", "prompt", "lora"}
        assert set(FEATURE_METHOD_MAP.keys()) == method_features
