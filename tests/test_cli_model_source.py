"""Tests for CLI model-source resolution (app/integrated_app/cli.py).

覆盖 `load_model` 的模型来源回退链：
    ① --model-path（显式本地）
    ② --hf-model-id（显式 Hub）
    ③ config.yaml 的 voxcpm2.model_path（离线优先默认）
    ④ openbmb/VoxCPM2（Hub 默认仓库）

以及 `get_default_local_model_path` 的存在性判断与异常兜底。

背景（为什么需要这些测试）：legacy CLI 此前在未传 --model-path 时无条件走
`VoxCPM.from_pretrained`，离线环境必然失败；HF 缓存残缺时更会抛
`Expecting value: line 1 column 1 (char 0)`（config.json 为空）。改成离线优先后，
必须锁死四条分支的优先级，否则可能把「用户显式指定 Hub」也误吞成本地。

全部用例使用假的 `voxcpm.core` 模块，不加载真实权重。
"""

import os
import sys
import types
from types import SimpleNamespace

# Ensure app/ is on sys.path so we can import integrated_app.cli
_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)


def _install_fake_voxcpm(monkeypatch):
    """注入假的 voxcpm.core，返回 FakeVoxCPM 类供断言。"""

    class FakeVoxCPM:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.loaded_by = "local"

        @classmethod
        def from_pretrained(cls, **kwargs):
            obj = cls()
            obj.kwargs = kwargs
            obj.loaded_by = "hub"
            return obj

    core = types.ModuleType("voxcpm.core")
    core.VoxCPM = FakeVoxCPM
    pkg = types.ModuleType("voxcpm")
    pkg.core = core
    monkeypatch.setitem(sys.modules, "voxcpm", pkg)
    monkeypatch.setitem(sys.modules, "voxcpm.core", core)
    return FakeVoxCPM


def _make_args(**overrides):
    """构造 load_model 需要的最小参数集。"""
    base = {
        "model_path": None,
        "hf_model_id": None,
        "zipenhancer_path": None,
        "no_denoiser": True,
        "no_optimize": True,
        "lora_path": None,
        "cache_dir": None,
        "local_files_only": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# get_default_local_model_path
# ---------------------------------------------------------------------------


class TestGetDefaultLocalModelPath:
    """config 本地权重路径的解析与兜底。"""

    def test_returns_path_when_dir_exists(self, tmp_path, monkeypatch):
        from integrated_app import cli

        model_dir = tmp_path / "VoxCPM2"
        model_dir.mkdir()
        monkeypatch.setattr("integrated_app.config.get_voxcpm2_model_path", lambda: str(model_dir))

        assert cli.get_default_local_model_path() == str(model_dir)

    def test_returns_none_when_dir_missing(self, tmp_path, monkeypatch):
        from integrated_app import cli

        missing = tmp_path / "not-there"
        monkeypatch.setattr("integrated_app.config.get_voxcpm2_model_path", lambda: str(missing))

        assert cli.get_default_local_model_path() is None

    def test_returns_none_when_config_raises(self, monkeypatch):
        from integrated_app import cli

        def boom():
            raise RuntimeError("config broken")

        monkeypatch.setattr("integrated_app.config.get_voxcpm2_model_path", boom)

        # 配置异常不应把异常抛给调用方，否则模型加载会被无关问题阻断
        assert cli.get_default_local_model_path() is None


# ---------------------------------------------------------------------------
# load_model 回退链
# ---------------------------------------------------------------------------


class TestLoadModelSourceFallback:
    """四条来源分支的优先级必须稳定。"""

    def test_explicit_model_path_wins_over_everything(self, tmp_path, monkeypatch):
        from integrated_app import cli

        _install_fake_voxcpm(monkeypatch)
        explicit = tmp_path / "explicit"
        explicit.mkdir()
        # 同时给一个本地默认，验证优先级：显式路径必须赢
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        monkeypatch.setattr(cli, "get_default_local_model_path", lambda: str(default_dir))

        model = cli.load_model(_make_args(model_path=str(explicit)))

        assert model.loaded_by == "local"
        assert model.kwargs["voxcpm_model_path"] == str(explicit)

    def test_explicit_hf_id_wins_over_local_default(self, tmp_path, monkeypatch):
        from integrated_app import cli

        _install_fake_voxcpm(monkeypatch)
        default_dir = tmp_path / "default"
        default_dir.mkdir()
        monkeypatch.setattr(cli, "get_default_local_model_path", lambda: str(default_dir))

        model = cli.load_model(_make_args(hf_model_id="someone/other-model"))

        # 用户显式指定 Hub 时必须走 Hub，不能被离线优先吞掉
        assert model.loaded_by == "hub"
        assert model.kwargs["hf_model_id"] == "someone/other-model"

    def test_defaults_to_local_config_path(self, tmp_path, monkeypatch):
        from integrated_app import cli

        _install_fake_voxcpm(monkeypatch)
        default_dir = tmp_path / "VoxCPM2"
        default_dir.mkdir()
        monkeypatch.setattr(cli, "get_default_local_model_path", lambda: str(default_dir))

        model = cli.load_model(_make_args())

        assert model.loaded_by == "local"
        assert model.kwargs["voxcpm_model_path"] == str(default_dir)

    def test_falls_back_to_hub_when_no_local(self, monkeypatch):
        from integrated_app import cli

        _install_fake_voxcpm(monkeypatch)
        monkeypatch.setattr(cli, "get_default_local_model_path", lambda: None)

        model = cli.load_model(_make_args())

        # 历史行为保底：本地也没有时仍走默认 Hub 仓库
        assert model.loaded_by == "hub"
        assert model.kwargs["hf_model_id"] == cli.DEFAULT_HF_MODEL_ID

    def test_hf_model_id_default_is_none_after_parse(self):
        """参数默认值必须是 None，否则 load_model 无法区分「显式指定」与「默认」。"""
        from integrated_app.cli import _build_parser

        args = _build_parser().parse_args(["batch", "--input", "x.txt", "--output-dir", "out"])
        assert args.hf_model_id is None

    def test_parser_accepts_explicit_hf_model_id(self):
        from integrated_app.cli import _build_parser

        args = _build_parser().parse_args(
            ["batch", "--input", "x.txt", "--output-dir", "out", "--hf-model-id", "org/model"]
        )
        assert args.hf_model_id == "org/model"
