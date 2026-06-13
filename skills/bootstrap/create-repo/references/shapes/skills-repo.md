# Shape: Skills repo / multi-skill tooling repo

Principles for a repo that produces agent skills or multi-skill tooling. Pair
with the language(s) used (`languages/python.md`, `languages/typescript.md`) and
`ci.md`. [`dero-skills`](https://github.com/nickderobertis/dero-skills) is a
worked reference implementation (and is where this skill itself lives).

- **Determinism vs judgment.** Anything deterministic in a skill should be a
  script; reserve prose instructions for genuine judgment.
- **Runtime independence is the defining invariant.** A skill's bundled scripts
  run inside *consuming* repos, where none of this repo's authoring tooling
  exists. They must not depend on the orchestrator (Nx), the repo-root manifests
  (`pyproject.toml` / `uv.lock` / `package.json` / lockfiles), asdf, direnv, or
  imports from the repo's own source tree. Make each script self-contained:
  **PEP 723** inline metadata for Python (`uv run --script foo.py`, dependencies
  declared in the header) and Node built-ins (or a vendored bundle) for
  JavaScript. Enforce this with a validation script, not just a convention.
- **Validate and smoke every skill in the gate.** Add tooling that (a) checks
  each `SKILL.md` frontmatter (name matches the directory and the `^[a-z0-9-]+$`
  shape, a trigger-oriented `description`, a `compatibility` note on runtime
  needs) and forbidden runtime dependencies, and (b) *runs* every bundled script
  once (`uv run --script` / `node`) to catch an import or syntax error before a
  consumer hits it. Wire both into `just check`.
- **Dogfood your own checks.** If the repo ships a repo-baseline or lint script,
  run it against this repo in the gate (`just baseline`) so the canonical example
  stays a passing example.
- **Install / consumer model.** Project-based installs only — no multiple install
  profiles. Provide a consumer-bootstrap path (e.g. `gh skill install` /
  `gh skill update`) so a downstream repo pulls skills from the canonical source
  rather than hand-copying their contents, and keep dependencies updated rather
  than pinning skill versions by default.
- **Multi-tool compatibility.** Make setup work across Cursor, Claude Code, and
  VS Code / Copilot.
- **Tooling boundaries.** pnpm for JavaScript, uv for Python; keep the two
  toolchains cleanly separated. An orchestrator (Nx) is acceptable as an
  *optional authoring accelerator* for caching validate/test across many skills —
  but it must never become a runtime dependency of the bundled scripts (see the
  runtime-independence invariant) and should be recorded as optional in the
  "Stack and composition" section.
- **Hooks.** Where JS exists, use husky; the pre-commit/pre-push hook should call
  `just check` and stay quiet on success.
- **Docs.** Root and nested `AGENTS.md`; include `tests/AGENTS.md` when test
  conventions matter; `CLAUDE.md` is a symlink to `AGENTS.md`.
- **Safety.** Narrow agent allowlist in `.claude/settings.json`; avoid relying on
  deny lists.
