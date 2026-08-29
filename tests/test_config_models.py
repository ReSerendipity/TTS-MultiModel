import pytest
from pydantic import ValidationError


class TestAdvancedParamsConfig:
    def test_default_values(self):
        from integrated_app.config_models import AdvancedParamsConfig

        cfg = AdvancedParamsConfig()
        assert cfg.max_len == 3000
        assert cfg.retry_badcase is True
        assert cfg.retry_badcase_max_times == 3
        assert cfg.retry_badcase_ratio_threshold == 6.0
        assert cfg.trim_silence_vad is True

    def test_custom_values(self):
        from integrated_app.config_models import AdvancedParamsConfig

        cfg = AdvancedParamsConfig(max_len=2048, retry_badcase=False)
        assert cfg.max_len == 2048
        assert cfg.retry_badcase is False

    def test_to_dict(self):
        from integrated_app.config_models import AdvancedParamsConfig

        cfg = AdvancedParamsConfig()
        d = cfg.to_dict()
        assert isinstance(d, dict)
        assert d["max_len"] == 3000
        assert d["retry_badcase"] is True

    def test_validation_max_len_range(self):
        from integrated_app.config_models import AdvancedParamsConfig

        # max_len has no Field constraints, but retry_badcase_ratio_threshold has gt=0
        with pytest.raises(ValidationError):
            AdvancedParamsConfig(retry_badcase_ratio_threshold=0)
        with pytest.raises(ValidationError):
            AdvancedParamsConfig(retry_badcase_ratio_threshold=-1.0)

    def test_validation_retry_max_times_range(self):
        from integrated_app.config_models import AdvancedParamsConfig

        # retry_badcase_max_times has ge=0, le=10
        with pytest.raises(ValidationError):
            AdvancedParamsConfig(retry_badcase_max_times=100)
        with pytest.raises(ValidationError):
            AdvancedParamsConfig(retry_badcase_max_times=-1)


class TestBuildAdvancedParams:
    def test_build_with_overrides(self):
        from integrated_app.engines.voxcpm2_engine import build_advanced_params

        cfg = build_advanced_params(max_len=2048)
        assert cfg.max_len == 2048
        assert cfg.retry_badcase is True

    def test_build_ignores_unknown_keys(self):
        from integrated_app.engines.voxcpm2_engine import build_advanced_params

        cfg = build_advanced_params(unknown_key=123, max_len=1024)
        assert cfg.max_len == 1024

    def test_build_no_overrides(self):
        from integrated_app.engines.voxcpm2_engine import build_advanced_params

        cfg = build_advanced_params()
        assert cfg.max_len == 3000


class TestAdvancedKwargs:
    def test_default_kwargs(self):
        from integrated_app.engines.voxcpm2_engine import _advanced_kwargs

        kwargs = _advanced_kwargs()
        assert kwargs["max_len"] == 3000
        assert kwargs["retry_badcase"] is True
        assert kwargs["retry_badcase_max_times"] == 3
        assert kwargs["retry_badcase_ratio_threshold"] == 6.0

    def test_custom_kwargs(self):
        from integrated_app.engines.voxcpm2_engine import _advanced_kwargs, build_advanced_params

        cfg = build_advanced_params(max_len=2048, retry_badcase=False)
        kwargs = _advanced_kwargs(cfg)
        assert kwargs["max_len"] == 2048
        assert kwargs["retry_badcase"] is False


class TestGetAdvancedParams:
    def test_returns_dict(self):
        from integrated_app.engines.voxcpm2_engine import get_advanced_params

        result = get_advanced_params()
        assert isinstance(result, dict)
        assert "max_len" in result
        assert "retry_badcase" in result
