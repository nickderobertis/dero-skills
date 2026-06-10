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

1. **Lay down the agent layer first.** Before anything else, create `AGENTS.md`
   (start from [`assets/AGENTS.md.template`](./assets/AGENTS.md.template)),
   symlink `CLAUDE.md` to it (`ln -s AGENTS.md CLAUDE.md`), and add
   `.claude/settings.json` with a narrow command allowlist (start from
   [`assets/claude-settings.json.template`](./assets/claude-settings.json.template)).
   Doing this first means the rest of the setup runs with far fewer approval
   prompts.
2. **Identify the product shape.** Name the artifact (CLI, web app, library /
   service, asdf plugin, skills repo, ...) and the implementation language(s).
   Compose references by mixing and matching — see
   [`references/composing.md`](./references/composing.md): one product shape, the
   language(s) it is built in, and `ci.md`. If the repo breaks down into more
   than one deliverable — multiple apps, packages, or languages — also pull in
   [`references/monorepo.md`](./references/monorepo.md): each project keeps its
   own shape + language, the root command surface delegates to an orchestrator
   (Nx), and CI runs only affected projects. Write down which guidance you
   excluded and why. Exclusions are for *optional tooling and layout* that
   doesn't fit (asdf, direnv, `src` layout, a release pipeline) — never for the
   non-negotiable invariants: a strict gate, e2e of real user journeys, and CI
   that proves the artifact. Those are not optional and are not "excluded with a
   rationale."
3. **Establish one command surface.** Add a `just` recipe set: `bootstrap`,
   `check`, `test`, `lint`, `format`, `upgrade`, from
   [`assets/justfile.template`](./assets/justfile.template). `just bootstrap`
   must work from a clean clone; `just check` is the full gate and must run the
   tests — including `test-e2e` — not just lint. Replace every `TODO`
   placeholder body with the real, stack-specific command; a recipe left as an
   `echo` placeholder is a gate that proves nothing.
4. **Make the gates strict and deterministic.** Formatting, linting, type
   checking, and tests fail on issues — no warnings-only mode.
5. **Make every script agent-friendly.** A script's output is context the next
   agent reads. Emit almost nothing on success; on failure, print the exact
   error and a concrete suggested action.
6. **Automate realistic tests.** Prefer full end-to-end tests that exercise
   features the way a user runs them. Good e2e coverage is how you and future
   agents actually see the system's behavior.
7. **Add CI that proves the artifact.** A clean checkout must bootstrap from
   scratch and run the complete gate (`just bootstrap` then `just check`), on
   the supported platform matrix. Start from
   [`assets/ci.yml.template`](./assets/ci.yml.template). CI that doesn't
   actually invoke the gate proves nothing. If the repo ships something users
   install, CI must *also* prove the **end-user install path**: a separate job
   that installs the artifact via the recommended end-user method (ideally one
   cross-platform script or command) on the real platform matrix and smoke-tests
   the installed entry point — `just bootstrap` sets up the *dev* environment,
   not the user's. See [`references/ci.md`](./references/ci.md).
8. **Run the gate yourself, then audit.** Do not declare the repo done from
   inspection. Actually run `just check` (which includes `test-e2e`) and iterate
   until it passes from a clean state; then run the baseline checker:

   ```bash
   uv run --script scripts/check_repo_baseline.py /path/to/repo
   ```

   It is silent on success and, on failure, names each missing invariant with a
   suggested fix. The repo is not done until both `just check` and the checker
   pass — fix failures, don't narrate them as next steps.

## Definition of done

The skill is applied correctly only when all of these hold. This is the compact
self-audit to run before handing off — it is the floor, not the ceiling.

- `AGENTS.md` exists; `CLAUDE.md` is a symlink to it; `.claude/settings.json`
  has a narrow allowlist.
- The `justfile` defines `bootstrap`, `check`, `test`, `lint`, `format`,
  `upgrade` with real bodies (no `TODO` placeholders left).
- `just check` runs format check + lint + type check + unit tests + e2e, and
  fails on any issue (no warnings-only mode).
- E2E exists and exercises the built artifact the way users run it, covering at
  least the primary happy path **and** one meaningful failure/recovery path —
  not a smoke test. It runs inside `just check` and CI (a too-expensive case is
  a documented exception CI still runs, e.g. nightly — never silently skipped).
- A CI workflow runs `just bootstrap` then `just check` on a clean checkout.
- If the repo ships an installable artifact, a CI job installs it via the
  recommended end-user method (ideally a cross-platform script/command) on the
  supported platform matrix and smoke-tests the installed entry point — proving
  the path users actually take, not just the dev `just bootstrap`.
- `just check` passed locally, and the baseline checker passed.

## Principles

1. **Start from the project's actual product shape.**
   - Identify the artifact (Python package/service/CLI, TS app, Rust CLI,
     Bash/plugin, skills repo, etc.).
   - Adapt tooling, tests, layout, docs, and CI to that artifact; don't blindly
     apply a generic template.
   - Explicitly state what guidance was excluded and why — but only optional
     tooling/layout qualifies. The non-negotiable invariants (strict gate, e2e
     of real journeys, CI proving the artifact) are never excluded.
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
   - E2E runs in the default `just check` and in CI — it is part of the gate,
     not an opt-in target. A test too expensive for every run is a documented
     exception that CI still executes (e.g. nightly), never silently excluded
     (no default `#[ignore]`, deselected marker, or check-skips-e2e wiring).
5. **`AGENTS.md` is the durable instruction layer.**
   - Root `AGENTS.md` defines repo-wide constraints; add nested `AGENTS.md`
     where subtree rules differ.
   - Include `tests/AGENTS.md` when test conventions matter.
   - Always make `CLAUDE.md` a symlink to `AGENTS.md` so the two never drift.
   - Set up `.claude/settings.json` with a narrow allowlist among the first
     files, to require fewer approvals while the rest of the repo is built.
   - Docs and comments should be written for future readers, not as a session
     log.
6. **Narrow agent permissions, kept current.**
   - Configure a narrow allowlist early in `.claude/settings.json`; allow only
     the predictable commands needed to build, test, and release.
   - Prefer allowlists over deny lists; keep dangerous operations out unless
     required.
   - Enforcement is the config's job, so `AGENTS.md` guidance is about keeping
     the allowlist *current*: when a new routine command joins the workflow, add
     it to the allowlist rather than re-approving it every time.
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
    - When users install the artifact, CI must also exercise the end-user
      install path — the recommended install method (ideally one cross-platform
      script/command) on the real platform matrix — then smoke-test the
      installed entry point. The dev `just bootstrap` is not that path.
11. **Releases and distribution should be first-class when applicable.**
    - If the repo produces installable artifacts, include packaging and release
      automation.
    - Include checksums and signing if appropriate; document versioning and
      compatibility expectations.
    - Document one recommended end-user install method and keep CI installing it
      verbatim, so the path users take is continuously proven on real platforms.
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
15. **Scripts and gates are agent context.**
    - Emit minimal output on success — ideally a single line, or nothing.
    - On failure, print the exact error and a concrete suggested action.
    - Every script's output is context the next agent must read; don't bury the
      signal in noise.
16. **Tests are context engineering too.**
    - Actively automate realistic tests — especially full end-to-end use of a
      feature from the user's perspective.
    - Good e2e coverage improves your own (and future agents') visibility into
      real behavior; seek it out instead of settling for unit-level smoke tests.
17. **`AGENTS.md` should compound over time.**
    - After finishing the user's main task, propose materially-helpful
      follow-ups: refinements to scripts, `AGENTS.md`, skills, or other context.
    - Judge each suggestion's impact on future work and surface only the ones
      that genuinely help; skip busywork.

## Composable references

References mix and match instead of forming one monolithic template per stack.
Pick **one product shape**, pull in the **language(s)** it is built in, and
always pull in `ci.md`. Where a focused intersection exists (for example
`python-cli`), prefer it; where you hit an intersection that has no reference
yet, create one. See [`references/composing.md`](./references/composing.md) for
the catalog and worked examples.

- **Product shapes** — `cli`, `web-app`, `nextjs`, `library`, `skills-repo`,
  `asdf-plugin` (language-agnostic where possible).
- **Languages** — `python`, `typescript`, `rust`, `bash`.
- **Cross-cutting** — `ci` (GitHub Actions), applied on top of every shape;
  `monorepo` (Nx orchestration, affected-only jobs, output caching), pulled in
  when the repo holds more than one app, package, or language.
- **Intersections** — e.g. `python-cli`, added when guidance is needed where a
  shape and a language meet.

## Templates and checker

- [`assets/AGENTS.md.template`](./assets/AGENTS.md.template) — starter durable
  instruction layer.
- [`assets/claude-settings.json.template`](./assets/claude-settings.json.template)
  — narrow `.claude/settings.json` allowlist for the `just` command surface.
- [`assets/justfile.template`](./assets/justfile.template) — starter command
  surface with the six required recipes plus `test-e2e` wired into `check`.
- [`assets/ci.yml.template`](./assets/ci.yml.template) — GitHub Actions workflow
  that bootstraps a clean checkout and runs the full gate on a platform matrix.
- [`scripts/check_repo_baseline.py`](./scripts/check_repo_baseline.py) — audits
  `AGENTS.md`, the `CLAUDE.md` symlink, `.claude/settings.json`, the `justfile`
  command surface, an e2e signal, and CI. Goes past presence: it fails on
  `TODO` placeholder recipe bodies, a `check` that doesn't run `test`, a missing
  e2e tier, and CI that never invokes `just check`. Silent on success; on
  failure prints each missing invariant with a suggested fix.
