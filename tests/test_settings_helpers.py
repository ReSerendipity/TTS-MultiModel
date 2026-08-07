"""routes/system/settings.py 单元测试 — 设置合并、黑名单与范围校验。

覆盖目标模块: bin/integrated_app/routes/system/settings.py
"""

from integrated_app.routes.system.settings import (
    _check_blacklist,
    _deep_update,
    _validate_ranges,
)


class TestDeepUpdate:
    def test_shallow_merge(self):
        base = {"a": 1, "b": {"x": 10}}
        patch = {"a": 2}
        result = _deep_update(base, patch)
        assert result["a"] == 2
        assert result["b"] == {"x": 10}
        assert base["a"] == 1  # 原字典不被修改

    def test_nested_merge(self):
        base = {"s": {"g": {"cfg": 1.0}}}
        patch = {"s": {"g": {"cfg": 2.0, "steps": 10}}}
        result = _deep_update(base, patch)
        assert result["s"]["g"]["cfg"] == 2.0
        assert result["s"]["g"]["steps"] == 10

    def test_new_keys_added(self):
        base = {"a": 1}
        result = _deep_update(base, {"b": {"c": 2}})
        assert result["b"]["c"] == 2

    def test_type_replaced_not_merged(self):
        base = {"a": {"x": 1}}
        result = _deep_update(base, {"a": 5})
        assert result["a"] == 5


class TestCheckBlacklist:
    def test_no_hit(self):
        # ui.sidebar_width 不在黑名单
        assert _check_blacklist({"ui": {"sidebar_width": 300}}) is None

    def test_hit_server_port(self):
        hit = _check_blacklist({"server": {"port": 7869}})
        assert hit == "server.port"

    def test_hit_top_level(self):
        hit = _check_blacklist({"api_auth": {"token": "secret"}})
        assert hit == "api_auth.token"

    def test_empty_patch(self):
        assert _check_blacklist({}) is None

    def test_models_blacklisted(self):
        hit = _check_blacklist({"models": {"voxcpm2": "/x"}})
        assert hit == "models"


class TestValidateRanges:
    def test_valid_values(self):
        assert _validate_ranges({"ui": {"sidebar_width": 300}}) is None

    def test_invalid_value(self):
        result = _validate_ranges({"ui": {"sidebar_width": 9999}})
        assert result is not None
        assert "sidebar_width" in result

    def test_non_numeric_skipped(self):
        assert _validate_ranges({"server": {"host": "localhost"}}) is None

    def test_empty(self):
        assert _validate_ranges({}) is None
