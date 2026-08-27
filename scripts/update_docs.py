#!/usr/bin/env python3
"""FILEMAP auto-sync: rewrite the AUTO-SYNC marker from git diff --name-status.

Family tool (Phase D3): DraftPeek first, then promoted to the other 5 family repos.
Stdlib only. Python implementation avoids the PowerShell 5.1 default-encoding
(GBK reads of UTF-8 no-BOM files) pitfall documented in the handoff report §2.3.

Usage:
    python scripts/update_docs.py [<repo-root>] [<tag>]
Defaults: repo-root = one level above this script; tag = "HEAD" (range tag~5..tag).

Exit codes: 0 always (best-effort). On no git range / missing FILEMAP it prints a
short note and exits 0, mirroring the degradation semantics of check_spec_refs.py.
"""
import datetime
import subprocess
import sys
from pathlib import Path


def sh(args, cwd):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "")


def main(argv):
    repo = Path(argv[0]).resolve() if argv and argv[0] else Path(__file__).resolve().parents[1]
    tag = argv[1] if len(argv) > 1 else "HEAD"
    spec = repo / "docs" / "FILEMAP.md"
    if not spec.exists():
        print(f"FILEMAP not found: {spec}; skipping")
        return 0
    rc, out = sh(["git", "-C", str(repo), "diff", "--name-status",
                  f"{tag}~5..{tag}", "--", "."], repo)
    changed = [l for l in out.splitlines() if l and not l.startswith("fatal")]
    if not changed:
        print("no git range; skipping")
        return 0
    added = mod = deleted = 0
    for line in changed:
        code = line.split("\t", 1)[0] if "\t" in line else line[:1]
        ch = code[0] if code else ""
        if ch in ("A", "?"):
            added += 1
        elif ch in ("M", "R", "C"):
            mod += 1
        elif ch == "D":
            deleted += 1
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    sync = f"\n<!-- AUTO-SYNC {stamp} : +{added} ~{mod} -{deleted} -->\n"
    content = spec.read_text(encoding="utf-8")
    head = content.split("<!-- AUTO-SYNC ")[0]
    spec.write_text(head + sync, encoding="utf-8")
    print(f"FILEMAP synced: +{added} ~{mod} -{deleted}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))