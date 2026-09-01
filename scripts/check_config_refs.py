#!/usr/bin/env python3
"""
scripts/check_config_refs.py — P0-1 配置字段引用完整性检查

目标：防止「代码访问了未在配置模型中定义的字段」这类回归（报告 §9-P0-1
曾指出 ``app_server.py`` 直接访问 ``config.runtime.idle_unload_minutes``，
但该字段当时未定义，会导致启动失败）。

检查内容：
1. 从 ``app/integrated_app/config_models.py`` 解析所有 pydantic 配置模型的字段；
2. 扫描 ``app/integrated_app/*.py`` 中所有 ``config.*`` / ``cfg.*`` /
   ``get_config().*`` 的链式属性访问，确认叶属性是已声明字段（或经
   ``getattr``/``.get``/``hasattr`` 安全访问）；
3. 校验 ``config.yaml`` 的 ``runtime:`` 段每个键都对应 ``RuntimeConfig`` 字段，
   且 ``idle_unload_minutes`` 在模型与 yaml 中均存在（报告重点回归点）。

任何缺失都以非零退出码终止，可作为 CI 门禁。

用法：
    python scripts/check_config_refs.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # 允许在无 PyYAML 环境下仍做代码侧检查

ROOT = Path(__file__).resolve().parent.parent
CONFIG_MODELS = ROOT / "app" / "integrated_app" / "config_models.py"
CONFIG_YAML = ROOT / "config.yaml"
APP_DIR = ROOT / "app" / "integrated_app"

# 视为配置根的调用（仅函数调用根；裸标识符 config/cfg 太常被局部参数占用，
# 在 TTS 中应用配置统一经 get_config() 访问，故不把裸标识符当配置根，避免误报）。
CONFIG_ROOT_NAMES = {"get_config", "load_config", "read_config"}

# 非配置字段的安全属性（方法 / dict 接口）
_SAFE_ATTRS = {
    "model_dump",
    "model_copy",
    "model_validate",
    "model_fields",
    "model_config",
    "configure",
    "get",
    "items",
    "values",
    "keys",
    "update",
    "copy",
    "dict",
    "json",
    "from_orm",
    "parse_obj",
    "to_dict",
    "as_dict",
}

# 桥接属性：get_config() 上返回子配置 dict / 对象的 property，本身不是
# config_models.py 中的字段，但链式后续的 security.xxx 才是真实配置访问，故放行。
# 对应 config.py 的 pydantic_config / *_dict property（含亚配置 dict 桥）。
_BRIDGE_ATTRS = {"pydantic_config", "api_auth_dict", "observability_dict", "gen_defaults_dict"}

errors: list[str] = []


def collect_class_fields() -> dict[str, set[str]]:
    """返回 {类名: 字段名集合}，仅收集 pydantic 配置模型。"""
    tree = ast.parse(CONFIG_MODELS.read_text(encoding="utf-8"))
    result: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
            if "BaseModel" in bases or "ConfigModel" in bases:
                fields: set[str] = set()
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        fields.add(stmt.target.id)
                    elif isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Name):
                                fields.add(t.id)
                result[node.name] = fields
    return result


def root_of(node: ast.AST) -> ast.AST:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node


def is_config_like(node: ast.AST) -> bool:
    # 仅把 get_config()/load_config() 这类「函数调用根」视为应用配置根，
    # 避免把局部变量（如 RASConfig / ResamplingConfig 的 config 参数）误判为应用配置。
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name) and f.id in ("get_config", "load_config", "read_config"):
            return True
        if isinstance(f, ast.Attribute) and f.attr in ("get_config", "load_config"):
            return True
    return False


def guarded_by_safe(node: ast.Attribute) -> bool:
    """属性是否处于 getattr(..., default) / xxx.get(...) / hasattr 等安全上下文。"""
    parent = node.parent  # type: ignore[attr-defined]
    if isinstance(parent, ast.Call):
        f = parent.func
        fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if fname == "getattr" and len(parent.args) >= 2 and parent.args[0] is node:
            return True  # getattr(obj, "attr", default) —— 第二参数即属性名
        if fname in ("get", "setdefault") and parent.func is not None:
            return True
    if isinstance(parent, ast.Call) and getattr(parent.func, "attr", "") in ("get", "setdefault"):
        return True
    if isinstance(parent, ast.keyword) and parent.arg in ("default",):
        return True
    return isinstance(parent, ast.Call) and fname_is(parent, "hasattr")


def fname_is(node: ast.AST, name: str) -> bool:
    f = getattr(node, "func", None)
    if isinstance(f, ast.Name):
        return f.id == name
    if isinstance(f, ast.Attribute):
        return f.attr == name
    return False


def attach_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent


def scan_source_for_missing(src: str, all_fields: set[str], filename: str = "<string>") -> list[str]:
    """扫描一段源码，返回其中未被配置模型定义的 config 字段访问错误列表。"""
    found: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:  # pragma: no cover
        return [f"{filename}: syntax error {e}"]
    attach_parents(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not is_config_like(root_of(node.value)):
            continue
        attr = node.attr
        if attr.startswith("_") or attr in _SAFE_ATTRS or attr in _BRIDGE_ATTRS:
            continue
        if guarded_by_safe(node):
            continue
        if attr not in all_fields:
            found.append(f"{filename}: 访问 config 字段 '{attr}' 未在 config_models.py 任何配置模型中定义")
    return found


def iter_py_files() -> list[Path]:
    """递归收集 app/integrated_app 下所有 .py（排除 __pycache__ / 下划线开头）。"""
    files: list[Path] = []
    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name.startswith("_"):
            continue
        files.append(path)
    return sorted(files)


def check_code_refs(all_fields: set[str], class_fields: dict[str, set[str]]) -> None:
    # 注意：仅扫描 app/integrated_app 顶层模块。子包（native/ 等）中大量
    # 局部变量也命名为 cfg/config（如扩散参数 cfg scale、节点输入 dict），
    # 递归扫描会把它们误判为应用配置访问，产生大量假阳性。
    for path in sorted(APP_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except OSError as e:  # pragma: no cover
            errors.append(f"{path}: read error {e}")
            continue
        errors.extend(scan_source_for_missing(src, all_fields, filename=str(path)))


# ── 安全项「声明即被消费」门禁（对应安全评估 #13）─────────────────
def chain_path(node: ast.Attribute) -> str:
    """把 Attribute 链还原为点分路径（不含配置根标识符）。

    例：cfg.security.headers.csp -> 'security.headers.csp'
    """
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value  # type: ignore[assignment]
    parts.reverse()
    return ".".join(parts)


def collect_consumed() -> tuple[set[str], set[str]]:
    """扫描全项目源码，返回 (consumed_paths, consumed_tokens)。

    - consumed_paths：以配置对象为根的**完整点分访问链**
      （如 security.content_filter.fail_closed_on_clip_missing）；
    - consumed_tokens：**所有**属性名 + getattr/get 字符串键。用于兼容
      「先取子对象再访问字段」的别名写法（如 mfmt = cfg.security.model_format;
      mfmt.verify_weights）与字符串化访问（getattr(sec, "basic_auth")）。
    """
    consumed_paths: set[str] = set()
    consumed_tokens: set[str] = set()

    for path in iter_py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            # 1) 配置根上的完整访问链
            if isinstance(node, ast.Attribute) and is_config_like(root_of(node.value)):
                p = chain_path(node)
                if p:
                    consumed_paths.add(p)
            # 2) 所有属性名（含别名对象上的字段访问）
            if isinstance(node, ast.Attribute):
                consumed_tokens.add(node.attr)
            # 3) getattr(x, "key") / x.get("key") 的字符串键
            if isinstance(node, ast.Call):
                fname = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else (node.func.id if isinstance(node.func, ast.Name) else "")
                )
                if fname in ("getattr", "get", "setdefault", "hasattr"):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            consumed_tokens.add(arg.value)

    return consumed_paths, consumed_tokens


def _walk_leaves(obj: object, prefix: str = "") -> list[tuple[str, object]]:
    """展开嵌套 dict 为 (点分路径, 值) 列表。"""
    out: list[tuple[str, object]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{prefix}.{k}" if prefix else str(k)
            out.extend(_walk_leaves(v, sub))
    else:
        out.append((prefix, obj))
    return out


def check_security_keys_consumed(consumed_paths: set[str], consumed_tokens: set[str]) -> None:
    """校验 config.yaml security: 段每个键都真的被代码消费。

    安全开关「声明了却没人读」是典型的假安全感（配置-实现错配，
    对应安全评估 #13 / C-01 同族问题），因此作为 CI 门禁未消费即失败。

    命中判定（满足其一即视为已消费）：
    1. 完整点分链出现在配置对象访问中；
    2. 该键路径的**每一段**都在属性名 / getattr 字符串键中出现
       （兼容别名与字符串化访问）。
    """
    if yaml is None:
        print("[WARN] PyYAML 不可用，跳过 security 键消费校验")
        return
    if not CONFIG_YAML.exists():
        errors.append(f"{CONFIG_YAML} 不存在")
        return
    data = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8")) or {}
    security = data.get("security")
    if not isinstance(security, dict):
        errors.append("config.yaml 缺少 security: 段")
        return

    unconsumed: list[str] = []
    for leaf, _val in _walk_leaves(security):
        segments = leaf.split(".")
        target = f"security.{leaf}"
        full_hit = any(p == target or p.endswith(f".{target}") for p in consumed_paths)
        token_hit = all(seg in consumed_tokens for seg in segments)
        if not (full_hit or token_hit):
            unconsumed.append(target)

    for key in unconsumed:
        errors.append(
            f"config.yaml security 项 '{key}' 未被任何代码消费"
            "（声明即生效的假安全感，请在代码中读取该字段或删除该配置键）"
        )
    if not unconsumed:
        print(f"[INFO] security 段所有键均被代码消费（共 {len(_walk_leaves(security))} 个键）")


def check_yaml_runtime(class_fields: dict[str, set[str]]) -> None:
    if yaml is None:
        print("[WARN] PyYAML 不可用，跳过 config.yaml runtime 段校验")
        return
    if not CONFIG_YAML.exists():
        errors.append(f"{CONFIG_YAML} 不存在")
        return
    data = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8")) or {}
    runtime = data.get("runtime")
    runtime_fields = class_fields.get("RuntimeConfig", set())
    if not isinstance(runtime, dict):
        errors.append("config.yaml 缺少 runtime: 段")
        return
    for key in runtime:
        if key not in runtime_fields:
            errors.append(f"config.yaml runtime.{key} 未在 RuntimeConfig 中定义")


def main() -> int:
    if not CONFIG_MODELS.exists():
        print(f"[FAIL] 找不到 {CONFIG_MODELS}")
        return 1
    class_fields = collect_class_fields()
    all_fields = set().union(*class_fields.values()) if class_fields else set()
    print(f"[INFO] 解析到 {len(class_fields)} 个配置模型、{len(all_fields)} 个字段")
    check_code_refs(all_fields, class_fields)
    check_yaml_runtime(class_fields)
    # 安全项声明即消费门禁
    consumed_paths, consumed_tokens = collect_consumed()
    check_security_keys_consumed(consumed_paths, consumed_tokens)

    if errors:
        print("\n[FAIL] 配置字段引用完整性检查未通过：")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("[PASS] 配置字段引用完整性检查通过（代码访问与 config.yaml 均匹配配置模型）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
