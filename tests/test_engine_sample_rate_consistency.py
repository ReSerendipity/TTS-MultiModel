"""引擎采样率单一事实源守卫（UPSTREAM_SYNC 落地 #4）。

背景：同一个"引擎输出采样率"曾同时写在四个地方，其中两份是错的——

    config.yaml models.engines.<name>.sample_rate   48000 / 22050   <- 权威
    engine_interface 注册实参                        48000 / 22050   <- 与权威一致
    resampling.ENGINE_SAMPLE_RATES（手抄兜底表）      24000 / 16000   <- **两处都错了近一倍**
    routes/.../voxcpm2/streaming.py 常量              48000           <- 碰巧对

resampling 那份错表当时全仓零引用所以没暴露；但它一旦被接上，按 24000 重采样
48kHz 音频会得到"时长翻倍、音调变低"的文件，而且不会抛错。

本文件不重复 scripts/check_engine_specs.py 的 config↔注册表↔磁盘比对，只守它
管不到的三点：
    1. 兜底表/兜底常量必须等于权威值（错表就是在这里被抓的）。
    2. 取值优先级：引擎实例实际值 > config 声明 > 兜底常量。
    3. engines/ 里不得再出现与权威值不符的裸采样率形参默认值
       （concatenate_lines(sample_rate=24000) 那一类——首轮报红量 0，
       因为只约束形参默认值，不约束函数体内有意的兜底赋值）。
"""

from __future__ import annotations

import ast
import asyncio
import wave
from pathlib import Path

import numpy as np
import pytest
import yaml

from integrated_app import model_registry, resampling
from integrated_app.engine_interface import engine_registry
from integrated_app.model_registry import registry
from integrated_app.routes.generate.voxcpm2 import streaming

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENGINES_DIR = _REPO_ROOT / "app" / "integrated_app" / "engines"
# 历史上被误写进兜底表、但没有任何现役引擎真的以它为原生输出率的值
_LEGACY_MISMATCH_RATES = {24000, 16000, 44100}


def _yaml_engines() -> dict[str, dict]:
    cfg = yaml.safe_load((_REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    engines = (cfg.get("models") or {}).get("engines") or {}
    assert engines, "config.yaml 里读不到 models.engines，本文件的断言会空转"
    return engines


def _declared_rate(entry: dict) -> int | None:
    for key in ("sample_rate", "spec"):
        if key == "spec":
            nested = entry.get("spec") or {}
            if isinstance(nested, dict) and nested.get("sample_rate"):
                return int(nested["sample_rate"])
        elif entry.get(key):
            return int(entry[key])
    return None


def test_config_declares_sample_rate_for_every_registered_engine() -> None:
    """每个注册引擎都必须在 config.yaml 有 sample_rate，否则注册侧默认值会悄悄生效。

    engine_registry.register(sample_rate=24000) 有默认值——新引擎忘记传参时
    不会报错，只会带着一个没人验证过的 24000 上线。
    """
    engines = _yaml_engines()
    names = engine_registry.list_engines()
    assert names, "注册表为空，本断言会空转"
    for name in names:
        meta = engine_registry.get_metadata(name) or {}
        assert meta.get("sample_rate"), f"{name} 注册时未声明 sample_rate，会吃到 register() 的默认值"
        entry = engines.get(name)
        assert entry is not None, f"{name} 已注册但 config.yaml models.engines 里没有它"
        declared = _declared_rate(entry)
        assert declared == meta["sample_rate"], f"{name}: config 声明 {declared} != 注册值 {meta['sample_rate']}"


def test_resampling_fallback_table_matches_authority() -> None:
    """兜底表里每个引擎的值都必须等于权威值——24000/16000 那次抄错就在这里被抓。"""
    assert resampling.ENGINE_SAMPLE_RATES, "兜底表被清空，下面的断言会空转"
    for name, rate in resampling.ENGINE_SAMPLE_RATES.items():
        meta = engine_registry.get_metadata(name)
        assert meta is not None, f"兜底表里出现未注册引擎 {name}"
        assert rate == meta["sample_rate"], (
            f"resampling.ENGINE_SAMPLE_RATES[{name!r}]={rate} 与权威值 {meta['sample_rate']} 不符；"
            "按此重采样会得到变速/变调的文件"
        )


def test_no_live_code_hardcodes_a_legacy_rate() -> None:
    """现役取值路径不得再返回历史错值。"""
    for name in engine_registry.list_engines():
        meta = engine_registry.get_metadata(name) or {}
        rate = meta.get("sample_rate")
        if rate in _LEGACY_MISMATCH_RATES:
            declared = _declared_rate(_yaml_engines()[name])
            assert declared == rate, f"{name} 的 {rate} 与 config({declared}) 不一致"
    assert resampling.get_declared_sample_rate("voxcpm2") == 48000


def _bare_rate_defaults_in(source: str) -> list[tuple[str, str, int]]:
    """从一段 Python 源码里挑出「形参默认值是裸采样率字面量」的 (函数, 参数, 值)。

    只认形参默认值，不认函数体内的赋值：兜底常量、分段自身速率推断这类
    写法是有意为之，一律纳入会把门禁首轮打成一片红，也就没人看了。
    """
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for arg, default in zip(
            node.args.args[len(node.args.args) - len(node.args.defaults) :], node.args.defaults, strict=False
        ):
            if not (isinstance(default, ast.Constant) and isinstance(default.value, int)):
                continue
            if arg.arg == "sr" or arg.arg.endswith("_sr") or "sample_rate" in arg.arg:
                found.append((node.name, arg.arg, default.value))
    return found


def _engine_of(relpath: str) -> str | None:
    """把 engines/ 下的文件路径映射到引擎名（最长名优先，防 indextts2 吃掉 indextts20）。"""
    lowered = relpath.lower()
    for name in sorted(engine_registry.list_engines(), key=len, reverse=True):
        if name.lower() in lowered:
            return name
    return None


def test_engine_dir_param_defaults_do_not_reinvent_the_rate() -> None:
    """engines/ 里的裸采样率形参默认值必须等于该引擎的权威声明值。

    抓的是 concatenate_lines(sample_rate=24000) 这一类：voxcpm2 实际输出 48k，
    默认值差了近一倍，唯一的现役调用方恰好显式传了 48000 所以一直没发作——
    下一个忘记传参的调用方就会拿到"时长翻倍、音调变低"的合并结果。
    """
    checked = 0
    for path in sorted(_ENGINES_DIR.rglob("*.py")):
        relpath = path.relative_to(_REPO_ROOT).as_posix()
        engine = _engine_of(relpath)
        if engine is None:
            continue
        authority = resampling.get_declared_sample_rate(engine)
        for func, arg, value in _bare_rate_defaults_in(path.read_text(encoding="utf-8")):
            checked += 1
            assert value == authority, (
                f"{relpath}::{func}({arg}={value}) 与 {engine} 的权威采样率 {authority} 不符；"
                f"改为默认 None 并在函数体里取 resampling.get_declared_sample_rate({engine!r})"
            )
    assert checked, "engines/ 下一个采样率形参默认值都没扫到，本断言已空转（扫描器或目录变了）"


def test_rate_default_scanner_catches_a_known_bad_sample() -> None:
    """扫描器自身的已知答案：坏样例必须被抓到，否则上一条测试的"绿"没有意义。"""
    assert _bare_rate_defaults_in("def f(a, sample_rate: int = 16000):\n    return a\n") == [
        ("f", "sample_rate", 16000)
    ]
    assert _bare_rate_defaults_in("def f(a, sr=22050):\n    return a\n") == [("f", "sr", 22050)]
    # 非采样率参数与函数体内赋值都不该报
    assert _bare_rate_defaults_in("def f(a, timeout: int = 16000):\n    return a\n") == []
    assert _bare_rate_defaults_in("def f(a):\n    sr = 24000\n    return sr\n") == []


def test_concatenate_lines_default_follows_the_authority() -> None:
    """concatenate_lines 不传 sample_rate 时按权威值合并，显式传参仍可覆盖。"""
    from integrated_app.engines.voxcpm2.script import ScriptLine, concatenate_lines

    authority = resampling.get_declared_sample_rate("voxcpm2")
    assert concatenate_lines.__defaults__[1] is None, "默认值又被写回字面量了"

    def _line(idx: int) -> ScriptLine:
        return ScriptLine(
            line_id=idx,
            role=None,
            text="hello",
            is_instruction=False,
            duration_ms=None,
            audio=np.zeros(authority, dtype=np.float32),
            error=None,
        )

    merged, sr = concatenate_lines([_line(1), _line(2)], silence_ms=0)
    assert sr == authority
    assert merged.shape[0] == 2 * authority, "默认路径下每秒音频未被按权威采样率计点"

    _, sr_override = concatenate_lines([_line(1), _line(2)], silence_ms=0, sample_rate=8000)
    assert sr_override == 8000, "显式覆盖被默认解析吃掉了"


def test_get_declared_sample_rate_prefers_config_over_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """config 有值时以 config 为准；没有时回落兜底表；两边都没有则 None。"""
    sentinel = 32_000  # 不属于任何现役引擎，用来证明"确实读到了 spec 而非兜底表"

    class _Spec:
        sample_rate = sentinel

    monkeypatch.setitem(model_registry._engine_specs, "voxcpm2", _Spec())
    assert resampling.get_declared_sample_rate("voxcpm2") == sentinel

    monkeypatch.delitem(model_registry._engine_specs, "voxcpm2")
    assert resampling.get_declared_sample_rate("voxcpm2") == resampling.ENGINE_SAMPLE_RATES["voxcpm2"]

    assert resampling.get_declared_sample_rate("no-such-engine") is None


def test_streaming_route_reads_model_rate_before_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """流式路由优先用已加载模型自报的采样率，其次 config，最后本模块兜底常量。"""

    class _Model:
        sample_rate = 44_100

    monkeypatch.setattr(registry, "voxcpm_model", _Model(), raising=False)
    assert streaming._resolve_stream_sample_rate() == 44_100

    monkeypatch.setattr(registry, "voxcpm_model", None, raising=False)
    monkeypatch.setitem(model_registry._engine_specs, "voxcpm2", type("S", (), {"sample_rate": 22_050})())
    assert streaming._resolve_stream_sample_rate() == 22_050

    monkeypatch.delitem(model_registry._engine_specs, "voxcpm2")
    assert streaming._resolve_stream_sample_rate() == streaming._STREAMING_SAMPLE_RATE_FALLBACK


def test_merge_and_save_wav_writes_the_rate_it_claims(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写进 WAV 头的采样率必须与时长换算用的是同一个，否则播放器按错的速率放。"""
    monkeypatch.setattr(streaming, "SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(registry, "voxcpm_model", type("M", (), {"sample_rate": 44_100})(), raising=False)

    chunk = np.zeros(44_100, dtype=np.int16)  # 恰好 1 秒 @44.1kHz
    filename, duration = asyncio.run(streaming._merge_and_save_wav([chunk], "srtest"))

    with wave.open(str(tmp_path / filename)) as wf:
        assert wf.getframerate() == 44_100, "WAV 头里的采样率不是引擎自报值"
    assert duration == pytest.approx(1.0, abs=0.01), f"时长换算用了错的采样率（得到 {duration}s）"
