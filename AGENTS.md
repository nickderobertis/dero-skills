# AGENTS.md

Durable instructions for working in `dero-skills`. Write for a future
maintainer, not as a session log. Deterministic steps belong in scripts; this
file is for constraints, tradeoffs, and judgment.

> `CLAUDE.md` is a symlink to this file (`ln -s AGENTS.md CLAUDE.md`) so the two
> never drift. Edit `AGENTS.md` only.

## What this repo is

The canonical repository for organization **Agent Skills** and the reusable
setup automation that consuming application repos copy in. It owns the skill
folders (`skills/`), stdlib validation tooling (`tools/`, `scripts/`), the
consumer bootstrap package (`consumer-bootstrap/`), and the platform docs
(`docs/`). Authoring details live in [`docs/authoring-skills.md`](docs/authoring-skills.md).

## Two standing goals on every task

The user drives product features and their request is the priority — but carry
two goals into *every* task. When either is the lowest-error path to what the
user asked, fold it into the same task without asking first; surface the rest as
follow-ups (see "After the main task").

1. **Engineer the context for next time.** Make the next agent (and you) see
   more for less: realistic end-to-end tests that exercise what the user
   actually sees — especially when they report a bug existing tests missed (this
   repo's suite is its only QA loop, see "Tests are context engineering") —
   scripts and skills that automate repetitive steps and shrink their output to
   signal, and terse `AGENTS.md` notes capturing what the code doesn't make
   obvious.
2. **Engineer the codebase and environment.** Be the engineer the user isn't:
   prioritize the technical initiatives that keep the codebase clean,
   maintainable, and repeatable, and keep setup automated and consistent
   (`just bootstrap` from a clean clone). Strict quality gates plus local/CI
   parity make results repeatable (here, `just check` on the `.tool-versions`
   pins, kept in lockstep by `just check-versions`). A clean base and a
   reproducible environment are usually how the user's feature ships with a low
   error rate.

## Stack and composition

How this repo composes the create-repo reference pieces (it dogfoods the skill,
so the baseline checker runs against this very section):

- **Product shape:** `skills-repo` — it authors and validates Agent Skills.
- **Language(s):** Python for the stdlib authoring tooling (`tools/`, the
  create-repo checker) and Bash for `scripts/`; skill scripts are portable
  per-language (PEP 723 Python, Node built-ins, Bash) and must not depend on
  this repo's toolchain.
- **References composed:** `shapes/skills-repo.md` + `languages/python.md` +
  `languages/bash.md` + `ci.md` + `project-graph.md`. Nx is **mandatory**, not an
  accelerator: the repo is a project graph and every root recipe delegates to it,
  so the gate needs bun/node as well as uv. Consumers still never run Nx.
- **Project graph:** nine projects split by test tier and by cost (see "Project
  graph" below). Fast tiers live with the code they cover; the two expensive ones
  — the judged llmlint tier and the `skilltest` eval — sit behind graph edges an
  unrelated change cannot reach.
- **Staged gate:** `just check` runs the **affected** tier, `just check all` the
  **broader** sweep — which, since this repo releases on merge, runs once at
  merge-to-main per `ci.md`.
- **Excluded, and why:** see "Excluded on purpose" below — `ty` and coverage
  (the tooling is small and stdlib-only), per-project `pyproject.toml`s, and
  direnv / `src` layout / pre-commit (anti-baggage, consistent with the skill's
  own guidance). The non-negotiable
  invariants (strict gate, e2e of real journeys, CI proving the artifact) are
  kept.

## Command surface

Use the `just` recipes; do not hand-roll equivalents. Every one delegates to Nx
— the root decides *which* projects run a target, each `project.json` decides
*what* it does. There is no hand-rolled loop over projects, and adding one is the
thing the graph exists to prevent.

- `just bootstrap` — set up from a clean clone: one install per ecosystem, never
  one per project (`bun install`, `uv sync` against the single root `uv.lock`,
  plus the uv-installed `llmlint` + `shellcheck` the gate's targets shell out to).
  Also activates the husky hooks — `commit-msg` and `pre-push`.
- `just check` — the gate at the **affected** tier: `nx affected` over
  `format-check lint validate smoke test`, which is the whole of the previous flat
  gate declared per project (ruff format/check, shellcheck, skill validation +
  smoke, the `.tool-versions`/CI pin check, `llmlint validate`, every project's
  pytest, and the create-repo baseline audit of this repo). Must pass before any
  commit or PR. `just check all` is the same gate as the **broader** tier — one
  `nx run-many` sweep, which CI runs at merge-to-main.
- `just format` / `just format-check` / `just lint` / `just validate` /
  `just test` — the individual targets, affected-scoped (`format` sweeps
  everything: formatting is not a "what changed" question). `just check-versions`
  and `just baseline` are focused entry points onto `authoring-tools:validate`
  and `repo-baseline:test`, which `check` already runs.
- `just lint-llm-validate [args]` — the deterministic, model-free `llmlint
  validate` gate (config + `llmlint: ignore` directives + fragment version bumps).
  No harness call, so it IS in `just check`; it also runs as the husky `pre-push`
  hook (skips if llmlint isn't installed; bypass with `git push --no-verify`).
- `just lint-llm [paths]` — the LLM-as-judge *model* lint. NOT in the gate — it
  drives a real harness (see "Optional LLM lint" below).
- `just skilltest [args]` — the `skilltest-pytest` skill evals. NOT in the gate
  (see "Skill evals" below); with no provider they skip.
- `just session-setup` — provision a session's dev toolchain: ensure `just`, then
  `setup-llmlint`. Runs automatically via the `SessionStart` hook (see "Harness
  split"); this is the manual entry point. Idempotent, no-ops in CI.
- `just upgrade` — upgrade dependencies, then re-run `just check`.

The gate runs **through Nx**, so `uv`, `node` and `bun` are all clean-clone
prerequisites, alongside the `uv tool` binaries `bootstrap` installs (`llmlint`,
`shellcheck`). Pins live in `.tool-versions`, kept in lockstep with CI by `just
check-versions`; how to provision them, and why a cloud session needs
`session-setup`, is in
[`docs/dev-toolchain.md`](docs/dev-toolchain.md).

### Optional LLM lint (`llmlint`)

`llmlint.yml` dogfoods the create-repo ongoing fragments for this stack via local
in-tree plugin paths, plus repo-specific uv/bun launch-convention rules. The
deterministic validate tier is in `just check` (the `llmlint-tier` project's
`validate` target); the model tier is optional locally but blocking in CI as the
separate `llmlint` job. Details: [`docs/llmlint.md`](docs/llmlint.md).

### Skill evals (`skilltest-pytest`)

Skills have opt-in end-to-end evals (`skilltest-pytest`, a dev dep) driving the
skill through a real harness. Each is a project of its own declaring a `skilltest`
target — a name no gate tier fans out over — so `just check` cannot collect it.
`just skilltest` runs one and is what sets `SKILLTEST_E2E=1`; without that the
cases skip even under a bare repo-wide `pytest`. `create-repo`'s is documented in
[`skills/bootstrap/create-repo/tests/AGENTS.md`](skills/bootstrap/create-repo/tests/AGENTS.md).

## Commits, releases, and merging

- **Conventional Commits are required.** The type drives releases: `feat:` →
  minor, `fix:` → patch, `feat!:`/`BREAKING CHANGE:` → major; other types
  (`chore`, `ci`, `docs`, `refactor`, `test`, `build`, `perf`) ship no release.
  Enforced locally by the husky `commit-msg` hook (activated by `just bootstrap`)
  and in CI by the `commitlint` job.
- **Squash-merge only, via PR, with auto-merge.** `main` is protected: merge
  commits and rebase-merging are disabled, and a PR can only land once the
  required status checks are green (admins may bypass). **The required set is
  exactly `check`, `commitlint`, `llmlint`** — the three contexts `ci.yml` reports
  on a `pull_request`, and the record whoever applies repository settings works
  from. `check-all` is deliberately *not* in it: it runs only on `push` to main,
  so a required context would never report on a PR and would block every one
  forever. `check` runs the gate at the affected tier, e2e tiers included as
  projects of their own. Queue a PR with
  `gh pr merge --auto --squash` and it merges itself when the checks pass. The
  **PR title** becomes the squash commit subject, so it must be a valid
  Conventional Commit — that is what `commitlint` lints and what
  semantic-release reads. Merged branches auto-delete.
- **Every PR gets a suppressions comment, and it is not a gate.** The
  `notignored` workflow posts the lint/type-check suppressions the PR adds, so a
  high-level review sees the checks it switched off. Deliberately **not** a
  required check: it skips fork PRs (read-only token, no comment), and a required
  context that never reports would block them forever.
- **PRs follow the template.** `.github/pull_request_template.md` asks for a
  terse **What** (the behavior change) and **Why** (its driver and impact), with
  an optional **Additional info** section — describe the change and its driver,
  not a walkthrough of the diff. It becomes the squash commit body.
- **Releases are automated, and the repo releases *on merge*.** On push to
  `main`, semantic-release analyses the commits since the last tag and, when
  warranted, publishes a GitHub Release + tag (`.releaserc.json`). It does not
  commit back to `main`, so it needs no bypass token. Nothing batches behind a
  release train, so the merged commit *is* the released commit: per `ci.md`'s
  rule the broader sweep runs at **merge-to-main** (`check-all`, which `release`
  depends on) and no later job re-gates that tree — the fact a later reader needs
  to tell a legitimate sweep from a duplicate.

## Project graph

The map, the uniform target names, the promoted tiers and the named inputs
holding cross-project edges in place are in
[`docs/project-graph.md`](docs/project-graph.md). Two rules belong here because
breaking either is silent: **`just check` fans out over `format-check lint
validate smoke test` and nothing else** (the expensive tiers use their own names,
`skilltest` and `lint-llm`, so the gate cannot reach them — `tests/project-graph/`
catches an addition), and **a target reading a file outside its project needs a
named input in `nx.json`**, or a cached pass outlives an edit to that file.

## Invariants (non-negotiable)

- **Runtime independence.** A skill's bundled scripts run in *consuming* repos,
  where none of this repo's authoring tooling exists. They must not depend on
  Nx, the repo-root `pyproject.toml`/`uv.lock`/`package.json`/`bun.lock`,
  asdf, direnv, or imports from `skills/`/`tools/`. Python skill scripts are
  self-contained via PEP 723 (`uv run --script`); JS skill scripts use Node
  built-ins; only a skill with its own `package.json` may use bun. (This repo
  provisions its *own* dev toolchain via asdf/`.tool-versions` — separate from
  this rule, which is that portable skill scripts must not *assume* such a
  manager, since consuming repos may not have one.)
- **SKILL.md frontmatter.** `name`, `description`, `compatibility` are required;
  `name` equals the directory basename and matches `^[a-z0-9]+(?:-[a-z0-9]+)*$`;
  `description` is trigger-oriented ("Use when ...").
- **Strict gate, no warnings-only.** A diagnostic is an error or is suppressed
  with a documented, tracked rationale.
- **Nx orchestrates, and never ships.** The graph is mandatory and the gate runs
  through it, but no script under `skills/**/scripts/` may invoke Nx — consumers
  of a skill never run it. Keep skill project naming `<scope>-<name>`.
- Never commit secrets, credentials, PHI, PII, or customer data.

## Scripts and output are context

- Tooling here is stdlib-only on purpose so it runs without installs.
- Scripts stay quiet on success and, on failure, print the exact error and a
  concrete next action. Treat all command output as context the next agent reads.

## Tests are context engineering

AI agents drive this repo with little human testing, so the suite is the *only*
QA loop — realism and complete coverage are how you and the next agent see
whether things work. A rule, not a preference.

- **Never mock the layer under test.** Run skill scripts the way a consuming repo
  would — `uv run --script` against real temp files and subprocesses — not a
  mocked stand-in. A mocked "e2e" proves the mock; a green mocked suite is worse
  than none (the next agent builds on its false confidence).
- **Complete, not minimal.** Cover every journey — happy path *and*
  failure/recovery — not one smoke test. A feature isn't done until a real e2e
  journey exercises it. Coverage is a floor, not the target.
- The baseline checker is dogfooded by the `repo-baseline` project, which fails
  on an **advisory** as well as an error — this is where those rules are written,
  so an advisory means the canonical example has fallen behind its own guidance.
- Graph assertions belong in `tests/project-graph/`, which drives the real `nx`
  binary: a `project.json` read back to itself proves nothing.

## Keeping the allowlist current

- The agent command allowlist lives in `.claude/settings.json`; the tool
  enforces it, so this file does not restate "follow the allowlist."
- Keep it current: when a new routine command joins the workflow (a new `just`
  recipe, say), add it to the allowlist instead of re-approving it each session.
  Keep it narrow and prefer allowlists over deny lists.

## Excluded on purpose

- `ty` (type checking) and coverage are not in the gate: the authoring code is
  small and stdlib-only, so ruff + pytest + skill validation is the right bar.
- **No per-project `pyproject.toml` / uv workspace members.** Nothing for uv to
  resolve — one stdlib-only dependency set, `package = false`, one root
  `uv.lock`, self-contained skill scripts — so extra manifests would be the
  packages-invented-to-have-projects `project-graph.md` says the mandate does not
  ask for. Revisit when a project needs a dependency the others must not.
- No direnv/`src` layout/pre-commit and no install profiles by default —
  consistent with the skill's own anti-baggage guidance.

## After the main task: refine and hand off

After completing the requested work, act on the two standing goals above:
propose materially-helpful follow-ups — refinements to scripts, this `AGENTS.md`,
skills, tests, or docs — and note each one's likely impact on future work. Skip
busywork; if nothing is materially helpful, say so.
