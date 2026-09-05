import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

print("=== 1. 语法检查 ===")
import py_compile

for f in ["app/integrated_app/config.py", "app/integrated_app/history_db.py", "app/integrated_app/app_server.py"]:
    try:
        py_compile.compile(f, doraise=True)
        print(f"  OK: {f}")
    except py_compile.PyCompileError as e:
        print(f"  FAIL: {f}: {e}")
        sys.exit(1)

print("\n=== 2. get_effective_retention_days() ===")
from integrated_app.config import get_effective_retention_days, get_history_keep_days

print(f"  get_effective_retention_days() = {get_effective_retention_days()} (期望 90, pii_retention_days 优先)")
print(f"  get_history_keep_days() = {get_history_keep_days()} (期望 0, 兼容别名)")
assert get_effective_retention_days() == 90, "pii_retention_days=90 应优先"
assert get_history_keep_days() == 0, "keep_days=0"

print("\n=== 3. 确认无重复清理路径 ===")
import re

with open("app/integrated_app/app_server.py", encoding="utf-8") as f:
    app_src = f.read()
purge_in_app = len(re.findall(r"purge_expired\s*\(", app_src))
print(f"  app_server.py 中 purge_expired( 实际调用次数: {purge_in_app} (期望 0, 已移除)")
assert purge_in_app == 0, "app_server.py 不应再调用 purge_expired"

with open("app/integrated_app/history_db.py", encoding="utf-8") as f:
    hist_src = f.read()
# 提取 get_history_db 函数体（排除 docstring），检查实际调用
import ast

tree = ast.parse(hist_src)
get_db_func = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "get_history_db":
        get_db_func = node
        break
assert get_db_func is not None
func_body_src = ast.get_source_segment(hist_src, get_db_func)
# 去掉 docstring
if (
    get_db_func.body
    and isinstance(get_db_func.body[0], ast.Expr)
    and isinstance(get_db_func.body[0].value, ast.Constant)
):
    docstring = get_db_func.body[0].value.value
    func_body_src = func_body_src.replace(docstring, "", 1)
print(f"  get_history_db() 代码体中 purge_expired(: {'purge_expired(' in func_body_src} (期望 True)")
print(f"  get_history_db() 代码体中 prune_old_records(: {'prune_old_records(' in func_body_src} (期望 False)")
assert "purge_expired(" in func_body_src
assert "prune_old_records(" not in func_body_src
print(
    f"  get_history_db() 导入 get_effective_retention_days: {'get_effective_retention_days' in func_body_src} (期望 True)"
)
assert "get_effective_retention_days" in func_body_src

print("\n=== 4. prune_old_records 方法仍存在（测试兼容） ===")
print(f"  prune_old_records 定义存在: {'def prune_old_records' in hist_src} (期望 True)")
assert "def prune_old_records" in hist_src

print("\nP0-2 验收全部通过 ✓")
