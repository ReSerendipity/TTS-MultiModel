#!/usr/bin/env python3
"""Ledger integrity check for docs/agents/REVISION_LOG.md (iron law #5 append guard).

Machine-checks the three append-protocol invariants of the self-evolution
revision ledger:
  1. version column is unique (guards against duplicated rows like v1.17);
  2. dates are non-decreasing in row order (append-only, sorted by date);
  3. every table row has the same number of pipe-delimited columns (guards
     against merged/glued trailing cells like the v1.18 row tail). Escaped
     pipes (``\\|`` inside code spans) are not counted as separators.

The ledger file itself is gitignored (local-only, lives beside AGENTS.md), so a
missing file degrades to "skip" with exit 0 — same CI-green policy as
scripts/check_spec_refs.py. This script is the validator; it never judges the
historical truthfulness of row contents.

Exit codes: 0 = pass or skipped, 1 = invariant violated.
Usage: python scripts/check_revision_log.py [path/to/REVISION_LOG.md]
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

DEFAULT_LEDGER = Path(__file__).resolve().parents[1] / "docs" / "agents" / "REVISION_LOG.md"
# A pipe only ends a cell when not escaped as `\|` (markdown table convention).
CELL_SPLIT = re.compile(r"(?<!\\)\|")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")


def split_cells(line: str) -> list[str]:
    parts = CELL_SPLIT.split(line.rstrip("\n").rstrip("\r"))
    return [p.strip() for p in parts[1:-1]]  # drop the phantom outer empties


def check(path: Path) -> list[str]:
    errors: list[str] = []
    seen: dict[str, int] = {}
    prev: tuple[date, int] | None = None
    header_cols: int | None = None

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.lstrip().startswith("|"):
            continue  # prose, blank lines and HTML comments carry no ledger data
        cells = split_cells(line)
        if header_cols is None:
            header_cols = len(cells)  # the first table row (header) defines the width
        elif len(cells) != header_cols:
            errors.append(f"L{lineno}: column count {len(cells)} != header {header_cols} (merged/glued cells?)")
        if cells[0] == "自进化版本" or all(SEPARATOR_CELL_RE.match(c) for c in cells if c):
            continue  # header / separator rows have no version or date to validate
        version, day = cells[0], cells[1]
        if version in seen:
            errors.append(f"L{lineno}: duplicate version {version} (first seen at L{seen[version]})")
        else:
            seen[version] = lineno
        if not DATE_RE.match(day):
            errors.append(f"L{lineno}: unparseable date cell {day!r}")
            continue
        d = date.fromisoformat(day)
        if prev is not None and d < prev[0]:
            errors.append(f"L{lineno}: date {day} regresses against L{prev[1]} ({prev[0].isoformat()})")
        prev = (d, lineno)

    if header_cols is None:
        errors.append("no table rows found (empty or corrupted ledger)")
    return errors


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LEDGER
    if not path.is_file():
        print(f"ledger not found ({path}); skipping (local-only file, CI green)", file=sys.stderr)
        return 0
    errors = check(path)
    if errors:
        print(f"REVISION_LOG ledger check: {len(errors)} violation(s) in {path}", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("REVISION_LOG ledger check OK: versions unique, dates non-decreasing, column counts consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
