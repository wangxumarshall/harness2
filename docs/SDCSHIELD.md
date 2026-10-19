# sdcshield (sd) — PR replay on the claude_code substrate

Task = one merged PR of the sdcshield repository, replayed from its base commit in a
fresh git worktree with a staged Claude Code harness (CLAUDE.md + .claude overlay).
The improvement channel is the `claude_cli` proposer on the same GLM-backed CLI.

## Requirements

- Linux, Python 3.12, the `claude` CLI on PATH (configured for your GLM endpoint via
  `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`)
- meson + ninja (L1/L2), the sdcshield repo on disk (`HARNESS2_SD_REPO`)
- No `harness2-setup` step: the sdcshield checkout is already on disk (`HARNESS2_SD_REPO`)
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
