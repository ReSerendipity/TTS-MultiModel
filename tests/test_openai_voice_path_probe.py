"""OpenAI 兼容端点的 `voice` 参数不得当文件存在性探针用。

`openai_api._persona_wav_exists` 之前的写法是
`os.path.exists(os.path.join(PERSONA_DIR, f"{voice}.wav"))`：请求体里的字符串被原样
拼进路径，命中就继续合成、不命中才 400 —— 响应差异即泄露「该绝对路径是否存在」。
这里锁住目录归属判定本身（端点级冒烟见 test_voice_clone_consent.py）。
"""

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))


@pytest.fixture
def persona_env(tmp_path, monkeypatch):
    """PERSONA_DIR 指向临时目录，并在其**外面**放一个可被探针命中的 wav。"""
    from integrated_app import openai_api, persona_manager

    root = tmp_path / "personas"
    root.mkdir()
    outside = tmp_path / "secret_target"
    outside.with_suffix(".wav").write_bytes(b"RIFF....WAVEfmt ")
    inside = root / "alice"
    inside.with_suffix(".wav").write_bytes(b"RIFF....WAVEfmt ")

    monkeypatch.setattr(persona_manager, "PERSONA_DIR", str(root))
    return openai_api, root, outside


class TestPersonaWavExistsContainment:
    def test_legitimate_persona_still_resolves(self, persona_env):
        openai_api, _root, _outside = persona_env
        assert openai_api._persona_wav_exists("alice") is True

    def test_missing_persona_is_false(self, persona_env):
        openai_api, _root, _outside = persona_env
        assert openai_api._persona_wav_exists("nobody") is False

    def test_relative_traversal_cannot_probe_outside(self, persona_env):
        """旧写法在此返回 True（等于给攻击者一个存在性预言机）。"""
        openai_api, root, outside = persona_env
        import os

        name = os.path.relpath(str(outside.with_suffix("")), str(root))
        assert ".." in name, "夹具要真的越出 PERSONA_DIR"
        assert openai_api._persona_wav_exists(name) is False

    def test_absolute_name_cannot_probe_outside(self, persona_env):
        """os.path.join(dir, 绝对名) 会整体丢弃 dir，旧写法直接命中任意路径。"""
        openai_api, _root, outside = persona_env
        assert openai_api._persona_wav_exists(str(outside.with_suffix(""))) is False

    def test_sibling_prefix_not_treated_as_inside(self, tmp_path, monkeypatch):
        """PERSONA_DIR=/x/personas 时 /x/personas_evil 不算其内部（前缀比对要带分隔符）。"""
        from integrated_app import openai_api, persona_manager

        root = tmp_path / "personas"
        root.mkdir()
        sibling = tmp_path / "personas_evil"
        sibling.mkdir()
        (sibling / "trap.wav").write_bytes(b"RIFF....WAVEfmt ")
        monkeypatch.setattr(persona_manager, "PERSONA_DIR", str(root))

        import os

        name = os.path.relpath(str(sibling / "trap"), str(root))
        assert openai_api._persona_wav_exists(name) is False
