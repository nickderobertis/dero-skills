# Command surface for the dero-skills authoring repo.
#
# `just bootstrap` works from a clean clone; `just check` is the full gate.
# The gate runs on uv alone (no Node needed); bun/Nx is an authoring
# accelerator wired up by `bootstrap` and exposed via `just nx`.

# List available recipes.
default:
    @just --list

# Set up from a clean clone: Python (uv), the Node/Nx authoring toolchain, and the
# llmlint binary (uv-installed) that the gate's `lint-llm-validate` step needs.
bootstrap:
    uv sync --locked
    bun install --frozen-lockfile
    ./scripts/setup-llmlint.sh

# Full quality gate. Must pass before every commit and in CI. `lint-llm-validate`
# is the deterministic, model-free llmlint gate (config/ignores/version bumps); it
# needs the uv-installed llmlint binary but no harness/token, so it belongs in the
# gate. The non-deterministic model tier (lint-llm/lint-llm-diff) stays out.
check: format-check lint validate check-versions test baseline lint-llm-validate

# Format this repo's Python in place.
format:
    uv run ruff format .

# Verify formatting without modifying files.
format-check:
    uv run ruff format --check .

# Lint this repo's Python.
lint:
    uv run ruff check .

# Validate and smoke-check every skill.
validate:
    ./scripts/validate-skills.sh

# Verify .tool-versions and the CI workflows pin consistent tool versions.
check-versions:
    uv run python tools/check_tool_versions.py .

# Run the test suite.
test:
    uv run pytest

# Run the opt-in skilltest skill evals (skilltest-pytest). NOT part of
# `just check`: each drives a real ~20-30min repo bootstrap through a harness, so
# it needs a provider (`oneharness` on PATH or a custom `SKILLTEST_PROVIDER`) plus
# a sandbox, and it never runs without the `--skilltest-e2e` opt-in. In the gate
# it skips. Pass extra pytest args to narrow, e.g. `just skilltest -x`.
# llmlint: ignore[tool_output_is_signal] a developer-facing test runner — pytest's own pass/fail output is the signal you invoke it for, exactly as `just test`/`just check` do.
skilltest *args:
    uv run pytest -m skilltest_e2e --skilltest-e2e {{args}}

# Audit this repo against the create-repo baseline invariants (dogfooding).
baseline:
    uv run --script skills/bootstrap/create-repo/scripts/check_repo_baseline.py .

# Run the cached Nx authoring targets across every project in the graph.
nx:
    bunx nx run-many -t validate smoke test

# Provision the dev toolchain for a session: ensure `just` (via uv, so a cloud
# session that ships uv/node/bun but not `just` can still run these recipes),
# verify uv/node/bun, then run setup-llmlint. Runs automatically via the Claude
# Code SessionStart hook; this is the manual entry point. Idempotent, no-ops in CI.
session-setup:
    ./scripts/session-setup.sh

# Install/refresh the optional llmlint toolchain (oneharness + llmlint). Runs
# automatically via the Claude Code SessionStart hook (through session-setup); this
# is the manual entry point for a plain terminal. Idempotent.
setup-llmlint:
    ./scripts/setup-llmlint.sh

# Optional LLM-as-judge lint (cross-language launch conventions). NOT part of
# `just check`: needs the `oneharness`+`llmlint` binaries and a Claude token, so
# it is not assumed by the uv-only gate. In a Claude Code session the SessionStart
# hook installs the binaries and selects the claude-code harness; in a terminal
# run `just setup-llmlint` once (uses the committed codex + gpt-5.5 default; auth
# via your own harness). Pass paths to narrow, e.g.
# `just lint-llm scripts/validate-skills.sh`.
lint-llm *paths:
    llmlint {{paths}}

# Fast, deterministic llmlint gate — NO model calls, no harness credential. Runs
# every static check in one pass: config structure, `llmlint: ignore` directives
# name real rules, and edited versioned fragments bumped their `version:`. This is
# a hard step of `just check` (it's model-free, so unlike the model tier it belongs
# in the gate) and the pre-flight CI runs before the model tier spends a harness
# call. Pass `--diff-base origin/main` to scope the version-bump check to the
# branch's changes, e.g. `just lint-llm-validate --diff-base origin/main`.
# `~/.local/bin` (where `bootstrap`/`setup-llmlint.sh` install llmlint via uv tool)
# is prepended so the gate resolves the binary even when it isn't already on PATH.
lint-llm-validate *args:
    PATH="$HOME/.local/bin:$PATH" llmlint validate {{args}}

# llmlint, scoped to the files this branch changed since it forked from main.
# A plain `--diff-base <ref>` uses three-dot/merge-base semantics (llmlint >=
# 0.3.15) — the `BASE...HEAD` fork-point range, not a diff against main's current
# tip — so no explicit `...HEAD` is needed. llmlint restricts the target set to
# the changed files (skipping empty diffs) and the judge to the changed lines, so
# a PR is judged on what it introduced. This is the blocking `llmlint` CI check;
# run it locally before pushing. BASE defaults to origin/main (fetch it first if
# your clone lacks it). Uses the committed codex + gpt-5.5 harness unless
# ONEHARNESS_* env overrides are set (the Claude Code SessionStart hook sets them).
lint-llm-diff base="origin/main" *args:
    llmlint --diff --diff-base "{{base}}" {{args}}

# Upgrade dependencies, then re-run the full gate.
upgrade:
    uv lock --upgrade
    uv sync
    bun update
    @just check
