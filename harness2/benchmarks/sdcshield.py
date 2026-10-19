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

"""sdcshield adapter: PR replay on the claude_code substrate.

Task = one merged PR of the sdcshield repository, replayed from its base commit in a
fresh worktree. L1 build + L2 tests run locally; L3 boards run a user-supplied fleet
command. The judge layer never sees the real PR's diff.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.spec import BenchPrompts, BenchSpec, resolve_channel_model
from ..substrate.base import Install, RunRequest
from ..substrate.claude_code import (
    CLAUDE_CODE_SURFACE,
    ClaudeCodeSubstrate,
    LEAK_GUARD_JSON,
    parse_stream,
)
from .tasks import select

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
    try:
        remote = _gh_remote(repo)
    except RuntimeError:
        return {}
    r = subprocess.run(
        [
            gh, "pr", "list", "-R", remote, "--state", "merged",
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
            subprocess.run(
                ["git", "-C", str(ws), "add", "-A"], capture_output=True, text=True
            )
            tree = subprocess.run(
                ["git", "-C", str(ws), "write-tree"], capture_output=True, text=True
            )
            if tree.returncode == 0 and tree.stdout.strip():
                (run_dir / "overlay_tree").write_text(
                    tree.stdout.strip(), encoding="utf-8"
                )
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
        overlay_tree = run_dir / "overlay_tree"
        if overlay_tree.is_file():
            base_tree = overlay_tree.read_text(encoding="utf-8").strip()
            subprocess.run(
                ["git", "-C", str(ws), "add", "-A"], capture_output=True, text=True
            )
            diff = subprocess.run(
                ["git", "-C", str(ws), "diff", "--cached", base_tree],
                capture_output=True, text=True,
            )
        else:
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
        shutil.rmtree(d, ignore_errors=True)
        subprocess.run(
            ["git", "-C", str(config.SD_REPO), "worktree", "prune"],
            capture_output=True, text=True,
        )

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
