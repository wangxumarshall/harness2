#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Rollback guard for the sd cell: drop a domain's latest adoption when it regressed.

Compares the judged pass-rate (adopt_ok, falling back to L1+L2 for legacy rows) of the
newest library entry against the window of entries before it; on regression,
Library.forget() removes the entry so the next sweep re-derives the harness without
it. Run it after judging, before syncing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harness2.core import runmark as RM  # noqa: E402
from harness2.core.library import Library  # noqa: E402
from harness2.substrate.claude_code import CLAUDE_CODE_SURFACE  # noqa: E402


def task_scores(results_root: Path) -> dict[str, list[bool]]:
    """task_id -> adoption verdicts of every judged run (from mark + scores.json).

    Prefers adopt_ok (L1+L2+L3) when present; falls back to l1/l2 for legacy rows.
    """
    out: dict[str, list[bool]] = {}
    if not results_root.is_dir():
        return out
    for rd in sorted(results_root.iterdir()):
        if not rd.is_dir():
            continue
        mark = RM.read_mark(rd) or {}
        task = str(mark.get("task_id") or "")
        scores = rd / "scores.json"
        if not task or not scores.is_file():
            continue
        try:
            s = json.loads(scores.read_text(encoding="utf-8"))
            # adopt_ok (L1+L2+L3) is the real verdict; l1/l2 is the legacy shape.
            # Both computations stay in the try: a wrong-shape scores.json (non-dict
            # top level, or a non-dict l1/l2 value) must skip the row, not abort
            # the whole guard run.
            if s.get("adopt_ok") is not None:
                ok = bool(s["adopt_ok"])
            else:
                ok = bool(s.get("l1", {}).get("ok") and s.get("l2", {}).get("ok"))
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
        out.setdefault(task, []).append(ok)
    return out


def _entry_number(slug: str) -> int:
    try:
        return int(slug.split("-", 1)[1])
    except (IndexError, ValueError):
        return 0


def guard(
    library_root: Path,
    results_root: Path,
    *,
    window: int = 3,
    threshold: float = 0.0,
    dry_run: bool = False,
) -> list[tuple[str, str]]:
    """Drop each domain's latest adoption when it regressed; return (domain, task)."""
    dropped: list[tuple[str, str]] = []
    if not library_root.is_dir():
        return dropped
    ts = task_scores(results_root)

    def rate(slugs: list[str]) -> float | None:
        flags = [ok for s in slugs for ok in ts.get(s, [])]
        return (sum(flags) / len(flags)) if flags else None

    for dom in sorted(p for p in library_root.iterdir() if p.is_dir()):
        lib = Library(library_root, dom.name, CLAUDE_CODE_SURFACE)
        entries = sorted(lib.entries(), key=_entry_number)
        if not entries:
            continue
        latest = entries[-1]
        before = rate(entries[-(window + 1) : -1])
        after = rate([latest])
        if after is None or before is None:
            continue
        if after < before - threshold:
            print(
                f"[guard] {dom.name}: dropping {latest} "
                f"(after={after:.2f} < before={before:.2f} - {threshold})",
                flush=True,
            )
            if not dry_run:
                lib.forget([latest])
            dropped.append((dom.name, latest))
    return dropped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--library-root", required=True, type=Path)
    ap.add_argument("--results-root", required=True, type=Path)
    ap.add_argument("--window", type=int, default=3)
    ap.add_argument("--threshold", type=float, default=0.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dropped = guard(
        args.library_root,
        args.results_root,
        window=args.window,
        threshold=args.threshold,
        dry_run=args.dry_run,
    )
    print(f"{len(dropped)} adoption(s) dropped" + (" (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
