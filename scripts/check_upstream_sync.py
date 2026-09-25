#!/usr/bin/env python3
"""TTS_MultiModel 上游同步探针（UPSTREAM_SYNC_PLAN C1）。

对本项目 vendor 的两处上游源码做三方哈希比对，区分六类状态（外加一个"没比对"的状态）：

    SYNC_LAG      上游已改、本地自锚点后未动 -> 上游有更新未吸收
    LOCAL_DRIFT   本地自锚点后改过 -> 已登记则 info，未登记则 warn
    CONFLICT      本地与上游都改了同一个文件 -> 必须人工合并
    NEW_UPSTREAM  上游新增、本地未 vendor
    LOCAL_ONLY    本地独有文件（不在白名单则 warn）
    LOCAL_DIR_ABSENT  本地目录未检出（reference_repos/ 不在版本控制里）-> 不参与门禁

比对用 git blob SHA-1（``sha1("blob <len>\\0" + content)``），与 GitHub Trees API
返回的 blob sha 同算法，因此**不需要下载文件内容**，一次 recursive tree 请求
即可覆盖全仓，配合本地缓存可把配额压到每分钟 1~2 个 API 调用。

用法：
    python scripts/check_upstream_sync.py --init      # 建立/刷新锚点快照
    python scripts/check_upstream_sync.py             # 比对并出报告
    python scripts/check_upstream_sync.py --offline    # 只查本地漂移，不联网
    python scripts/check_upstream_sync.py --selftest   # 先跑已知答案自检，再继续正式比对
    python scripts/check_upstream_sync.py --json
    python scripts/check_upstream_sync.py --strict     # 有 warn 即非零退出
    python scripts/check_upstream_sync.py --no-report  # 不写 docs/reports/（供门禁用）

门禁调用口径（= .pre-commit-config.yaml 里的 check-upstream-sync）：
    python scripts/check_upstream_sync.py --offline --selftest --strict --no-report
退出码：0 通过 / 1 有未登记漂移或冲突 / 2 自检没过（探针自身坏了，拒绝给出结论）。

配额：匿名 GitHub API 60 req/h。设 GITHUB_TOKEN 可提到 5000 req/h。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = REPO_ROOT / "configs" / "upstream_sync_state.json"
CACHE_PATH = REPO_ROOT / "cache" / "upstream_tree_cache.json"
REPORT_DIR = REPO_ROOT / "docs" / "reports"
API = "https://api.github.com"

# 两处 vendor 的映射关系。upstream_prefix 是 GitHub 仓库内的路径前缀，
# local_dir 相对 REPO_ROOT；两侧目录结构 1:1，因此 relpath 可直接拼接。
TARGETS: dict[str, dict[str, Any]] = {
    "voxcpm": {
        "repo": "OpenBMB/VoxCPM",
        "branch": "main",
        "upstream_prefix": "src/voxcpm/",
        "local_dir": "app/integrated_app/vendor/voxcpm",
        "watch": ["src/voxcpm/"],
        # 我们自己放进 vendor 目录的文件，不参与 LOCAL_ONLY 判定
        "local_only_allowlist": ["SOURCE.md"],
    },
    "indextts": {
        "repo": "index-tts/index-tts",
        "branch": "main",
        "upstream_prefix": "indextts/",
        "local_dir": "reference_repos/index-tts-main/indextts",
        "watch": ["indextts/"],
        "local_only_allowlist": ["SOURCE.md"],
    },
}

SKIP_SUFFIX = {".pyc", ".pyo"}


def _utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):  # 非 Windows 控制台可能无 reconfigure
            s.reconfigure(encoding="utf-8", errors="replace")


def blob_sha_of_bytes(data: bytes) -> str:
    """Raw bytes 的 git blob SHA-1（与 GitHub Trees API 的 ``sha`` 同算法）。"""
    digest = hashlib.sha1()
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


def git_blob_sha(path: Path) -> str:
    """Return the git blob SHA-1 of a file (matches GitHub Trees API ``sha``)."""
    return blob_sha_of_bytes(path.read_bytes())


def _gh_get(url: str, cache: bool = True) -> Any:
    """GET a GitHub API resource with a small on-disk cache."""
    cached: dict[str, Any] = {}
    if cache and CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
    hit = cached.get(url)
    if hit:
        return hit["body"]

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "tts-multimodel-upstream-sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)  # nosec B310 - 固定 https 主机
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"GitHub API {exc.code} for {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"网络不可达：{url} ({exc})") from exc

    if cache:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cached[url] = {"body": body}
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cached), encoding="utf-8")
        tmp.replace(CACHE_PATH)
    return body


def fetch_head_sha(repo: str, branch: str) -> str:
    body = _gh_get(f"{API}/repos/{repo}/commits/{branch}?per_page=1")
    return body["sha"]


def fetch_tree(repo: str, sha: str) -> dict[str, str]:
    """Return ``{path: blob_sha}`` for every blob under the watched prefixes."""
    watch = TARGETS[_repo_to_target_key(repo)]["watch"]
    body = _gh_get(f"{API}/repos/{repo}/git/trees/{sha}?recursive=1")
    if body.get("truncated"):
        raise RuntimeError(
            f"{repo}@{sha[:8]} tree 被 GitHub 截断，recursive 比对不可信，请缩小 watch 范围或克隆后本地比。"
        )
    out: dict[str, str] = {}
    for entry in body["tree"]:
        if entry["type"] != "blob":
            continue
        path = entry["path"]
        if path.endswith(".pyc") or not any(path.startswith(p) for p in watch):
            continue
        out[path] = entry["sha"]
    return out


def _repo_to_target_key(repo: str) -> str:
    for key, spec in TARGETS.items():
        if spec["repo"] == repo:
            return key
    raise KeyError(repo)


def local_files(target: str) -> dict[str, Path]:
    spec = TARGETS[target]
    root = REPO_ROOT / spec["local_dir"]
    prefix = spec["upstream_prefix"]
    if not root.is_dir():
        return {}
    found: dict[str, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix in SKIP_SUFFIX or "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        found[prefix + rel] = path
    return found


def write_state() -> dict[str, Any]:
    """建立锚点：记录上游 HEAD 的 commit 与该 commit 下每个文件的 blob sha + 本地当前 sha。"""
    state: dict[str, Any] = {
        "schema": 1,
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "由 scripts/check_upstream_sync.py --init 生成。anchor_commit 是本地 vendor 内容的比对基准；"
        "registered_modifications 登记本地相对上游的主动改动，未登记的本地改动会被判为 warn。",
        "targets": {},
    }
    for key, spec in TARGETS.items():
        head = fetch_head_sha(spec["repo"], spec["branch"])
        tree = fetch_tree(spec["repo"], head)
        locals_ = local_files(key)
        files: dict[str, dict[str, str]] = {}
        for path in sorted(set(tree) | set(locals_)):
            entry: dict[str, str] = {}
            if path in tree:
                entry["upstream"] = tree[path]
            if path in locals_:
                entry["local"] = git_blob_sha(locals_[path])
            files[path] = entry
        prev = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
        prev_t = (prev.get("targets") or {}).get(key, {})
        state["targets"][key] = {
            "repo": spec["repo"],
            "branch": spec["branch"],
            "upstream_prefix": spec["upstream_prefix"],
            "local_dir": spec["local_dir"],
            "anchor_commit": head,
            "anchor_verified_at": _dt.date.today().isoformat(),
            "registered_modifications": prev_t.get("registered_modifications") or _seed_registered(key, files),
            "files": files,
        }
        lag = sum(1 for f, v in files.items() if v.get("local") and v.get("upstream") and v["local"] != v["upstream"])
        print(f"  [{key}] anchor={head[:8]} files={len(files)} 本地与上游不一致={lag}")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _dump(STATE_PATH, state)
    print(f"锚点已写入 {STATE_PATH.relative_to(REPO_ROOT)}")
    return state


def _seed_registered(target: str, files: dict[str, dict[str, str]]) -> dict[str, str]:
    """首次 init 时，把已实测确认的本地改动按文件登记进去（描述需人工核对）。"""
    hints = {
        "voxcpm": {
            "src/voxcpm/core.py": "from_pretrained 增加 revision 参数用于固定 HF 版本（实测 diff 仅此一处，"
            "原 SOURCE.md 写的『本地路径支持/设备处理』与实际不符）",
            "src/voxcpm/model/voxcpm.py": "LlamaTokenizerFast.from_pretrained 行尾加 # nosec B615（非行为性）",
            "src/voxcpm/model/voxcpm2.py": "同上，# nosec B615（非行为性）",
            "src/voxcpm/training/data.py": "load_dataset 行尾加 # nosec B615（非行为性）",
        },
        "indextts": {},
    }
    out: dict[str, str] = {}
    for path, desc in hints.get(target, {}).items():
        v = files.get(path) or {}
        if v.get("local") and v.get("upstream") and v["local"] != v["upstream"]:
            out[path] = desc
    return out


def check(offline: bool = False) -> dict[str, Any]:
    if not STATE_PATH.exists():
        raise SystemExit(f"缺少锚点文件 {STATE_PATH}，请先运行 --init")
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    results: dict[str, Any] = {"generated_at": state.get("generated_at"), "targets": {}, "counts": {}}
    tally: dict[str, int] = {}

    for key, tgt in state["targets"].items():
        spec = TARGETS[key]
        local_root = REPO_ROOT / spec["local_dir"]
        if not local_root.is_dir():
            # 本地目录压根没检出：`reference_repos/` 被 .gitignore 排除，干净克隆上就没有。
            # 这**不是**"上游新增 225 个文件"——把"探测不到"写成"有结论"比误报更危险，
            # 会让人去追一批根本不存在的同步任务。单列一类，且不参与 --strict 判定。
            tally["LOCAL_DIR_ABSENT"] = tally.get("LOCAL_DIR_ABSENT", 0) + 1
            results["targets"][key] = {
                "repo": spec["repo"],
                "status": "LOCAL_DIR_ABSENT",
                "local_dir": spec["local_dir"],
                "counts": {},
            }
            continue
        locals_ = local_files(key)
        local_now = {p: git_blob_sha(f) for p, f in locals_.items()}
        registered = set((tgt.get("registered_modifications") or {}).keys())

        head_tree: dict[str, str] = {}
        head_sha = tgt["anchor_commit"]
        if not offline:
            head_sha = fetch_head_sha(spec["repo"], spec["branch"])
            head_tree = fetch_tree(spec["repo"], head_sha)
        else:
            # 离线时以上游锚点树充当 HEAD：判不出"上游动了没"，但锚点当刻
            # 就存在的本地差异仍须报出来，否则会误显示为 0。
            head_tree = {p: v["upstream"] for p, v in tgt["files"].items() if v.get("upstream")}

        buckets: dict[str, list[dict[str, str]]] = {
            "SYNC_LAG": [],
            "CONFLICT": [],
            "LOCAL_DRIFT": [],
            "LOCAL_DRIFT_REGISTERED": [],
            "NEW_UPSTREAM": [],
            "LOCAL_ONLY": [],
        }
        anchor = tgt["files"]
        allow = set(spec["local_only_allowlist"])
        for path in sorted(set(anchor) | set(local_now) | set(head_tree)):
            if path.split("/")[-1] in allow:
                continue  # 我们自己放进 vendor 的文件（SOURCE.md），不属于源码漂移
            a = anchor.get(path) or {}
            up_anchor, local_anchor = a.get("upstream"), a.get("local")
            local_now_sha = local_now.get(path)
            up_head_sha = head_tree.get(path)

            local_changed = bool(local_now_sha and local_anchor and local_now_sha != local_anchor)
            upstream_changed = bool(up_anchor and up_head_sha and up_anchor != up_head_sha)

            if local_now_sha and not local_anchor:
                local_changed = True  # 锚点里没有，说明是新出现的本地文件
            if not local_anchor and not local_now_sha:
                continue

            if local_changed and upstream_changed:
                bucket = "CONFLICT"
            elif upstream_changed:
                bucket = "SYNC_LAG"
            elif local_changed:
                # registered_modifications 里的描述是针对**锚点当刻那份内容**写的；
                # 文件又动了，描述是否仍然成立无从判断，一律要求重新核对登记。
                bucket = "LOCAL_DRIFT"
            elif local_now_sha and local_anchor and up_head_sha and local_now_sha != up_head_sha:
                # 锚点当刻就不一致、且双方都没再动 -> 既有差异，已由 registered 解释或该被登记
                bucket = "LOCAL_DRIFT_REGISTERED" if path in registered else "LOCAL_DRIFT"
            else:
                continue
            buckets[bucket].append(
                {
                    "path": path,
                    "anchor_upstream": up_anchor or "",
                    "head_upstream": up_head_sha or "",
                    "local": local_now_sha or "",
                }
            )

        for path in sorted(set(head_tree) - set(local_now)):
            if path.split("/")[-1] in allow:
                continue
            buckets["NEW_UPSTREAM"].append({"path": path})
        for path in sorted(set(local_now) - set(head_tree) - set(anchor)):
            if path.split("/")[-1] in set(spec["local_only_allowlist"]):
                continue
            buckets["LOCAL_ONLY"].append({"path": path})

        counts = {k: len(v) for k, v in buckets.items()}
        for k, n in counts.items():
            tally[k] = tally.get(k, 0) + n
        results["targets"][key] = {
            "repo": spec["repo"],
            "anchor_commit": tgt["anchor_commit"],
            "head_commit": head_sha,
            "moved": head_sha != tgt["anchor_commit"],
            "probed_upstream": not offline,
            "counts": counts,
            "buckets": {k: v for k, v in buckets.items() if v},
        }
    results["counts"] = tally
    return results


def _dump(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def write_report(res: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().strftime("%Y%m%d")
    out = REPORT_DIR / f"upstream_sync_{today}.md"
    lines = [
        "# 上游同步探针报告",
        "",
        f"- 生成时间：{_dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 锚点建立于：{res['generated_at']}",
        f"- 探针基准文件：`{STATE_PATH.relative_to(REPO_ROOT)}`",
        "",
        "## 汇总",
        "",
        "| 状态 | 数量 | 含义 |",
        "|---|---|---|",
    ]
    meaning = {
        "SYNC_LAG": "上游已改、本地自锚点后未动（可吸收）",
        "CONFLICT": "双方都改了同一文件（需人工合并）",
        "LOCAL_DRIFT": "本地改过且**未登记**",
        "LOCAL_DRIFT_REGISTERED": "本地改过、已登记",
        "NEW_UPSTREAM": "上游新增、本地未 vendor",
        "LOCAL_ONLY": "本地独有、不在白名单",
        "LOCAL_DIR_ABSENT": "本地目录未检出（**不代表任何同步缺口**）",
    }
    for k in meaning:
        lines.append(f"| {k} | {res['counts'].get(k, 0)} | {meaning[k]} |")
    lines.append("")
    for key, t in res["targets"].items():
        if t.get("status") == "LOCAL_DIR_ABSENT":
            lines += [
                f"## {key}（`{t['repo']}`）—— 未比对",
                "",
                f"- `{t['local_dir']}` 在本机不存在，本目标**一个文件都没比对**。",
                "- 该目录由外部检出提供（`reference_repos/` 在 `.gitignore` 里），"
                "干净克隆上缺失属正常状态，不计入门禁。",
                "",
            ]
            continue
        moved = "已移动" if t["moved"] else ("未移动" if t.get("probed_upstream", True) else "未联网探测，状态未知")
        lines += [
            f"## {key}（`{t['repo']}`）",
            "",
            f"- 锚点 commit：`{t['anchor_commit'][:10]}`",
            f"- 上游 HEAD：`{t['head_commit'][:10]}` —— **{moved}**",
            "",
        ]
        for bucket, items in t["buckets"].items():
            lines.append(f"### {bucket}（{len(items)}）")
            lines.append("")
            for it in items:
                lines.append(f"- `{it['path']}`")
            lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def selftest() -> int:
    """已知答案自检：只断言"探针自己没坏"，不断言仓库当前状态。

    探针坏了不会报错，只会输出一张"看着像结论"的报告，所以正式比对前必须先验
    两件事：① 哈希口径与 git 一致（用与仓库内容无关的硬编码向量）；② 状态文件
    里"应判为同"和"应判为不同"两类样本都还在。

    早期版本在这里还逐个比对 differing 文件的当前哈希与锚点记录，于是"有人改了
    已登记的 vendor 文件"会被报成退出码 2（探针坏了）——恰恰反了：那次改动本该由
    正式比对归成 LOCAL_DRIFT、以退出码 1 失败。两个成因必须占两个码。
    """
    # 三个向量逐条与 `git hash-object --stdin` 对过，改任何一个都说明哈希口径变了。
    known_answers = {
        b"": "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
        b"hello\n": "ce013625030ba8dba906f756967f9e9ca394464a",
        b"a\nb\n": "422c2b7ab3b3c668038da977e4e93a5fc623169c",
    }
    for data, expected in known_answers.items():
        got = blob_sha_of_bytes(data)
        assert got == expected, f"门禁失败：哈希口径已变，blob_sha_of_bytes({data!r})={got}，git 给的是 {expected}"

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    tgt = state["targets"]["voxcpm"]

    identical = [
        p for p, v in tgt["files"].items() if v.get("local") and v.get("upstream") and v["local"] == v["upstream"]
    ]
    differing = [
        p for p, v in tgt["files"].items() if v.get("local") and v.get("upstream") and v["local"] != v["upstream"]
    ]
    assert identical, "门禁失败：状态文件里没有『应判为相同』的样本，锚点快照已退化"
    assert differing, "门禁失败：状态文件里没有『应判为不同』的样本（vendor/voxcpm 已知有 4 处本地改动）"
    print(f"selftest OK  known_answers={len(known_answers)} identical={len(identical)} differing={len(differing)}")
    return 0


def main() -> int:
    _utf8_stdio()
    ap = argparse.ArgumentParser(description="上游同步探针")
    ap.add_argument("--init", action="store_true", help="建立/刷新锚点快照（需联网）")
    ap.add_argument("--offline", action="store_true", help="不联网，只报本地漂移")
    ap.add_argument("--selftest", action="store_true", help="门禁样本自检")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非 markdown 报告")
    ap.add_argument("--no-report", action="store_true", help="只打印分类计数，不落报告文件（供门禁调用）")
    ap.add_argument("--strict", action="store_true", help="存在未登记本地改动/冲突时非零退出")
    args = ap.parse_args()

    if args.init:
        write_state()
        args.offline = True
    if args.selftest:
        # 自检只证明"探针自己没坏"，不代表 vendor 没漂移——跑完必须继续正式比对。
        # 早先这里直接 return，于是 `--selftest --strict` 组合把 --strict 吞掉了，
        # 对"登记为相同、后来被人改了"的文件完全不设防（恰是最常见的漂移场景）。
        try:
            selftest()
        except AssertionError as exc:
            print(f"{exc}\n（比对链路未通过，拒绝出报告）", file=sys.stderr)
            return 2

    res = check(offline=args.offline)
    c = res["counts"]
    unregistered = c.get("LOCAL_DRIFT", 0) + c.get("LOCAL_ONLY", 0)
    conflicts = c.get("CONFLICT", 0)
    print(
        f"SYNC_LAG={c.get('SYNC_LAG', 0)} CONFLICT={conflicts} "
        f"LOCAL_DRIFT(未登记)={unregistered} LOCAL_DRIFT(已登记)={c.get('LOCAL_DRIFT_REGISTERED', 0)} "
        f"NEW_UPSTREAM={c.get('NEW_UPSTREAM', 0)}"
        + (f" LOCAL_DIR_ABSENT={c['LOCAL_DIR_ABSENT']}(该目标未比对)" if c.get("LOCAL_DIR_ABSENT") else "")
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif not args.no_report:
        print(f"报告：{write_report(res).relative_to(REPO_ROOT)}")
    return 1 if args.strict and (unregistered or conflicts) else 0


if __name__ == "__main__":
    sys.exit(main())
