"""P1-3 剧本 Schema 接入 + 说话人感知分块测试。

覆盖点：
1. Schema 解析角色标签、情绪提示、指令行与空台词；
2. 3 角色以上长剧本按“≤3 角色 / ≤300 字符”分块，块内不串角色、块间不丢行；
3. 指令行跟随下一句台词，不被拆散或丢弃；
4. 同一块不混引擎；
5. speaker_map 真正被消费：Persona 经 Voice Profile 解析，跨引擎角色显式报错；
6. 假模型逐行生成时，每个角色始终使用自己的参考音频（音色漂移检查）。
"""

import sys
from pathlib import Path

import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))


def _roles_script(roles: list[str], lines: int) -> str:
    """生成 roles 轮转的 lines 行剧本，每行台词唯一。"""
    rows = []
    for i in range(lines):
        role = roles[i % len(roles)]
        rows.append(f"[{role}] 第{i + 1}句台词")
    return "\n".join(rows)


class TestSchemaParse:
    """schema.script_dubbing.parse_script 的剧本语义。"""

    def test_roles_and_emotion(self):
        from integrated_app.schema.script_dubbing import parse_script

        segs = parse_script("[爱丽丝](开心) 你好\n[鲍勃] 嗨")

        assert [s.speaker for s in segs] == ["爱丽丝", "鲍勃"]
        assert segs[0].text == "(开心)你好"
        assert segs[1].text == "嗨"

    def test_instruction_segment(self):
        from integrated_app.schema.script_dubbing import parse_script

        segs = parse_script("[A] 一\n[uv_break]\n[pause=500]\n[B] 二")

        assert [s.is_instruction for s in segs] == [False, True, True, False]
        assert segs[1].text == "uv_break"
        assert segs[2].text == "pause=500"

    def test_empty_line_kept_for_error_reporting(self):
        from integrated_app.schema.script_dubbing import parse_script

        segs = parse_script("[A]\n[B] 有台词")

        assert segs[0].speaker == "A"
        assert segs[0].text == ""
        assert segs[1].text == "有台词"

    def test_continuation_lines_merged(self):
        from integrated_app.schema.script_dubbing import parse_script

        segs = parse_script("[A] 第一行\n第二行\n[B] 别的")

        assert segs[0].text == "第一行 第二行"


class TestSpeakerAwarePlanning:
    """说话人感知分块：人数上限、字符上限、保序、不串角色。"""

    def test_four_roles_long_script_blocks(self):
        from integrated_app.script_planner import plan_script_text

        roles = ["旁白", "小明", "妈妈", "老师"]
        plan = plan_script_text(_roles_script(roles, 24), max_speakers=3, max_chars=300)

        assert plan.blocks, "长剧本必须产出多个块"
        for block in plan.blocks:
            assert len(block.speakers) <= 3, f"块 {block.index} 超过 3 个角色: {block.speakers}"
            assert block.char_count <= 300
            assert all(seg.speaker in block.speakers for seg in block.segments if not seg.is_instruction)

        # 行序与角色/文本都不得漂移
        flattened = [seg for block in plan.blocks for seg in block.segments]
        assert len(flattened) == 24
        for i in range(24):
            assert flattened[i].speaker == roles[i % 4]
            assert flattened[i].text == f"第{i + 1}句台词"

    def test_rendered_tags_are_intact_and_parse_back(self):
        from integrated_app.schema.script_dubbing import parse_script
        from integrated_app.script_planner import plan_script_segments

        original = parse_script(_roles_script(["A", "B", "C", "D"], 20))
        blocks = plan_script_segments(original, max_speakers=3, max_chars=300)

        for block in blocks:
            reparsed = parse_script(block.text)
            assert [s.speaker for s in reparsed] == [s.speaker for s in block.segments]
            assert all(s.speaker in block.speakers for s in reparsed)

        # 重新解析全部块，角色与文本顺序应与原剧本一致
        joined = "\n".join(block.text for block in blocks)
        reparsed_all = parse_script(joined)
        assert [(s.speaker, s.text) for s in reparsed_all] == [(s.speaker, s.text) for s in original]

    def test_instruction_follows_next_line(self):
        from integrated_app.schema.script_dubbing import parse_script
        from integrated_app.script_planner import plan_script_segments

        segments = parse_script("[A] 一\n[uv_break]\n[B] 二")
        blocks = plan_script_segments(segments, max_speakers=3, max_chars=300)

        lines = blocks[0].text.splitlines()
        assert "[uv_break]" in lines
        assert lines.index("[uv_break]") < lines.index("[B] 二"), "指令必须紧邻下一句台词之前"

    def test_long_single_line_split_keeps_speaker(self):
        from integrated_app.schema.script_dubbing import ScriptSegment
        from integrated_app.script_planner import plan_script_segments

        long_text = "这是很长的一句话。" * 40  # 480 字符，超过 300
        blocks = plan_script_segments(
            [ScriptSegment(speaker="旁白", text=long_text, index=0)],
            max_speakers=3,
            max_chars=80,
        )

        assert len(blocks) > 1
        assert all(block.speakers == ("旁白",) for block in blocks)
        assert all(block.text.startswith("[旁白] ") for block in blocks)
        assert all(len(block.text) <= 80 for block in blocks)
        assert all(seg.speaker == "旁白" for block in blocks for seg in block.segments)

    def test_engine_boundary_splits_blocks(self):
        from integrated_app.schema.script_dubbing import SpeakerConfig
        from integrated_app.script_planner import plan_script_text

        speakers = {
            "A": SpeakerConfig(name="A", engine="indextts2"),
            "B": SpeakerConfig(name="B", engine="voxcpm2"),
        }
        plan = plan_script_text("[A] 甲\n[B] 乙\n[A] 丙", speakers=speakers)

        assert len(plan.blocks) >= 2, "引擎切换必须切块"
        for block in plan.blocks:
            assert block.engine in {"indextts2", "voxcpm2"}
            assert {speakers[s].engine for s in block.speakers} == {block.engine}

    def test_config_limits_are_honoured(self):
        from integrated_app.schema.script_dubbing import ScriptConfig, SpeakerConfig
        from integrated_app.script_planner import plan_script_config

        config = ScriptConfig(
            script_text=_roles_script(["A", "B", "C", "D"], 12),
            speakers={r: SpeakerConfig(name=r, engine="voxcpm2") for r in "ABCD"},
            max_speakers_per_block=2,
            max_chars_per_block=120,
        )

        plan = plan_script_config(config)

        assert all(len(block.speakers) <= 2 for block in plan.blocks)
        assert all(block.char_count <= 120 for block in plan.blocks)

    def test_undeclared_speaker_warning(self):
        from integrated_app.script_planner import plan_script_text

        plan = plan_script_text("[未声明] 台词", default_engine="voxcpm2")

        assert any("未声明" in w for w in plan.warnings)


class TestSpeakerMapBinding:
    """speaker_map 归一化：Persona 走 Voice Profile，跨引擎显式上报。"""

    @pytest.fixture
    def persona_dir(self, tmp_path, monkeypatch):
        from integrated_app import persona_manager as pm

        monkeypatch.setattr(pm, "PERSONA_DIR", str(tmp_path))
        (tmp_path / "alice.wav").write_bytes(b"RIFF....WAVEfmt ")
        (tmp_path / "alice.txt").write_text("爱丽丝参考文本", encoding="utf-8")
        return tmp_path

    def test_persona_name_resolved(self, persona_dir):
        from integrated_app.script_planner import build_speaker_bindings

        result = build_speaker_bindings({"爱丽丝": {"persona_name": "alice", "engine": "voxcpm2"}})

        assert not result.unsupported
        binding = result.bindings["爱丽丝"]
        assert binding.wav_path == str(persona_dir / "alice.wav")
        assert binding.ref_text == "爱丽丝参考文本"

    def test_cross_engine_reported_not_dropped(self, persona_dir):
        from integrated_app.script_planner import build_speaker_bindings

        result = build_speaker_bindings(
            {"爱丽丝": {"persona_name": "alice", "engine": "indextts2"}},
            target_engine="voxcpm2",
        )

        assert result.unsupported == [("爱丽丝", "indextts2")]
        assert result.bindings["爱丽丝"].wav_path, "即使跨引擎也要保留资产层解析结果"

    def test_missing_persona_warns(self, persona_dir):
        from integrated_app.script_planner import build_speaker_bindings

        result = build_speaker_bindings({"无名": {"persona_name": "ghost", "engine": "voxcpm2"}})

        assert result.bindings["无名"].wav_path is None
        assert any("ghost" in w for w in result.warnings)

    def test_speaker_config_object_accepted(self, persona_dir):
        from integrated_app.schema.script_dubbing import SpeakerConfig
        from integrated_app.script_planner import build_speaker_bindings

        result = build_speaker_bindings(
            {"爱丽丝": SpeakerConfig(name="爱丽丝", persona_name="alice", engine="voxcpm2")}
        )

        assert result.bindings["爱丽丝"].wav_path == str(persona_dir / "alice.wav")

    def test_prepare_request_merges_persona_map(self, persona_dir, tmp_path):
        from integrated_app.script_planner import prepare_script_request

        raw = tmp_path / "raw.wav"
        raw.write_bytes(b"RIFF....WAVEfmt ")

        request = prepare_script_request(
            "[甲] 一\n[乙] 二",
            speaker_map={"甲": {"wav": str(raw), "engine": "voxcpm2"}},
            persona_map={"乙": str(raw)},
        )

        assert request.persona_map["甲"]["wav"] == str(raw)
        assert request.persona_map["乙"]["wav"] == str(raw)
        assert not request.unsupported_engines
        assert all(len(b.speakers) <= 3 for b in request.plan.blocks)


class _FakeModel:
    """记录每次 generate 的文本与参考音频，返回固定长度静音。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate(self, *, text: str, reference_wav_path: str, **_kwargs):
        self.calls.append((text, reference_wav_path))
        return np.zeros(64, dtype=np.float32)


class TestGenerationDrift:
    """3 角色以上长剧本的逐行生成：角色与音色都不得漂移。"""

    @pytest.fixture
    def four_role_setup(self, tmp_path):
        from integrated_app.script_planner import prepare_script_request

        roles = ["旁白", "小明", "妈妈", "老师"]
        speaker_map = {}
        for i, role in enumerate(roles, start=1):
            wav = tmp_path / f"voice_{i}.wav"
            wav.write_bytes(b"RIFF....WAVEfmt " + bytes([i]) * 16)
            speaker_map[role] = {"wav": str(wav), "text": f"参考{i}", "engine": "voxcpm2"}

        script = _roles_script(roles, 24)
        request = prepare_script_request(script, speaker_map=speaker_map, target_engine="voxcpm2")
        return roles, speaker_map, request, tmp_path

    def test_no_speaker_or_voice_drift(self, four_role_setup):
        from integrated_app.engines.voxcpm2.script import generate_script_blocks

        roles, speaker_map, request, _tmp = four_role_setup
        model = _FakeModel()

        results = generate_script_blocks(
            model=model,
            blocks=request.plan.blocks,
            persona_map=request.persona_map,
        )

        assert len(results) == 24
        assert all(r.error is None for r in results), [r.error for r in results]
        assert len(model.calls) == 24

        # 每句文本唯一，可反查角色；每次调用必须使用该角色的参考音频
        text_to_role = {f"第{i + 1}句台词": roles[i % 4] for i in range(24)}
        expected_wav = {text: speaker_map[role]["wav"] for text, role in text_to_role.items()}
        for text, wav in model.calls:
            assert expected_wav[text] == wav, f"台词 {text} 使用了错误的参考音频 {wav}"

        # 同一角色跨块仍用同一音色（无漂移）
        seen: dict[str, set[str]] = {}
        for text, wav in model.calls:
            seen.setdefault(text_to_role[text], set()).add(wav)
        assert all(len(v) == 1 for v in seen.values())

        # 块内角色约束
        assert all(len(block.speakers) <= 3 for block in request.plan.blocks)

    def test_tampered_block_rejected(self, tmp_path):
        from integrated_app.engines.voxcpm2.script import generate_script_blocks
        from integrated_app.schema.script_dubbing import ScriptSegment
        from integrated_app.script_planner import ScriptBlock

        wav = tmp_path / "only_a.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        block = ScriptBlock(
            index=0,
            speakers=("A",),
            segments=(ScriptSegment(speaker="B", text="越权台词", index=0),),
            text="[B] 越权台词",
            char_count=10,
        )
        model = _FakeModel()

        results = generate_script_blocks(
            model=model,
            blocks=[block],
            persona_map={"A": {"wav": str(wav)}, "B": {"wav": str(wav)}},
        )

        assert len(results) == 1
        assert "不在本块说话人集合" in (results[0].error or "")
        assert model.calls == [], "越权行不得触发模型推理"


class TestEngineWiring:
    """VoxCPM2Engine.generate_script 真正消费 speaker_map，而非静默忽略。"""

    def _engine(self, monkeypatch):
        from integrated_app.engines.voxcpm2.engine import VoxCPM2Engine

        engine = VoxCPM2Engine()
        monkeypatch.setattr(engine, "is_ready", lambda: True)
        return engine

    def test_speaker_map_reaches_studio(self, monkeypatch, tmp_path):
        from integrated_app.engines.voxcpm2 import script as script_module

        wav = tmp_path / "a.wav"
        wav.write_bytes(b"RIFF....WAVEfmt ")
        captured: dict = {}

        def fake_studio(**kwargs):
            captured.update(kwargs)
            return ((48000, np.zeros(8, dtype=np.float32), "x.wav"), "ok")

        monkeypatch.setattr(script_module, "fn_voxcpm_script_studio", fake_studio)

        engine = self._engine(monkeypatch)
        engine.generate_script(
            "[A] 你好",
            speaker_map={"A": {"wav": str(wav), "text": "转写", "engine": "voxcpm2"}},
        )

        assert captured["persona_map_with_wav"]["A"]["wav"] == str(wav)
        assert captured["persona_map_with_wav"]["A"]["text"] == "转写"
        assert captured["planner_blocks"], "必须把说话人感知分块下传"
        assert len(captured["planner_blocks"][0].speakers) <= 3

    def test_non_voxcpm2_engine_role_raises(self, monkeypatch):
        from integrated_app.engines.voxcpm2 import script as script_module
        from integrated_app.exceptions import GenerationError

        called = []
        monkeypatch.setattr(script_module, "fn_voxcpm_script_studio", lambda **kw: called.append(kw))

        engine = self._engine(monkeypatch)
        with pytest.raises(GenerationError) as excinfo:
            engine.generate_script(
                "[A] 你好",
                speaker_map={"A": {"persona_name": "ghost", "engine": "indextts2"}},
            )

        assert "indextts2" in str(excinfo.value)
        assert called == [], "跨引擎角色不得进入 VoxCPM2 合成"
