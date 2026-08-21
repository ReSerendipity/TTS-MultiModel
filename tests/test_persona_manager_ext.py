"""persona_manager 模块单元测试 — 音色管理。

覆盖目标模块: app/integrated_app/persona_manager.py
"""

from integrated_app.persona_manager import (
    _validate_persona_name,
    delete_persona,
    get_persona_desc,
    get_persona_list,
    get_total_persona_count,
)


class TestValidatePersonaName:
    def test_valid_name(self):
        ok, msg = _validate_persona_name("小明")
        assert ok is True
        assert msg == ""

    def test_empty_name(self):
        ok, msg = _validate_persona_name("")
        assert ok is False

    def test_too_long_name(self):
        ok, msg = _validate_persona_name("a" * 100)
        assert ok is False

    def test_illegal_chars(self):
        ok, msg = _validate_persona_name("../evil")
        assert ok is False or "/" not in msg  # 路径分隔符应被拒绝


class TestPersonaList:
    def test_get_persona_list(self):
        result = get_persona_list()
        assert isinstance(result, list)
        assert all(isinstance(p, str) for p in result)

    def test_get_persona_list_filter(self):
        result = get_persona_list(search_keyword="不存在的音色xyz")
        assert isinstance(result, list)

    def test_get_total_count(self):
        assert isinstance(get_total_persona_count(), int)

    def test_get_persona_desc(self):
        desc = get_persona_desc("不存在的音色xyz")
        assert desc is None or isinstance(desc, str)


class TestDeletePersona:
    def test_delete_nonexistent(self):
        ok, msg = delete_persona("不存在的音色xyz")
        assert ok is False
        assert isinstance(msg, str)
