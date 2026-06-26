# Authoring skills

This repo is the canonical source of organization Agent Skills. This guide is
for maintainers adding or editing a skill here. For how application
repos consume skills, see [`consuming-repos.md`](./consuming-repos.md).

## Repository layout

```text
skills/<scope>/<skill-name>/   # the skills themselves
tools/                         # shared validation tooling (stdlib Python)
scripts/                       # repo-wide helper scripts
consumer-bootstrap/            # files application repos copy in
docs/                          # this documentation
```

`<scope>` groups related skills. Today: `bootstrap` (repo setup and
scaffolding). Add new scopes sparingly.

## Skill folder structure

Every skill is **self-contained**. Required:

```text
skills/<scope>/<skill-name>/
  SKILL.md
```

Optional, add as needed:

```text
  references/   # deep-dive docs the skill links to
  scripts/      # runnable helpers (see runtime rules below)
  assets/       # templates, fixtures, sample files
  tests/        # pytest tests for the skill's scripts
  project.json  # Nx project (add when the skill has scripts/tests/assets)
```

## SKILL.md frontmatter

Every `SKILL.md` starts with YAML frontmatter:

```md
---
name: example-skill
description: Use when ...
compatibility: Requires uv and Python 3.12+ only if bundled Python scripts are used.
---
```

Rules:

- `name` must equal the skill directory basename.
- `name` uses lowercase letters, numbers, and single hyphens only.
- `description` must be trigger-oriented and specific — start with "Use when …"
  and name the concrete situation that should activate the skill.
- `compatibility` states runtime needs and when they apply (Python via uv, Node,
  bun). Tailor it to what the skill actually bundles.
- Never include secrets, credentials, PHI, PII, or customer data.
- Keep platform-specific install instructions out of skill content unless
  unavoidable; platform setup lives in `consumer-bootstrap/` and `docs/`.

## Runtime independence (hard rule)

Scripts bundled in a skill run in *consuming* repos, where none of this repo's
authoring infrastructure exists. A skill's runtime scripts **must not** depend
on:

- Nx
- the repo-root `pyproject.toml`, `uv.lock`, `package.json`, or `bun.lock`
- asdf or direnv
- imports from this repo's source tree (`skills/`, `tools/`)

How to satisfy this:

- **Python scripts** are self-contained via [PEP 723](https://peps.python.org/pep-0723/)
  inline metadata and run with:

  ```bash
  uv run --script scripts/<script>.py [args...]
  ```

  Start the file with:

  ```python
  # /// script
  # requires-python = ">=3.12"
  # dependencies = []
  # ///
  ```

- **JavaScript scripts** use Node built-ins only and run with:

  ```bash
  node scripts/<script>.mjs [args...]
  ```

- **Only** a skill with its own skill-local `package.json` (and `bun.lock`)
  may require:

  ```bash
  bun install --frozen-lockfile
  bun run <script>
  ```

## Nx is for authoring and CI only

Each maintained skill with scripts, references, assets, tests, or
security-sensitive behavior should be an Nx project (`project.json`). Nx caches
and runs `validate`, `smoke`, and `test` targets here and in CI.

Agents consuming installed skills must **never** need to run Nx. See the example
`project.json` in any skill; the target naming convention is `<scope>-<name>`.

## Validation tooling

Two stdlib-only tools enforce the rules above:

- `tools/validate_skill.py <skill-dir>` — checks frontmatter, the name/basename
  match, the naming pattern, the runtime-independence rules, and scans for
  obvious secrets.
- `tools/smoke_skill_scripts.py <skill-dir>` — byte-compiles / syntax-checks the
  bundled scripts without executing side effects.

Run the whole quality gate (format, lint, skill validation, tests, and the
repo's own baseline self-check) with `just check`. To run just the skill checks:

```bash
./scripts/validate-skills.sh            # validate + smoke every skill
./scripts/validate-skills.sh --no-smoke # validate only
```

Or via Nx per skill (Nx is a dev/CI tool here, run through bun):

```bash
bunx nx run bootstrap-create-repo:validate
bunx nx run bootstrap-create-repo:test
bunx nx run-many -t validate smoke test
```

## Checklist for a new skill

1. Create `skills/<scope>/<skill-name>/SKILL.md` with valid frontmatter.
2. Add `references/`, `scripts/`, `assets/`, `tests/` as needed.
3. Make scripts self-contained (PEP 723 for Python, built-ins for Node).
4. Add `project.json` with `validate`, `smoke`, and `test` targets.
5. Run `./scripts/validate-skills.sh` and `uv run pytest skills/<scope>/<skill-name>/tests`
   (omit the `pytest` step for a docs-only skill with no `tests/`).
6. Open a PR. CI runs the same validation.
