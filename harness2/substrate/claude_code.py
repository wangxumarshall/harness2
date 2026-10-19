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

from ..core.tree import SurfaceSpec

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
