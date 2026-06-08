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

## Command surface

Use the `just` recipes; do not hand-roll equivalents.

- `just bootstrap` — set up from a clean clone (uv + the Nx authoring toolchain).
- `just check` — full quality gate: `ruff format --check`, `ruff check`, skill
  validation + smoke, `pytest`, and the create-repo baseline self-check. Must
  pass before any commit or PR.
- `just format` / `just lint` / `just validate` / `just test` — individual steps.
- `just nx` — cached Nx authoring targets (validate/smoke/test) across skills.
- `just upgrade` — upgrade dependencies, then re-run `just check`.

The gate runs on uv alone, so it needs no Node. Nx/pnpm is an optional
accelerator; `uv` and `node` (with Corepack for pnpm) are the clean-clone
prerequisites for `just bootstrap`.

## Invariants (non-negotiable)

- **Runtime independence.** A skill's bundled scripts run in *consuming* repos,
  where none of this repo's authoring tooling exists. They must not depend on
  Nx, the repo-root `pyproject.toml`/`uv.lock`/`package.json`/`pnpm-lock.yaml`,
  asdf, direnv, or imports from `skills/`/`tools/`. Python skill scripts are
  self-contained via PEP 723 (`uv run --script`); JS skill scripts use Node
  built-ins; only a skill with its own `package.json` may use pnpm.
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

- Tests are how you and future agents see real behavior, so invest in them.
- Prefer realistic end-to-end coverage (e.g. running a skill script the way a
  consuming repo would, via `uv run --script`) over narrow unit smoke tests.
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
- No asdf/direnv/`src` layout/pre-commit and no install profiles or version
  pins by default — consistent with the skill's own anti-baggage guidance.

## After the main task: refine and hand off

After completing the requested work, propose materially-helpful follow-ups —
refinements to scripts, this `AGENTS.md`, skills, tests, or docs — and note each
one's likely impact on future work. Skip busywork; if nothing is materially
helpful, say so.
