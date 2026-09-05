#!/usr/bin/env python3
"""训练隔离 AST 门禁（P2-6 后端设计评估落地）。

扫描服务路径（app_server.py + routes/ 下除 training.py 外的所有模块），
禁止其 import 训练相关模块。训练代码（LoRA 微调等）应与在线推理服务
严格隔离，避免：
  - 训练依赖（peft / datasets 等）被意外引入推理启动路径
  - 训练路由的重型初始化拖慢服务启动
  - 训练状态污染推理上下文

检测方式：AST 静态分析，匹配以下违规模式：
  - ``import ...training...``
  - ``from ...training... import ...``
  - ``from .training import ...``（相对导入）
  - ``from ..training import ...``

用法：
    python scripts/check_training_isolation.py
    python scripts/check_training_isolation.py --json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path
from typing import Any


def _reexec_with_venv_python() -> None:
    venv_python = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
        os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])


_reexec_with_venv_python()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_APP_DIR = _PROJECT_ROOT / "app" / "integrated_app"

# 扫描范围：服务入口 + 所有路由（训练路由本身除外）
_SCAN_TARGETS = [
    _APP_DIR / "app_server.py",
    _APP_DIR / "routes",
]

# 排除：训练路由模块自身（routes/training.py 及其子包）
_EXCLUDE_STEMS = {"training"}

# 违规导入的模块名片段（匹配 module path 的任一段）
_FORBIDDEN_FRAGMENTS = {"training"}


class Violation:
    def __init__(self, filepath: str, lineno: int, module: str, kind: str) -> None:
        self.filepath = filepath
        self.lineno = lineno
        self.module = module
        self.kind = kind  # "import" or "from"

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.filepath,
            "line": self.lineno,
            "module": self.module,
            "kind": self.kind,
        }


def _is_excluded(path: Path) -> bool:
    return path.stem in _EXCLUDE_STEMS


def _collect_py_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix == ".py" and not _is_excluded(target) else []
    files: list[Path] = []
    for p in target.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        if _is_excluded(p):
            continue
        files.append(p)
    return files


def _module_has_forbidden_fragment(module: str) -> bool:
    parts = module.split(".")
    return any(p in _FORBIDDEN_FRAGMENTS for p in parts)


def scan_file(filepath: Path) -> list[Violation]:
    violations: list[Violation] = []
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError) as e:
        return [Violation(str(filepath), 0, f"<parse error: {e}>", "parse_error")]

    rel = str(filepath.relative_to(_PROJECT_ROOT))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_has_forbidden_fragment(alias.name):
                    violations.append(Violation(rel, node.lineno, alias.name, "import"))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            # 相对导入：node.level > 0，module 可能为 "training"（from .training import）
            if _module_has_forbidden_fragment(mod):
                violations.append(Violation(rel, node.lineno, f"{'.' * node.level}{mod}", "from"))

    return violations


def run() -> list[Violation]:
    all_violations: list[Violation] = []
    for target in _SCAN_TARGETS:
        if not target.exists():
            continue
        for filepath in _collect_py_files(target):
            all_violations.extend(scan_file(filepath))
    return all_violations


def main() -> int:
    parser = argparse.ArgumentParser(description="训练隔离 AST 门禁")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    violations = run()

    if args.json:
        print(json.dumps([v.to_dict() for v in violations], ensure_ascii=False, indent=2))
    else:
        print("=== TTS_MultiModel 训练隔离 AST 门禁 ===")
        if not violations:
            print("[OK] 未发现服务路径导入训练模块")
        else:
            print(f"[FAIL] 发现 {len(violations)} 处违规导入：")
            for v in violations:
                print(f"  {v.filepath}:{v.lineno}  {v.kind} {v.module}")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
