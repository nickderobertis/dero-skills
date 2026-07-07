# Command surface for the dero-skills authoring repo.
#
# `just bootstrap` works from a clean clone; `just check` is the full gate.
# The gate runs on uv alone (no Node needed); bun/Nx is an authoring
# accelerator wired up by `bootstrap` and exposed via `just nx`.

# List available recipes.
default:
    @just --list

# Set up from a clean clone: Python (uv) and the Node/Nx authoring toolchain.
bootstrap:
    uv sync --locked
    bun install --frozen-lockfile

# Full quality gate. Must pass before every commit and in CI.
check: format-check lint validate check-versions test baseline

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

# Run the cached Nx authoring targets across all skills.
nx:
    bunx nx run-many -t validate smoke test

# Install/refresh the optional llmlint toolchain (oneharness + llmlint). Runs
# automatically via the Claude Code SessionStart hook; this is the manual entry
# point for a plain terminal. Idempotent.
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

# llmlint, scoped to the files this branch changed since it forked from main
# (the merge-base diff, not a diff against main's current tip). This is the
# blocking `llmlint` CI check; run it locally before pushing. BASE defaults to
# origin/main. Uses the committed codex + gpt-5.5 harness unless ONEHARNESS_*
# env overrides are set (the Claude Code SessionStart hook sets them).
lint-llm-diff base="origin/main":
    ./scripts/lint-llm-diff.sh {{base}}

# Upgrade dependencies, then re-run the full gate.
upgrade:
    uv lock --upgrade
    uv sync
    bun update
    @just check
