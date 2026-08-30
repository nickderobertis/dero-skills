# Command surface for the dero-skills authoring repo.
#
# `just bootstrap` works from a clean clone; `just check` is the full gate.
# This repo is laid out as an Nx project graph, so these recipes DELEGATE to the
# orchestrator rather than listing per-project commands: the root decides *which*
# projects run a target, each project's `project.json` decides *what* that target
# does. There is no hand-rolled loop over projects anywhere below.

# The merge base the affected tier keys off, as it arrives from the environment.
# Derive it EXPLICITLY: Nx's implicit default is not deterministic across a
# shallow CI checkout. CI exports NX_BASE (via `nx-set-shas`); locally this falls
# back to the fork point from main.
_nx_base_input := env_var_or_default("NX_BASE", "origin/main")

# Validate that environment input at the boundary, before anything interpolates
# it into a command: a git ref or SHA is letters, digits and `. _ / -`, so a
# value carrying a quote, a `;`, or a `$(...)` is refused here rather than handed
# to a shell. Everything below uses `base`, never the raw input.
base := if _nx_base_input =~ '^[A-Za-z0-9._/-]+$' { _nx_base_input } else { error("NX_BASE must be a plain git ref or SHA — letters, digits and . _ / - only; got: " + _nx_base_input) }

# List available recipes.
default:
    @just --list

# Set up from a clean clone. One install per ecosystem, never one per project:
# bun installs the Node toolchain Nx runs on (and the release/commitlint tooling),
# `uv sync` resolves the whole workspace against the single root `uv.lock`, and the
# two uv-installed binaries the gate's own targets shell out to — `llmlint` for the
# model-free `llmlint validate`, `shellcheck` for the shell projects' `lint`.
bootstrap:
    bun install --frozen-lockfile
    uv sync --locked
    uv tool install --quiet shellcheck-py
    ./scripts/setup-llmlint.sh

# Full quality gate. Must pass before every commit and in CI.
#
# Defaults to the AFFECTED tier — every target this change can reach and nothing
# else, which is what development and review run. `just check all` runs the
# BROADER tier: one full sweep over every project. This repo releases on merge
# (semantic-release on push to main), so per references/ci.md that sweep belongs
# at merge-to-main and nothing downstream re-gates the same tree. The tier is a
# flag on this one command, never a second gate, so local and CI cannot drift.
#
# The targets are the whole of the previous flat gate, now declared per project:
# `format-check` (ruff format --check), `lint` (ruff check / shellcheck),
# `validate` (skill frontmatter, the .tool-versions↔CI pin check, and the
# deterministic model-free `llmlint validate`), `smoke` (every bundled skill
# script is executed once) and `test` (each project's pytest, plus the create-repo
# baseline audit of this repo). The non-deterministic llmlint model tier and the
# harness-driven skilltest eval declare target names no tier here fans out over,
# so the gate can never reach them.
#
# just resolves the tier, rather than a shell `if`, for two reasons: `just -n
# check` then prints the one command that will actually run, and a mistyped tier
# aborts instead of quietly buying the weaker tier.
check tier="affected":
    {{ if tier == "all" { "bunx nx run-many" } else if tier == "affected" { "bunx nx affected --base=" + base } else { error("unknown tier '" + tier + "' — use 'affected' (the default) or 'all'") } }} -t format-check lint validate smoke test

# Format every project's sources in place — formatting is not a "what changed"
# question, so this sweeps the whole graph rather than the affected set.
format:
    bunx nx run-many -t format

# Verify formatting without modifying files, for the projects this change reaches.
format-check:
    bunx nx affected --base={{base}} -t format-check

# Lint the projects this change can reach; fail on findings.
lint:
    bunx nx affected --base={{base}} -t lint

# Run the deterministic validation targets (skill frontmatter + forbidden runtime
# deps, toolchain-pin consistency, llmlint config) and smoke-run every bundled
# skill script, for the projects this change reaches.
validate:
    bunx nx affected --base={{base}} -t validate smoke

# Verify .tool-versions and the CI workflows pin consistent tool versions.
check-versions:
    bunx nx run authoring-tools:validate

# The test tier on its own, for iterating: the same `test` target the gate fans
# out over, scoped to the projects this change can reach. That includes the e2e
# tiers, which are projects of their own — so e2e is gated on every change that
# can reach it rather than being an opt-in extra.
test:
    bunx nx affected --base={{base}} -t test

# Audit this repo against the create-repo baseline invariants (dogfooding). The
# audit is the `repo-baseline` project's test tier, so `just check` runs it too;
# this is the focused entry point.
baseline:
    bunx nx run repo-baseline:test

# Run the opt-in skilltest skill evals. NOT part of `just check`: `skilltest` is a
# target name no gate tier fans out over, because each eval drives a real
# ~20-30min repo bootstrap through a harness — external contact promotes it out
# of the affected tier unconditionally (references/ci.md). It needs a provider
# (`oneharness` on PATH or a custom `SKILLTEST_PROVIDER`) plus a sandbox. Pass
# extra pytest args to narrow, e.g. `just skilltest -x`.
# llmlint: ignore[tool_output_is_signal] a developer-facing test runner — pytest's own pass/fail output is the signal you invoke it for, exactly as `just test`/`just check` do.
skilltest *args:
    bunx nx run bootstrap-create-repo-skilltest:skilltest {{args}}

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
# `just check`: it needs the `oneharness`+`llmlint` binaries and a harness token,
# and it is non-deterministic, so references/ci.md promotes it out of the gate
# unconditionally. It is the `llmlint-tier` project's `lint-llm` target — a name
# no gate tier fans out over — and that project depends on nothing, so no change
# elsewhere in the graph can reach it. In a Claude Code session the SessionStart
# hook installs the binaries and selects the claude-code harness; in a terminal
# run `just setup-llmlint` once (uses the committed codex + gpt-5.5 default; auth
# via your own harness). Pass paths to narrow, e.g.
# `just lint-llm scripts/setup-llmlint.sh`.
lint-llm *paths:
    bunx nx run llmlint-tier:lint-llm {{paths}}

# Fast, deterministic llmlint gate — NO model calls, no harness credential. Runs
# every static check in one pass: config structure, `llmlint: ignore` directives
# name real rules, and edited versioned fragments bumped their `version:`. It is
# the `llmlint-tier` project's `validate` target, so `just check` runs it; this
# recipe is the focused entry point the husky pre-push hook and CI use, where the
# `--diff-base` form additionally scopes the version-bump check to the branch's
# changes, e.g. `just lint-llm-validate --diff-base origin/main`.
# `~/.local/bin` (where `bootstrap`/`setup-llmlint.sh` install llmlint via uv tool)
# is prepended so it resolves the binary even when it isn't already on PATH.
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
# `base` is validated at the same boundary as NX_BASE above, and for the same
# reason: it is interpolated into a shell command, so a value carrying a quote, a
# `;` or a `$(...)` is refused here rather than handed to a shell.
lint-llm-diff base="origin/main" *args:
    PATH="$HOME/.local/bin:$PATH" llmlint --diff --diff-base "{{ if base =~ '^[A-Za-z0-9._/-]+$' { base } else { error("--diff-base must be a plain git ref or SHA — letters, digits and . _ / - only; got: " + base) } }}" {{args}}

# Upgrade dependencies, then re-run the gate as the BROADER tier: an upgrade can
# reach any project, so the affected set would understate it.
upgrade:
    uv lock --upgrade
    uv sync
    bun update
    @just check all
