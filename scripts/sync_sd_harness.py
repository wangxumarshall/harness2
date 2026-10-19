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

"""Sync an adopted claude_code harness tree into the sdcshield working repo.

The four always-on files are composed into the repo's CLAUDE.md; skills, agents,
scripts and the hooks file are copied to their native paths. Review the diff and
commit it in the target repo.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harness2 import config  # noqa: E402
from harness2.core import tree as HT  # noqa: E402
from harness2.substrate.claude_code import (  # noqa: E402
    CLAUDE_CODE_SURFACE,
    compose_claude_md,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "harness_dir", help="adopted harness tree (e.g. runs/sd/library/.../harness)"
    )
    ap.add_argument("--repo", default=None, help="target repo (default: config.SD_REPO)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo) if args.repo else config.SD_REPO
    tree = HT.read_tree(CLAUDE_CODE_SURFACE, args.harness_dir)
    if not tree:
        raise SystemExit(f"{args.harness_dir} is not a claude_code harness tree")

    actions = []
    for rel in sorted(tree):
        if rel != "CLAUDE.md" and rel in CLAUDE_CODE_SURFACE.always_on:
            continue  # composed into CLAUDE.md, not copied as a file
        if rel.startswith("sub_agents/") and rel.endswith("/AGENT.md"):
            name = rel[len("sub_agents/") : -len("/AGENT.md")]
            dst = repo / ".claude" / "agents" / f"{name}.md"
        else:
            dst = repo / rel
        actions.append((rel, dst))

    print(f"target repo: {repo}")
    for rel, dst in actions:
        print(f"  {rel} -> {dst.relative_to(repo)}")
    print("  (composed) CLAUDE.md <- CLAUDE.md + GUARDRAILS + MEMORY + TOOL_GUIDANCE")
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    for rel, dst in actions:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(tree[rel], encoding="utf-8")
    (repo / "CLAUDE.md").write_text(compose_claude_md(tree), encoding="utf-8")
    print("synced. Review the diff and commit it in the target repo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
