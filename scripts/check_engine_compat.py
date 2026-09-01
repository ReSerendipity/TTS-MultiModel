#!/usr/bin/env python3
"""TTS_MultiModel 引擎兼容性检测脚本。

检测 VoxCPM2 / IndexTTS2 两个已注册引擎在当前 Python 环境下的
依赖层兼容性，不加载真实模型，不需要 GPU。

检测项（9 项）：
    1. torch >= 2.5.1
    2. transformers >= 4.57.0
    3. numpy >= 1.24.0（pyproject.toml 下界）
    4. pydantic >= 2.0（pyproject.toml 下界）
    5. funasr 可 import
    6. fastapi 可 import
    7. VoxCPM2 模块可 import
    8. IndexTTS2 模块可 import
    9. IndexTTS20 模块可 import（2.0，与 2.5 共用 indextts 包）

用法：
    python scripts/check_engine_compat.py
    python scripts/check_engine_compat.py --json
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import sys
from pathlib import Path
from typing import Any


def _reexec_with_venv_python() -> None:
    """存在项目 .venv 且当前解释器非 .venv 时，用 .venv 解释器重跑自身。

    pre-commit 的 language:system 钩子可能解析到系统 python（可能已装 CPU torch
    却缺 transformers/funasr 等运行依赖），导致误判 FAIL；只要项目带 .venv 就
    一律切到 .venv 重跑，保证检测结果反映真实运行环境。
    跨平台：Windows 为 .venv/Scripts/python.exe；无 .venv（如 CI）时保持原行为。
    """
    venv_python = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
        os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])


_reexec_with_venv_python()
# ---------------------------------------------------------------------------
# 路径设置：确保 bin/ 在 sys.path 中，以便 import integrated_app
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_APP_DIR = str(_PROJECT_ROOT / "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# 离线模式环境变量（避免 import 时触发网络请求）
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _get_version(package_name: str) -> str | None:
    """安全获取已安装包的版本号。

    优先使用 package.__version__，不存在时回退到 importlib.metadata。
    """
    try:
        mod = importlib.import_module(package_name)
        if hasattr(mod, "__version__"):
            return str(mod.__version__)
    except Exception:
        pass
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def _parse_version(version_str: str) -> tuple[int, ...]:
    """将版本字符串解析为可比较的元组，如 '4.57.0' -> (4, 57, 0)。"""
    parts: list[int] = []
    for part in version_str.split("."):
        # 去除可能的后缀（如 '4.57.0rc0' -> '4', '57', '0'）
        numeric = ""
        for ch in part:
            if ch.isdigit():
                numeric += ch
            else:
                break
        parts.append(int(numeric) if numeric else 0)
    return tuple(parts)


def _version_satisfies(actual: str, minimum: str) -> bool:
    """检查 actual 版本是否 >= minimum 版本。"""
    return _parse_version(actual) >= _parse_version(minimum)


# ---------------------------------------------------------------------------
# 检测项定义
# ---------------------------------------------------------------------------
class CheckResult:
    """单项检测结果。"""

    def __init__(
        self,
        name: str,
        label: str,
        status: str,
        detail: str = "",
        version: str | None = None,
    ) -> None:
        self.name = name
        self.label = label
        self.status = status  # "OK" / "WARN" / "FAIL"
        self.detail = detail
        self.version = version

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "label": self.label,
            "status": self.status,
        }
        if self.version:
            d["version"] = self.version
        if self.detail:
            d["detail"] = self.detail
        return d


def check_package_version(
    name: str,
    label: str,
    import_name: str,
    min_version: str,
) -> CheckResult:
    """检测 pip 包版本是否满足最低要求。"""
    version = _get_version(import_name)
    if version is None:
        return CheckResult(name, label, "FAIL", f"未安装（{import_name} import 失败）")
    if _version_satisfies(version, min_version):
        return CheckResult(name, label, "OK", f">= {min_version}", version)
    return CheckResult(name, label, "WARN", f">= {min_version} 不满足!", version)


def check_import(name: str, label: str, import_path: str) -> CheckResult:
    """检测模块是否可 import。"""
    try:
        importlib.import_module(import_path)
        return CheckResult(name, label, "OK", "import OK")
    except ImportError as e:
        return CheckResult(name, label, "FAIL", f"import 失败: {e}")
    except Exception as e:
        return CheckResult(name, label, "FAIL", f"import 异常 ({type(e).__name__}): {e}")


def check_class_import(name: str, label: str, module_path: str, class_name: str) -> CheckResult:
    """检测模块中的类是否可 import。"""
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name, None)
        if cls is None:
            return CheckResult(name, label, "FAIL", f"模块可 import 但类 {class_name} 不存在")
        return CheckResult(name, label, "OK", f"import OK ({class_name})")
    except ImportError as e:
        return CheckResult(name, label, "FAIL", f"import 失败: {e}")
    except Exception as e:
        return CheckResult(name, label, "FAIL", f"import 异常 ({type(e).__name__}): {e}")


def run_all_checks() -> list[CheckResult]:
    """执行全部 9 项检测，返回结果列表。"""
    results: list[CheckResult] = []

    # 1. torch >= 2.5.1
    results.append(check_package_version("torch", "torch", "torch", "2.5.1"))

    # 2. transformers >= 4.57.0
    results.append(check_package_version("transformers", "transformers", "transformers", "4.57.0"))

    # 3. numpy >= 1.24.0（pyproject 下界；旧 2.2.6 门槛源自已停用的 dots.tts）
    results.append(check_package_version("numpy", "numpy", "numpy", "1.24.0"))

    # 4. pydantic >= 2.0（pyproject 下界；旧 2.12.5 门槛源自已停用的 dots.tts）
    results.append(check_package_version("pydantic", "pydantic", "pydantic", "2.0"))

    # 5. funasr 可 import
    results.append(check_import("funasr", "funasr", "funasr"))

    # 6. fastapi 可 import
    results.append(check_import("fastapi", "fastapi", "fastapi"))

    # 7. VoxCPM2 模块可 import
    results.append(
        check_class_import(
            "voxcpm2",
            "VoxCPM2",
            "integrated_app.engines.voxcpm2.engine",
            "VoxCPM2Engine",
        )
    )

    # 8. IndexTTS2 模块可 import
    results.append(
        check_class_import(
            "indextts2",
            "IndexTTS2",
            "integrated_app.engines.indextts2_engine",
            "IndexTTS2Engine",
        )
    )

    # 9. IndexTTS20 模块可 import（与 2.5 共用同一 indextts 代码包的薄子类）
    results.append(
        check_class_import(
            "indextts20",
            "IndexTTS20",
            "integrated_app.engines.indextts2_engine",
            "IndexTTS20Engine",
        )
    )

    return results


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------
def format_text_output(results: list[CheckResult]) -> str:
    """生成人类可读的文本输出。"""
    lines: list[str] = []
    lines.append("=== TTS_MultiModel 引擎兼容性检测 ===")
    lines.append("")

    for r in results:
        status_tag = f"[{r.status}]"
        # 对齐标签（最长标签约 13 字符）
        label_padded = r.label.ljust(13)
        version_part = f": {r.version}" if r.version else ""

        if r.status == "OK":
            detail_part = (
                f"  {r.detail}" if r.detail and r.detail != "import OK" and not r.detail.startswith("import OK") else ""
            )
            if not version_part and r.detail:
                version_part = f"  {r.detail}"
            lines.append(f"{status_tag} {label_padded}{version_part}{detail_part}")
        elif r.status == "WARN":
            warn_version = f": {r.version}" if r.version else ""
            lines.append(f"{status_tag} {label_padded}{warn_version}  ({r.detail})")
        else:
            lines.append(f"{status_tag} {label_padded}{version_part}  {r.detail}")

    lines.append("---")

    passed = sum(1 for r in results if r.status == "OK")
    total = len(results)
    lines.append(f"总体：{passed}/{total} 通过")

    return "\n".join(lines)


def format_json_output(results: list[CheckResult]) -> str:
    """生成 JSON 格式输出。"""
    passed = sum(1 for r in results if r.status == "OK")
    total = len(results)
    output = {
        "summary": {
            "total": total,
            "passed": passed,
            "failed": sum(1 for r in results if r.status == "FAIL"),
            "warned": sum(1 for r in results if r.status == "WARN"),
        },
        "checks": [r.to_dict() for r in results],
    }
    return json.dumps(output, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="TTS_MultiModel 引擎兼容性检测")
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出检测结果",
    )
    args = parser.parse_args()

    results = run_all_checks()

    if args.json:
        print(format_json_output(results))
    else:
        print(format_text_output(results))

    # 退出码：全部 OK 返回 0，有 WARN 返回 0，有 FAIL 返回 1
    has_fail = any(r.status == "FAIL" for r in results)
    return 1 if has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
