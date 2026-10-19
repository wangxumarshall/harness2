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


if __name__ == "__main__":
    unittest.main()
