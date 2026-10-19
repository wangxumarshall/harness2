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

"""ClaudeCodeSubstrate: the claude CLI (headless -p) as a solver substrate.

Carries the c1..c8 surface (CLAUDE_CODE_SURFACE) and the one install mode
(claude_workspace): the four always-on files compose into one CLAUDE.md, sub_agents/
translates to .claude/agents/, skills stage in place, and the hooks file rides along as
a --settings overlay. The leak-guard hooks ship with EVERY install and are enforced
immutable by the sdcshield adapter's validate_tree.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.tree import SurfaceSpec
from .base import Install, Launcher, RunRequest

CLAUDE_CODE_SURFACE = SurfaceSpec(
    name="cc",
    always_on=("CLAUDE.md", "GUARDRAILS.md", "MEMORY.md", "TOOL_GUIDANCE.md"),
    always_on_max=24_000,
    always_on_cap_reason=(
        "CLAUDE.md rides in every turn's context; every token here competes with "
        "the agent's own output budget"
    ),
    skill_dir=".claude/skills",
    skill_name_rule="must_match",
    trigger_hint=(
        "Fix it by adding a trigger line naming `{name}` to GUARDRAILS.md, e.g. "
        '"When <the situation this skill handles>, load the `{name}` skill."'
    ),
    autotrigger_file="GUARDRAILS.md",
    trigger_line="- When {desc}, load the `{name}` skill.\n",
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file=".claude/settings.harness.json",
    hook_events=frozenset(
        {
            "PreToolUse",
            "PostToolUse",
            "Notification",
            "UserPromptSubmit",
            "Stop",
            "SubagentStop",
            "PreCompact",
            "SessionStart",
            "SessionEnd",
        }
    ),
    compile_skill_scripts=False,
    reject_empty_always_on=False,
    stubs=(
        (
            "scripts/README.md",
            "<!-- c7: deterministic helpers, run as `python3 scripts/<name>.py`. "
            "Add one by emitting scripts/<name>.py. -->\n",
        ),
        (
            "sub_agents/README.md",
            "<!-- c5: delegated subagents, each spawned in its OWN context. Add one as "
            "sub_agents/<name>/AGENT.md with frontmatter `description:`; it installs as "
            ".claude/agents/<name>.md. -->\n",
        ),
        (
            ".claude/skills/README.md",
            "<!-- c6: reusable workflows loaded on demand. Add one as "
            ".claude/skills/<name>/SKILL.md with frontmatter `name:` matching the "
            "directory and a `description:`. -->\n",
        ),
    ),
)


LEAK_GUARD_JSON = json.dumps(
    {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "WebFetch|WebSearch",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                "echo 'leakguard: network tools are disabled for this "
                                "task' >&2; exit 2"
                            ),
                        }
                    ],
                },
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 scripts/harness2_leakguard.py",
                        }
                    ],
                },
            ]
        }
    },
    indent=2,
)

LEAKGUARD_PY = r'''#!/usr/bin/env python3
"""PreToolUse guard for replayed tasks: block commands that can fetch the real PR."""
import json, re, sys

_DENY = re.compile(r"\bgh\s+pr\b|github\.com|\bgit\s+fetch\b|\bgit\s+remote\b", re.I)

try:
    _ev = json.load(sys.stdin)
except Exception:
    sys.exit(0)
cmd = str((_ev.get("tool_input") or {}).get("command", ""))
if _DENY.search(cmd):
    print(f"leakguard: blocked (replay isolation): {cmd[:120]}", file=sys.stderr)
    sys.exit(2)
sys.exit(0)
'''


def compose_claude_md(tree: dict[str, str]) -> str:
    """Join the four always-on bodies into one CLAUDE.md with marker comments."""
    parts = []
    for name in CLAUDE_CODE_SURFACE.always_on:
        body = tree.get(name, "").strip()
        if body:
            parts.append(f"<!-- harness2:{name} -->\n\n{body}")
    return ("\n\n".join(parts) + "\n") if parts else ""


class ClaudeCodeSubstrate:
    """claude CLI behind the one Substrate interface."""

    name = "cc"

    def __init__(self, launcher: Launcher | None = None):
        self._launcher = launcher

    def surface(self) -> SurfaceSpec:
        return CLAUDE_CODE_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install:
        """Stage the tree as a Claude Code workspace overlay: composed CLAUDE.md, in-place
        skills, translated agents, scripts, and the hooks settings file."""
        if spec.install_mode != "claude_workspace":
            raise ValueError(
                f"install_mode {spec.install_mode!r} is not the claude mode; this "
                "substrate has exactly one: 'claude_workspace'"
            )
        tree_dir = Path(tree_dir)
        if not HT.is_tree(CLAUDE_CODE_SURFACE, tree_dir):
            raise ValueError(f"{tree_dir} is not a claude_code harness tree (no always-on file)")
        tree = HT.read_tree(CLAUDE_CODE_SURFACE, tree_dir)
        staged = Path(tempfile.mkdtemp(prefix="adapt-cc-ws-"))
        (staged / "CLAUDE.md").write_text(compose_claude_md(tree), encoding="utf-8")
        for rel, content in tree.items():
            if rel.startswith(".claude/skills/") or rel.startswith("scripts/"):
                dst = staged / rel
            elif rel.startswith("sub_agents/") and rel.endswith("/AGENT.md"):
                name = rel[len("sub_agents/") : -len("/AGENT.md")]
                dst = staged / ".claude" / "agents" / f"{name}.md"
            elif rel == CLAUDE_CODE_SURFACE.hooks_file:
                dst = staged / rel
            else:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content, encoding="utf-8")
        hooks_dst = staged / CLAUDE_CODE_SURFACE.hooks_file
        if not hooks_dst.is_file():
            hooks_dst.parent.mkdir(parents=True, exist_ok=True)
            hooks_dst.write_text(LEAK_GUARD_JSON, encoding="utf-8")
        guard = staged / "scripts" / "harness2_leakguard.py"
        guard.parent.mkdir(parents=True, exist_ok=True)
        guard.write_text(LEAKGUARD_PY, encoding="utf-8")  # written LAST: never tree-replaceable
        return Install(
            staging_dir=staged,
            fingerprint=RM.fingerprint(tree_dir),
            extras={
                "install_mode": "claude_workspace",
                **{k: v for k, v in ctx.items() if not k.startswith("file:")},
            },
        )
