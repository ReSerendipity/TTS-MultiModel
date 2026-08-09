"""engines/voxcpm2/_base.py 单元测试 — 高级参数构建与模板生成。

覆盖目标模块: bin/integrated_app/engines/voxcpm2/_base.py
"""

import numpy as np

from integrated_app.engines.voxcpm2._base import (
    _advanced_kwargs,
    _check_segment_quality,
    build_advanced_params,
    get_advanced_params,
)


class TestAdvancedParams:
    def test_get_advanced_params(self):
        params = get_advanced_params()
        assert isinstance(params, dict)
        assert "max_len" in params

    def test_build_valid_overrides(self):
        config = build_advanced_params(max_len=4000)
        assert config.max_len == 4000

    def test_build_invalid_keys_ignored(self):
        config = build_advanced_params(cfg_val=5.0)  # 拼写错误
        assert config is not None

    def test_build_validation_fallback(self):
        config = build_advanced_params(max_len="not-a-number")
        assert config is not None  # fail-soft 回退默认

    def test_advanced_kwargs_keys(self):
        kwargs = _advanced_kwargs()
        assert set(kwargs) == {
            "max_len",
            "retry_badcase",
            "retry_badcase_max_times",
            "retry_badcase_ratio_threshold",
        }


class TestCheckSegmentQuality:
    def test_good_segment(self):
        t = np.linspace(0, 1.0, 24000, endpoint=False)
        wav = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        ok, reason = _check_segment_quality(wav, 24000, expected_min_duration=0.5)
        assert ok is True

    def test_empty_segment(self):
        ok, reason = _check_segment_quality(np.array([]), 24000)
        assert ok is False
