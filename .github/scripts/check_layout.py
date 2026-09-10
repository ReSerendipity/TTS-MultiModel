"""Structure-guard v2: freeze the approved repo root layout and block stray dumps.

Reads .github/layout-rules.yaml. Exit codes: 0 = OK, 1 = hard failure.

v2 改进（2026-09-07 / 2026-09-09）：
  - 已被 .gitignore 忽略的根条目不再计入 WARN（它们不构成仓库卫生问题，
    也不应污染 root_allowlist）。这让 WARN 真正意味着"有东西该整理了"。
  - 命中 forbid_root_patterns -> FAIL（阻断），且判定在 ignore 之前：即使
    该文件被 gitignore，散落转储照样拦。
  - artifact_root_patterns（会再生的产物，如 coverage.xml）只 WARN 不阻断。
  - 2026-09-09：改用 f-string、去掉 coding 声明，满足 ruff UP009/UP031，
    避免污染各仓的 lint 门禁。
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))  # .../.github/scripts -> repo
RULES_PATH = os.path.join(REPO_ROOT, ".github", "layout-rules.yaml")


def strip_quotes(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def load_rules(path):
    """Minimal YAML reader for our specific structure (scalars + '- ' lists)."""
    rules = {}
    cur_list = None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            stripped = line.strip()
            if stripped.startswith("- "):
                if cur_list is not None:
                    cur_list.append(strip_quotes(stripped[2:]))
                continue
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                k = k.strip()
                v = v.strip()
                if v == "":
                    cur_list = []
                    rules[k] = cur_list
                else:
                    rules[k] = strip_quotes(v)
                    cur_list = None
    return rules


def ignored_set(repo, entries):
    """批量询问 git：哪些根条目已被 .gitignore 忽略。

    注意：必须走 bytes。Windows 下 text=True 会把 \\n 变成 \\r\\n，
    git 会把 \\r 当成路径的一部分，导致全部匹配失败。
    """
    if not entries:
        return set()
    try:
        r = subprocess.run(["git", "check-ignore", "--stdin"], cwd=repo,
                           input="\n".join(entries).encode("utf-8"),
                           capture_output=True)
        return set(x for x in r.stdout.decode("utf-8", "replace").splitlines() if x)
    except Exception:
        return set()


def main():
    if not os.path.exists(RULES_PATH):
        print("FAIL: .github/layout-rules.yaml missing")
        return 1
    rules = load_rules(RULES_PATH)
    allow = set(rules.get("root_allowlist", []))
    forbid = [re.compile(p) for p in rules.get("forbid_root_patterns", [])]
    artifact = [re.compile(p) for p in rules.get("artifact_root_patterns", [])]
    require = rules.get("require_dirs", [])
    gignore = rules.get("gitignored_expect", [])

    fails = []
    warns = []

    entries = [e for e in os.listdir(REPO_ROOT) if e != ".git"]
    ignored = ignored_set(REPO_ROOT, entries)
    checked = [e for e in entries if e not in ignored]

    # 先对全部条目判 forbid：即使被 gitignore 忽略，散落转储也该拦下来
    # （gitignore 只保证它不进仓库，但根目录照样被弄脏）。
    for e in entries:
        if e in allow:
            continue
        if any(p.search(e) for p in forbid):
            fails.append(f"FORBID pattern matched root entry: {e}")

    # 再对"未被忽略"的未登记条目给出 WARN（不阻断）
    for e in checked:
        if e in allow:
            continue
        if any(p.search(e) for p in forbid):
            continue  # 上面已计为 FAIL，不重复计入 WARN
        if any(p.search(e) for p in artifact):
            warns.append(f"Artifact at root (auto-tidy will relocate): {e}")
            continue
        warns.append(f"Unrecognized root entry (review / add to allowlist): {e}")

    for d in require:
        if not os.path.exists(os.path.join(REPO_ROOT, d)):
            fails.append(f"Required directory missing: {d}")

    for g in gignore:
        gp = os.path.join(REPO_ROOT, g)
        if os.path.exists(gp) and g not in ignored:
            warns.append(f"Expected-gitignored entry is NOT ignored: {g}")

    for w in warns:
        print("WARN: " + w)
    for f in fails:
        print("FAIL: " + f)

    if fails:
        print(f"\nstructure-guard: {len(fails)} failure(s), {len(warns)} warning(s).")
        return 1
    print("structure-guard: OK "
          f"({len(entries)} root entries, {len(checked)} checked, "
          f"{len(ignored)} ignored, {len(warns)} warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
