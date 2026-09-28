"""training/config.py 单元测试 — 配置模型校验 / JSON·YAML 往返 / 原子写 / argbind 兼容层。

覆盖目标模块: app/integrated_app/training/config.py（此前 0%，217 条语句无一行被测）

覆盖范围:
- DatasetConfig / LoRAConfig / OptimizerConfig / TrainingConfig 的默认值与边界校验
- _pydantic_to_dict 的 Path 递归字符串化、_recursive_field_replace 的 data_dir/output_dir 还原
- get_default_config 的输出目录推导
- save_training_config 的原子写（JSON / YAML、父目录创建、失败时 .tmp 清理）
- load_training_config 的 FileNotFoundError / 解析错误 / 根节点非 mapping / 多字段校验错误聚合
- load_yaml_config / parse_args_with_config 这两个 argbind 向后兼容辅助函数
- pydantic 缺失时的 dataclass 兜底分支（屏蔽 sys.modules['pydantic'] 重新加载同一文件）

注意: config.py 只依赖 argbind / pyyaml / pydantic，不需要 torch；但 argbind 属于
training 附加依赖，某些精简环境下可能缺失，因此沿用仓库既有口径在导入失败时跳过。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys
from typing import Any

import pytest

try:
    from integrated_app.training import config as cfg_mod
    from integrated_app.training.config import (
        DatasetConfig,
        LoRAConfig,
        OptimizerConfig,
        TrainingConfig,
        get_default_config,
        load_training_config,
        load_yaml_config,
        parse_args_with_config,
        save_training_config,
    )

    HAS_CONFIG = True
except Exception:  # noqa: BLE001 - argbind/pyyaml 缺失时整模块不可用，跳过而非报错
    HAS_CONFIG = False

pytestmark = pytest.mark.skipif(not HAS_CONFIG, reason="training.config 需要 argbind + pyyaml")

if HAS_CONFIG:
    from integrated_app.training.config import _pydantic_to_dict, _recursive_field_replace

BASE_KWARGS: dict[str, Any] = {
    "dataset": {"data_dir": pathlib.Path("/tmp/ds")},
    "lora": {},
    "optimizer": {},
    "output_dir": pathlib.Path("/tmp/out"),
}


#: pydantic 的 ValidationError 继承自 ValueError，因此下面所有"字段被拒"的断言统一用
#: ValueError 兜住即可 —— 既避免 pytest.raises(Exception)（ruff B017），也不依赖具体版本。
VALIDATION_ERROR: type[ValueError] = ValueError


def _make_config(**overrides: Any) -> TrainingConfig:
    """构造一份合法的 TrainingConfig，overrides 覆盖顶层字段。"""
    payload = {**BASE_KWARGS, **overrides}
    if hasattr(TrainingConfig, "model_validate"):
        return TrainingConfig.model_validate(payload)
    return TrainingConfig(**payload)


# =====================================================================
# 子配置模型：默认值与边界
# =====================================================================


class TestDatasetConfig:
    def test_defaults(self) -> None:
        ds = DatasetConfig(data_dir=pathlib.Path("/tmp/ds"))
        assert ds.sample_rate == 16000
        assert ds.min_duration_sec == 1.0
        assert ds.max_duration_sec == 20.0
        assert ds.split_ratio == 0.9
        assert ds.text_normalizer == "text_frontend"
        assert ds.text_norm_lang == "auto"
        assert ds.stratify_by_speaker is True

    def test_data_dir_coerced_to_path(self) -> None:
        assert DatasetConfig(data_dir="/tmp/ds").data_dir == pathlib.Path("/tmp/ds")

    @pytest.mark.parametrize("rate", [7999, 48001])
    def test_sample_rate_out_of_range_rejected(self, rate: int) -> None:
        with pytest.raises(VALIDATION_ERROR, match="sample_rate"):
            DatasetConfig(data_dir=pathlib.Path("/tmp/ds"), sample_rate=rate)

    def test_split_ratio_out_of_range_rejected(self) -> None:
        with pytest.raises(VALIDATION_ERROR, match="split_ratio"):
            DatasetConfig(data_dir=pathlib.Path("/tmp/ds"), split_ratio=0.5)

    def test_unknown_text_normalizer_rejected(self) -> None:
        with pytest.raises(VALIDATION_ERROR, match="text_normalizer"):
            DatasetConfig(data_dir=pathlib.Path("/tmp/ds"), text_normalizer="whisper")


class TestLoRAConfig:
    def test_defaults(self) -> None:
        lora = LoRAConfig()
        assert lora.target_modules == ["to_q", "to_k", "to_v", "to_out.0"]
        assert (lora.rank, lora.alpha, lora.dropout, lora.bias) == (8, 16.0, 0.05, "none")

    def test_default_target_modules_not_shared_between_instances(self) -> None:
        """default_factory 语义：改动一个实例的列表不得污染下一个实例（可变默认值回归）。"""
        first = LoRAConfig()
        first.target_modules.append("to_x")
        assert "to_x" not in LoRAConfig().target_modules

    @pytest.mark.parametrize("rank", [3, 65])
    def test_rank_out_of_range_rejected(self, rank: int) -> None:
        with pytest.raises(VALIDATION_ERROR, match="rank"):
            LoRAConfig(rank=rank)

    def test_bias_literal_rejects_unknown(self) -> None:
        with pytest.raises(VALIDATION_ERROR, match="bias"):
            LoRAConfig(bias="some")


class TestOptimizerConfig:
    def test_defaults(self) -> None:
        opt = OptimizerConfig()
        assert opt.optimizer_type == "adamw"
        assert opt.lr == 1e-4
        assert opt.weight_decay == 0.01
        assert tuple(opt.betas) == (0.9, 0.999)

    def test_lr_must_be_positive(self) -> None:
        with pytest.raises(VALIDATION_ERROR, match="lr"):
            OptimizerConfig(lr=0.0)

    def test_unknown_optimizer_type_rejected(self) -> None:
        with pytest.raises(VALIDATION_ERROR, match="optimizer_type"):
            OptimizerConfig(optimizer_type="lion")


class TestTrainingConfig:
    def test_output_dir_required(self) -> None:
        payload = {k: v for k, v in BASE_KWARGS.items() if k != "output_dir"}
        with pytest.raises(VALIDATION_ERROR, match="output_dir"):
            TrainingConfig.model_validate(payload)

    @pytest.mark.parametrize("epochs", [0, 201])
    def test_epochs_out_of_range_rejected(self, epochs: int) -> None:
        with pytest.raises(VALIDATION_ERROR, match="epochs"):
            _make_config(epochs=epochs)

    def test_precision_rejects_unsupported_dtype(self) -> None:
        with pytest.raises(VALIDATION_ERROR, match="precision"):
            _make_config(precision="fp8")

    def test_early_stopping_defaults_disabled(self) -> None:
        cfg = _make_config()
        assert cfg.early_stopping_patience == 0
        assert cfg.early_stopping_min_delta == 0.0

    def test_grad_accum_steps_upper_bound(self) -> None:
        with pytest.raises(VALIDATION_ERROR, match="grad_accum_steps"):
            _make_config(grad_accum_steps=33)


# =====================================================================
# 字段边界约束
# =====================================================================


class TestFieldBounds:
    """逐条打穿每个 ge/le/gt/lt 约束的两侧。

    只测一侧等于没测：删掉另一侧的界，测试仍然绿。这些约束是训练入口的护栏
    （lr<=0 会静默训出废权重、rank>64 会直接爆显存），必须两侧都有守护。
    """

    @pytest.mark.parametrize("value", [-0.1, 10.1])
    def test_min_duration_sec_bounds(self, value: float) -> None:
        with pytest.raises(VALIDATION_ERROR, match="min_duration_sec"):
            DatasetConfig(data_dir=pathlib.Path("/tmp/ds"), min_duration_sec=value)

    @pytest.mark.parametrize("value", [0.5, 601.0])
    def test_max_duration_sec_bounds(self, value: float) -> None:
        with pytest.raises(VALIDATION_ERROR, match="max_duration_sec"):
            DatasetConfig(data_dir=pathlib.Path("/tmp/ds"), max_duration_sec=value)

    @pytest.mark.parametrize("value", [0.0, 1025.0])
    def test_alpha_bounds(self, value: float) -> None:
        with pytest.raises(VALIDATION_ERROR, match="alpha"):
            LoRAConfig(alpha=value)

    @pytest.mark.parametrize("value", [-0.01, 0.51])
    def test_dropout_bounds(self, value: float) -> None:
        with pytest.raises(VALIDATION_ERROR, match="dropout"):
            LoRAConfig(dropout=value)

    @pytest.mark.parametrize("value", [-0.01, 0.11])
    def test_weight_decay_bounds(self, value: float) -> None:
        with pytest.raises(VALIDATION_ERROR, match="weight_decay"):
            OptimizerConfig(weight_decay=value)

    @pytest.mark.parametrize("value", [0.0, -1.0])
    def test_lr_rejects_zero_and_negative(self, value: float) -> None:
        with pytest.raises(VALIDATION_ERROR, match="lr"):
            OptimizerConfig(lr=value)

    def test_warmup_steps_non_negative(self) -> None:
        with pytest.raises(VALIDATION_ERROR, match="warmup_steps"):
            _make_config(warmup_steps=-1)

    def test_save_every_n_epochs_at_least_one(self) -> None:
        with pytest.raises(VALIDATION_ERROR, match="save_every_n_epochs"):
            _make_config(save_every_n_epochs=0)

    @pytest.mark.parametrize("value", [0, 33])
    def test_batch_size_bounds(self, value: int) -> None:
        with pytest.raises(VALIDATION_ERROR, match="batch_size"):
            _make_config(batch_size=value)

    @pytest.mark.parametrize("value", [-1, 51])
    def test_early_stopping_patience_bounds(self, value: int) -> None:
        with pytest.raises(VALIDATION_ERROR, match="early_stopping_patience"):
            _make_config(early_stopping_patience=value)

    def test_early_stopping_min_delta_non_negative(self) -> None:
        with pytest.raises(VALIDATION_ERROR, match="early_stopping_min_delta"):
            _make_config(early_stopping_min_delta=-0.1)

    def test_epochs_lower_bound_rejected(self) -> None:
        with pytest.raises(VALIDATION_ERROR, match="epochs"):
            _make_config(epochs=0)

    @pytest.mark.parametrize("precision", ["fp32", "fp16", "bf16"])
    def test_all_declared_precisions_accepted(self, precision: str) -> None:
        """合法取值集合本身也要有正向守护，否则收紧 Literal 会无人察觉。"""
        assert _make_config(precision=precision).precision == precision


# =====================================================================
# 序列化辅助
# =====================================================================


class TestPydanticToDict:
    def test_paths_become_str(self) -> None:
        raw = _pydantic_to_dict(_make_config())
        assert isinstance(raw["output_dir"], str)
        assert isinstance(raw["dataset"]["data_dir"], str)

    def test_walks_nested_lists_and_dicts(self) -> None:
        class _Stub:
            """只暴露 .dict() 的假配置，用来走 model_dump 缺失时的分支。"""

            def dict(self) -> dict[str, Any]:
                return {
                    "p": pathlib.Path("/tmp/x"),
                    "nested": {"p": pathlib.Path("/tmp/y")},
                    "items": [pathlib.Path("/tmp/z"), ("t", pathlib.Path("/tmp/w"))],
                    "plain": 3,
                }

        out = _pydantic_to_dict(_Stub())
        assert out["p"] == str(pathlib.Path("/tmp/x"))
        assert out["nested"]["p"] == str(pathlib.Path("/tmp/y"))
        assert out["items"][0] == str(pathlib.Path("/tmp/z"))
        assert out["items"][1][1] == str(pathlib.Path("/tmp/w"))
        assert out["plain"] == 3


class TestRecursiveFieldReplace:
    def test_only_dir_keys_rebuilt(self) -> None:
        out = _recursive_field_replace({"data_dir": "/a", "output_dir": "/b", "other": "/c"})
        assert isinstance(out["data_dir"], pathlib.Path)
        assert isinstance(out["output_dir"], pathlib.Path)
        assert out["other"] == "/c"

    def test_descends_into_lists(self) -> None:
        out = _recursive_field_replace([{"data_dir": "/a"}, "plain", 5])
        assert isinstance(out[0]["data_dir"], pathlib.Path)
        assert out[1] == "plain"
        assert out[2] == 5

    def test_non_string_value_left_alone(self) -> None:
        assert _recursive_field_replace({"data_dir": 12})["data_dir"] == 12


class TestGetDefaultConfig:
    def test_output_dir_derived_from_data_dir(self) -> None:
        cfg = get_default_config(pathlib.Path("/data/myvoice"))
        assert cfg.output_dir == pathlib.Path("/data/myvoice_lora_output")

    def test_accepts_str_path(self) -> None:
        assert get_default_config("/data/myvoice").dataset.data_dir == pathlib.Path("/data/myvoice")

    def test_recipe_stays_consistent_with_schema_defaults(self) -> None:
        """把"经验最优配方"钉成一条跨路径一致性不变量。

        get_default_config 显式传的 kwargs（epochs=10、batch_size=2 …）与 TrainingConfig
        的 Field 默认值当前是同一组数字。只断言任意一侧的值都没有区分力——改坏一处、
        另一处仍能让那条断言绿。这里改为要求两条路径逐项相等：任一侧单独漂移即红，
        并且失败信息直接点出是哪个字段。
        """
        recipe = get_default_config(pathlib.Path("/data/myvoice"))
        baseline = _make_config()  # 只填必填字段，其余全部走 Field 默认值

        for name in (
            "epochs",
            "batch_size",
            "grad_accum_steps",
            "warmup_steps",
            "precision",
            "save_every_n_epochs",
            "seed",
        ):
            assert getattr(recipe, name) == getattr(baseline, name), name
        assert recipe.dataset.sample_rate == baseline.dataset.sample_rate
        assert recipe.lora.rank == baseline.lora.rank
        assert recipe.optimizer.lr == baseline.optimizer.lr


# =====================================================================
# 落盘 / 读取
# =====================================================================


class TestSaveTrainingConfig:
    def test_json_round_trip(self, tmp_path: pathlib.Path) -> None:
        cfg = get_default_config(tmp_path / "ds")
        target = tmp_path / "nested" / "deeper" / "config.json"
        save_training_config(cfg, target)

        assert target.parent.exists(), "父目录应自动创建"
        on_disk = json.loads(target.read_text(encoding="utf-8"))
        assert on_disk["dataset"]["data_dir"] == str(tmp_path / "ds")

        reloaded = load_training_config(target)
        assert reloaded.dataset.data_dir == cfg.dataset.data_dir
        assert reloaded.output_dir == cfg.output_dir
        assert reloaded.epochs == cfg.epochs
        # 临时文件落在 target.parent（mkstemp 的 dir 参数），glob 必须扫那一层；
        # 扫 tmp_path 顶层是恒真的（Path.glob 不递归），曾经这样写过、检不出残留。
        assert list(target.parent.glob("*.tmp")) == [], "不应残留 .tmp 半成品"

    def test_yaml_round_trip_keeps_unicode(self, tmp_path: pathlib.Path) -> None:
        cfg = get_default_config(tmp_path / "中文数据集")
        target = tmp_path / "config.yml"
        save_training_config(cfg, target)
        text = target.read_text(encoding="utf-8")
        assert "中文数据集" in text, "YAML 需 allow_unicode，否则中文路径被转义成不可读形态"
        assert load_training_config(target).dataset.data_dir == tmp_path / "中文数据集"

    def test_oserror_during_replace_cleans_tmp(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*_a: Any, **_k: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(cfg_mod.os, "replace", _boom)
        with pytest.raises(OSError, match="disk full"):
            save_training_config(get_default_config(tmp_path / "ds"), tmp_path / "config.json")
        assert list(tmp_path.glob("*.tmp")) == [], "写失败必须清掉 .tmp，否则下次 resume 会读到半文件"

    def test_unexpected_error_during_replace_cleans_tmp(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("unexpected")

        monkeypatch.setattr(cfg_mod.os, "replace", _boom)
        with pytest.raises(RuntimeError, match="unexpected"):
            save_training_config(get_default_config(tmp_path / "ds"), tmp_path / "config.json")
        assert list(tmp_path.glob("*.tmp")) == []


class TestLoadTrainingConfig:
    def test_missing_file_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(FileNotFoundError, match="训练配置文件不存在"):
            load_training_config(tmp_path / "nope.json")

    def test_broken_json_wrapped_as_value_error(self, tmp_path: pathlib.Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="格式错误"):
            load_training_config(bad)

    def test_broken_yaml_wrapped_as_value_error(self, tmp_path: pathlib.Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("dataset:\n  data_dir: [unclosed\n", encoding="utf-8")
        with pytest.raises(ValueError, match="格式错误"):
            load_training_config(bad)

    @pytest.mark.parametrize(("suffix", "body"), [("json", "[1, 2]"), ("yaml", "- a\n- b\n")])
    def test_non_mapping_root_rejected(self, tmp_path: pathlib.Path, suffix: str, body: str) -> None:
        bad = tmp_path / f"root.{suffix}"
        bad.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match="根节点必须是 mapping"):
            load_training_config(bad)

    def test_unknown_suffix_parsed_as_json(self, tmp_path: pathlib.Path) -> None:
        cfg_file = tmp_path / "config.conf"
        cfg_file.write_text(
            json.dumps({"dataset": {"data_dir": "/d"}, "lora": {}, "optimizer": {}, "output_dir": "/o"}),
            encoding="utf-8",
        )
        assert load_training_config(cfg_file).output_dir == pathlib.Path("/o")

    def test_validation_errors_aggregated_in_one_message(self, tmp_path: pathlib.Path) -> None:
        """校验失败必须一次列全所有坏字段，而不是修一个报一个。"""
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "dataset:\n  data_dir: /d\n  sample_rate: 1\nlora: {}\noptimizer:\n  lr: 0\noutput_dir: /o\nepochs: 999\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError) as excinfo:
            load_training_config(bad)
        message = str(excinfo.value)
        # 不断言"共 N 个"里的 N：N = len(ValidationError.errors())，由 pydantic 的错误枚举决定，
        # 版本升级改枚举方式就会假红。这里只钉本仓库自己保证的不变量：
        # 头部计数 == 正文逐条列出的行数，且每条坏字段都被点名。
        header = re.search(r"共 (\d+) 个错误字段", message)
        assert header, message
        listed = re.findall(r"^\s+\d+\. \[", message, flags=re.MULTILINE)
        assert int(header.group(1)) == len(listed) == 3, message
        for loc in ("sample_rate", "optimizer.lr", "epochs"):
            assert loc in message


# =====================================================================
# argbind 兼容层
# =====================================================================


class TestLoadYamlConfig:
    def test_returns_mapping(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "conf.yml"
        f.write_text("epochs: 5\nnested:\n  a: 1\n", encoding="utf-8")
        assert load_yaml_config(f) == {"epochs": 5, "nested": {"a": 1}}

    def test_broken_yaml_raises_value_error(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "conf.yml"
        f.write_text("a: [1, 2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="解析失败"):
            load_yaml_config(f)

    def test_non_mapping_raises_value_error(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "conf.yml"
        f.write_text("just a scalar\n", encoding="utf-8")
        with pytest.raises(ValueError, match="top-level mapping"):
            load_yaml_config(f)

    def test_missing_file_error_is_not_wrapped(self, tmp_path: pathlib.Path) -> None:
        """按现状钉住一处不对称：load_training_config 会把缺文件包装成带中文提示的
        FileNotFoundError，而本函数直接把裸 FileNotFoundError 漏出去（config.py 未捕获）。
        改成 ValueError 属于对外行为变更，需连同调用方一起评估，故先固化现状。
        """
        with pytest.raises(FileNotFoundError):
            load_yaml_config(tmp_path / "nope.yml")


class TestParseArgsWithConfig:
    """parse_args_with_config 的合并语义。

    argbind.parse_args() 会从全局 registry 反推 CLI 参数表，依赖具体导入顺序，在测试里
    不可靠（未绑定时直接抛 "ValueError: 'str' is not callable"）。因此这里替换掉 argbind，
    只验证本函数自己的合并逻辑。
    """

    class _Scope:
        def __init__(self, recorded: list[Any]) -> None:
            self._recorded = recorded

        def __enter__(self) -> object:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    def test_without_config_returns_cli_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg_mod.argbind, "parse_args", lambda **_k: {"epochs": 9})
        assert parse_args_with_config(None) == {"epochs": 9}

    def test_yaml_layer_merged_into_cli_args(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = tmp_path / "conf.yml"
        f.write_text("epochs: 3\nseed: 7\n", encoding="utf-8")
        scoped: list[Any] = []

        def fake_parse_args(**kwargs: Any) -> dict[str, Any]:
            if "yaml_args" in kwargs:
                # 替身故意对 YAML 原文做可辨识的改写：合并结果里出现这两个键，才说明
                # "YAML 确实经过了 argbind 作用域二次解析后再合并"这一行真的被执行了；
                # 若那行被删（直接拿 load_yaml_config 的结果去 update），断言立刻变红。
                return {**kwargs["yaml_args"], "seed": 700, "via_argbind": True}
            return {"epochs": 9, "lr": 1e-4}

        monkeypatch.setattr(cfg_mod.argbind, "parse_args", fake_parse_args)

        def fake_scope(args: Any) -> TestParseArgsWithConfig._Scope:
            # 存副本：函数结尾是 cli_args.update(yaml_args)，原地改写同一个 dict，
            # 直接存引用会让这里断言到的是"合并后"的内容而不是"打开作用域时"的内容。
            scoped.append(dict(args))
            return self._Scope(scoped)

        monkeypatch.setattr(cfg_mod.argbind, "scope", fake_scope)
        merged = parse_args_with_config(f)
        # 合并结果里出现这两个键，才说明"YAML 确实经过 argbind 作用域二次解析后再合并"这行
        # 真的被执行；若那行被删（直接拿 load_yaml_config 的结果去 update），断言立刻变红。
        assert merged["lr"] == 1e-4 and merged["via_argbind"] is True
        # 作用域是以 CLI 参数打开的（否则 YAML 解析拿不到 CLI 覆盖语境）
        assert scoped == [{"epochs": 9, "lr": 1e-4}]
        # 重叠字段当前实现是 YAML 覆盖 CLI —— 与函数 docstring 声称的"CLI 优先级更高"相反，
        # 已按现状钉住；若要翻转优先级属于行为变更，需连训练入口脚本一并评估。
        assert merged["epochs"] == 3 and merged["seed"] == 700


# =====================================================================
# pydantic 缺失兜底分支
# =====================================================================


def _load_without_pydantic(monkeypatch: pytest.MonkeyPatch) -> Any:
    """把 pydantic 从 sys.modules 里摘掉后重新加载同一个 config.py，拿到兜底实现。"""
    monkeypatch.setitem(sys.modules, "pydantic", None)
    source = pathlib.Path(cfg_mod.__file__).resolve()
    spec = importlib.util.spec_from_file_location("_config_without_pydantic", str(source))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestFallbackWithoutPydantic:
    def test_module_detects_missing_pydantic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert _load_without_pydantic(monkeypatch).BaseModel is None

    def test_default_config_and_dict(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        mod = _load_without_pydantic(monkeypatch)
        cfg = mod.get_default_config(tmp_path / "ds")
        dumped = cfg.dict()
        assert dumped["output_dir"] == str(tmp_path / "ds_lora_output")
        assert dumped["dataset"]["sample_rate"] == 16000
        assert dumped["lora"]["target_modules"] == ["to_q", "to_k", "to_v", "to_out.0"]
        assert dumped["optimizer"]["betas"] == [0.9, 0.999]

    def test_json_round_trip(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        mod = _load_without_pydantic(monkeypatch)
        target = tmp_path / "config.json"
        mod.save_training_config(mod.get_default_config(tmp_path / "ds"), target)
        back = mod.load_training_config(target)
        assert back.dataset.sample_rate == 16000
        assert back.epochs == 10
        assert back.precision == "fp16"
        assert back.seed == 42

    def test_missing_field_wrapped_as_value_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        mod = _load_without_pydantic(monkeypatch)
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"lora": {}, "optimizer": {}, "output_dir": "/o"}), encoding="utf-8")
        with pytest.raises(ValueError, match="训练配置字段错误"):
            mod.load_training_config(bad)

    def test_output_dir_defaults_when_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mod = _load_without_pydantic(monkeypatch)
        cfg = mod.TrainingConfig(
            dataset=mod.DatasetConfig(data_dir=pathlib.Path("/d")),
            lora=mod.LoRAConfig(),
            optimizer=mod.OptimizerConfig(),
        )
        assert cfg.output_dir == pathlib.Path("./training_output")
