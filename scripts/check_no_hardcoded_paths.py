#!/usr/bin/env python3
"""路径可移植性检查：阻止本机绝对路径入库。

- 默认：扫描暂存区（git diff --cached），在提交时拦截新引入的硬编码路径
- --all：全库扫描（人工审计用，需配合 docs/CODING_STANDARDS.md 第 1 节）
- 豁免：文档(*.md)、docs/、data/、测试、锁定文件、语法定义、.github、配置类文件、二进制、
        文件头标注 DEV-ONLY 的运维脚本（其默认值允许保留本机路径）
- 规则详见 docs/CODING_STANDARDS.md「1. 路径可移植性（强制）」
"""

import re
import subprocess  # nosec B404（仅以参数列表调用 git，无 shell=True，风险可控）
import sys
from pathlib import Path

EXEMPT_SUFFIXES = {
    ".md",
    ".lock",
    ".pyc",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff2",
    ".ttf",
    ".otf",
    ".exe",
    ".whl",
    ".zip",
    ".7z",
    ".db",
    ".sqlite",
}
EXEMPT_FILES = {".gitattributes", ".mailmap", ".gitignore"}
EXEMPT_DIRS = {"docs", "data", "tests", "__tests__", ".github"}
SCRIPT_DIR = Path(__file__).resolve().parent  # 脚本固定位于仓库 scripts/ 下
_REPO_HINT = SCRIPT_DIR.parent


def _repo_root():
    out = subprocess.run(  # nosec B603, B607（固定参数列表 git rev-parse，无 shell=True）
        ["git", "-C", str(_REPO_HINT), "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    return Path(out.stdout.strip())


ROOT = _repo_root()
EXEMPT_MARKER = b"DEV-ONLY"  # 头部标注 DEV-ONLY 的运维脚本豁免（已知例外）

# 本机绝对路径模式（Windows 与 POSIX；排除测试/示例常见占位符）
# 占位符：me/doro/admin/user/username/YourName/u/ttsuser（测试断言、示例文案、容器内用户）
_PLACEHOLDER = r"(?:me\b|doro\b|admin\b|user\b|username\b|yourname\b|u\b|ttsuser\b)"
PATTERNS = [
    re.compile(rf"C:\\Users\\(?!{_PLACEHOLDER})[A-Za-z]"),  # 字面 C:\Users\...
    re.compile(rf"C:\\\\Users\\\\(?!{_PLACEHOLDER})[A-Za-z]"),  # Python 转义 C:\\Users\\...
    re.compile(rf"C:/Users/(?!{_PLACEHOLDER})[A-Za-z]"),  # 正斜杠 C:/Users/...
    re.compile(rf"/home/(?!{_PLACEHOLDER})[a-z][a-z0-9_-]*/"),  # /home/<user>/...
    re.compile(rf"/Users/(?!{_PLACEHOLDER})[A-Za-z]"),  # /Users/<真实用户名>/...
]

# 已知豁免文件（相对路径子串；内容允许含本机路径或占位符，人工复核过）
ALLOWLIST = [
    "scripts/check_spec_refs.py",  # 家族 auditor wrapper：docstring 说明仓外依赖
    "scripts/release_gate.ps1",  # 防泄漏断言自身（必然含被检查的模式）
    "scripts/installer/killttsmultimodel.ps1",  # 注释说明开发路径
    "dockerfile",  # 容器内路径（/app /home/<user>）合法
    "locales/",  # UI 占位文案（folder_path_placeholder 等）
    ".env.example",  # 示例文件（占位符）
    "local.properties.example",  # 示例文件（占位符）
]


def _files_from(cmd):
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))  # nosec B603, B607（git 只读命令）
    return [p for p in out.stdout.split("\0") if p]


def is_exempt(path):
    p = Path(path)
    name = p.name.lower()
    if name in EXEMPT_FILES:
        return True
    if p.suffix.lower() in EXEMPT_SUFFIXES or name.endswith(".tmlanguage.json"):
        return True
    parts = {str(x).lower() for x in p.parts}
    if parts & EXEMPT_DIRS:
        return True
    low = p.as_posix().lower()
    if any(a in low for a in ALLOWLIST):
        return True
    return bool(".test" in name or ".spec" in name)


def check_file(rel_path, errors):
    path = ROOT / rel_path
    try:
        raw = path.read_bytes()
    except OSError:
        return
    if b"\x00" in raw[:4096]:
        return  # 二进制
    if is_exempt(path):
        return
    if EXEMPT_MARKER in raw[:1024]:
        return  # DEV-ONLY 运维脚本（已标注，默认值豁免）
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        for pat in PATTERNS:
            if pat.search(line):
                errors.append(f"  {path}:{i}: {line.strip()[:120]}")
                break


def main():
    all_mode = "--all" in sys.argv
    files = (
        _files_from(["git", "ls-files", "-z"])
        if all_mode
        else _files_from(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"])
    )
    errors = []
    for f in files:
        check_file(f, errors)
    if errors:
        print("[path-portability] 检测到本机绝对路径（禁止入库）：")
        print("\n".join(errors))
        print("\n修复方式：相对路径（$PSScriptRoot / %~dp0 / Path(__file__).resolve().parents[N]）")
        print("或环境变量 + 项目内默认值；仅本机使用的运维脚本请标注 DEV-ONLY 并参数化。")
        print("规则见 docs/CODING_STANDARDS.md 第 1 节。")
        return 1
    if all_mode:
        print("[path-portability] 全库扫描通过：未发现硬编码本机绝对路径")
    return 0


if __name__ == "__main__":
    sys.exit(main())
