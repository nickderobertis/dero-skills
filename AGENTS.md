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
- **Language(s):** Python for the stdlib authoring tooling (`tools/`, `scripts/`,
  the create-repo checker); skill scripts are portable per-language (PEP 723
  Python, Node built-ins, Bash) and must not depend on this repo's toolchain.
- **References composed:** `shapes/skills-repo.md` + `languages/python.md` +
  `ci.md`, with Nx from `monorepo.md` as an *optional authoring accelerator*
  (the gate runs on uv alone; consumers never run Nx).
- **Excluded, and why:** see "Excluded on purpose" below — `ty` and coverage
  (the tooling is small and stdlib-only), and direnv / `src` layout / pre-commit
  (anti-baggage, consistent with the skill's own guidance). The non-negotiable
  invariants (strict gate, e2e of real journeys, CI proving the artifact) are
  kept.

## Command surface

Use the `just` recipes; do not hand-roll equivalents.

- `just bootstrap` — set up from a clean clone (uv + the Nx authoring toolchain;
  also activates the husky `commit-msg` hook).
- `just check` — full quality gate: `ruff format --check`, `ruff check`, skill
  validation + smoke, the `.tool-versions`/CI version-consistency check,
  `pytest`, and the create-repo baseline self-check. Must pass before any commit
  or PR.
- `just format` / `just lint` / `just validate` / `just check-versions` /
  `just test` — individual steps.
- `just nx` — cached Nx authoring targets (validate/smoke/test) across skills.
- `just lint-llm [paths]` — optional LLM-as-judge lint (`llmlint`). NOT in the
  gate (see "Optional LLM lint" below).
- `just lint-llm-validate [args]` — the deterministic, model-free `llmlint
  validate` gate (config structure + `llmlint: ignore` directives + fragment
  version bumps). No harness call; NOT in the gate (needs the llmlint binary). CI
  runs it before the model tier (see "Optional LLM lint").
- `just skilltest [args]` — run the `skilltest-pytest` natural-language skill
  evals. NOT in the gate (see "Skill evals" below); with no provider they skip.
- `just upgrade` — upgrade dependencies, then re-run `just check`.

The gate runs on uv alone, so it needs no Node. Nx/bun is an optional
accelerator; `uv`, `node`, and `bun` are the clean-clone prerequisites for
`just bootstrap` (bun is the package manager and script runner; node is the
underlying runtime for the Nx/semantic-release tooling). The toolchain is
pinned in `.tool-versions`: provision `just`/`uv`/`node`/`bun` with asdf (or a
compatible manager such as mise) via `asdf install`, then run `just bootstrap`.
The `python` pin records the
targeted version (uv supplies Python per `requires-python`); `just
check-versions` keeps every pin in lockstep with CI.

### Optional LLM lint (`llmlint`)

`llmlint.yml` configures [`llmlint`](https://github.com/nickderobertis/llmlint),
an LLM-as-judge linter for invariants ruff/pytest can't express. It combines two
tiers (see the header in `llmlint.yml`):

- **The create-repo skill's ongoing rule fragments**, wired in as `plugins`.
  Because this repo *hosts* the fragments, it dogfoods them via **local in-tree
  paths** (`skills/bootstrap/create-repo/assets/llmlint/...`) rather than the
  `@version`-pinned hosted URLs the composer emits for a consuming repo — so a
  fragment edit takes effect here immediately. We pull the fragments applicable
  to this repo's stack (`base` + `shapes/skills-repo` + `languages/python` +
  `languages/bash` + `ci`); the one-time `buildout/` fragments are excluded
  because they don't persist.
- **The repo's bespoke cross-language launch conventions**, defined inline:
  **Python and Python packages run through `uv`** (`uv run`/`uv run --script`/
  `uvx`/`uv add`), **TypeScript and npm packages run through `bun`** (`bun run`/
  `bunx`/`bun add`), and the **anti-pattern of a Bash script that only wraps a
  single-language program** instead of calling uv/bun directly. Each rule's
  description carries its full uv/bun criteria, so all rules share the one
  `default` agent — no domain-specific prompt is needed.

It runs through the [`oneharness`](https://github.com/nickderobertis/oneharness)
driver (>= 0.3.0, for `llmlint`'s read-only mode and the `ONEHARNESS_<FIELD>` env
overrides; `setup-llmlint.sh` pins the concrete floor).

**Harness split — committed default vs. Claude Code.** The committed
`oneharness.toml`/`llmlint.yml` target **codex + gpt-5.5** (what a contributor
running `llmlint` from a terminal gets). Inside a Claude Code session the
`SessionStart` hook runs `scripts/setup-llmlint.sh`, which installs llmlint (via
`uv tool install llmlint-cli` — one PyPI dependency resolution that also fetches
`oneharness-cli`, no Rust toolchain or github.com reachability needed; llmlint >=
0.3.7 finds the `oneharness` binary beside its own in the tool venv, so no
separate install) and exports `ONEHARNESS_HARNESSES=claude-code` +
`ONEHARNESS_MODEL=claude-opus-4-8`
into the session — env overrides that beat the committed file, so the run uses
the only harness authenticated there (Claude Code + Opus). The flip works only
because the agents in `llmlint.yml` deliberately do **not** pin a harness/model
(pinning would emit `--harness`/`--model` flags that beat the env). `IS_SANDBOX`
is injected into just the claude-code harness via `oneharness.toml` so it runs
under root without `--dangerously-skip-permissions` refusing; `oneharness.toml`
also supplies the default harness the bundled `config_lint` plugin needs.

This is **deliberately not in `just check`**: the uv-only gate must stay
reproducible from a clean clone, whereas `llmlint` needs the separately installed
binaries and a harness token (in Claude Code the inherited session token;
elsewhere `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`, or — for the codex
default — `OPENAI_API_KEY`). Run it on demand with `just lint-llm`;
`just setup-llmlint` is the manual install path for a terminal (the hook covers
Claude Code sessions). Since the repo now runs all JS through bun (`bun install`,
`bunx nx`), the bun rule no longer fires on the authoring toolchain.

**Blocking CI check.** The `llmlint` job in `.github/workflows/ci.yml` runs two
steps on every PR. First — cheaply, with no model call or credential — `just
lint-llm-validate --diff-base origin/main` (`llmlint validate`, >= 0.3.17): the
deterministic gate that the config parses, every `llmlint: ignore` directive names
a real rule, and any edited versioned fragment bumped its `version:`. It fails
fast (cents, not dollars) on a config/suppression/version-bump slip before the
paid tier runs. Then the model tier: `just lint-llm-diff` — `llmlint --diff
--diff-base "origin/main"`, where a plain ref means three-dot/merge-base semantics
(llmlint >= 0.3.15, so no explicit `...HEAD`) and llmlint scopes both the target
set (only the changed files, skipping empty diffs) and the judge (only the *lines*
the branch changed) to the branch's **merge-base with main** (the fork point, not
main's current tip, so unrelated later commits on main are never linted, and the
judge doesn't flag pre-existing code a change merely sits near). llmlint >= 0.3.11
does the changed-file selection itself, so no wrapper script is needed. CI installs
the committed Codex harness via bun and authenticates it with the `OPENAI_API_KEY`
repo secret; without that secret the model step fails (the validate step needs no
credential). It is a separate job (not folded into `check`) to keep the clean-clone
gate uv-only — add it to the required status checks in branch protection to make it
block merges.

### Skill evals (`skilltest-pytest`)

Skills have opt-in end-to-end evals (`skilltest-pytest`, a dev dep) that drive the
skill through a real harness. They are slow and never in `just check`; run with
`just skilltest`. `create-repo`'s is documented in
[`skills/bootstrap/create-repo/tests/AGENTS.md`](skills/bootstrap/create-repo/tests/AGENTS.md).

- **Conventional Commits are required.** The type drives releases: `feat:` →
  minor, `fix:` → patch, `feat!:`/`BREAKING CHANGE:` → major; other types
  (`chore`, `ci`, `docs`, `refactor`, `test`, `build`, `perf`) ship no release.
  Enforced locally by the husky `commit-msg` hook (activated by `just bootstrap`)
  and in CI by the `commitlint` job.
- **Squash-merge only, via PR, with auto-merge.** `main` is protected: merge
  commits and rebase-merging are disabled, and a PR can only land once the
  required `check` and `commitlint` status checks are green (admins may bypass).
  `check` runs the full gate — including the e2e tests — so requiring it gates
  every merge on the same gate you run locally. Queue a PR with
  `gh pr merge --auto --squash` and it merges itself when the checks pass. The
  **PR title** becomes the squash commit subject, so it must be a valid
  Conventional Commit — that is what `commitlint` lints and what
  semantic-release reads. Merged branches auto-delete.
- **PRs follow the template.** `.github/pull_request_template.md` asks for a
  terse **What** (the behavior change) and **Why** (its driver and impact), with
  an optional **Additional info** section — describe the change and its driver,
  not a walkthrough of the diff. It becomes the squash commit body.
- **Releases are automated.** On push to `main`, semantic-release analyses the
  commits since the last tag and, when warranted, publishes a GitHub Release +
  tag with generated notes (`.releaserc.json`). It does not commit back to
  `main`, so it needs no bypass token — the changelog lives in the Release.

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
- **Nx is authoring/CI only.** Consumers never run Nx; keep its naming
  `<scope>-<name>`.
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
- The baseline checker is dogfooded: `just check` runs it against this repo, so
  the repo stays a working example of the `create-repo` skill.

## Keeping the allowlist current

- The agent command allowlist lives in `.claude/settings.json`; the tool
  enforces it, so this file does not restate "follow the allowlist."
- Keep it current: when a new routine command joins the workflow (a new `just`
  recipe, say), add it to the allowlist instead of re-approving it each session.
  Keep it narrow and prefer allowlists over deny lists.

## Excluded on purpose

- `ty` (type checking) and coverage are not in the gate: the authoring code is
  small and stdlib-only, so ruff + pytest + skill validation is the right bar.
- No direnv/`src` layout/pre-commit and no install profiles by default —
  consistent with the skill's own anti-baggage guidance. The dev toolchain is
  pinned in `.tool-versions` (provisioned via asdf or a compatible manager).

## After the main task: refine and hand off

After completing the requested work, act on the two standing goals above:
propose materially-helpful follow-ups — refinements to scripts, this `AGENTS.md`,
skills, tests, or docs — and note each one's likely impact on future work. Skip
busywork; if nothing is materially helpful, say so.
