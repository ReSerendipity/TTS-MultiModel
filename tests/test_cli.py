"""Tests for the CLI module (app/integrated_app/cli.py).

Covers:
- File validators (validate_file_exists, require_file_exists, validate_output_path)
- Range validators (validate_ranges)
- Helper functions (build_final_text, resolve_prompt_text)
- Architecture detection (detect_model_architecture)
- Argument validation (validate_design_args, validate_clone_args, validate_batch_args)
- Batch input parsers (_parse_text_input, _parse_json_input, _parse_csv_input)
- Parser construction (_build_parser)
- Main entry point dispatch (main)
- Legacy mode dispatch (_dispatch_legacy)

These tests use mock objects to avoid loading real VoxCPM models.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure app/ is on sys.path so we can import integrated_app.cli
_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)


# ---------------------------------------------------------------------------
# File validators
# ---------------------------------------------------------------------------


class TestValidateFileExists:
    """Test validate_file_exists function."""

    def test_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        from integrated_app.cli import validate_file_exists

        path = validate_file_exists(str(f), "test file")
        assert path == f
        assert path.exists()

    def test_nonexistent_file(self):
        from integrated_app.cli import validate_file_exists

        with pytest.raises(FileNotFoundError, match="test file"):
            validate_file_exists("/nonexistent/path", "test file")

    def test_default_file_type(self, tmp_path):
        from integrated_app.cli import validate_file_exists

        f = tmp_path / "data.txt"
        f.write_text("data")
        path = validate_file_exists(str(f))
        assert path == f


class TestRequireFileExists:
    """Test require_file_exists function."""

    def test_existing_file(self, tmp_path):
        from integrated_app.cli import require_file_exists

        f = tmp_path / "audio.wav"
        f.write_text("audio")
        parser = MagicMock()
        path = require_file_exists(str(f), parser, "audio file")
        assert path == f
        parser.error.assert_not_called()

    def test_nonexistent_file_calls_parser_error(self):
        from integrated_app.cli import require_file_exists

        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            require_file_exists("/nonexistent", parser, "audio file")
        parser.error.assert_called_once()


class TestValidateOutputPath:
    """Test validate_output_path function."""

    def test_creates_parent_dirs(self, tmp_path):
        from integrated_app.cli import validate_output_path

        output = tmp_path / "subdir" / "deeper" / "out.wav"
        path = validate_output_path(str(output))
        assert path.parent.exists()

    def test_existing_dir(self, tmp_path):
        from integrated_app.cli import validate_output_path

        output = tmp_path / "out.wav"
        path = validate_output_path(str(output))
        assert path == output


# ---------------------------------------------------------------------------
# Range validators
# ---------------------------------------------------------------------------


class TestValidateRanges:
    """Test validate_ranges function."""

    def _make_args(self, **kwargs):
        defaults = {
            "cfg_value": 2.0,
            "inference_timesteps": 10,
            "lora_r": 32,
            "lora_alpha": 16,
            "lora_dropout": 0.0,
        }
        defaults.update(kwargs)
        return MagicMock(**defaults)

    def test_valid_ranges(self):
        from integrated_app.cli import validate_ranges

        parser = MagicMock()
        args = self._make_args()
        validate_ranges(args, parser)
        parser.error.assert_not_called()

    def test_cfg_too_low(self):
        from integrated_app.cli import validate_ranges

        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            validate_ranges(self._make_args(cfg_value=0.05), parser)
        parser.error.assert_called_once()

    def test_cfg_too_high(self):
        from integrated_app.cli import validate_ranges

        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            validate_ranges(self._make_args(cfg_value=10.1), parser)

    def test_timesteps_out_of_range(self):
        from integrated_app.cli import validate_ranges

        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            validate_ranges(self._make_args(inference_timesteps=101), parser)

    def test_lora_r_zero(self):
        from integrated_app.cli import validate_ranges

        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            validate_ranges(self._make_args(lora_r=0), parser)

    def test_lora_dropout_out_of_range(self):
        from integrated_app.cli import validate_ranges

        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            validate_ranges(self._make_args(lora_dropout=1.5), parser)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestBuildFinalText:
    """Test build_final_text function."""

    def test_with_control(self):
        from integrated_app.cli import build_final_text

        result = build_final_text("hello", "warm female voice")
        assert result == "(warm female voice)hello"

    def test_without_control(self):
        from integrated_app.cli import build_final_text

        result = build_final_text("hello", None)
        assert result == "hello"

    def test_empty_control(self):
        from integrated_app.cli import build_final_text

        result = build_final_text("hello", "")
        assert result == "hello"

    def test_whitespace_control(self):
        from integrated_app.cli import build_final_text

        result = build_final_text("hello", "   ")
        assert result == "hello"


class TestResolvePromptText:
    """Test resolve_prompt_text function."""

    def test_with_prompt_text(self):
        from integrated_app.cli import resolve_prompt_text

        args = MagicMock(prompt_text="hello world", prompt_file=None)
        parser = MagicMock()
        result = resolve_prompt_text(args, parser)
        assert result == "hello world"

    def test_with_prompt_file(self, tmp_path):
        from integrated_app.cli import resolve_prompt_text

        f = tmp_path / "prompt.txt"
        f.write_text("file content")
        args = MagicMock(prompt_text=None, prompt_file=str(f))
        parser = MagicMock()
        result = resolve_prompt_text(args, parser)
        assert result == "file content"

    def test_both_text_and_file_errors(self):
        from integrated_app.cli import resolve_prompt_text

        args = MagicMock(prompt_text="hello", prompt_file="some_file.txt")
        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            resolve_prompt_text(args, parser)

    def test_neither_returns_none(self):
        from integrated_app.cli import resolve_prompt_text

        args = MagicMock(prompt_text=None, prompt_file=None)
        parser = MagicMock()
        result = resolve_prompt_text(args, parser)
        assert result is None


# ---------------------------------------------------------------------------
# Architecture detection
# ---------------------------------------------------------------------------


class TestDetectModelArchitecture:
    """Test detect_model_architecture function."""

    def test_no_model_location(self):
        from integrated_app.cli import detect_model_architecture

        args = MagicMock(model_path=None, hf_model_id=None)
        assert detect_model_architecture(args) is None

    def test_voxcpm2_in_id(self):
        from integrated_app.cli import detect_model_architecture

        args = MagicMock(model_path=None, hf_model_id="some/voxcpm2-model")
        assert detect_model_architecture(args) == "voxcpm2"

    def test_voxcpm_1_5_in_id(self):
        from integrated_app.cli import detect_model_architecture

        args = MagicMock(model_path=None, hf_model_id="some/voxcpm1.5-model")
        assert detect_model_architecture(args) == "voxcpm"

    def test_local_dir_with_config(self, tmp_path):
        from integrated_app.cli import detect_model_architecture

        d = tmp_path / "model"
        d.mkdir()
        (d / "config.json").write_text(json.dumps({"architecture": "VoxCPM2"}))
        args = MagicMock(model_path=str(d), hf_model_id=None)
        assert detect_model_architecture(args) == "voxcpm2"

    def test_local_dir_no_config(self, tmp_path):
        from integrated_app.cli import detect_model_architecture

        d = tmp_path / "model"
        d.mkdir()
        args = MagicMock(model_path=str(d), hf_model_id=None)
        assert detect_model_architecture(args) is None


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


class TestValidateDesignArgs:
    """Test validate_design_args function."""

    def test_valid_design(self):
        from integrated_app.cli import validate_design_args

        args = MagicMock(
            prompt_audio=None,
            reference_audio=None,
            prompt_text=None,
            prompt_file=None,
            text="hello",
            control=None,
        )
        parser = MagicMock()
        validate_design_args(args, parser)
        parser.error.assert_not_called()

    def test_design_with_prompt_audio_errors(self):
        from integrated_app.cli import validate_design_args

        args = MagicMock(
            prompt_audio="prompt.wav",
            reference_audio=None,
            prompt_text=None,
            prompt_file=None,
            text="hello",
            control=None,
        )
        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            validate_design_args(args, parser)


class TestValidateCloneArgs:
    """Test validate_clone_args function."""

    def test_clone_with_reference_audio(self):
        from integrated_app.cli import validate_clone_args

        args = MagicMock(
            prompt_audio=None,
            reference_audio="ref.wav",
            prompt_text=None,
            prompt_file=None,
            control=None,
        )
        parser = MagicMock()
        result = validate_clone_args(args, parser)
        assert result is None  # No prompt_text
        parser.error.assert_not_called()

    def test_clone_without_any_audio_errors(self):
        from integrated_app.cli import validate_clone_args

        args = MagicMock(
            prompt_audio=None,
            reference_audio=None,
            prompt_text=None,
            prompt_file=None,
            control=None,
        )
        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            validate_clone_args(args, parser)


# ---------------------------------------------------------------------------
# Batch input parsers
# ---------------------------------------------------------------------------


class TestParseTextInput:
    """Test _parse_text_input function."""

    def test_simple_lines(self, tmp_path):
        from integrated_app.cli import _parse_text_input

        f = tmp_path / "input.txt"
        f.write_text("line one\nline two\nline three\n")
        tasks = _parse_text_input(f)
        assert len(tasks) == 3
        assert tasks[0]["text"] == "line one"

    def test_skips_empty_and_comment_lines(self, tmp_path):
        from integrated_app.cli import _parse_text_input

        f = tmp_path / "input.txt"
        f.write_text("# comment\n\nactual text\n")
        tasks = _parse_text_input(f)
        assert len(tasks) == 1
        assert tasks[0]["text"] == "actual text"


class TestParseJsonInput:
    """Test _parse_json_input function."""

    def test_string_array(self, tmp_path):
        from integrated_app.cli import _parse_json_input

        f = tmp_path / "input.json"
        f.write_text(json.dumps(["hello", "world"]))
        parser = MagicMock()
        tasks = _parse_json_input(f, parser)
        assert len(tasks) == 2
        assert tasks[0]["text"] == "hello"

    def test_object_array(self, tmp_path):
        from integrated_app.cli import _parse_json_input

        f = tmp_path / "input.json"
        f.write_text(json.dumps([{"text": "hello"}, {"text": "world", "control": "warm"}]))
        parser = MagicMock()
        tasks = _parse_json_input(f, parser)
        assert len(tasks) == 2
        assert tasks[1]["control"] == "warm"

    def test_invalid_json(self, tmp_path):
        from integrated_app.cli import _parse_json_input

        f = tmp_path / "input.json"
        f.write_text("not json")
        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            _parse_json_input(f, parser)

    def test_non_array_json(self, tmp_path):
        from integrated_app.cli import _parse_json_input

        f = tmp_path / "input.json"
        f.write_text(json.dumps({"text": "hello"}))
        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            _parse_json_input(f, parser)


class TestParseCsvInput:
    """Test _parse_csv_input function."""

    def test_with_header(self, tmp_path):
        from integrated_app.cli import _parse_csv_input

        f = tmp_path / "input.csv"
        f.write_text("text,control\nhello,warm\nworld,cold\n")
        parser = MagicMock()
        tasks = _parse_csv_input(f, parser)
        assert len(tasks) == 2
        assert tasks[0]["text"] == "hello"
        assert tasks[0]["control"] == "warm"

    def test_without_header(self, tmp_path):
        from integrated_app.cli import _parse_csv_input

        f = tmp_path / "input.csv"
        f.write_text("hello,warm\nworld,cold\n")
        parser = MagicMock()
        tasks = _parse_csv_input(f, parser)
        assert len(tasks) == 2

    def test_empty_csv(self, tmp_path):
        from integrated_app.cli import _parse_csv_input

        f = tmp_path / "input.csv"
        f.write_text("")
        parser = MagicMock()
        tasks = _parse_csv_input(f, parser)
        assert tasks == []


class TestParseBatchInput:
    """Test _parse_batch_input dispatch function."""

    def test_txt_file(self, tmp_path):
        from integrated_app.cli import _parse_batch_input

        f = tmp_path / "input.txt"
        f.write_text("hello\nworld\n")
        parser = MagicMock()
        tasks = _parse_batch_input(f, MagicMock(), parser)
        assert len(tasks) == 2

    def test_json_file(self, tmp_path):
        from integrated_app.cli import _parse_batch_input

        f = tmp_path / "input.json"
        f.write_text(json.dumps(["hello", "world"]))
        parser = MagicMock()
        tasks = _parse_batch_input(f, MagicMock(), parser)
        assert len(tasks) == 2

    def test_csv_file(self, tmp_path):
        from integrated_app.cli import _parse_batch_input

        f = tmp_path / "input.csv"
        f.write_text("text\nhello\nworld\n")
        parser = MagicMock()
        tasks = _parse_batch_input(f, MagicMock(), parser)
        assert len(tasks) == 2


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


class TestBuildParser:
    """Test _build_parser function."""

    def test_parser_has_design_subcommand(self):
        from integrated_app.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["design", "--text", "hello", "--output", "out.wav"])
        assert args.command == "design"
        assert args.text == "hello"
        assert args.output == "out.wav"

    def test_parser_has_clone_subcommand(self):
        from integrated_app.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["clone", "--text", "hello", "--reference-audio", "ref.wav", "--output", "out.wav"])
        assert args.command == "clone"
        assert args.reference_audio == "ref.wav"

    def test_parser_has_batch_subcommand(self):
        from integrated_app.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["batch", "--input", "in.txt", "--output-dir", "out/"])
        assert args.command == "batch"
        assert args.input == "in.txt"
        assert args.output_dir == "out/"

    def test_legacy_mode(self):
        from integrated_app.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--text", "hello", "--output", "out.wav"])
        assert args.command is None
        assert args.text == "hello"

    def test_default_values(self):
        from integrated_app.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["design", "--text", "hi", "--output", "out.wav"])
        assert args.cfg_value == 2.0
        assert args.inference_timesteps == 10
        assert args.normalize is False
        assert args.hf_model_id == "openbmb/VoxCPM2"

    def test_lora_defaults(self):
        from integrated_app.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["design", "--text", "hi", "--output", "out.wav"])
        assert args.lora_r == 32
        assert args.lora_alpha == 16
        assert args.lora_dropout == 0.0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


class TestMainEntry:
    """Test main() function dispatch."""

    def test_design_dispatch(self):
        """main() should dispatch to cmd_design for design subcommand."""
        from integrated_app import cli

        with (
            patch.object(cli, "cmd_design") as mock_design,
            patch.object(cli, "validate_ranges"),
            patch.object(cli, "_build_parser") as mock_parser,
        ):
            mock_parser.return_value.parse_args.return_value = MagicMock(
                command="design", text="hello", output="out.wav"
            )
            cli.main()
            mock_design.assert_called_once()

    def test_clone_dispatch(self):
        from integrated_app import cli

        with (
            patch.object(cli, "cmd_clone") as mock_clone,
            patch.object(cli, "validate_ranges"),
            patch.object(cli, "_build_parser") as mock_parser,
        ):
            mock_parser.return_value.parse_args.return_value = MagicMock(
                command="clone", text="hello", output="out.wav"
            )
            cli.main()
            mock_clone.assert_called_once()

    def test_batch_dispatch(self):
        from integrated_app import cli

        with (
            patch.object(cli, "cmd_batch") as mock_batch,
            patch.object(cli, "validate_ranges"),
            patch.object(cli, "_build_parser") as mock_parser,
        ):
            mock_parser.return_value.parse_args.return_value = MagicMock(command="batch")
            cli.main()
            mock_batch.assert_called_once()

    def test_legacy_dispatch(self):
        from integrated_app import cli

        with (
            patch.object(cli, "_dispatch_legacy") as mock_legacy,
            patch.object(cli, "validate_ranges"),
            patch.object(cli, "_build_parser") as mock_parser,
        ):
            mock_parser.return_value.parse_args.return_value = MagicMock(command=None)
            cli.main()
            mock_legacy.assert_called_once()


# ---------------------------------------------------------------------------
# Legacy dispatch
# ---------------------------------------------------------------------------


class TestDispatchLegacy:
    """Test _dispatch_legacy function."""

    def test_batch_mode(self):
        from integrated_app import cli

        args = MagicMock(
            input="batch.txt",
            text=None,
            output_dir="out/",
            prompt_audio=None,
            prompt_text=None,
            prompt_file=None,
            reference_audio=None,
        )
        parser = MagicMock()
        with patch.object(cli, "cmd_batch") as mock_batch:
            _dispatch_legacy_safe(args, parser)
            mock_batch.assert_called_once()

    def test_single_with_text_and_output(self):
        from integrated_app import cli

        args = MagicMock(
            input=None,
            text="hello",
            output="out.wav",
            prompt_audio=None,
            prompt_text=None,
            prompt_file=None,
            reference_audio=None,
        )
        parser = MagicMock()
        with patch.object(cli, "cmd_design") as mock_design:
            _dispatch_legacy_safe(args, parser)
            mock_design.assert_called_once()

    def test_single_with_prompt_calls_clone(self):
        from integrated_app import cli

        args = MagicMock(
            input=None,
            text="hello",
            output="out.wav",
            prompt_audio="prompt.wav",
            prompt_text="text",
            prompt_file=None,
            reference_audio=None,
        )
        parser = MagicMock()
        with patch.object(cli, "cmd_clone") as mock_clone:
            _dispatch_legacy_safe(args, parser)
            mock_clone.assert_called_once()

    def test_input_and_text_conflict(self):
        from integrated_app import cli

        args = MagicMock(
            input="batch.txt",
            text="hello",
            output_dir="out/",
        )
        parser = MagicMock()
        parser.error.side_effect = SystemExit(2)
        with pytest.raises(SystemExit):
            cli._dispatch_legacy(args, parser)


def _dispatch_legacy_safe(args, parser):
    """Wrapper that skips warn_legacy_mode to avoid noise."""
    from integrated_app import cli

    with patch.object(cli, "warn_legacy_mode"):
        return cli._dispatch_legacy(args, parser)
