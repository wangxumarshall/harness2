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
