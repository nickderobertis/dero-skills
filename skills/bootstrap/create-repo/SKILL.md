---
name: create-repo
description: Use when creating a new repository or bootstrapping an existing project's tooling, tests, AGENTS.md, and CI so the setup fits the actual product and enforces strict, deterministic quality gates.
compatibility: Requires uv and Python 3.12+ only if the bundled repo-baseline checker script is used.
---

# Create repo

Use this when standing up a new repository, or retrofitting an existing one, so
its tooling, tests, instructions, and CI fit the actual product and hold a high,
deterministic quality bar. The goal is a repo a future maintainer (or agent) can
clone, run one command, and trust.

## How to apply

1. **Identify the product shape first.** Name the artifact (Python
   package/service/CLI, TypeScript/Next.js app, Rust CLI, Bash/asdf plugin,
   skills repo, ...). Pick the matching section in
   [`references/stack-add-ons.md`](./references/stack-add-ons.md) and adapt — do
   not paste a generic template. Write down which guidance you excluded and why.
2. **Establish one command surface.** Add a `just` recipe set: `bootstrap`,
   `check`, `test`, `lint`, `format`, `upgrade`. Start from
   [`assets/justfile.template`](./assets/justfile.template). `just bootstrap`
   must work from a clean clone; `just check` is the full gate.
3. **Make the gates strict and deterministic.** Formatting, linting, type
   checking, and tests fail on issues — no warnings-only mode.
4. **Write `AGENTS.md`.** Use [`assets/AGENTS.md.template`](./assets/AGENTS.md.template)
   for repo-wide invariants; add nested `AGENTS.md` where a subtree differs. If
   the repo uses Claude, make `CLAUDE.md` a symlink to `AGENTS.md`.
5. **Add CI that proves the artifact.** A clean checkout must bootstrap from
   scratch and run the complete gate, on the supported platform matrix.
6. **Audit the result.** Run the baseline checker against the repo:

   ```bash
   uv run --script scripts/check_repo_baseline.py /path/to/repo
   ```

   It flags missing `AGENTS.md`, a `CLAUDE.md` that is not symlinked to it, a
   missing or incomplete `justfile` command surface, and missing CI workflows.

## Principles

1. **Start from the project's actual product shape.**
   - Identify the artifact (Python package/service/CLI, TS app, Rust CLI,
     Bash/plugin, skills repo, etc.).
   - Adapt tooling, tests, layout, docs, and CI to that artifact; don't blindly
     apply a generic template.
   - Explicitly state what guidance was excluded and why.
2. **One command-oriented workflow.**
   - Provide a small, memorable command surface (typically via `just`):
     `bootstrap`, `check`, `test`, `lint`, `format`, `upgrade`.
   - `just bootstrap` must work from a clean clone; `just check` is the full
     quality gate.
   - Success output should be minimal; failure output should be concise but
     actionable.
   - No warnings-only mode: diagnostics are errors or intentionally suppressed
     with a documented reason.
3. **Quality gates must be strict and deterministic.**
   - Formatting, linting, type checking, and tests must be enforced and fail on
     issues.
   - Prefer deterministic, reproducible checks; avoid environment-dependent
     behavior.
   - Any suppressed diagnostics must have a clear rationale and tracking.
4. **E2E tests should cover real user journeys.**
   - E2E must exercise the built artifact the way users run it (not just
     unit-level smoke tests).
   - Cover critical success and failure paths; validate behavior at boundaries
     (process / network / filesystem).
5. **`AGENTS.md` is the durable instruction layer.**
   - Root `AGENTS.md` defines repo-wide constraints; add nested `AGENTS.md`
     where subtree rules differ.
   - Include `tests/AGENTS.md` when test conventions matter.
   - If using Claude, make `CLAUDE.md` a symlink to `AGENTS.md` to avoid drift.
   - Docs and comments should be written for future readers, not as a session
     log.
6. **Narrow agent permissions and safe automation.**
   - Configure a narrow allowlist early where agent tooling is used.
   - Avoid broad shell access; allow only predictable commands needed to build,
     test, and release.
   - Prefer allowlists over deny lists; keep dangerous operations out unless
     required.
7. **Deterministic work goes into scripts; judgment stays in instructions.**
   - Script repeatable steps (setup, checks, generation, fixtures, releases).
   - Use `AGENTS.md` for judgment points, tradeoffs, and handoffs — don't force
     manual repetition.
8. **Prefer official, typed, async clients at boundaries.**
   - Prefer official clients if async and well-typed; otherwise write a narrow
     typed client.
   - Validate external inputs at trust boundaries (runtime schema validation);
     keep boundary code explicit and tested.
9. **Tooling should be modern, but repo-native.**
   - Choose idiomatic tools for the ecosystem; don't import conventions from
     other stacks without need.
   - Keep the default path simple; optional tooling must earn its complexity.
10. **CI should prove the artifact on realistic platforms.**
    - CI must bootstrap from scratch and run the complete quality gate.
    - Test on the supported OS/platform matrix; validate that generated files
      are up to date.
    - CI should simulate a future maintainer or user, not just a developer's
      local environment.
11. **Releases and distribution should be first-class when applicable.**
    - If the repo produces installable artifacts, include packaging and release
      automation.
    - Include checksums and signing if appropriate; document versioning and
      compatibility expectations.
12. **Avoid unnecessary template baggage.**
    - Exclude tools and layouts that don't fit (asdf, direnv, `src` layout,
      pre-commit, etc.) unless clearly justified.
    - Avoid previews, multiple install profiles, and noisy defaults; minimize
      ongoing maintenance burden.
13. **Updates should be regular and validated.**
    - Provide `just upgrade` (or equivalent), document the update procedure, and
      run the full gate after upgrades.
    - Avoid pinning unless necessary; treat upgrades as routine, scripted
      maintenance.
14. **Architecture guidance should be explicit but not over-prescriptive.**
    - Specify non-negotiable invariants (boundaries, portability, security, data
      validation).
    - Leave implementation flexibility inside those invariants; encode
      invariants in tests and docs.

## Stack-specific add-ons

The principles above are stack-agnostic. Apply exactly one product add-on plus
the CI add-on from [`references/stack-add-ons.md`](./references/stack-add-ons.md):

- Python repo (package / service / CLI)
- TypeScript / Next.js app
- Rust CLI repo
- Bash / asdf plugin-style repo
- Skills repo / multi-skill tooling repo
- GitHub Actions / CI patterns (applies to all of the above)

## Templates and checker

- [`assets/justfile.template`](./assets/justfile.template) — starter command
  surface with the six required recipes.
- [`assets/AGENTS.md.template`](./assets/AGENTS.md.template) — starter durable
  instruction layer.
- [`scripts/check_repo_baseline.py`](./scripts/check_repo_baseline.py) —
  deterministic audit of the stack-agnostic invariants.
