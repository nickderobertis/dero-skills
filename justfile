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

# Audit this repo against the create-repo baseline invariants (dogfooding).
baseline:
    uv run --script skills/bootstrap/create-repo/scripts/check_repo_baseline.py .

# Run the cached Nx authoring targets across all skills.
nx:
    bunx nx run-many -t validate smoke test

# Upgrade dependencies, then re-run the full gate.
upgrade:
    uv lock --upgrade
    uv sync
    bun update
    @just check
