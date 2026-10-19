# Claude Code Substrate + sdcshield Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Claude Code (`claude -p` headless) as a third solver substrate and sdcshield PR-replay as a fourth benchmark to Harness², so the user's Claude Code + GLM stack recursively improves its own harness (CLAUDE.md, skills, agents, hooks, scripts).

**Architecture:** Two new modules — `harness2/substrate/claude_code.py` (CLAUDE_CODE_SURFACE + staging/trajectory/preflight, mirroring `codex.py`) and `harness2/benchmarks/sdcshield.py` (SdShieldAdapter: PR replay in worktrees + three-layer scoring L1 meson build / L2 meson test / L3 board command). The method layer (recursion/selection/library/ledger/schedule) is reused unchanged; the proposer/selector improvement channel uses the existing `claude_cli` path (`core/proposer.py:210`).

**Tech Stack:** Python 3.12 (stdlib only — no new dependencies), git worktrees, `claude` CLI with stream-json output, meson/ninja, unittest.

**Spec:** `docs/superpowers/specs/2026-10-19-claude-code-substrate-design.md`

## Global Constraints

- Python >= 3.12, stdlib only; no new entries in `pyproject.toml` dependencies.
- Every user-visible string that a BenchPrompts field carries must NOT contain the words benchmark / evaluation / grader / rubric / score (`core/spec.py:111-123` enforces).
- Method layer (`core/recursion.py`, `core/selection.py`, `core/library.py`, `core/ledger.py`, `core/schedule.py`) must not be modified. Allowed core touches: `INSTALL_MODES` tuple (`core/spec.py:129`), `proposer.py` claude_cli env block, `cli/_common.py` dispatch tables, `cli/check.py` choices, `cli/run_sweep.py` metadata lines, `config.py` additions.
- Line length 100, ruff rules E4/E7/E9/F/I (`pyproject.toml`).
- `score()` contract: judge failure returns `None`, never a zero, never cached (`core/spec.py:247-250`).
- The leak-guard PreToolUse hooks are immutable: every adopted tree must carry them verbatim; `settings.harness.json` may contain ONLY the `hooks` key (never `permissions`/`env`).
- Tests run offline: no `claude` calls, no network, no board access in unit tests.
- Test invocation form (tests/ has no `__init__.py`): `python3 -m unittest discover -s tests -t . -p "<pattern>" -v`

## File Structure

```
harness2/substrate/claude_code.py      # NEW: CLAUDE_CODE_SURFACE, leak-guard constants,
                                       #      compose_claude_md, stream renderer, ClaudeCodeSubstrate
harness2/benchmarks/sdcshield.py       # NEW: SD_BASE prompts, sd_spec, PR enumeration,
                                       #      SdShieldAdapter (launcher + validate_tree + score)
tests/test_claude_code_substrate.py    # NEW: surface/staging/renderer tests
tests/test_sdcshield_adapter.py        # NEW: PR-cache/domain/validate/score-shell tests (git fixture)
scripts/sync_sd_harness.py             # NEW: deploy adopted tree into the sdcshield repo
scripts/sd_rollback_guard.py           # NEW: drop library adoptions that regressed (spec §7)
docs/SDCSHIELD.md                      # NEW: run guide + board-command contract
harness2/core/spec.py                  # MOD: INSTALL_MODES += "claude_workspace"
harness2/core/proposer.py              # MOD: claude_cli env respects ANTHROPIC_BASE_URL
harness2/config.py                     # MOD: SD_*/CC_* settings
harness2/substrate/__init__.py         # MOD: exports
harness2/cli/_common.py                # MOD: BENCHES/SUBSTRATES/make_adapter
harness2/cli/check.py                  # MOD: choices
harness2/cli/run_sweep.py              # MOD: executor/endpoint metadata
```

---

### Task 1: `claude_workspace` install mode + `CLAUDE_CODE_SURFACE`

**Files:**
- Modify: `harness2/core/spec.py:129`
- Create: `harness2/substrate/claude_code.py` (surface only in this task)
- Modify: `harness2/substrate/__init__.py`
- Test: `tests/test_claude_code_substrate.py`

**Interfaces:**
- Consumes: `harness2.core.tree.SurfaceSpec`, `tree.is_allowed`
- Produces: `CLAUDE_CODE_SURFACE: SurfaceSpec` with `name="cc"`, `always_on=("CLAUDE.md", "GUARDRAILS.md", "MEMORY.md", "TOOL_GUIDANCE.md")`, `skill_dir=".claude/skills"`, `hooks_file=".claude/settings.harness.json"`, `hook_events` = the Claude Code event set. `"claude_workspace"` is a valid `INSTALL_MODES` member.

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_code_substrate.py`:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_claude_code_substrate.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness2.substrate.claude_code'`

- [ ] **Step 3: Write minimal implementation**

In `harness2/core/spec.py`, change line 129:

```python
INSTALL_MODES = (
    "workspace_agents_md",
    "home_dir_jinja",
    "workspace_config",
    "codex_root",
    "claude_workspace",
)
```

Create `harness2/substrate/claude_code.py` with the license header (copy from `harness2/substrate/codex.py:1-13`), this docstring, and the surface:

```python
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
```

In `harness2/substrate/__init__.py`, add to the imports and `__all__` (in THIS task only `CLAUDE_CODE_SURFACE`; `ClaudeCodeSubstrate` joins in Task 3):

```python
from .claude_code import CLAUDE_CODE_SURFACE
```

```python
    "CLAUDE_CODE_SURFACE",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_claude_code_substrate.py" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add harness2/core/spec.py harness2/substrate/claude_code.py harness2/substrate/__init__.py tests/test_claude_code_substrate.py
git commit -m "feat(cc): claude_workspace install mode and CLAUDE_CODE_SURFACE"
```

---

### Task 2: compose + leak-guard constants

**Files:**
- Modify: `harness2/substrate/claude_code.py`
- Test: `tests/test_claude_code_substrate.py`

**Interfaces:**
- Consumes: `CLAUDE_CODE_SURFACE.always_on`
- Produces:
  - `compose_claude_md(tree: dict[str, str]) -> str` — joins the four always-on bodies with `<!-- harness2:<name> -->` markers, skips empty bodies
  - `LEAK_GUARD_JSON: str` — the base hooks file content (PreToolUse deny for WebFetch/WebSearch + Bash guard)
  - `LEAKGUARD_PY: str` — source of `scripts/harness2_leakguard.py` (blocks `gh pr`, `github.com`, `git fetch`, `git remote` in Bash tool input)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_claude_code_substrate.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_claude_code_substrate.py" -v`
Expected: FAIL with `ImportError: cannot import name 'compose_claude_md'`

- [ ] **Step 3: Write minimal implementation**

Add `import json` to the module imports of `harness2/substrate/claude_code.py`, then append:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_claude_code_substrate.py" -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add harness2/substrate/claude_code.py tests/test_claude_code_substrate.py
git commit -m "feat(cc): CLAUDE.md composition and immutable leak-guard hooks"
```

---

### Task 3: `materialise` — tree to workspace overlay

**Files:**
- Modify: `harness2/substrate/claude_code.py`
- Modify: `harness2/substrate/__init__.py`
- Test: `tests/test_claude_code_substrate.py`

**Interfaces:**
- Consumes: `HT.read_tree`, `HT.is_tree`, `RM.fingerprint`, `Install` from `.base`
- Produces: `ClaudeCodeSubstrate.materialise(tree_dir: Path, spec, ctx: Mapping[str, str]) -> Install`. Staging layout: `CLAUDE.md` (composed), `.claude/skills/<n>/...` (in place), `.claude/agents/<n>.md` (translated from `sub_agents/<n>/AGENT.md`), `scripts/<n>.py`, `.claude/settings.harness.json` (tree's copy, or `LEAK_GUARD_JSON` when absent), `scripts/harness2_leakguard.py` (ALWAYS overwritten from `LEAKGUARD_PY`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_claude_code_substrate.py` (and add `LEAK_GUARD_JSON` to the module-level import from `harness2.substrate.claude_code`):

```python
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
```

Add `from harness2.core import tree as HT` to the test module imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_claude_code_substrate.py" -v`
Expected: FAIL with `ImportError: cannot import name 'ClaudeCodeSubstrate'`

- [ ] **Step 3: Write minimal implementation**

Extend imports in `harness2/substrate/claude_code.py`:

```python
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
```

Append the class (execute/read_trajectory/preflight bodies come in Task 5):

```python
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
```

Update `harness2/substrate/__init__.py` to also import and export `ClaudeCodeSubstrate`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_claude_code_substrate.py" -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add harness2/substrate/claude_code.py harness2/substrate/__init__.py tests/test_claude_code_substrate.py
git commit -m "feat(cc): materialise translates the tree into a workspace overlay"
```

---

### Task 4: stream-json trajectory renderer

**Files:**
- Modify: `harness2/substrate/claude_code.py`
- Test: `tests/test_claude_code_substrate.py`

**Interfaces:**
- Consumes: nothing new
- Produces:
  - `parse_stream(path: Path) -> list[dict]` — JSONL events, junk tolerated, `{"type": "_read_error", "message": ...}` on OSError
  - `render_stream(path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400) -> str` — shared `[ACTION]/[OBSERVATION]/[OBSERVATION ERROR]/[ASSISTANT]/[USER]/[HARNESS]/[ERROR]` grammar
  - `final_message(path: Path) -> str` — text of the last `result` event

- [ ] **Step 1: Write the failing test**

Append to `tests/test_claude_code_substrate.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_claude_code_substrate.py" -v`
Expected: FAIL with `ImportError: cannot import name 'render_stream'`

- [ ] **Step 3: Write minimal implementation**

Append to `harness2/substrate/claude_code.py`:

```python
_TRUNC_FMT = "…(+{n} chars — render truncation)"


def _clip(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = s.replace("\x00", "")
    if n <= 0 or len(s) <= n:
        return s
    return s[:n] + _TRUNC_FMT.format(n=len(s) - n)


def parse_stream(path: Path) -> list[dict]:
    """Read the claude -p --output-format stream-json stream, junk tolerated."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [{"type": "_read_error", "message": str(e)}]
    events: list[dict] = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def _content_items(ev: dict) -> list[dict]:
    msg = ev.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, list):
        return [c for c in content if isinstance(c, dict)]
    if isinstance(content, str) and content.strip():
        return [{"type": "text", "text": content}]
    return []


def _render_event(ev: dict, lines: list[str], obs_trunc: int, args_trunc: int) -> None:
    et = ev.get("type", "")
    if et == "assistant":
        for c in _content_items(ev):
            if c.get("type") == "tool_use":
                lines.append(
                    f"[ACTION] {c.get('name', 'tool')}: "
                    + _clip(json.dumps(c.get("input", {}), ensure_ascii=False), args_trunc)
                )
            elif c.get("type") == "text" and str(c.get("text", "")).strip():
                lines.append(f"[ASSISTANT] {_clip(c.get('text'), obs_trunc)}")
        return
    if et == "user":
        for c in _content_items(ev):
            if c.get("type") == "tool_result":
                out = c.get("content")
                if isinstance(out, list):
                    out = " ".join(
                        str(x.get("text", "")) for x in out if isinstance(x, dict)
                    )
                err = " ERROR" if c.get("is_error") else ""
                lines.append(f"[OBSERVATION{err}] {_clip(out, obs_trunc)}")
            elif c.get("type") == "text" and str(c.get("text", "")).strip():
                lines.append(f"[USER] {_clip(c.get('text'), obs_trunc)}")
        return
    if et == "system":
        if ev.get("subtype") == "init":
            lines.append(
                f"[HARNESS] session init | model={ev.get('model', '?')} "
                f"cwd={ev.get('cwd', '?')}"
            )
        return
    if et == "result":
        lines.append(
            "[HARNESS] result | turns={t} cost=${c} | {r}".format(
                t=ev.get("num_turns"),
                c=ev.get("total_cost_usd"),
                r=_clip(ev.get("result"), 1000),
            )
        )
        return
    if et == "_read_error":
        lines.append(f"[ERROR] failed to read trajectory: {ev.get('message')}")


def render_stream(
    path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400
) -> str:
    """Render a claude stream-json session into the shared grammar."""
    lines: list[str] = []
    for ev in parse_stream(Path(path)):
        _render_event(ev, lines, obs_trunc, args_trunc)
    rendered = "\n".join(lines).strip()
    if not rendered:
        return "(no parseable events in trajectory)"
    if len(rendered) > max_chars:
        dropped = len(rendered) - max_chars
        rendered = rendered[:max_chars] + "\n" + _TRUNC_FMT.format(n=dropped)
    return rendered


def final_message(path: Path) -> str:
    """Text of the last result event: the deliverable summary."""
    last = ""
    for ev in parse_stream(Path(path)):
        if ev.get("type") == "result":
            txt = str(ev.get("result") or "")
            if txt.strip():
                last = txt
    return last
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_claude_code_substrate.py" -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add harness2/substrate/claude_code.py tests/test_claude_code_substrate.py
git commit -m "feat(cc): stream-json trajectory renderer in the shared grammar"
```

---

### Task 5: `execute` + `preflight` (substrate class complete)

**Files:**
- Modify: `harness2/substrate/claude_code.py`
- Test: `tests/test_claude_code_substrate.py`

**Interfaces:**
- Consumes: `RunRequest`, `Launcher` from `.base`
- Produces: `ClaudeCodeSubstrate.execute(task_ref: str, install: Install, run_ids: list[str], timeout: int) -> list[Path]` (delegates to the adapter-supplied launcher; `RuntimeError` without one); `preflight() -> None` (claude on PATH + `--version` works); `read_trajectory(stream_path, *, obs_trunc, max_chars, args_trunc=400) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_claude_code_substrate.py`:

```python
class ExecuteTests(unittest.TestCase):
    def test_execute_requires_launcher(self):
        from harness2.substrate.base import Install
        from harness2.substrate.claude_code import ClaudeCodeSubstrate

        sub = ClaudeCodeSubstrate()
        with tempfile.TemporaryDirectory() as td:
            inst = Install(staging_dir=Path(td), fingerprint="f")
            with self.assertRaises(RuntimeError):
                sub.execute("pr-1", inst, ["r1"], 60)

    def test_execute_delegates_to_launcher(self):
        from harness2.substrate.base import Install, RunRequest
        from harness2.substrate.claude_code import ClaudeCodeSubstrate

        seen = []

        def launcher(req: RunRequest, install: Install) -> list[Path]:
            seen.append((req.task_ref, req.run_ids, req.timeout, install.fingerprint))
            return [Path("/tmp/r1")]

        sub = ClaudeCodeSubstrate(launcher=launcher)
        with tempfile.TemporaryDirectory() as td:
            inst = Install(staging_dir=Path(td), fingerprint="f")
            out = sub.execute("pr-9", inst, ["r1"], 120)
        self.assertEqual(out, [Path("/tmp/r1")])
        self.assertEqual(seen, [("pr-9", ("r1",), 120, "f")])

    def test_read_trajectory_delegates(self):
        from harness2.substrate.claude_code import ClaudeCodeSubstrate

        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        f.write(_STREAM)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        sub = ClaudeCodeSubstrate()
        out = sub.read_trajectory(Path(f.name), obs_trunc=1000, max_chars=10_000)
        self.assertIn("[ACTION] Bash:", out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_claude_code_substrate.py" -v`
Expected: FAIL with `AttributeError: 'ClaudeCodeSubstrate' object has no attribute 'execute'`

- [ ] **Step 3: Write minimal implementation**

Append inside `ClaudeCodeSubstrate` (after `materialise`):

```python
    def execute(
        self, task_ref: str, install: Install, run_ids: list[str], timeout: int
    ) -> list[Path]:
        if self._launcher is None:
            raise RuntimeError(
                "ClaudeCodeSubstrate was constructed without a launcher body; the "
                "benchmark adapter must supply one (it owns worktree, env scrubbing "
                "and read-back)"
            )
        return self._launcher(
            RunRequest(task_ref=task_ref, run_ids=tuple(run_ids), timeout=int(timeout)),
            install,
        )

    def read_trajectory(
        self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400
    ) -> str:
        return render_stream(
            Path(stream_path), obs_trunc=obs_trunc, max_chars=max_chars, args_trunc=args_trunc
        )

    def preflight(self) -> None:
        """The claude CLI must exist and run; the model chain is pinged by the adapter."""
        cli = shutil.which("claude")
        if cli is None:
            raise RuntimeError("claude CLI not found on PATH; install Claude Code first")
        r = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"claude --version failed: {(r.stderr or '')[-200:]}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_claude_code_substrate.py" -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
git add harness2/substrate/claude_code.py tests/test_claude_code_substrate.py
git commit -m "feat(cc): execute launcher protocol and preflight"
```

---

### Task 6: config entries + claude_cli env fix

**Files:**
- Modify: `harness2/config.py` (after the CODEX block, ~line 163)
- Modify: `harness2/core/proposer.py` (module-level helper + `env()` at line 105)
- Test: `tests/test_sdcshield_adapter.py`

**Interfaces:**
- Consumes: `config.env`, `config.env_int`
- Produces (config.py module attrs, read at call time so tests can monkeypatch):
  - `SD_REPO: Path` — env `HARNESS2_SD_REPO`, default `<repo>/../sdcshield`
  - `SD_MAX_TURNS: int` — env `HARNESS2_SD_MAX_TURNS`, default 200
  - `CC_IMPROVER_MODEL: str` — env `HARNESS2_CC_IMPROVER_MODEL`, default `"glm-5.3"` (label only; the CLI's own model config wins)
  - `SD_BOARD_CMD: str` — env `HARNESS2_SD_BOARD_CMD`, default `""`
  - `SD_SKIP_BOARD: bool` — env `HARNESS2_SD_SKIP_BOARD`, default False
  - `SD_TEST_CMD: str` — env `HARNESS2_SD_TEST_CMD`, default `""`
- Produces (proposer.py): `claude_cli_env(base: dict, project: str, region: str, model: str) -> dict` — Vertex wiring UNLESS `ANTHROPIC_BASE_URL` is already set (custom endpoint / GLM bridge wins).

- [ ] **Step 1: Write the failing test**

Create `tests/test_sdcshield_adapter.py`:

```python
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

"""Offline tests for the sdcshield adapter: PR cache, domains, validation, scoring shell."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def make_repo(base: Path) -> Path:
    """A one-PR sdcshield lookalike: framework/cpu change merged as PR #1."""
    repo = base / "sdcshield"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "CLAUDE.md").write_text("# seed harness\n", encoding="utf-8")
    (repo / "framework" / "cpu").mkdir(parents=True)
    (repo / "framework" / "cpu" / "det.c").write_text("int main(void){return 0;}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "checkout", "-b", "feat/one")
    (repo / "framework" / "cpu" / "det.c").write_text("int main(void){return 1;}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feat one")
    _git(repo, "checkout", "main")
    _git(
        repo, "merge", "--no-ff", "-m",
        "Merge pull request #1 from wangxu/feat-one", "feat/one",
    )
    return repo


class EnvFixture(unittest.TestCase):
    """Patches config module attrs (read at call time) to a throwaway repo+runs root."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.repo = make_repo(self.base)
        self.runs = self.base / "runs"
        for p in (patch.object(config, "SD_REPO", self.repo),
                  patch.object(config, "RUNS", self.runs)):
            p.start()
            self.addCleanup(p.stop)

    def _adapter(self):
        from harness2.benchmarks.sdcshield import SdShieldAdapter

        return SdShieldAdapter(split_file=None)


class ConfigTests(EnvFixture):
    def test_sd_settings_exist(self):
        self.assertIsInstance(config.SD_REPO, Path)
        self.assertGreater(config.SD_MAX_TURNS, 0)
        self.assertTrue(config.CC_IMPROVER_MODEL)
        self.assertIsInstance(config.SD_SKIP_BOARD, bool)

    def test_claude_cli_env_respects_custom_endpoint(self):
        from harness2.core.proposer import claude_cli_env

        base = {"PATH": "/bin"}
        with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "http://glm:9100"}, clear=False):
            e = claude_cli_env(base, "proj", "global", "m")
        self.assertNotIn("CLAUDE_CODE_USE_VERTEX", e)
        self.assertEqual(e["PATH"], "/bin")

        env_now = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_BASE_URL"}
        with patch.dict(os.environ, env_now, clear=True):
            e = claude_cli_env(base, "proj", "global", "m")
        self.assertEqual(e["CLAUDE_CODE_USE_VERTEX"], "1")
        self.assertEqual(e["ANTHROPIC_MODEL"], "m")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: FAIL with `AttributeError: module 'harness2.config' has no attribute 'SD_REPO'` (and `claude_cli_env` missing)

- [ ] **Step 3: Write minimal implementation**

In `harness2/config.py`, after the `CODEX_HOME_DIR` line (~163):

```python
SD_REPO = Path(env("HARNESS2_SD_REPO", str(ROOT.parent / "sdcshield"))).expanduser().resolve()
SD_MAX_TURNS = env_int("HARNESS2_SD_MAX_TURNS", 200)
CC_IMPROVER_MODEL = env("HARNESS2_CC_IMPROVER_MODEL", "glm-5.3")
SD_BOARD_CMD = env("HARNESS2_SD_BOARD_CMD", "")
SD_SKIP_BOARD = env("HARNESS2_SD_SKIP_BOARD", "") not in ("", "0", "false")
SD_TEST_CMD = env("HARNESS2_SD_TEST_CMD", "")
```

In `harness2/core/proposer.py`, add a module-level helper (after the imports):

```python
def claude_cli_env(
    base: dict[str, str], project: str, region: str, model: str
) -> dict[str, str]:
    """The claude_cli channel env: Vertex wiring, unless the CLI already targets a
    custom endpoint (e.g. a GLM bridge via ANTHROPIC_BASE_URL) — then the CLI's own
    configuration wins and must not be overridden."""
    e = dict(base)
    if os.environ.get("ANTHROPIC_BASE_URL"):
        return e
    e.update(
        {
            "CLAUDE_CODE_USE_VERTEX": "1",
            "ANTHROPIC_VERTEX_PROJECT_ID": project,
            "CLOUD_ML_REGION": region,
            "ANTHROPIC_MODEL": model,
        }
    )
    return e
```

Then replace the `claude_cli` branch of `Proposer.env` (lines 107-118) with:

```python
        if self.spec.kind == "claude_cli":
            e = claude_cli_env(e, self.project, self.region, self.spec.model)
        else:
```

(the `else` branch and everything after stays unchanged).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add harness2/config.py harness2/core/proposer.py tests/test_sdcshield_adapter.py
git commit -m "feat(sd): SD/CC config entries; claude_cli channel respects custom endpoints"
```

---

### Task 7: sdcshield prompts, spec, PR enumeration

**Files:**
- Create: `harness2/benchmarks/sdcshield.py`
- Test: `tests/test_sdcshield_adapter.py`

**Interfaces:**
- Consumes: `BenchPrompts`, `BenchSpec`, `resolve_channel_model`, `config.*`
- Produces:
  - `SD_BASE: BenchPrompts`
  - `sd_spec() -> BenchSpec` (name="sd", install_mode="claude_workspace", reserved_scripts=frozenset({"harness2_leakguard.py"}), allow_subagents=True)
  - `_domain_of(files: list[str], title: str) -> str`
  - `_strip_criteria(body: str) -> str`
  - `_merged_prs() -> list[dict]` — records with keys `number, merge_sha, base_sha, title, body, files, domain`, cached at `<runs_root>/_tasks/prs.json`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sdcshield_adapter.py`:

```python
class DomainTests(unittest.TestCase):
    def test_path_and_title_heuristics(self):
        from harness2.benchmarks.sdcshield import _domain_of

        self.assertEqual(_domain_of(["framework/cpu/det.c"], "feat: x"), "detector-cpu")
        self.assertEqual(_domain_of(["framework/gpu/g.c"], "fix"), "detector-gpu")
        self.assertEqual(_domain_of(["framework/idxd/i.c"], "fix"), "detector-idxd")
        self.assertEqual(_domain_of(["framework/build.c"], "fix"), "framework")
        self.assertEqual(_domain_of(["bats/testenv.bash"], "fix"), "testing")
        self.assertEqual(_domain_of(["tests/cpu/x.c"], "fix"), "testing")
        self.assertEqual(_domain_of(["docs/x.md"], "feat: y"), "docs")
        self.assertEqual(_domain_of(["anything"], "docs: polish"), "docs")
        self.assertEqual(_domain_of(["README.md"], "chore"), "misc")

    def test_strip_criteria_removes_checklists(self):
        from harness2.benchmarks.sdcshield import _strip_criteria

        body = "Add a detector.\n\n## Checklist\n- [x] built\n- [ ] tested\n\n## Why\nbecause\n"
        out = _strip_criteria(body)
        self.assertNotIn("Checklist", out)
        self.assertNotIn("[x]", out)
        self.assertIn("Add a detector.", out)
        self.assertIn("because", out)


class PrEnumerationTests(EnvFixture):
    def test_merged_prs_cached_and_shaped(self):
        from harness2.benchmarks.sdcshield import _merged_prs

        records = _merged_prs()
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r["number"], 1)
        self.assertEqual(r["domain"], "detector-cpu")
        self.assertEqual(r["files"], ["framework/cpu/det.c"])
        self.assertTrue(r["base_sha"])
        self.assertNotEqual(r["base_sha"], r["merge_sha"])
        cache = self.runs / "sd" / "_tasks" / "prs.json"
        self.assertTrue(cache.is_file())
        self.assertEqual(_merged_prs(), records)  # cached read is stable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'harness2.benchmarks.sdcshield'`

- [ ] **Step 3: Write minimal implementation**

Create `harness2/benchmarks/sdcshield.py` (license header as in `harness2/benchmarks/lab.py:1-13`), then:

```python
"""sdcshield adapter: PR replay on the claude_code substrate.

Task = one merged PR of the sdcshield repository, replayed from its base commit in a
fresh worktree. L1 build + L2 tests run locally; L3 boards run a user-supplied fleet
command. The judge layer never sees the real PR's diff.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .. import config
from ..core.spec import BenchPrompts, BenchSpec, resolve_channel_model

SD_BASE = BenchPrompts(
    work=(
        "C development on sdcshield, a silent-data-corruption detection suite for "
        "server hardware: CPU/GPU/IDXd detectors, meson build tooling, the test suite, "
        "and fleet deployment automation. The repository at the current directory is "
        "the source of truth. A change is done when it builds cleanly, the test suite "
        "passes, and the tree is left consistent."
    ),
    domain_word="component",
    deliverable_word="change",
    inv_levers=(
        "- read the meson build definitions and the existing component before editing\n"
        "- reproduce the reported behaviour with the smallest build or test invocation "
        "before changing code\n"
        "- keep changes minimal and consistent with the file's existing style\n"
        "- run a build and the relevant tests on the result before finishing\n"
    ),
    dom_levers=(
        "- follow the component's error-handling and logging conventions\n"
        "- prefer data-driven config over new code paths where the suite already does so\n"
        "- keep detector logic, build wiring and test fixtures in their own files\n"
    ),
    yardstick=(
        "Did the run make the requested change, build it, run the relevant tests, and "
        "leave the tree consistent?"
    ),
    selection_checks=(
        "Prefer the run whose change builds, passes the tests, matches the request, "
        "and avoids unrelated churn."
    ),
)

_DOMAIN_BY_PREFIX = {
    "framework/cpu": "detector-cpu",
    "framework/gpu": "detector-gpu",
    "framework/idxd": "detector-idxd",
    "framework": "framework",
    "bats": "testing",
    "tests": "testing",
    "scripts": "tooling",
    "docs": "docs",
    "configs": "configs",
    "sdc-fleet-orchestration": "orchestration",
}

_MERGE_RE = re.compile(r"Merge pull request #(\d+)")
_TODO_RE = re.compile(r"^[-*]\s+\[[ xX]\]")


def _domain_of(files: list[str], title: str) -> str:
    if (title or "").lower().startswith("doc"):
        return "docs"
    for f in sorted(files):
        for prefix, domain in _DOMAIN_BY_PREFIX.items():
            if f == prefix or f.startswith(prefix + "/"):
                return domain
    return "misc"


def _strip_criteria(body: str) -> str:
    """Drop checklist headings and checkbox lines; keep the substance."""
    lines, skip = [], False
    for ln in body.splitlines():
        low = ln.lower().strip()
        if low.startswith("#"):
            skip = "checklist" in low or "review" in low or "acceptance" in low
            if skip:
                continue
        if _TODO_RE.match(ln):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def sd_spec() -> BenchSpec:
    """The sdcshield BenchSpec: one cell, claude_code substrate."""
    channel = resolve_channel_model("claude_cli", config.CC_IMPROVER_MODEL, effort="high")
    return BenchSpec(
        name="sd",
        runs_root=config.RUNS / "sd",
        install_mode="claude_workspace",
        prompts=SD_BASE,
        channels={"proposer": channel, "selector": channel},
        domain_word="component",
        allow_subagents=True,
        reserved_scripts=frozenset({"harness2_leakguard.py"}),
        proposer_deliverables="contents",
        obs_trunc=50_000,
    )


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args[:2])} failed: {(r.stderr or '').strip()[-300:]}"
        )
    return r.stdout


def _gh_remote(repo: Path) -> str:
    url = _git(repo, "remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[/:]([^/]+/[^/.]+)", url)
    if not m:
        raise RuntimeError(f"cannot derive a GitHub repo from origin {url!r}")
    return m.group(1)


def _gh_pr_data(repo: Path) -> dict[int, dict]:
    """number -> {title, body} via gh, when available; empty when not."""
    gh = shutil.which("gh")
    if gh is None:
        return {}
    r = subprocess.run(
        [
            gh, "pr", "list", "-R", _gh_remote(repo), "--state", "merged",
            "--limit", "1000", "--json", "number,title,body",
        ],
        capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        return {}
    try:
        rows = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return {}
    return {int(row["number"]): row for row in rows if isinstance(row, dict)}


def _merged_prs() -> list[dict]:
    """Merged PRs of the sdcshield repo, oldest first; cached under runs_root/_tasks."""
    cache_file = sd_spec().runs_root / "_tasks" / "prs.json"
    if cache_file.is_file():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    repo = config.SD_REPO
    if not (repo / ".git").exists():
        raise RuntimeError(
            f"sdcshield repo missing at {repo}; set HARNESS2_SD_REPO (see .env.example)"
        )
    log = _git(
        repo, "log", "--merges", "--first-parent", "-n", "1000", "--pretty=%H%x1f%s"
    )
    gh = _gh_pr_data(repo)
    records = []
    for line in log.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\x1f")
        m = _MERGE_RE.search(subject)
        if not m:
            continue
        n = int(m.group(1))
        row = gh.get(n, {})
        title = str(row.get("title") or subject)
        body = _strip_criteria(str(row.get("body") or ""))
        base_sha = _git(repo, "rev-parse", f"{sha}^1").strip()
        files = [
            f.strip()
            for f in _git(repo, "diff", "--name-only", f"{sha}^1", sha).splitlines()
            if f.strip()
        ]
        records.append(
            {
                "number": n,
                "merge_sha": sha,
                "base_sha": base_sha,
                "title": title,
                "body": body,
                "files": files,
                "domain": _domain_of(files, title),
            }
        )
    records.sort(key=lambda r: r["number"])
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add harness2/benchmarks/sdcshield.py tests/test_sdcshield_adapter.py
git commit -m "feat(sd): prompts, spec and cached PR enumeration with domain mapping"
```

---

### Task 8: `SdShieldAdapter` — lifecycle and launcher

**Files:**
- Modify: `harness2/benchmarks/sdcshield.py`
- Test: `tests/test_sdcshield_adapter.py`

**Interfaces:**
- Consumes: `ClaudeCodeSubstrate`, `CLAUDE_CODE_SURFACE`, `LEAK_GUARD_JSON`, `parse_stream`, `HT.write_tree`, `RM.*`, `Install`/`RunRequest` from `..substrate.base`
- Produces: `SdShieldAdapter(*, substrate: str = "cc", model: str | None = None, timeout: int | None = None, split_file: Path | None = None)` implementing the `BenchmarkAdapter` protocol except `validate_tree` (Task 9) and `score` (Task 10). Run-dir layout: `workspace/` (the worktree), `claude_session.jsonl`, `diff.patch`, `metrics.json`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sdcshield_adapter.py`:

```python
class AdapterTests(EnvFixture):
    def test_tasks_domains_prompts(self):
        ad = self._adapter()
        self.assertEqual(ad.tasks(), ["pr-1"])
        self.assertEqual(ad.domain_of("pr-1"), "detector-cpu")
        prompt = ad.task_prompt("pr-1")
        self.assertIn("# TASK pr-1", prompt)
        self.assertIn("diff is the deliverable", prompt)
        self.assertIsNone(ad.task_files("pr-1"))

    def test_base_harness_carries_seed_and_leak_guard(self):
        from harness2.substrate.claude_code import LEAK_GUARD_JSON

        ad = self._adapter()
        d = ad.base_harness()
        self.assertIn("# seed harness", (d / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertEqual(
            (d / ".claude" / "settings.harness.json").read_text(encoding="utf-8"),
            LEAK_GUARD_JSON,
        )

    def test_install_base_vs_staged(self):
        ad = self._adapter()
        base = ad._install(None)
        self.assertEqual(base.fingerprint, "base")
        self.assertEqual(base.extras["harness"], "base")
        staged = ad._install(Path(ad.base_harness()))
        self.assertNotEqual(staged.fingerprint, "base")
        self.assertEqual(staged.extras["harness"], "staged")

    def test_sd_env_scrubs_credentials_and_isolates_config(self):
        from harness2.benchmarks.sdcshield import _sd_env

        with patch.dict(
            os.environ,
            {"GH_TOKEN": "x", "ANTHROPIC_BASE_URL": "http://glm", "PATH": "/bin"},
            clear=True,
        ):
            e = _sd_env(Path("/tmp/rd"))
        self.assertNotIn("GH_TOKEN", e)
        self.assertEqual(e["ANTHROPIC_BASE_URL"], "http://glm")
        self.assertEqual(e["CLAUDE_CONFIG_DIR"], "/tmp/rd/claude-home")

    def test_collect_writes_diff_and_metrics(self):
        run_dir = self.base / "rd"
        run_dir.mkdir()
        ws = run_dir / "workspace"
        ws.mkdir()
        (ws / "new.txt").write_text("hello\n", encoding="utf-8")
        _git(ws, "init", "-b", "main")
        _git(ws, "config", "user.email", "t@example.com")
        _git(ws, "config", "user.name", "t")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-m", "init")
        (ws / "new.txt").write_text("changed\n", encoding="utf-8")
        (run_dir / "claude_session.jsonl").write_text(
            json.dumps(
                {"type": "result", "subtype": "success", "result": "ok",
                 "total_cost_usd": 0.25, "num_turns": 2, "is_error": False}
            ),
            encoding="utf-8",
        )
        ad = self._adapter()
        ad._collect(run_dir, ws)
        diff = (run_dir / "diff.patch").read_text(encoding="utf-8")
        self.assertIn("changed", diff)
        m = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(m["num_turns"], 2)
        self.assertFalse(m["is_error"])
        self.assertTrue(ad.delivered(run_dir))
        self.assertIn("diff.patch", ad.deliverable_text(run_dir))

    def test_trajectory_reads_stream(self):
        run_dir = self.base / "rd2"
        run_dir.mkdir()
        (run_dir / "claude_session.jsonl").write_text(
            json.dumps({"type": "assistant", "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "hi"}]}}),
            encoding="utf-8",
        )
        ad = self._adapter()
        out = ad.trajectory(run_dir, obs_trunc=100)
        self.assertIn("[ASSISTANT] hi", out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: FAIL with `ImportError: cannot import name 'SdShieldAdapter'`

- [ ] **Step 3: Write minimal implementation**

Extend the imports of `harness2/benchmarks/sdcshield.py`:

```python
import hashlib
import os
import shutil
import signal
import subprocess
import tempfile
import threading

from ..core import runmark as RM
from ..core import tree as HT
from ..substrate.base import Install, RunRequest
from ..substrate.claude_code import (
    CLAUDE_CODE_SURFACE,
    ClaudeCodeSubstrate,
    LEAK_GUARD_JSON,
    parse_stream,
)
```

(merge into the existing block; drop duplicates). Then append:

```python
_BASE_LOCK = threading.Lock()
_BASE_DONE: set[Path] = set()

_SD_DROP_ENV = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_HOST",
        "GITHUB_API_URL",
        "GITHUB_REPOSITORY",
        "CLAUDE_CODE_USE_VERTEX",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLOUD_ML_REGION",
    }
)


def _sd_env(run_dir: Path) -> dict[str, str]:
    """Scrubbed environment for a rollout: no GitHub credentials, isolated config."""
    e = {k: v for k, v in os.environ.items() if k not in _SD_DROP_ENV}
    e["CLAUDE_CONFIG_DIR"] = str(Path(run_dir) / "claude-home")
    return e


def _run_claude(cmd, *, cwd, env, stdin_text, stdout, timeout):
    """Run the claude CLI in its own process group; kill the group on wall timeout."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=stdout,
        stderr=subprocess.PIPE, start_new_session=True, text=True,
    )
    try:
        _, err = proc.communicate(input=stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.communicate()
        raise
    if err and err.strip():
        print(f"  [sd-cc] claude stderr: {err.strip()[-300:]}", flush=True)


class SdShieldAdapter:
    """The BenchmarkAdapter surface for sdcshield PR replay, on the claude_code substrate."""

    def __init__(
        self,
        *,
        substrate: str = "cc",
        model: str | None = None,
        timeout: int | None = None,
        split_file: Path | None = None,
    ):
        if substrate != "cc":
            raise ValueError(f"sdcshield runs on the cc substrate only, got {substrate!r}")
        self.split_file = split_file
        self.spec = sd_spec()
        self.substrate = ClaudeCodeSubstrate(launcher=self._launch_cc)
        self.model = model or "claude-cli-default"
        self.timeout = int(timeout or 7200)
        self._results_root = self.spec.runs_root / "results" / "cc"
        self._records: dict[int, dict] | None = None

    # -- task stream ---------------------------------------------------------

    def _records_map(self) -> dict[int, dict]:
        if self._records is None:
            self._records = {r["number"]: r for r in _merged_prs()}
        return self._records

    def tasks(self, split: str = "all") -> list[str]:
        mapping = {f"pr-{n}": r["domain"] for n, r in self._records_map().items()}
        return select(mapping, split, self.split_file, benchmark="sd")

    def domain_of(self, task_id: str) -> str:
        return self._record(task_id)["domain"]

    def _record(self, task_id: str) -> dict:
        try:
            n = int(task_id.split("-", 1)[1])
        except (IndexError, ValueError):
            raise KeyError(f"bad task id {task_id!r}; expected pr-<number>") from None
        try:
            return self._records_map()[n]
        except KeyError:
            raise KeyError(f"unknown task {task_id!r}") from None

    def task_prompt(self, task_id: str, *, max_chars: int = 12_000) -> str:
        r = self._record(task_id)
        parts = [f"# TASK {task_id}", f"**Title:** {r['title']}"]
        if r["body"]:
            parts.append("## Work order\n" + r["body"])
        parts.append(
            "## Notes\n"
            "- The repository at the current directory is the working tree.\n"
            "- Make the change directly in the tree; the resulting diff is the "
            "deliverable.\n"
            "- Build and run the relevant tests before finishing."
        )
        return "\n\n".join(parts)[:max_chars]

    def task_files(self, task_id: str) -> Path | None:
        return None

    # -- rollout lifecycle ---------------------------------------------------

    def _run_dir(self, run_id: str) -> Path:
        return self._results_root / run_id

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        out = self._run_dir(run_id)
        if RM.is_complete(out, harness_dir=harness_dir) and self.artifacts_intact(out):
            return out
        if out.is_dir() and any(out.iterdir()):
            reason = RM.why_not(out, harness_dir=harness_dir)
            print(f"  [sd-cc] re-rolling {run_id}: {reason}", flush=True)
            self._safe_rmtree(out)
        install = self._install(harness_dir)
        try:
            self.substrate.execute(
                task_id, install, [run_id], int(timeout or self.timeout)
            )
        finally:
            if install.extras.get("harness") == "staged":
                shutil.rmtree(install.staging_dir, ignore_errors=True)
        if not (out / "metrics.json").is_file():
            raise RuntimeError(
                f"rollout produced no metrics.json: {out} — the claude session stream "
                "had no result event (dead chain / missing CLI), so this failure must "
                "not be cached"
            )
        RM.mark_complete(
            out,
            run_id=run_id,
            harness_dir=harness_dir,
            extra={
                "output_fingerprint": self._output_fingerprint(out),
                "task_id": task_id,
                "domain": self.domain_of(task_id),
            },
        )
        return out

    def _install(self, harness_dir: Path | None) -> Install:
        """BASE (harness_dir=None) is a value: fingerprint 'base'."""
        tree_dir = self.base_harness() if harness_dir is None else Path(harness_dir)
        inst = self.substrate.materialise(tree_dir, self.spec, {})
        is_base = harness_dir is None
        return Install(
            staging_dir=inst.staging_dir,
            fingerprint="base" if is_base else inst.fingerprint,
            extras={**inst.extras, "harness": "base" if is_base else "staged"},
        )

    def base_harness(self) -> Path:
        """Seed c1 from the repo's CLAUDE.md; carry the leak-guard hooks."""
        d = self.spec.runs_root / f"_base_harness_cc_{os.getpid()}"
        with _BASE_LOCK:
            if d not in _BASE_DONE:
                seed_path = config.SD_REPO / "CLAUDE.md"
                seed = (
                    seed_path.read_text(encoding="utf-8", errors="replace")
                    if seed_path.is_file()
                    else ""
                )
                if len(seed) > CLAUDE_CODE_SURFACE.always_on_max:
                    seed = (
                        seed[: CLAUDE_CODE_SURFACE.always_on_max]
                        + "\n<!-- truncated at the always-on cap -->\n"
                    )
                HT.write_tree(
                    CLAUDE_CODE_SURFACE,
                    {
                        "CLAUDE.md": seed,
                        CLAUDE_CODE_SURFACE.hooks_file: LEAK_GUARD_JSON,
                    },
                    d,
                )
                _BASE_DONE.add(d)
        return d

    def _launch_cc(self, req: RunRequest, install: Install) -> list[Path]:
        dirs = []
        record = self._record(req.task_ref)
        for run_id in req.run_ids:
            run_dir = self._run_dir(run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            ws = run_dir / "workspace"
            if ws.exists():
                shutil.rmtree(ws, ignore_errors=True)
                _git(config.SD_REPO, "worktree", "prune")
            _git(
                config.SD_REPO, "worktree", "add", "--detach", str(ws), record["base_sha"]
            )
            for entry in ("CLAUDE.md", ".claude", "scripts"):
                src = Path(install.staging_dir) / entry
                if not src.exists():
                    continue
                dst = ws / entry
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
            stream = run_dir / "claude_session.jsonl"
            cmd = [
                "claude", "-p",
                "--output-format", "stream-json", "--verbose",
                "--dangerously-skip-permissions",
                "--settings", str(ws / ".claude" / "settings.harness.json"),
                "--max-turns", str(config.SD_MAX_TURNS),
            ]
            try:
                with stream.open("w", encoding="utf-8") as out:
                    _run_claude(
                        cmd,
                        cwd=ws,
                        env=_sd_env(run_dir),
                        stdin_text=self.task_prompt(req.task_ref),
                        stdout=out,
                        timeout=req.timeout,
                    )
            except subprocess.TimeoutExpired:
                print(f"  [sd-cc] wall-timeout for {req.task_ref} @ {run_id}", flush=True)
            self._collect(run_dir, ws)
            dirs.append(run_dir)
        return dirs

    def _collect(self, run_dir: Path, ws: Path) -> None:
        """Record the diff (the deliverable) and the metrics (from the result event)."""
        subprocess.run(
            ["git", "-C", str(ws), "add", "-N", "."], capture_output=True, text=True
        )
        diff = subprocess.run(
            ["git", "-C", str(ws), "diff"], capture_output=True, text=True
        )
        (run_dir / "diff.patch").write_text(diff.stdout, encoding="utf-8")
        metrics = {"cost_usd": 0.0, "num_turns": 0, "is_error": True, "subtype": None}
        for ev in parse_stream(run_dir / "claude_session.jsonl"):
            if ev.get("type") == "result":
                metrics = {
                    "cost_usd": float(ev.get("total_cost_usd") or 0.0),
                    "num_turns": int(ev.get("num_turns") or 0),
                    "is_error": bool(ev.get("is_error")),
                    "subtype": ev.get("subtype"),
                }
        (run_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

    def _safe_rmtree(self, d: Path) -> None:
        """rm -rf restricted to this cell's results root — a guard, not a convenience."""
        d = Path(d).resolve()
        root = self._results_root.resolve()
        if root != d and root not in d.parents:
            raise RuntimeError(f"refusing to remove {d}: outside the sd results root {root}")
        subprocess.run(
            ["git", "-C", str(config.SD_REPO), "worktree", "prune"],
            capture_output=True, text=True,
        )
        shutil.rmtree(d, ignore_errors=True)

    # -- read-back -----------------------------------------------------------

    def delivered(self, run_dir: Path) -> bool:
        p = Path(run_dir) / "diff.patch"
        return p.is_file() and bool(
            p.read_text(encoding="utf-8", errors="replace").strip()
        )

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        stream = Path(run_dir) / "claude_session.jsonl"
        if not stream.is_file():
            return f"(no trajectory recorded for {Path(run_dir).name})"
        return self.substrate.read_trajectory(
            stream, obs_trunc=obs_trunc, max_chars=10**9
        )

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        p = Path(run_dir) / "diff.patch"
        if not p.is_file():
            return {}
        return {"diff.patch": p.read_text(encoding="utf-8", errors="replace")}

    def deliverable_text(self, run_dir: Path) -> str:
        files = self.deliverable_files(run_dir)
        if not files:
            return "(this run produced NO deliverables)"
        return "\n\n".join(
            f"----- FILE: {n} -----\n{t.strip() or '(empty file)'}"
            for n, t in files.items()
        )

    def _output_fingerprint(self, run_dir: Path) -> str | None:
        p = Path(run_dir) / "diff.patch"
        if not self.delivered(run_dir):
            return None
        return hashlib.sha1(p.read_bytes()).hexdigest()

    def artifacts_intact(self, run_dir: Path) -> bool:
        marker = RM.read_mark(run_dir) or {}
        return (
            (Path(run_dir) / "metrics.json").is_file()
            and "output_fingerprint" in marker
            and marker["output_fingerprint"] == self._output_fingerprint(run_dir)
        )
```

Also add `from .tasks import select` to the module imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add harness2/benchmarks/sdcshield.py tests/test_sdcshield_adapter.py
git commit -m "feat(sd): SdShieldAdapter lifecycle, worktree launcher and read-back"
```

---

### Task 9: `validate_tree` — adoption gates

**Files:**
- Modify: `harness2/benchmarks/sdcshield.py`
- Test: `tests/test_sdcshield_adapter.py`

**Interfaces:**
- Consumes: `HT.always_on_text`, `LEAK_GUARD_JSON`
- Produces: `SdShieldAdapter.validate_tree(tree: dict[str, str]) -> None`; raises `ValueError` listing ALL problems in actionable prose (the message is fed back to the proposer verbatim as retry feedback). Rules: hooks file may carry ONLY the `hooks` key; the two leak-guard PreToolUse entries must appear verbatim; `scripts/harness2_leakguard.py` is reserved; the always-on text must keep naming `meson`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sdcshield_adapter.py`:

```python
class ValidateTests(EnvFixture):
    def _tree(self, **over):
        from harness2.substrate.claude_code import LEAK_GUARD_JSON

        tree = {
            "CLAUDE.md": "# t\nRun `meson` builds before finishing.\n",
            "GUARDRAILS.md": "",
            "MEMORY.md": "",
            "TOOL_GUIDANCE.md": "",
            ".claude/settings.harness.json": LEAK_GUARD_JSON,
        }
        tree.update(over)
        return tree

    def test_valid_tree_passes(self):
        ad = self._adapter()
        ad.validate_tree(self._tree())

    def test_permissions_key_rejected(self):
        ad = self._adapter()
        bad = json.loads(self._tree()[".claude/settings.harness.json"])
        bad["permissions"] = {"allow": ["Bash"]}
        with self.assertRaises(ValueError) as cm:
            ad.validate_tree(
                {**self._tree(), ".claude/settings.harness.json": json.dumps(bad)}
            )
        self.assertIn("ONLY the `hooks` key", str(cm.exception))

    def test_missing_deny_entry_rejected(self):
        ad = self._adapter()
        bad = json.loads(self._tree()[".claude/settings.harness.json"])
        bad["hooks"]["PreToolUse"] = bad["hooks"]["PreToolUse"][:1]  # drop Bash guard
        with self.assertRaises(ValueError) as cm:
            ad.validate_tree(
                {**self._tree(), ".claude/settings.harness.json": json.dumps(bad)}
            )
        self.assertIn("immutable", str(cm.exception))

    def test_reserved_script_rejected(self):
        ad = self._adapter()
        with self.assertRaises(ValueError) as cm:
            ad.validate_tree(self._tree(**{"scripts/harness2_leakguard.py": "x"}))
        self.assertIn("reserved", str(cm.exception))

    def test_missing_meson_rule_rejected(self):
        ad = self._adapter()
        with self.assertRaises(ValueError) as cm:
            ad.validate_tree(self._tree(**{"CLAUDE.md": "# no build rule\n"}))
        self.assertIn("meson", str(cm.exception))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: FAIL with `AttributeError: ... no attribute 'validate_tree'`

- [ ] **Step 3: Write minimal implementation**

Append to `SdShieldAdapter`:

```python
    def validate_tree(self, tree: dict[str, str]) -> None:
        """Adoption gates: hooks-only settings, immutable leak-guard, reserved script,
        and the build rule. ALL problems land in one ValueError (retry feedback)."""
        errs: list[str] = []
        src = tree.get(CLAUDE_CODE_SURFACE.hooks_file)
        if src is not None:
            try:
                doc = json.loads(src)
            except json.JSONDecodeError as exc:
                errs.append(f"{CLAUDE_CODE_SURFACE.hooks_file} is not valid JSON: {exc}")
                doc = None
            if isinstance(doc, dict):
                extra = sorted(set(doc) - {"hooks"})
                if extra:
                    errs.append(
                        f"{CLAUDE_CODE_SURFACE.hooks_file} may carry ONLY the `hooks` "
                        f"key (found {', '.join(extra)}); permissions and env are not "
                        "editable"
                    )
                deny = json.loads(LEAK_GUARD_JSON)["hooks"]["PreToolUse"]
                hooks = doc.get("hooks")
                got = hooks.get("PreToolUse", []) if isinstance(hooks, dict) else []
                for entry in deny:
                    if entry not in got:
                        errs.append(
                            "the leak-guard PreToolUse hooks are immutable: the entry "
                            f"{json.dumps(entry)[:120]} must stay in "
                            f"{CLAUDE_CODE_SURFACE.hooks_file} verbatim"
                        )
        if "scripts/harness2_leakguard.py" in tree:
            errs.append(
                "scripts/harness2_leakguard.py is reserved; it ships with every install "
                "and cannot be part of a harness tree"
            )
        body = HT.always_on_text(CLAUDE_CODE_SURFACE, tree)
        if "meson" not in body.lower():
            errs.append(
                "the always-on text must keep the build rule (name `meson`) — a change "
                "that drops it stops building before finishing. Add the meson rule back."
            )
        if errs:
            raise ValueError(" | ".join(errs))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
git add harness2/benchmarks/sdcshield.py tests/test_sdcshield_adapter.py
git commit -m "feat(sd): validate_tree adoption gates (hooks-only, leak-guard, meson rule)"
```

---

### Task 10: `score` — three layers + `preflight`

**Files:**
- Modify: `harness2/benchmarks/sdcshield.py`
- Test: `tests/test_sdcshield_adapter.py`

**Interfaces:**
- Consumes: `config.SD_TEST_CMD`, `config.SD_BOARD_CMD`, `config.SD_SKIP_BOARD`, `_sd_env`
- Produces: `SdShieldAdapter.score(run_dir: Path, task_id: str) -> dict | None` returning `{"l1": {"ok", "warnings"}, "l2": {"ok", "passed", "total"}, "l3": {"ok", "status"}, "adopt_ok": bool}` (written to `run_dir/scores.json`); `SdShieldAdapter.preflight() -> None`. The board command runs as `sh -c "$HARNESS2_SD_BOARD_CMD"` with `SD_WORKSPACE`, `SD_BUILDDIR`, `SD_BOARDS="0201 0202"` in the environment.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sdcshield_adapter.py`:

```python
class ScoreTests(EnvFixture):
    def _ws(self, name: str) -> tuple[Path, Path]:
        run_dir = self.base / name
        run_dir.mkdir(exist_ok=True)
        ws = run_dir / "workspace"
        ws.mkdir(exist_ok=True)
        (ws / "meson.build").write_text("project('t', 'c')\n", encoding="utf-8")
        return run_dir, ws

    def test_l2_skips_when_build_failed(self):
        ad = self._adapter()
        run_dir, ws = self._ws("rd-fail")
        out = ad._l2_tests(ws, run_dir, {"ok": False, "warnings": 0})
        self.assertFalse(out["ok"])
        self.assertEqual(out["note"], "build failed")

    def test_l3_skipped_by_env(self):
        ad = self._adapter()
        run_dir, _ = self._ws("rd-skip")
        with patch.object(config, "SD_SKIP_BOARD", True):
            out = ad._l3_boards(run_dir, {"ok": True, "warnings": 0})
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "skipped-by-env")

    def test_l3_runs_board_command(self):
        ad = self._adapter()
        run_dir, _ = self._ws("rd-board")
        with patch.object(config, "SD_SKIP_BOARD", False), patch.object(
            config, "SD_BOARD_CMD", 'test "$SD_BOARDS" = "0201 0202"'
        ):
            out = ad._l3_boards(run_dir, {"ok": True, "warnings": 0})
        self.assertTrue(out["ok"])
        self.assertEqual(out["status"], "ran")
        self.assertTrue((run_dir / "boards.log").is_file())

    def test_l3_missing_command_raises_config_error(self):
        ad = self._adapter()
        run_dir, _ = self._ws("rd-nocmd")
        with patch.object(config, "SD_SKIP_BOARD", False), patch.object(
            config, "SD_BOARD_CMD", ""
        ):
            with self.assertRaises(config.ConfigError):
                ad._l3_boards(run_dir, {"ok": True, "warnings": 0})

    def test_score_returns_none_without_workspace(self):
        ad = self._adapter()
        empty = self.base / "rd-empty"
        empty.mkdir()
        self.assertIsNone(ad.score(empty, "pr-1"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: FAIL with `AttributeError: ... no attribute '_l2_tests'`

- [ ] **Step 3: Write minimal implementation**

Append to `SdShieldAdapter`:

```python
    # -- three-layer scoring -------------------------------------------------

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        """L1 build + L2 tests + L3 boards. Judge failure => None, never cached."""
        ws = Path(run_dir) / "workspace"
        if not ws.is_dir():
            return None
        try:
            l1 = self._l1_build(ws, Path(run_dir))
            l2 = self._l2_tests(ws, Path(run_dir), l1)
            l3 = self._l3_boards(Path(run_dir), l1)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"  [sd-cc] judging {Path(run_dir).name} failed: {exc}", flush=True)
            return None
        out = {
            "l1": l1,
            "l2": l2,
            "l3": l3,
            "adopt_ok": bool(l1["ok"] and l2["ok"] and l3["ok"]),
        }
        (Path(run_dir) / "scores.json").write_text(
            json.dumps(out, indent=1), encoding="utf-8"
        )
        return out

    def _l1_build(self, ws: Path, run_dir: Path) -> dict:
        bd = ws / "builddir-h2"
        if bd.exists():
            shutil.rmtree(bd)
        r = subprocess.run(
            ["meson", "setup", str(bd)], cwd=ws, capture_output=True, text=True,
            timeout=1800,
        )
        if r.returncode != 0:
            (run_dir / "build.log").write_text(r.stdout + r.stderr, encoding="utf-8")
            return {"ok": False, "warnings": 0, "note": "meson setup failed"}
        n = subprocess.run(
            ["ninja", "-C", str(bd)], cwd=ws, capture_output=True, text=True,
            timeout=3600,
        )
        warnings = len(
            re.findall(r"\bwarning\b", (n.stdout or "") + (n.stderr or ""), re.I)
        )
        (run_dir / "build.log").write_text(
            r.stdout + n.stdout + n.stderr, encoding="utf-8"
        )
        return {"ok": n.returncode == 0, "warnings": warnings}

    def _l2_tests(self, ws: Path, run_dir: Path, l1: dict) -> dict:
        if not l1["ok"]:
            return {"ok": False, "passed": 0, "total": 0, "note": "build failed"}
        bd = ws / "builddir-h2"
        env = {**_sd_env(run_dir.parent), "SD_BUILDDIR": str(bd), "SD_WORKSPACE": str(ws)}
        if config.SD_TEST_CMD:
            r = subprocess.run(
                ["sh", "-c", config.SD_TEST_CMD], cwd=ws, env=env,
                capture_output=True, text=True, timeout=7200,
            )
            (run_dir / "tests.log").write_text(r.stdout + r.stderr, encoding="utf-8")
            return {"ok": r.returncode == 0, "cmd": "custom"}
        r = subprocess.run(
            ["meson", "test", "-C", str(bd), "--num-processes", "2"],
            cwd=ws, capture_output=True, text=True, timeout=7200,
        )
        (run_dir / "tests.log").write_text(r.stdout + r.stderr, encoding="utf-8")
        ok_n = int((re.search(r"^Ok:\s+(\d+)", r.stdout or "", re.M) or ["", "0"])[1])
        fail_n = int((re.search(r"^Fail:\s+(\d+)", r.stdout or "", re.M) or ["", "0"])[1])
        return {"ok": r.returncode == 0, "passed": ok_n, "total": ok_n + fail_n}

    def _l3_boards(self, run_dir: Path, l1: dict) -> dict:
        if not l1["ok"]:
            return {"ok": False, "status": "skipped-build-failed"}
        if config.SD_SKIP_BOARD:
            return {"ok": True, "status": "skipped-by-env"}
        if not config.SD_BOARD_CMD:
            raise config.ConfigError(
                "HARNESS2_SD_BOARD_CMD is required for board gating (deploy + stress "
                "+ excitation on 0201/0202); set it in .env, or set "
                "HARNESS2_SD_SKIP_BOARD=1 for local runs without adoption"
            )
        ws = run_dir / "workspace"
        bd = ws / "builddir-h2"
        r = subprocess.run(
            ["sh", "-c", config.SD_BOARD_CMD],
            cwd=ws,
            env={
                **_sd_env(run_dir),
                "SD_WORKSPACE": str(ws),
                "SD_BUILDDIR": str(bd),
                "SD_BOARDS": "0201 0202",
            },
            capture_output=True, text=True, timeout=7 * 3600,
        )
        (run_dir / "boards.log").write_text(r.stdout + r.stderr, encoding="utf-8")
        return {"ok": r.returncode == 0, "status": "ran"}

    def preflight(self) -> None:
        """Everything money-adjacent, checked before a rollout is paid for."""
        self.substrate.preflight()
        if not (config.SD_REPO / ".git").exists():
            raise RuntimeError(
                f"sdcshield repo missing at {config.SD_REPO}; set HARNESS2_SD_REPO"
            )
        for tool in ("meson", "ninja"):
            if shutil.which(tool) is None:
                raise RuntimeError(
                    f"{tool} not found on PATH; install it for L1/L2 gating"
                )
        ping_dir = Path(tempfile.mkdtemp(prefix="sd-preflight-"))
        r = subprocess.run(
            ["claude", "-p", "--output-format", "json", "Reply with the word: pong"],
            capture_output=True, text=True, timeout=300, env=_sd_env(ping_dir),
        )
        if r.returncode != 0 or "pong" not in (r.stdout or "").lower():
            raise RuntimeError(
                "claude -p roundtrip failed under the scrubbed env (check "
                "ANTHROPIC_BASE_URL / credentials work without ~/.claude config): "
                + (r.stderr or r.stdout or "")[-300:]
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: PASS (22 tests)

Full suite: `python3 -m unittest discover -s tests -t . -v` — all PASS.

- [ ] **Step 5: Commit**

```bash
git add harness2/benchmarks/sdcshield.py tests/test_sdcshield_adapter.py
git commit -m "feat(sd): three-layer scoring and preflight with model roundtrip"
```

---

### Task 11: CLI wiring

**Files:**
- Modify: `harness2/cli/_common.py:30-32` (BENCHES/SUBSTRATES), `:45-67` (make_adapter), `:99` (help text)
- Modify: `harness2/cli/check.py:27,29`
- Modify: `harness2/cli/run_sweep.py:213,219`
- Test: `tests/test_sdcshield_adapter.py`

**Interfaces:**
- Consumes: `SdShieldAdapter`
- Produces: `make_adapter("sd", None, "cc", split_file)` → `SdShieldAdapter`; `--bench` accepts `sd`; `--substrate` accepts `cc`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sdcshield_adapter.py`:

```python
class CliWiringTests(EnvFixture):
    def test_make_adapter_dispatches_sd_cc(self):
        from harness2.cli._common import BENCHES, SUBSTRATES, make_adapter

        self.assertIn("sd", BENCHES)
        self.assertIn("cc", SUBSTRATES)
        ad = make_adapter("sd", None, "cc")
        self.assertEqual(ad.spec.name, "sd")
        self.assertEqual(ad.substrate.name, "cc")

    def test_make_adapter_rejects_sd_on_other_substrates(self):
        from harness2.cli._common import make_adapter

        with self.assertRaises(ValueError):
            make_adapter("sd", None, "oc")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: FAIL with `ValueError: unknown bench 'sd'`

- [ ] **Step 3: Write minimal implementation**

In `harness2/cli/_common.py`:

```python
BENCHES = ("jb", "wb", "lab", "sd")
WB_SUBSETS = ("office", "web", "code")
SUBSTRATES = ("oc", "cx", "cc")
```

In `make_adapter`, insert before the final `LabAdapter` fallback:

```python
    if bench == "sd":
        from ..benchmarks.sdcshield import SdShieldAdapter

        return SdShieldAdapter(substrate=substrate, split_file=split_file)
```

(`SdShieldAdapter.__init__` itself raises for substrate != "cc", which the second test asserts.) Update the `--bench` help at line 99 to `"jb | wb | lab | sd"` and the `--substrate` help to `"oc (opencode) | cx (codex) | cc (claude code)"`.

In `harness2/cli/check.py` line 27: `choices=("jb", "lab", "wb", "sd")`; line 29: `choices=("oc", "cx", "cc")`.

In `harness2/cli/run_sweep.py` line 213, replace:

```python
        "executor": config.SOLVER_MODEL_OC if args.substrate == "oc" else config.SOLVER_MODEL_CX,
```

with:

```python
        "executor": (
            config.SOLVER_MODEL_OC
            if args.substrate == "oc"
            else (
                config.SOLVER_MODEL_CX
                if args.substrate == "cx"
                else config.CC_IMPROVER_MODEL
            )
        ),
```

and line 219, replace:

```python
        "endpoint": config.CODEX_BASE_URL if args.substrate == "cx" else "",
```

with:

```python
        "endpoint": (
            config.CODEX_BASE_URL
            if args.substrate == "cx"
            else (config.env("ANTHROPIC_BASE_URL", "") if args.substrate == "cc" else "")
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: PASS (24 tests)

Full suite: `python3 -m unittest discover -s tests -t . -v` — all PASS.

- [ ] **Step 5: Commit**

```bash
git add harness2/cli/_common.py harness2/cli/check.py harness2/cli/run_sweep.py tests/test_sdcshield_adapter.py
git commit -m "feat(cli): wire the sd benchmark and cc substrate into dispatch"
```

---

### Task 12: sync script + run guide

**Files:**
- Create: `scripts/sync_sd_harness.py`
- Create: `docs/SDCSHIELD.md`
- Test: `tests/test_sdcshield_adapter.py`

**Interfaces:**
- Consumes: `HT.read_tree`, `compose_claude_md`, `CLAUDE_CODE_SURFACE`, `config.SD_REPO`
- Produces: `python3 scripts/sync_sd_harness.py <harness_dir> [--repo R] [--dry-run]` — copies skills/agents/scripts/hooks into the sdcshield repo, composes the four always-on files into its `CLAUDE.md` (always-on files other than CLAUDE.md are NOT copied as separate files).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sdcshield_adapter.py` (add `from harness2.core import tree as HT` and `from harness2.substrate.claude_code import CLAUDE_CODE_SURFACE` to the module imports if not present):

```python
class SyncTests(EnvFixture):
    def test_sync_dry_run_and_write(self):
        import subprocess
        import sys

        from harness2.substrate.claude_code import LEAK_GUARD_JSON

        target = self.base / "daily-repo"
        target.mkdir()
        (target / "CLAUDE.md").write_text("old\n", encoding="utf-8")
        script = Path(__file__).resolve().parent.parent / "scripts" / "sync_sd_harness.py"
        with tempfile.TemporaryDirectory() as td:
            tree_d = HT.write_tree(
                CLAUDE_CODE_SURFACE,
                {
                    "CLAUDE.md": "# improved\nbuild with meson\n",
                    "GUARDRAILS.md": "rules v2\n",
                    "MEMORY.md": "",
                    "TOOL_GUIDANCE.md": "tools\n",
                    ".claude/skills/deploy/SKILL.md": (
                        "---\nname: deploy\ndescription: d\n---\n"
                    ),
                    "sub_agents/reviewer/AGENT.md": "---\ndescription: r\n---\n",
                    ".claude/settings.harness.json": LEAK_GUARD_JSON,
                },
                Path(td) / "h",
            )
            r = subprocess.run(
                [sys.executable, str(script), str(tree_d),
                 "--repo", str(target), "--dry-run"],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("dry run", r.stdout)
            self.assertEqual((target / "CLAUDE.md").read_text(encoding="utf-8"), "old\n")

            r = subprocess.run(
                [sys.executable, str(script), str(tree_d), "--repo", str(target)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            composed = (target / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("# improved", composed)
            self.assertIn("harness2:GUARDRAILS.md", composed)
            self.assertTrue(
                (target / ".claude" / "skills" / "deploy" / "SKILL.md").is_file()
            )
            self.assertTrue((target / ".claude" / "agents" / "reviewer.md").is_file())
            self.assertFalse((target / "GUARDRAILS.md").exists())  # composed, not copied
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: FAIL with `FileNotFoundError` (script missing)

- [ ] **Step 3: Write minimal implementation**

Create `scripts/sync_sd_harness.py`:

```python
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
```

Create `docs/SDCSHIELD.md`:

```markdown
# sdcshield (sd) — PR replay on the claude_code substrate

Task = one merged PR of the sdcshield repository, replayed from its base commit in a
fresh git worktree with a staged Claude Code harness (CLAUDE.md + .claude overlay).
The improvement channel is the `claude_cli` proposer on the same GLM-backed CLI.

## Requirements

- Linux, Python 3.12, the `claude` CLI on PATH (configured for your GLM endpoint via
  `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`)
- meson + ninja (L1/L2), the sdcshield repo on disk (`HARNESS2_SD_REPO`)
- L3 boards: a fleet command (see below)

## Environment

| Variable | Meaning |
|---|---|
| `HARNESS2_SD_REPO` | sdcshield checkout (default: sibling of this repo) |
| `HARNESS2_SD_MAX_TURNS` | claude -p turn budget (default 200) |
| `HARNESS2_CC_IMPROVER_MODEL` | label for the claude_cli channel (default glm-5.3) |
| `HARNESS2_SD_TEST_CMD` | custom L2 command (`sh -c`, cwd=workspace, `SD_BUILDDIR` set) |
| `HARNESS2_SD_BOARD_CMD` | L3 fleet command — REQUIRED for adoption |
| `HARNESS2_SD_SKIP_BOARD` | `1` skips L3 and lets adoption pass on L1+L2 (dev only) |

`HARNESS2_SD_BOARD_CMD` runs as `sh -c` in the workspace with `SD_WORKSPACE`,
`SD_BUILDDIR` and `SD_BOARDS="0201 0202"` exported; exit 0 = both boards pass.
Wrap your existing deploy + stress + excitation scripts here.

## Run

```bash
harness2 --bench sd --k 1 --mode parallel --only-domains testing --max-tasks 2
HARNESS2_SD_SKIP_BOARD=1 harness2-judge --bench sd --k 1 --mode parallel
harness2-report --bench sd --k 1 --mode parallel
```

Judging and reporting must use the same benchmark, subset, `--k`, `--mode`, and seed
as the refinement run. `score()` returns `l1` (build), `l2` (tests), `l3` (boards)
and `adopt_ok` (all three green).

## Deploy an adopted harness into your daily repo

```bash
python3 scripts/sync_sd_harness.py runs/sd/library/<cell>/harness --dry-run
python3 scripts/sync_sd_harness.py runs/sd/library/<cell>/harness
```

## Replay isolation

Rollouts run in a worktree at the PR's base commit, under a scrubbed environment
(no GitHub credentials, isolated `CLAUDE_CONFIG_DIR`) and immutable PreToolUse
leak-guard hooks (WebFetch/WebSearch and `gh pr`/`github.com`/`git fetch` blocked).
The leak-guard entries in `.claude/settings.harness.json` cannot be removed by a
proposal, and the file may only carry the `hooks` key.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: PASS (25 tests)

Full suite: `python3 -m unittest discover -s tests -t . -v` — all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_sd_harness.py docs/SDCSHIELD.md tests/test_sdcshield_adapter.py
git commit -m "feat(sd): harness sync script and run guide"
```

---

### Task 13: rollback guard (spec §7 — auto-revert on regression)

**Files:**
- Create: `scripts/sd_rollback_guard.py`
- Test: `tests/test_sdcshield_adapter.py`

**Interfaces:**
- Consumes: `Library(root, domain, surface)` with `entries()` / `forget()` (`core/library.py:83,90`), `RM.read_mark`, the `task_id`/`domain` keys Task 8 writes into every run mark, `scores.json` from Task 10.
- Produces: `guard(library_root: Path, results_root: Path, *, window: int = 3, threshold: float = 0.0) -> list[tuple[str, str]]` — for each domain in the library, if the latest adopted entry's judged pass-rate is below the prior entries' rate minus `threshold`, `Library.forget([latest])` drops it (the next sweep re-derives without it) and the pair `(domain, task)` is returned. CLI: `python3 scripts/sd_rollback_guard.py --library-root R --results-root R2 [--window 3] [--threshold 0.0] [--dry-run]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sdcshield_adapter.py`:

```python
class RollbackGuardTests(EnvFixture):
    def _mk_run(self, results_root: Path, run_id: str, task: str, ok: bool) -> None:
        from harness2.core import runmark as RM

        rd = results_root / run_id
        rd.mkdir(parents=True)
        RM.mark_complete(
            rd, run_id=run_id, harness_dir=None,
            extra={"task_id": task, "domain": "detector-cpu"},
        )
        (rd / "scores.json").write_text(
            json.dumps({"l1": {"ok": ok}, "l2": {"ok": ok}}), encoding="utf-8"
        )

    def _mk_lib(self, library_root: Path, tasks: list[str]) -> None:
        from harness2.core.library import Library
        from harness2.substrate.claude_code import CLAUDE_CODE_SURFACE

        lib = Library(library_root, "detector-cpu", CLAUDE_CODE_SURFACE)
        for t in tasks:
            lib.add(
                t,
                {"CLAUDE.md": "# h\nbuild with meson\n"},
                experience={
                    "cue": "c", "lesson": "l", "evidence": "e", "edits": "", "confidence": ""
                },
                label="composed", run_id="r",
            )

    def test_guard_drops_regressed_adoption(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from sd_rollback_guard import guard

        library_root = self.base / "lib"
        results_root = self.base / "res"
        self._mk_lib(library_root, ["pr-1", "pr-2"])
        self._mk_run(results_root, "r1", "pr-1", True)
        self._mk_run(results_root, "r2", "pr-2", False)
        dropped = guard(library_root, results_root)
        self.assertEqual(dropped, [("detector-cpu", "pr-2")])
        from harness2.core.library import Library
        from harness2.substrate.claude_code import CLAUDE_CODE_SURFACE

        lib = Library(library_root, "detector-cpu", CLAUDE_CODE_SURFACE)
        self.assertEqual(lib.entries(), ["pr-1"])

    def test_guard_keeps_stable_adoption(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from sd_rollback_guard import guard

        library_root = self.base / "lib2"
        results_root = self.base / "res2"
        self._mk_lib(library_root, ["pr-1", "pr-2"])
        self._mk_run(results_root, "r1", "pr-1", True)
        self._mk_run(results_root, "r2", "pr-2", True)
        dropped = guard(library_root, results_root)
        self.assertEqual(dropped, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sd_rollback_guard'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/sd_rollback_guard.py`:

```python
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

Compares the judged L1+L2 pass-rate of the newest library entry against the window
of entries before it; on regression, Library.forget() removes the entry so the next
sweep re-derives the harness without it. Run it after judging, before syncing.
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
    """task_id -> L1+L2 verdicts of every judged run (from mark + scores.json)."""
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
        except json.JSONDecodeError:
            continue
        ok = bool(s.get("l1", {}).get("ok") and s.get("l2", {}).get("ok"))
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest discover -s tests -t . -p "test_sdcshield_adapter.py" -v`
Expected: PASS (27 tests)

Full suite: `python3 -m unittest discover -s tests -t . -v` — all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/sd_rollback_guard.py tests/test_sdcshield_adapter.py
git commit -m "feat(sd): rollback guard drops regressed library adoptions"
```

---

### Task 14: end-to-end smoke (verification, not unit-tested)

**Files:**
- Create: `runs/` artifacts (not committed)

**Interfaces:**
- Consumes: everything above
- Produces: a verified single-PR run through refine → judge → report.

- [ ] **Step 1: Preflight**

Run: `python3 -m harness2.cli.check --bench sd --substrate cc`
Expected: all checks pass (claude roundtrip, repo, meson/ninja).

- [ ] **Step 2: Dry-run task selection**

Run: `HARNESS2_SD_SKIP_BOARD=1 python3 -m harness2.cli.run_sweep --bench sd --substrate cc --k 1 --mode parallel --only-domains testing --max-tasks 1 --dry-run`
Expected: lists one `pr-<n>` task in the `testing` domain, no model calls.

- [ ] **Step 3: One real refinement run**

Run: `HARNESS2_SD_SKIP_BOARD=1 python3 -m harness2.cli.run_sweep --bench sd --substrate cc --k 1 --mode parallel --only-domains testing --max-tasks 1`
Expected: completes; `runs/sd/results/cc/<run_id>/` contains `claude_session.jsonl`,
`diff.patch`, `metrics.json`; the proposer produced and probed at least one candidate
harness tree.

- [ ] **Step 4: Judge, guard, and report**

Run: `HARNESS2_SD_SKIP_BOARD=1 python3 -m harness2.cli.judge --bench sd --substrate cc --k 1 --mode parallel`
Expected: `scores.json` with `l1`/`l2` populated (`l3.status == "skipped-by-env"`).

Locate the library root (the parent of the domain directories holding `INDEX.md`):

Run: `find runs/sd -name INDEX.md | head -3`
Then run the guard in dry-run against that root, e.g.:
`python3 scripts/sd_rollback_guard.py --library-root <that-parent> --results-root runs/sd/results/cc --dry-run`
Expected: prints the domains it inspected and "0 adoption(s) dropped (dry run)" — or a
named regression, which at k=1/1-task means a bug to investigate, not to auto-drop.

Run: `python3 -m harness2.cli.report --bench sd --substrate cc --k 1 --mode parallel`
Expected: a report table.

- [ ] **Step 5: Record findings and stop**

If any step fails, fix and re-run that step only. When all pass, report: task id used,
whether the adopted harness differed from base, and the l1/l2 numbers. Do NOT enable
board gating in this task — that is the user's operational decision with their fleet
command.
