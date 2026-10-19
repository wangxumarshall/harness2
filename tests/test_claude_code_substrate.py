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

from harness2.core import tree as HT
from harness2.core.spec import BenchPrompts, BenchSpec, resolve_channel_model
from harness2.core.tree import is_allowed
from harness2.substrate.claude_code import CLAUDE_CODE_SURFACE, LEAK_GUARD_JSON


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


class MaterialiseTests(unittest.TestCase):
    def _tree(self) -> dict[str, str]:
        return {
            "CLAUDE.md": "# t\n",
            "GUARDRAILS.md": "rules\n",
            "MEMORY.md": "m\n",
            "TOOL_GUIDANCE.md": "g\n",
            ".claude/skills/deploy/SKILL.md": "---\nname: deploy\ndescription: d\n---\nbody\n",
            "sub_agents/reviewer/AGENT.md": "---\ndescription: reviews\n---\nbe strict\n",
            "scripts/helper.py": "print('hi')\n",
            ".claude/settings.harness.json": json.dumps(
                {"hooks": {"PreToolUse": json.loads(LEAK_GUARD_JSON)["hooks"]["PreToolUse"]
                           + [{"matcher": "Write", "hooks": [{"type": "command", "command": "true"}]}]}}
            ),
        }

    def test_materialise_translates_tree(self):
        from harness2.substrate.claude_code import ClaudeCodeSubstrate

        with tempfile.TemporaryDirectory() as td:
            d = HT.write_tree(CLAUDE_CODE_SURFACE, self._tree(), Path(td) / "tree")
            sub = ClaudeCodeSubstrate()
            inst = sub.materialise(d, make_spec(runs_root=Path(td) / "runs"), {})
            staged = inst.staging_dir
            self.assertEqual(
                (staged / "CLAUDE.md").read_text(encoding="utf-8"),
                "<!-- harness2:CLAUDE.md -->\n\n# t\n\n"
                "<!-- harness2:GUARDRAILS.md -->\n\nrules\n\n"
                "<!-- harness2:MEMORY.md -->\n\nm\n\n"
                "<!-- harness2:TOOL_GUIDANCE.md -->\n\ng\n",
            )
            self.assertTrue((staged / ".claude/skills/deploy/SKILL.md").is_file())
            self.assertEqual(
                (staged / ".claude/agents/reviewer.md").read_text(encoding="utf-8"),
                "---\ndescription: reviews\n---\nbe strict\n",
            )
            self.assertTrue((staged / "scripts/helper.py").is_file())
            # tree's hooks (with the extra Write matcher) are kept verbatim
            self.assertIn('"Write"', (staged / ".claude/settings.harness.json").read_text())
            # leakguard ships with every install
            self.assertTrue((staged / "scripts/harness2_leakguard.py").is_file())
            self.assertTrue(inst.fingerprint)

    def test_materialise_defaults_hooks_and_overwrites_leakguard(self):
        from harness2.substrate.claude_code import LEAKGUARD_PY, ClaudeCodeSubstrate

        tree = self._tree()
        del tree[".claude/settings.harness.json"]
        tree["scripts/harness2_leakguard.py"] = "print('tampered')"  # must be overwritten
        with tempfile.TemporaryDirectory() as td:
            d = HT.write_tree(CLAUDE_CODE_SURFACE, tree, Path(td) / "tree")
            sub = ClaudeCodeSubstrate()
            inst = sub.materialise(d, make_spec(runs_root=Path(td) / "runs"), {})
            staged = inst.staging_dir
            self.assertEqual(
                (staged / ".claude/settings.harness.json").read_text(encoding="utf-8"),
                LEAK_GUARD_JSON,
            )
            self.assertEqual(
                (staged / "scripts/harness2_leakguard.py").read_text(encoding="utf-8"),
                LEAKGUARD_PY,
            )

    def test_materialise_rejects_wrong_mode_and_non_tree(self):
        from harness2.substrate.claude_code import ClaudeCodeSubstrate

        sub = ClaudeCodeSubstrate()
        with tempfile.TemporaryDirectory() as td:
            d = HT.write_tree(CLAUDE_CODE_SURFACE, {"CLAUDE.md": "x"}, Path(td) / "tree")
            with self.assertRaises(ValueError):
                sub.materialise(d, make_spec(install_mode="codex_root"), {})
            with self.assertRaises(ValueError):
                sub.materialise(Path(td) / "nope", make_spec(), {})


_STREAM = "\n".join(
    [
        "junk preamble line",
        json.dumps({"type": "system", "subtype": "init", "model": "glm", "cwd": "/ws"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "building now"},
                        {"type": "tool_use", "id": "t1", "name": "Bash",
                         "input": {"command": "ninja -C builddir"}},
                    ],
                },
            }
        ),
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1",
                         "content": "build ok", "is_error": False}
                    ],
                },
            }
        ),
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1",
                         "content": "boom", "is_error": True}
                    ],
                },
            }
        ),
        "{ torn json",
        json.dumps({"type": "result", "subtype": "success", "result": "done summary",
                    "total_cost_usd": 0.5, "num_turns": 3, "is_error": False}),
    ]
)


class RenderTests(unittest.TestCase):
    def _stream(self) -> Path:
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write(_STREAM)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return Path(f.name)

    def test_render_grammar(self):
        from harness2.substrate.claude_code import render_stream

        out = render_stream(self._stream(), obs_trunc=1000, max_chars=10_000)
        self.assertIn("[HARNESS] session init | model=glm", out)
        self.assertIn("[ASSISTANT] building now", out)
        self.assertIn("[ACTION] Bash: ", out)
        self.assertIn("ninja -C builddir", out)
        self.assertIn("[OBSERVATION] build ok", out)
        self.assertIn("[OBSERVATION ERROR] boom", out)
        self.assertIn("[HARNESS] result | turns=3 cost=$0.5 | done summary", out)

    def test_render_truncates_to_max_chars(self):
        from harness2.substrate.claude_code import render_stream

        out = render_stream(self._stream(), obs_trunc=1000, max_chars=120)
        self.assertLessEqual(len(out), 160)
        self.assertIn("render truncation", out)

    def test_final_message(self):
        from harness2.substrate.claude_code import final_message

        self.assertEqual(final_message(self._stream()), "done summary")

    def test_empty_stream(self):
        from harness2.substrate.claude_code import render_stream

        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write("")
        f.close()
        self.addCleanup(Path(f.name).unlink)
        out = render_stream(Path(f.name), obs_trunc=100, max_chars=1000)
        self.assertIn("no parseable events", out)

    def test_error_and_user_branches(self):
        from harness2.substrate.claude_code import render_stream

        out = render_stream(
            Path("/nonexistent/h2-trajectory.jsonl"),
            obs_trunc=100,
            max_chars=1000,
        )
        self.assertIn("[ERROR] failed to read trajectory", out)

        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "plain user note"}],
                    },
                }
            )
        )
        f.close()
        self.addCleanup(Path(f.name).unlink)
        out = render_stream(Path(f.name), obs_trunc=100, max_chars=1000)
        self.assertIn("[USER] plain user note", out)


if __name__ == "__main__":
    unittest.main()
