"""LoRA 训练配置骨架的守卫测试（UPSTREAM_SYNC A6 前置）。

背景：``scripts/train_voxcpm_finetune.py`` 的用法说明里写着
``--config_path=configs/lora_config.yaml``，但仓库从来没有这个文件，
照着文档敲就是 FileNotFoundError。本次补了配置，所以要把"配置能不能用"
钉成断言，否则下次改 train() 签名或 LoRAConfig 字段时又会静默脱钩。

不用 import 被测模块：训练脚本在模块级 import argbind/torch/voxcpm，
而 ``voxcpm`` 只有应用运行时才把 vendor 目录塞进 sys.path；测试里直接
用 ast 读签名，既快又不依赖 CUDA。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[1]
TRAIN_SCRIPT = _REPO / "scripts" / "train_voxcpm_finetune.py"
CONFIG = _REPO / "configs" / "lora_config.yaml"
VENDOR_MODEL = _REPO / "app" / "integrated_app" / "vendor" / "voxcpm" / "model" / "voxcpm2.py"


def _train_params() -> dict[str, ast.expr | None]:
    tree = ast.parse(TRAIN_SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "train":
            args = node.args
            positional = args.posonlyargs + args.args
            defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
            return {a.arg: d for a, d in zip(positional, defaults, strict=True)}
    raise AssertionError("train_voxcpm_finetune.py 里找不到 train() 函数")


def _lora_config_fields() -> set[str]:
    tree = ast.parse(VENDOR_MODEL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LoRAConfig":
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError("voxcpm2.py 里找不到 LoRAConfig")


@pytest.fixture(scope="module")
def cfg() -> dict:
    assert CONFIG.exists(), "configs/lora_config.yaml 不存在，训练脚本的用法说明是假的"
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "load_yaml_config 要求顶层必须是 mapping"
    return data


class TestConfigMatchesTrainSignature:
    def test_no_unknown_keys(self, cfg) -> None:
        """train(**yaml_args) 是裸展开，多一个未知键就是 TypeError。"""
        params = _train_params()
        unknown = set(cfg) - set(params)
        assert not unknown, f"配置里有 train() 不接受的键：{sorted(unknown)}"

    def test_all_required_keys_present(self, cfg) -> None:
        params = _train_params()
        required = {name for name, default in params.items() if default is None and name != "lambdas"}
        # lambdas / lora 的默认值是 None 但函数内部会兜底，所以只盯真正无默认的两个
        truly_required = {n for n in required if n in {"pretrained_path", "train_manifest"}}
        missing = truly_required - set(cfg)
        assert not missing, f"缺少必填键：{sorted(missing)}"

    def test_lora_subkeys_are_real_fields(self, cfg) -> None:
        """LoRAConfig 是 Pydantic 模型，写错键名不会报错只会静默用默认值。

        实测踩过：把秩写成 ``rank`` 时 Pydantic 直接忽略它，训练照跑但
        rank 一直是默认的 8，调参看起来"没有效果"。
        """
        fields = _lora_config_fields()
        lora = cfg.get("lora") or {}
        assert lora, "lora 段为空，这份配置就没在配 LoRA"
        bogus = set(lora) - fields
        assert not bogus, f"LoRAConfig 没有这些字段：{sorted(bogus)}；真实字段：{sorted(fields)}"
        assert "r" in lora, f"秩必须用字段名 r（不是 rank），可用字段：{sorted(fields)}"

    def test_lambdas_keys_match_training_conventions(self, cfg) -> None:
        lambdas = cfg.get("lambdas") or {}
        assert set(lambdas) <= {"loss/diff", "loss/stop"}, sorted(lambdas)
        assert all(isinstance(v, (int, float)) for v in lambdas.values())

    def test_no_placeholder_paths_left_unannotated(self, cfg) -> None:
        """数据清单本仓不含，路径必须指向 data/ 下且写清要自备 —— 不能骗人去跑。"""
        for key in ("train_manifest", "val_manifest"):
            value = str(cfg.get(key, ""))
            assert value.startswith("data/"), f"{key}={value!r} 不在 data/ 下"
        doc = CONFIG.read_text(encoding="utf-8")
        assert "未经真机训练验证" in doc, "配置未声明自己是未验证骨架"


class TestDocsReferenceTheFile:
    def test_script_usage_points_at_existing_path(self) -> None:
        src = TRAIN_SCRIPT.read_text(encoding="utf-8")
        assert "configs/lora_config.yaml" in src
        assert CONFIG.exists()
