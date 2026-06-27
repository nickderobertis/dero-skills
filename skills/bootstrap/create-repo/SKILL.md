---
name: create-repo
description: Use when creating a new repository or bootstrapping an existing project's tooling, tests, AGENTS.md, and CI so the setup fits the actual product and enforces strict, deterministic quality gates.
compatibility: Bundled scripts need uv and Python 3.12+ (the plan composer and the repo-baseline checker); the GitHub governance setup script also needs an authenticated `gh` CLI with admin rights on the target repo.
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
   prompts. Keep `AGENTS.md` terse — it is always-loaded context. Folder-scoped
   rules belong in a nested `AGENTS.md`; content that is neither always relevant
   nor cleanly scoped to one folder belongs in a reference doc linked from
   `AGENTS.md`, not inlined (see Principle 5). Optionally add a `SessionStart`
   hook in `.claude/settings.json` pointing at an idempotent, non-blocking
   `scripts/session-setup.sh` that provisions the dev toolchain (and skips in
   CI), plus a `PreToolUse` hook if you enforce the allowlist with a tool — both
   stay quiet on success.
2. **Compose the plan for your stack.** Name the artifact (CLI, web app, library
   / service, asdf plugin, skills repo, ...) and the implementation language(s),
   then run the composer — it mixes and matches the references for *your* stack
   and emits one document: the composed guidance followed by a single
   verification checklist assembled from the `## Verification` items that live
   with each reference.

   ```bash
   uv run --script scripts/compose_repo_plan.py --shape cli --language python \
     [--releasing] [--monorepo] [--intersection <name>] -o REPO_PLAN.md
   ```

   It always pulls in `base.md` (the always-applied invariants) and `ci.md`, adds
   the product shape and language(s), pulls in `releasing.md`/`monorepo.md` when
   you pass `--releasing`/`--monorepo`, and auto-includes the right intersection
   (e.g. `cli` + `python` → `python-cli`). Run `--list` to see the available
   flags, or [`references/composing.md`](./references/composing.md) for the model
   and worked examples. Pass `--monorepo` when the repo breaks into more than one
   deliverable (multiple apps, packages, or languages): each project keeps its
   own shape + language, the root command surface delegates to an orchestrator
   (Nx), and CI runs only affected projects. **Record the composition** the plan
   prints — the shape, the language(s), the references composed, and which
   guidance you excluded and why — in the "Stack and composition" section of
   `AGENTS.md`; the baseline checker verifies it is filled in. Exclusions are for
   *optional tooling and layout* that doesn't fit (asdf, direnv, `src` layout, a
   release pipeline) — never for the non-negotiable invariants: a strict gate,
   realistic un-mocked e2e of every real user journey, and CI that proves the
   artifact. Those are not optional and are not "excluded with a rationale."
3. **Establish one command surface.** Add a `just` recipe set: `bootstrap`,
   `check`, `test`, `lint`, `format`, `upgrade`, from
   [`assets/justfile.template`](./assets/justfile.template). `just bootstrap`
   must work from a clean clone; `just check` is the full gate and must run the
   tests — including `test-e2e` — not just lint. Replace every `TODO`
   placeholder body with the real, stack-specific command; a recipe left as an
   `echo` placeholder is a gate that proves nothing.
4. **Make the gates strict and deterministic.** Formatting, linting, type
   checking, and tests fail on issues — no warnings-only mode. Tests run with
   coverage measured and the gate fails below the threshold; 95% line coverage
   is the default bar (lower it only with a documented reason in `AGENTS.md`).
5. **Make every script agent-friendly.** A script's output is context the next
   agent reads. Emit almost nothing on success; on failure, print the exact
   error and a concrete suggested action.
6. **Automate realistic tests — an invariant, not a preference.** In an
   agent-driven repo the test suite is the *only* QA loop, so realism and
   completeness carry the same force as the strict gate. **Never mock the layer
   under test** — an "e2e" that mocks the network, files, subprocess, or the
   entry point proves the mock, not the product, and a green mocked suite is
   worse than none (the next agent builds on its false confidence). Drive the
   real artifact across real boundaries the way a user does. "Done" means
   **complete, not minimal**: every user-facing journey, happy path *and*
   failure/recovery — not one smoke test. Coverage is a floor (satisfiable with
   mocks that prove nothing), not the target. Enumerate the journeys in
   `AGENTS.md` so coverage is auditable and grows with the repo.
7. **Add CI that proves the artifact.** A clean checkout must bootstrap from
   scratch and run the complete gate (`just bootstrap` then `just check`), on
   the supported platform matrix. Start from
   [`assets/ci.yml.template`](./assets/ci.yml.template). CI that doesn't
   actually invoke the gate proves nothing. If the repo ships something users
   install, CI must *also* prove the **end-user install path**: a separate job
   that installs the artifact via the recommended end-user method (ideally one
   cross-platform script or command) on the real platform matrix and smoke-tests
   the installed entry point — `just bootstrap` sets up the *dev* environment,
   not the user's. See [`references/ci.md`](./references/ci.md). CI only gates if
   the platform blocks a merge until it is green, so also configure the repo's
   merge model and branch protection — squash-merge only, auto-merge on, all
   gating checks (including the full-e2e gate job and the separate, fork-safe
   `llmlint` LLM-judge check — see [`references/llmlint.md`](./references/llmlint.md))
   required, head branches deleted on merge, admins able to override — per the
   "Repository settings"
   section of [`references/ci.md`](./references/ci.md). Record the model in the
   "Commits, releases, and merging" section of `AGENTS.md`. Add a required
   GitHub pull-request template
   ([`assets/pull_request_template.md.template`](./assets/pull_request_template.md.template)
   → `.github/pull_request_template.md`) so every PR states the behavior change
   (**What**) and its driver and impact (**Why**) in terse, pithy prose — not a
   walkthrough of the diff; a third **Additional info** section is optional and
   usually omitted. Because the squash body is taken from the PR description,
   this is also what lands in history.
8. **Run the gate yourself and iterate to green.** Do not declare the repo done
   from inspection. Actually run `just check` (which includes `test-e2e`) and
   iterate until it passes from a clean state.
9. **Upgrade to the latest dependencies, then audit.** As one of the last steps,
   run `just upgrade` so the repo lands on *current* dependency versions instead
   of whatever you happened to scaffold with — a freshly-created repo should
   start life on the latest deps, not stale ones. Because `upgrade` re-runs the
   full gate, this both refreshes the lockfiles and proves the repo still passes
   on the upgraded versions; commit the refreshed lockfiles. Then run the
   baseline checker:

   ```bash
   uv run --script scripts/check_repo_baseline.py /path/to/repo
   ```

   It is silent on success and, on failure, names each missing invariant with a
   suggested fix. The repo is not done until `just upgrade` (gate included) and
   the checker both pass — fix failures, don't narrate them as next steps.

## Verification (run before handing off)

Do not declare the repo done from inspection. The checklist is **not a static
list here** — it is assembled per-stack by the composer (step 2) from the
`## Verification` items that live with each reference, so it carries exactly the
checks your shape, language(s), and cross-cutting choices imply and grows when a
reference does. Walk the checklist the plan emitted, in order, confirming each
component piece is actually present and real — most "skipped steps" are an item
there that was assumed rather than checked. This is the floor, not the ceiling;
the two automated gates the checklist ends on are necessary but not sufficient.

If you no longer have the plan, regenerate it (`compose_repo_plan.py --shape ...
-o REPO_PLAN.md`) and walk its checklist. It always opens with `base.md`'s
universal items (agent layer, recorded composition, command surface, strict
gate, coverage, real e2e, upgrade) and closes with the two automated gates:

1. `just check` passes locally from a clean state, **and**
2. the baseline checker passes:

   ```bash
   uv run --script scripts/check_repo_baseline.py /path/to/repo
   ```

Fix failures until both are green — do not narrate them as next steps. The
checker is silent on success and, on failure, names each missing invariant with
a suggested fix.

## Principles

1. **Start from the project's actual product shape.**
   - Identify the artifact (Python package/service/CLI, TS app, Rust CLI,
     Bash/plugin, skills repo, etc.).
   - Adapt tooling, tests, layout, docs, and CI to that artifact; don't blindly
     apply a generic template.
   - Explicitly state what guidance was excluded and why — but only optional
     tooling/layout qualifies. The non-negotiable invariants (strict gate,
     realistic un-mocked e2e of every real journey, CI proving the artifact) are
     never excluded.
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
   - Coverage is a default gate, not opt-in: measure it on the test run and fail
     below the threshold (95% line coverage by default). Lower the bar only with
     a documented reason in `AGENTS.md`.
   - Prefer deterministic, reproducible checks; avoid environment-dependent
     behavior.
   - Any suppressed diagnostics must have a clear rationale and tracking.
4. **E2E covers real user journeys — realism is non-negotiable.**
   - In an agent-driven repo the test suite is the *only* QA loop, so this carries
     the same force as the strict gate, not the weight of a preference.
   - **Never mock the layer under test.** Mocking the network, filesystem,
     subprocess, or the entry point yields an "e2e" that proves the mock, not the
     product; a green mocked suite is worse than none (false confidence the next
     agent builds on). Drive the real artifact across real boundaries the way a
     user does. Mock only a genuinely external third party you can't run, and say
     which.
   - "Done" means **complete, not minimal**: every user-facing journey, success
     **and** failure/recovery, validated at boundaries — landing in the suite,
     the source of truth for what's covered, as features land.
   - E2E runs in the default `just check` and CI — part of the gate, not opt-in. A
     test too expensive for every run is a documented exception CI still executes
     (e.g. nightly), never silently excluded (no default `#[ignore]`, deselected
     marker, or check-skips-e2e wiring).
5. **`AGENTS.md` is the durable instruction layer — keep it terse.**
   - Root `AGENTS.md` is always-loaded context: every agent session reads it, so
     its length is a standing tax on the context budget. Use terse, pithy
     language and resist letting it accrete.
   - Apply an inclusion test before adding a line: keep it only if it is relevant
     to a future task **and** the task wouldn't surface it anyway (a failing
     gate, `just --list`, the code, or a linked doc). State the rule or intent;
     leave the mechanism, tool/rule-IDs, and inventories of what a tool already
     shows to their source of truth — link it, don't restate it. A deliberate
     *decision* (why the tooling is what it is, what was excluded and why) passes:
     it is future-relevant and not otherwise recoverable.
   - Place content by relevance and scope. Always-relevant, repo-wide
     constraints stay in the root `AGENTS.md` (short). Rules cleanly scoped to a
     subtree go in a nested `AGENTS.md` in that folder (e.g. `tests/AGENTS.md`),
     loaded only when working there. Content that is **neither always relevant
     nor cleanly scoped to one folder** moves out into a reference doc (e.g.
     under `docs/`) linked from `AGENTS.md` and pulled in on demand — not inlined.
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
      automation. Conventional Commits drive it: a bot computes the version,
      writes the changelog and manifests, and tags; the tag triggers build and
      publish. **Releasing is fully automated with no manual deploy step** — the
      only human action is merging a PR; nobody hand-edits a version, hand-tags,
      or hand-dispatches a publish. Decouple versioning from building, and lint
      the PR title (the squash commit the release reads) as a required check. See
      `references/releasing.md`.
    - Include checksums and signing if appropriate; document versioning and
      compatibility expectations.
    - Document one recommended end-user install method and keep CI installing it
      verbatim, so the path users take is continuously proven on real platforms.
      When a CLI ships through several install surfaces, keep them all on one
      asset-naming contract (see `references/shapes/cli.md`).
12. **Avoid unnecessary template baggage.**
    - Exclude tools and layouts that don't fit (asdf, direnv, `src` layout,
      heavyweight pre-commit frameworks, etc.) unless clearly justified. A *fast*
      pre-commit/pre-push hook that just calls the gate (lefthook, or husky where
      JS exists) is fine; the baggage to avoid is a framework that re-specifies
      the tools the gate already runs.
    - Avoid previews, multiple install profiles, and noisy defaults; minimize
      ongoing maintenance burden.
13. **Updates should be regular and validated.**
    - Provide `just upgrade` (or equivalent), document the update procedure, and
      run the full gate after upgrades.
    - Run `just upgrade` as one of the last setup steps too, so a freshly-created
      repo starts on the latest dependencies rather than whatever was scaffolded.
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
16. **Tests are context engineering — and the only QA loop.**
    - These repos run on AI agents with little human testing, so the suite is how
      you and the next agent *see* whether the system works. Realism and complete
      coverage are the only way it stays reliable without a human in the loop — a
      rule, not a "nice to have."
    - Drive the real artifact across real boundaries from the user's side. Don't
      mock the layer under test or settle for a happy-path smoke test — a green
      mocked suite is a liability, not coverage.
    - Make the journeys a growing contract in the suite — the source of truth for
      what's covered: when a feature lands, its real e2e journey lands with it.
17. **Every task carries two standing goals beyond the user's ask.**
    - The user drives product features; their request is the priority. But carry
      two goals into *every* task: (1) engineer the context for next time —
      realistic e2e tests that exercise what the user sees (especially for bugs
      existing tests missed), scripts/skills that automate repetitive steps and
      shrink their output to signal, and terse `AGENTS.md` notes capturing what
      the code doesn't make obvious; (2) engineer the codebase and environment —
      prioritize the technical initiatives that keep it clean, maintainable, and
      repeatable, with automated, consistent setup.
    - When either goal is the lowest-error path to what the user asked, fold it
      into the same task without asking first. Otherwise propose it as a
      materially-helpful follow-up after the main task, judging each one's impact
      and skipping busywork. The default `AGENTS.md` encodes both goals so they
      compound over time.

## Composable references

References mix and match instead of forming one monolithic template per stack.
The composer (step 2) assembles them — pick **one product shape**, pass the
**language(s)** it is built in, and it always pulls in `base.md` and `ci.md` and
prefers a focused intersection (for example `python-cli`) where one exists. Where
you hit an intersection that has no reference yet, create one — adding the file
extends the composer's `--intersection` choices automatically. See
[`references/composing.md`](./references/composing.md) for the catalog and worked
examples.

Each reference carries its own `## Verification` section, and the composer lifts
those items into the plan's single checklist — so a check lives next to the
guidance that motivates it, and editing one reference updates both.

- **Always applied** — `base.md` (the shape/language-agnostic invariants, first
  in every plan) and `ci` (GitHub Actions, on top of every shape).
- **Product shapes** — `cli`, `web-app`, `nextjs`, `library`, `skills-repo`,
  `asdf-plugin` (language-agnostic where possible).
- **Languages** — `python`, `typescript`, `rust`, `bash`.
- **Cross-cutting (flagged)** — `releasing` (Conventional Commits → automated
  release), via `--releasing` when the repo ships a versioned artifact;
  `monorepo` (Nx orchestration, affected-only jobs, output caching), via
  `--monorepo` when the repo holds more than one app, package, or language.
- **Intersections** — e.g. `python-cli`, `rust-cli`, added when guidance is
  needed where a shape and a language meet.

## Templates, composer, and checker

- [`scripts/compose_repo_plan.py`](./scripts/compose_repo_plan.py) — the
  composer (step 2). Takes `--shape`, `--language` (repeatable), `--releasing`,
  `--monorepo`, and `--intersection`, and emits one document for that stack: the
  composed guidance plus a single verification checklist assembled from each
  reference's `## Verification` items. Discovers the available flags by scanning
  `references/`, auto-derives intersections (`cli` + `python` → `python-cli`) and
  the Next.js → web-app + TypeScript implications, and writes the plan to stdout
  or `-o FILE` with notes on stderr. `--llmlint-config FILE` /
  `--llmlint-buildout-config FILE` additionally compose the repo's `llmlint.yml`
  (the LLM-judge tier) by wiring the selected references' rule fragments in as
  `@version`-pinned plugins — see [`references/llmlint.md`](./references/llmlint.md).
  Self-contained via PEP 723. Run `--list` to see the catalog.
- [`assets/AGENTS.md.template`](./assets/AGENTS.md.template) — starter durable
  instruction layer.
- [`assets/claude-settings.json.template`](./assets/claude-settings.json.template)
  — narrow `.claude/settings.json` allowlist for the `just` command surface.
- [`assets/justfile.template`](./assets/justfile.template) — starter command
  surface with the six required recipes plus `test-e2e` wired into `check`.
- [`assets/ci.yml.template`](./assets/ci.yml.template) — GitHub Actions workflow
  that bootstraps a clean checkout and runs the full gate on a platform matrix,
  plus a separate, fork-safe `llmlint` job (the diff-scoped LLM-judge check).
- [`assets/setup-llmlint.sh.template`](./assets/setup-llmlint.sh.template) /
  [`assets/lint-llm-diff.sh.template`](./assets/lint-llm-diff.sh.template) — drop
  at `scripts/setup-llmlint.sh` (idempotent toolchain install, wired into the
  SessionStart hook) and `scripts/lint-llm-diff.sh` (the merge-base-scoped lint
  the `llmlint` CI check runs). See [`references/llmlint.md`](./references/llmlint.md).
- [`assets/pull_request_template.md.template`](./assets/pull_request_template.md.template)
  — required GitHub PR template: terse **What** (the behavior change) and **Why**
  (its driver and impact), with an optional **Additional info** section. Drop it
  at `.github/pull_request_template.md`.
- [`scripts/check_repo_baseline.py`](./scripts/check_repo_baseline.py) — audits
  `AGENTS.md`, the `CLAUDE.md` symlink, `.claude/settings.json`, the recorded
  reference composition, the `justfile` command surface, an e2e signal, a
  coverage signal, CI, and the GitHub PR template. Goes past presence: it fails
  on `TODO` placeholder recipe bodies, a `check` that doesn't run `test`, a
  missing e2e tier, no coverage signal (a coverage recipe/config or a documented
  `AGENTS.md` decision), an unfilled "Stack and composition" section, CI that
  never invokes `just check`, and a PR template that is missing or lacks its
  **What**/**Why** sections. It also raises an advisory (non-failing) warning when the
  root `AGENTS.md` grows past a soft line cap — a nudge to keep the always-loaded
  instruction layer terse. Silent on success; on failure prints each missing
  invariant with a suggested fix.
- [`scripts/setup_github_governance.py`](./scripts/setup_github_governance.py) —
  applies the merge model + branch protection from the "Repository settings"
  section of [`references/ci.md`](./references/ci.md) to a GitHub repo via the
  `gh` CLI. Idempotent; takes the required check contexts as arguments and
  supports `--dry-run`. Configures squash-only merging, auto-merge,
  delete-on-merge, and branch protection requiring every gating check (admins
  can override by default).
