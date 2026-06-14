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
- `just upgrade` — upgrade dependencies, then re-run `just check`.

The gate runs on uv alone, so it needs no Node. Nx/pnpm is an optional
accelerator; `uv` and `node` (with Corepack for pnpm) are the clean-clone
prerequisites for `just bootstrap`. The toolchain is pinned in `.tool-versions`:
provision `just`/`uv`/`node` with asdf (or a compatible manager such as mise)
via `asdf install`, then run `just bootstrap`. The `python` pin records the
targeted version (uv supplies Python per `requires-python`); `just
check-versions` keeps every pin in lockstep with CI.

## Commits, releases, and merging

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
  Nx, the repo-root `pyproject.toml`/`uv.lock`/`package.json`/`pnpm-lock.yaml`,
  asdf, direnv, or imports from `skills/`/`tools/`. Python skill scripts are
  self-contained via PEP 723 (`uv run --script`); JS skill scripts use Node
  built-ins; only a skill with its own `package.json` may use pnpm. (This repo
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

After completing the requested work, propose materially-helpful follow-ups —
refinements to scripts, this `AGENTS.md`, skills, tests, or docs — and note each
one's likely impact on future work. Skip busywork; if nothing is materially
helpful, say so.
