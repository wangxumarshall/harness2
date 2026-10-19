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

"""Focused tests for the claude_code substrate: surface, staging, trajectory rendering."""

import json
import tempfile
import unittest
from pathlib import Path

from harness2.core.spec import BenchPrompts, BenchSpec, resolve_channel_model
from harness2.core.tree import is_allowed
from harness2.substrate.claude_code import CLAUDE_CODE_SURFACE


def make_spec(install_mode: str = "claude_workspace", runs_root: Path | None = None) -> BenchSpec:
    ch = resolve_channel_model("claude_cli", "test-model")
    return BenchSpec(
        name="t",
        runs_root=runs_root or Path(tempfile.mkdtemp(prefix="cc-spec-")),
        install_mode=install_mode,
        prompts=BenchPrompts(work="test work"),
        channels={"proposer": ch, "selector": ch},
    )


class SurfaceTests(unittest.TestCase):
    def test_edit_contract_paths_are_on_the_surface(self):
        on = [
            "CLAUDE.md", "GUARDRAILS.md", "MEMORY.md", "TOOL_GUIDANCE.md",
            ".claude/skills/deploy-check/SKILL.md",
            ".claude/skills/deploy-check/scripts/run.py",
            "sub_agents/reviewer/AGENT.md",
            "scripts/helper.py",
            ".claude/settings.harness.json",
        ]
        for rel in on:
            self.assertTrue(is_allowed(CLAUDE_CODE_SURFACE, rel), rel)

    def test_off_surface_paths_are_rejected(self):
        off = [
            ".claude/settings.json",          # permissions live here; not editable
            ".claude/agents/reviewer.md",     # edit contract is sub_agents/, agents/ is staged
            "plugin/x.ts",                    # no plugin dir on this surface
            "evil.txt",
            "../escape.md",
            ".claude/settings.local.json",
        ]
        for rel in off:
            self.assertFalse(is_allowed(CLAUDE_CODE_SURFACE, rel), rel)

    def test_install_mode_registered(self):
        from harness2.core.spec import INSTALL_MODES

        self.assertIn("claude_workspace", INSTALL_MODES)

    def test_hook_events_cover_claude_code_set(self):
        self.assertIn("PreToolUse", CLAUDE_CODE_SURFACE.hook_events)
        self.assertIn("PostToolUse", CLAUDE_CODE_SURFACE.hook_events)
        self.assertIn("Stop", CLAUDE_CODE_SURFACE.hook_events)
        self.assertIn("SessionStart", CLAUDE_CODE_SURFACE.hook_events)


class ComposeTests(unittest.TestCase):
    def test_compose_joins_markers_and_skips_empty(self):
        from harness2.substrate.claude_code import compose_claude_md

        tree = {
            "CLAUDE.md": "# title\n",
            "GUARDRAILS.md": "be careful\n",
            "MEMORY.md": "",
            "TOOL_GUIDANCE.md": "tools here\n",
        }
        out = compose_claude_md(tree)
        self.assertIn("<!-- harness2:CLAUDE.md -->\n\n# title", out)
        self.assertIn("<!-- harness2:GUARDRAILS.md -->\n\nbe careful", out)
        self.assertIn("<!-- harness2:TOOL_GUIDANCE.md -->", out)
        self.assertNotIn("harness2:MEMORY.md", out)  # empty body skipped

    def test_leak_guard_json_shape(self):
        from harness2.substrate.claude_code import LEAK_GUARD_JSON

        doc = json.loads(LEAK_GUARD_JSON)
        self.assertEqual(set(doc), {"hooks"})
        pre = doc["hooks"]["PreToolUse"]
        matchers = [e.get("matcher") for e in pre]
        self.assertIn("WebFetch|WebSearch", matchers)
        self.assertIn("Bash", matchers)
        for entry in pre:
            for h in entry["hooks"]:
                self.assertEqual(h["type"], "command")
                self.assertTrue(h["command"].strip())

    def test_leakguard_script_denies_gh_pr(self):
        import os
        import subprocess
        import sys
        import tempfile

        from harness2.substrate.claude_code import LEAKGUARD_PY

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "g.py"
            p.write_text(LEAKGUARD_PY, encoding="utf-8")
            for blocked in ["gh pr view 3", "curl https://github.com/x/y", "git fetch origin"]:
                r = subprocess.run(
                    [sys.executable, str(p)],
                    input=json.dumps({"tool_input": {"command": blocked}}),
                    capture_output=True, text=True,
                )
                self.assertEqual(r.returncode, 2, blocked)
            r = subprocess.run(
                [sys.executable, str(p)],
                input=json.dumps({"tool_input": {"command": "ninja -C builddir"}}),
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
