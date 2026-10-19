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
from harness2.core import tree as HT
from harness2.substrate.claude_code import CLAUDE_CODE_SURFACE


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
        with patch.dict(
            os.environ,
            {"ANTHROPIC_BASE_URL": "http://glm:9100", "ANTHROPIC_AUTH_TOKEN": "tok"},
            clear=False,
        ):
            e = claude_cli_env({}, "proj", "global", "m")
        self.assertNotIn("CLAUDE_CODE_USE_VERTEX", e)
        self.assertEqual(e.get("ANTHROPIC_BASE_URL"), "http://glm:9100")
        self.assertEqual(e.get("ANTHROPIC_AUTH_TOKEN"), "tok")

        env_now = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_BASE_URL"}
        with patch.dict(os.environ, env_now, clear=True):
            e = claude_cli_env(base, "proj", "global", "m")
        self.assertEqual(e["CLAUDE_CODE_USE_VERTEX"], "1")
        self.assertEqual(e["ANTHROPIC_MODEL"], "m")


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

    def test_safe_rmtree_clears_worktree_registration(self):
        ad = self._adapter()
        run_dir = ad._run_dir("r-wt")
        run_dir.mkdir(parents=True)
        ws = run_dir / "workspace"
        _git(self.repo, "worktree", "add", "--detach", str(ws), "HEAD")
        self.assertTrue(ws.exists())
        ad._safe_rmtree(run_dir)
        self.assertFalse(ws.exists())
        # the registration must be cleared: re-adding the same path succeeds
        _git(self.repo, "worktree", "add", "--detach", str(ws), "HEAD")
        self.assertTrue(ws.exists())

    def test_collect_diff_excludes_overlay_noise(self):
        import subprocess as sp

        ad = self._adapter()
        run_dir = self.base / "rd-ov"
        run_dir.mkdir()
        ws = run_dir / "workspace"
        ws.mkdir()
        (ws / "CLAUDE.md").write_text("seed\n", encoding="utf-8")
        (ws / "det.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
        _git(ws, "init", "-b", "main")
        _git(ws, "config", "user.email", "t@example.com")
        _git(ws, "config", "user.name", "t")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-m", "init")
        # overlay: composed CLAUDE.md + injected leakguard
        (ws / "CLAUDE.md").write_text(
            "<!-- harness2:CLAUDE.md -->\n\nseed\n", encoding="utf-8"
        )
        (ws / "scripts").mkdir()
        (ws / "scripts" / "harness2_leakguard.py").write_text("guard\n", encoding="utf-8")
        sp.run(["git", "-C", str(ws), "add", "-A"], capture_output=True, text=True)
        tree = sp.run(
            ["git", "-C", str(ws), "write-tree"], capture_output=True, text=True
        )
        (run_dir / "overlay_tree").write_text(tree.stdout.strip(), encoding="utf-8")
        (run_dir / "claude_session.jsonl").write_text(
            json.dumps({"type": "result", "subtype": "success", "result": "ok"}),
            encoding="utf-8",
        )
        # agent work: only det.c changes
        (ws / "det.c").write_text("int main(void){return 1;}\n", encoding="utf-8")
        ad._collect(run_dir, ws)
        diff = (run_dir / "diff.patch").read_text(encoding="utf-8")
        self.assertIn("det.c", diff)
        self.assertNotIn("harness2_leakguard", diff)
        self.assertNotIn("harness2:CLAUDE.md", diff)
        self.assertTrue(ad.delivered(run_dir))
        # a no-op workspace (identical to overlay) delivers nothing
        (ws / "det.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
        ad._collect(run_dir, ws)
        self.assertFalse(ad.delivered(run_dir))


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

    def test_l2_custom_command_gets_per_run_env(self):
        ad = self._adapter()
        run_dir, ws = self._ws("rd-cmd")
        cmd = (
            'echo "$CLAUDE_CONFIG_DIR" > "$SD_WORKSPACE/cfg.txt" '
            '&& test -n "$SD_BUILDDIR"'
        )
        with patch.object(config, "SD_TEST_CMD", cmd):
            out = ad._l2_tests(ws, run_dir, {"ok": True, "warnings": 0})
        self.assertTrue(out["ok"])
        self.assertEqual(out["cmd"], "custom")
        self.assertEqual(out["passed"], 0)
        self.assertEqual(out["total"], 0)
        got = (ws / "cfg.txt").read_text(encoding="utf-8").strip()
        self.assertEqual(got, str(run_dir / "claude-home"))

    def test_score_shape_includes_judge_keys(self):
        ad = self._adapter()
        run_dir = self.base / "rd-keys"
        run_dir.mkdir()
        ws = run_dir / "workspace"
        ws.mkdir()
        (ws / "meson.build").write_text("project('t', 'c')\n", encoding="utf-8")
        from harness2.substrate.claude_code import LEAK_GUARD_JSON

        with patch.object(config, "SD_SKIP_BOARD", True), patch.object(
            config, "SD_TEST_CMD", "true"
        ):
            out = ad.score(run_dir, "pr-1")
        self.assertIsNotNone(out)
        self.assertIn("n_passed", out)
        self.assertIn("n_criteria", out)
        self.assertEqual(out["n_criteria"], 3)
        self.assertIn("all_pass", out)
        # l3 skipped-by-env counts as ok in this configuration
        self.assertEqual(out["n_passed"], 3)
        self.assertTrue(out["all_pass"])


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


class PreflightTests(EnvFixture):
    def test_check_sd_skips_opencode_and_dependencies(self):
        import types

        from harness2 import preflight as PF
        from harness2.benchmarks.sdcshield import sd_spec

        calls: list[str] = []

        def _tasks():
            calls.append("tasks")
            return []

        def _preflight():
            calls.append("preflight")

        ad = types.SimpleNamespace(
            spec=sd_spec(), tasks=_tasks, preflight=_preflight,
            substrate=types.SimpleNamespace(name="cc"),
        )
        env = {
            "HARNESS2_SOLVER_MODEL": "x/y",
            "HARNESS2_IMPROVER_MODEL": "x/y",
        }
        # `check()` calls `config.validate_models`, which reads the module-level
        # `SOLVER_MODEL_OC`/`IMPROVER_MODEL` (bound at import), not `os.environ`.
        # Patch them too so the offline stub satisfies the model check.
        with patch.dict(os.environ, env, clear=False), patch.object(
            config, "SOLVER_MODEL_OC", "x/y"
        ), patch.object(config, "IMPROVER_MODEL", "x/y"):
            errors = PF.check(ad)
        self.assertEqual(errors, [])
        self.assertEqual(calls, ["tasks", "preflight"])


class PromptSurfaceTests(unittest.TestCase):
    def test_cc_prompt_surface_resolves(self):
        from harness2.core.prompts import _st

        st = _st("cc")
        self.assertIn("CLAUDE.md", st.surface_md)
        self.assertIn(".claude/settings.harness.json", st.surface_md)
        self.assertIn(".claude/skills/<name>/SKILL.md", st.surface_md)
        self.assertIn("sub_agents/<name>/AGENT.md", st.surface_md)
        self.assertIn("`GUARDRAILS.md`", st.dom)
        self.assertIn("`.claude/settings.harness.json`", st.inv)
        # tts composition extracted real spans (anchors present, non-empty)
        self.assertIn("# EDITABLE HARNESS SURFACE", st.tts_surface)
        self.assertIn("# HOW TO MAKE A CHANGE", st.tts_surface)
        self.assertIn("THE DELIVERABLE'S SHAPE", st.surface_md)

    def test_cc_surface_via_claude_code_surface_name(self):
        from harness2.core.prompts import _st
        from harness2.substrate.claude_code import CLAUDE_CODE_SURFACE

        self.assertEqual(_st(CLAUDE_CODE_SURFACE).surface_md, _st("cc").surface_md)


if __name__ == "__main__":
    unittest.main()
