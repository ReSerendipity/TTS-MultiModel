"""`scripts/check_pin_crossconflicts.py` 的判定逻辑（不联网）。

网络部分用 monkeypatch 喂假元数据；重点锁两类历史踩点：
① `==X.*` 通配（曾被解析成 `==X.` 造成 httpx 假阳性）；
② 上界/`==` 型冲突（`check_pin_floors.py` 那类下界检查抓不到）。
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

import check_pin_crossconflicts as ccc  # noqa: E402


class TestSatisfies:
    def test_wildcard_equal_spec(self):
        assert ccc._satisfies("==", "1.0.9", "1.*") is True
        assert ccc._satisfies("==", "4.13.2", "4.9.*") is False
        assert ccc._satisfies("==", "4.9.3", "4.9.*") is True
        assert ccc._satisfies("==", "4.10.0", "4.9.*") is False

    def test_bounds(self):
        assert ccc._satisfies("<", "0.23.2", "0.22") is False
        assert ccc._satisfies("<", "0.21.4", "0.22") is True
        assert ccc._satisfies(">=", "1.32.0", "1.5.0") is True
        assert ccc._satisfies("<", "1.4.1", "1.4") is False

    def test_local_version_and_prerelease_do_not_confuse(self):
        assert ccc._satisfies(">=", "2.13.0+cu132", "2.5.1") is True
        # 已知近似（脚本里明确记了这条偏差）：rc/dev 按基线参与比较，
        # 所以 `1.4.0rc1` 相对 `<1.4` 判为「不满足」→ 会被报成冲突。
        # PEP 440 真语义下 rc < 正式版，本应满足；这里刻意取保守方向：
        # 预发布版进锁本身就该被看见，宁可多报一条人工确认。
        assert ccc._satisfies("<", "1.4.0rc1", "1.4") is False
        assert ccc._satisfies("<", "1.3.0", "1.4") is True


class TestReadPins:
    def test_only_double_equals_and_comments(self, tmp_path):
        f = tmp_path / "requirements-lock.txt"
        f.write_text(
            "# comment ==fake==1\ntransformers==4.52.1\ndatasets>=2.0\nmpmath==1.3.0  # 手工降定\n",
            encoding="utf-8",
        )
        pins = ccc.read_pins(f)
        assert set(pins) == {"transformers", "mpmath"}
        assert pins["mpmath"][0] == "1.3.0"


class TestCheckEndToEnd:
    """喂假 PyPI 元数据，复现 #97 的四类冲突形状。"""

    META = {
        "transformers==4.52.1": ["tokenizers<0.22,>=0.21", "huggingface-hub<1.0,>=0.30.0"],
        "tokenizers==0.23.2": [],
        "huggingface-hub==0.36.2": [],
        "sympy==1.14.0": ["mpmath<1.4,>=1.1.0"],
        "mpmath==1.4.1": [],
        "hydra-core==1.3.7": ["antlr4-python3-runtime==4.9.*"],
        "antlr4-python3-runtime==4.13.2": [],
        "httpx==0.28.1": ["httpcore==1.*", "anyio"],
        "httpcore==1.0.9": [],
        "anyio==4.14.2": [],
    }

    def _run(self, tmp_path, monkeypatch):
        f = tmp_path / "requirements-lock.txt"
        f.write_text("\n".join(f"{k.split('==')[0]}=={k.split('==')[1]}" for k in self.META) + "\n", encoding="utf-8")
        monkeypatch.setattr(ccc, "requires_dist", lambda name, ver: self.META.get(f"{name}=={ver}", []))
        return ccc.check(f)

    def test_finds_ceiling_and_wildcard_conflicts(self, tmp_path, monkeypatch):
        conflicts, unchecked = self._run(tmp_path, monkeypatch)
        joined = "\n".join(conflicts)
        assert "transformers==4.52.1 要求 tokenizers<0.22" in joined
        assert "sympy==1.14.0 要求 mpmath<1.4" in joined
        assert "hydra-core==1.3.7 要求 antlr4-python3-runtime==4.9.*" in joined
        assert unchecked == []
        # 关键：httpcore==1.* 对 1.0.9 是满足的，不得成为第 4 条假阳性
        assert "httpcore" not in joined

    def test_clean_lock_yields_no_conflicts(self, tmp_path, monkeypatch):
        meta = dict(self.META)
        meta["tokenizers==0.21.4"] = meta.pop("tokenizers==0.23.2")
        meta["mpmath==1.3.0"] = meta.pop("mpmath==1.4.1")
        meta["antlr4-python3-runtime==4.9.3"] = meta.pop("antlr4-python3-runtime==4.13.2")
        src = "\n".join(f"{k.split('==')[0]}=={k.split('==')[1]}" for k in meta) + "\n"
        f = tmp_path / "requirements-lock.txt"
        f.write_text(src, encoding="utf-8")
        monkeypatch.setattr(ccc, "requires_dist", lambda name, ver: meta.get(f"{name}=={ver}", []))
        conflicts, unchecked = ccc.check(f)
        assert conflicts == [] and unchecked == []

    def test_unreachable_metadata_is_reported_not_swallowed(self, tmp_path, monkeypatch):
        f = tmp_path / "requirements-lock.txt"
        f.write_text("weirdpkg==1.0\nother==2.0\n", encoding="utf-8")
        monkeypatch.setattr(ccc, "requires_dist", lambda name, ver: None)
        conflicts, unchecked = ccc.check(f)
        assert conflicts == []
        assert len(unchecked) == 2, "取不到元数据必须计入未核验，不能当成通过"
