#!/usr/bin/env python3
"""Thin wrapper -> shared family auditor. Fail if NEW phantom refs appear.

The auditor lives OUTSIDE this repo (C:\\Users\\Doro\\.spec_audit).  On a
developer machine it is found and the check is authoritative; in a fresh CI
checkout it is absent and the check degrades to "skip" (keeps CI green).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
AUDITORS = [
    HERE / ".spec_audit" / "audit_spec_refs.py",
    HERE.parent / ".spec_audit" / "audit_spec_refs.py",
]
auditor = next((p for p in AUDITORS if p.is_file()), None)
if auditor is None:
    print("family auditor not found; skipping (CI green)", file=sys.stderr)
    sys.exit(0)

with tempfile.TemporaryDirectory(prefix="spec_audit_") as td:
    out = Path(td) / "current.json"
    out_md = Path(td) / "current.md"
    subprocess.run(
        [sys.executable, str(auditor), "--project", HERE.name, "--json", str(out), "--md", str(out_md)], check=True
    )
    data = json.loads(out.read_text(encoding="utf-8"))[0]

hard = [f for f in data["findings"] if f["status"] == "PHANTOM" and f["tier"] == "ASSERTIVE"]
dl = data["dead_links"]
wf = data["workflows"]["missing"]
pc = data["precommit"]["declared_not_configured"]
print(f"phantom={len(hard)} dead_links={len(dl)} bad_workflow={len(wf)} bad_hook={len(pc)}")
for x in hard:
    print(f"  PHANTOM {x['ref']}  in {', '.join(x['specs'])}")
for d in dl:
    print(f"  DEAD    {d['spec']}:{d['line']} -> {d['link']}")
sys.exit(1 if (hard or dl or wf or pc) else 0)
