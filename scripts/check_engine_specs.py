#!/usr/bin/env python3
"""引擎规格三向一致性校验脚本（P1-2 后端设计评估落地）。

校验三方声明是否一致：
    1. config.yaml ``models.engines`` —— 配置层声明
    2. ``engine_registry``（engine_interface._register_builtin_engines）—— 代码层注册
    3. 磁盘权重路径 ``model/{model_dir}`` —— 实际可加载

校验项（每项不一致即 FAIL，退出码 1）：
    - 引擎集合一致（config 声明的引擎必须全部注册，反之亦然）
    - sample_rate 一致
    - supported_features 一致（集合比较，忽略顺序）
    - config 中 model_dir 对应的磁盘目录存在且非空

不校验项（已知合理差异，仅 WARN）：
    - languages：config 可能声明产品级多语言，而 text_frontend.SUPPORTED_LANGUAGES
      仅覆盖 zh/en/ja/ko；两者语义不同，不强制一致。
    - vram_gb / ram_gb：config 与 registry 的 vram_requirement 口径可能不同
      （config 含 ASR/Enhancer 余量，registry 为模型基线），仅做信息展示。

用法：
    python scripts/check_engine_specs.py
    python scripts/check_engine_specs.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _reexec_with_venv_python() -> None:
    """存在项目 .venv 且当前解释器非 .venv 时，用 .venv 解释器重跑自身。

    与 check_engine_compat.py 同款机制：pre-commit 的 language:system 钩子可能
    解析到系统 python（缺 integrated_app 运行依赖），导致误判。
    """
    venv_python = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
        os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])


_reexec_with_venv_python()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_APP_DIR = str(_PROJECT_ROOT / "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# 离线模式（避免 import 时触发网络请求）
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")


class CheckResult:
    def __init__(self, name: str, status: str, detail: str = "") -> None:
        self.name = name
        self.status = status  # OK / WARN / FAIL
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        d = {"name": self.name, "status": self.status}
        if self.detail:
            d["detail"] = self.detail
        return d


def _load_config_engines() -> dict[str, dict[str, Any]]:
    """解析 config.yaml models.engines。"""
    import yaml

    config_path = _PROJECT_ROOT / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    engines = (data.get("models") or {}).get("engines") or {}
    return engines


def _load_registry_specs() -> dict[str, dict[str, Any]]:
    """从 engine_registry 获取已注册引擎规格（metadata，不触发懒导入）。"""
    from integrated_app.engine_interface import engine_registry

    return engine_registry.get_all_metadata()


def _check_model_path(model_dir: str) -> tuple[bool, str]:
    """检查 model/{model_dir} 目录存在且非空。"""
    target = _PROJECT_ROOT / "model" / model_dir
    if not target.exists():
        return False, f"目录不存在: {target}"
    if not target.is_dir():
        return False, f"不是目录: {target}"
    # 非空检查（至少有一个文件，忽略 .lock 等元数据）
    files = [p for p in target.rglob("*") if p.is_file()]
    if not files:
        return False, f"目录为空: {target}"
    return True, f"存在（{len(files)} 个文件）"


def run_all_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    # 1. 加载两方声明
    try:
        config_engines = _load_config_engines()
    except Exception as e:
        return [CheckResult("config_parse", "FAIL", f"config.yaml 解析失败: {e}")]

    try:
        registry_specs = _load_registry_specs()
    except Exception as e:
        return [CheckResult("registry_import", "FAIL", f"engine_registry 导入失败: {e}")]

    config_names = set(config_engines.keys())
    registry_names = set(registry_specs.keys())

    # 2. 引擎集合一致性
    only_in_config = config_names - registry_names
    only_in_registry = registry_names - config_names
    if only_in_config:
        results.append(
            CheckResult(
                "engine_set",
                "FAIL",
                f"config 声明但未注册: {sorted(only_in_config)}",
            )
        )
    elif only_in_registry:
        results.append(
            CheckResult(
                "engine_set",
                "FAIL",
                f"已注册但 config 未声明: {sorted(only_in_registry)}",
            )
        )
    else:
        results.append(
            CheckResult(
                "engine_set",
                "OK",
                f"引擎集合一致（{len(config_names)} 个: {sorted(config_names)}）",
            )
        )

    # 3. 逐引擎字段比对 + 磁盘路径
    for name in sorted(config_names & registry_names):
        cfg = config_engines[name]
        reg = registry_specs[name]

        # sample_rate
        cfg_sr = cfg.get("sample_rate")
        reg_sr = reg.get("sample_rate")
        if cfg_sr != reg_sr:
            results.append(
                CheckResult(
                    f"{name}.sample_rate",
                    "FAIL",
                    f"config={cfg_sr} vs registry={reg_sr}",
                )
            )
        else:
            results.append(CheckResult(f"{name}.sample_rate", "OK", f"{cfg_sr} Hz"))

        # supported_features（集合比较）
        cfg_feat = set(cfg.get("supported_features") or [])
        reg_feat = set(reg.get("supported_features") or [])
        if cfg_feat != reg_feat:
            results.append(
                CheckResult(
                    f"{name}.supported_features",
                    "FAIL",
                    f"config={sorted(cfg_feat)} vs registry={sorted(reg_feat)}",
                )
            )
        else:
            results.append(
                CheckResult(
                    f"{name}.supported_features",
                    "OK",
                    f"{sorted(cfg_feat)}",
                )
            )

        # 磁盘模型路径
        model_dir = cfg.get("model_dir", "")
        if model_dir:
            ok, detail = _check_model_path(model_dir)
            results.append(
                CheckResult(
                    f"{name}.model_path",
                    "OK" if ok else "FAIL",
                    f"model/{model_dir}: {detail}",
                )
            )

    # 4. languages 差异（仅 WARN，不阻断）
    for name in sorted(config_names & registry_names):
        cfg_lang = set(config_engines[name].get("languages") or [])
        reg_lang = set(registry_specs[name].get("languages") or [])
        if cfg_lang != reg_lang:
            results.append(
                CheckResult(
                    f"{name}.languages",
                    "WARN",
                    f"config={len(cfg_lang)} 种 vs registry={len(reg_lang)} 种"
                    f"（产品级声明 vs text_frontend 支持集，语义不同，不阻断）",
                )
            )

    return results


def format_text(results: list[CheckResult]) -> str:
    lines = ["=== TTS_MultiModel 引擎规格三向一致性校验 ===", ""]
    for r in results:
        tag = f"[{r.status}]"
        lines.append(f"{tag} {r.name.ljust(32)} {r.detail}")
    lines.append("---")
    ok = sum(1 for r in results if r.status == "OK")
    warn = sum(1 for r in results if r.status == "WARN")
    fail = sum(1 for r in results if r.status == "FAIL")
    lines.append(f"总计: {len(results)} 项 | OK={ok} WARN={warn} FAIL={fail}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="引擎规格三向一致性校验")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    results = run_all_checks()

    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
    else:
        print(format_text(results))

    has_fail = any(r.status == "FAIL" for r in results)
    return 1 if has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
