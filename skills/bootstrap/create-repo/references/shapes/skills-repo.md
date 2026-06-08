# Shape: Skills repo / multi-skill tooling repo

Principles for a repo that produces agent skills or multi-skill tooling. Pair
with the language(s) used (`languages/python.md`, `languages/typescript.md`) and
`ci.md`.

- **Determinism vs judgment.** Anything deterministic in a skill should be a
  script; reserve prose instructions for genuine judgment. Scripts must be
  self-contained so consuming repos can run them without this repo's tooling.
- **Install model.** Project-based installs only — no multiple install profiles.
  Keep dependencies updated regularly rather than pinning by default.
- **Multi-tool compatibility.** Make setup work across Cursor, Claude Code, and
  VS Code.
- **Tooling boundaries.** pnpm for JavaScript, uv for Python; keep the two
  toolchains cleanly separated.
- **Hooks.** Where JS exists, use husky; the pre-commit/pre-push hook should call
  `just check` and stay quiet on success.
- **Docs.** Root and nested `AGENTS.md`; include `tests/AGENTS.md` when test
  conventions matter; `CLAUDE.md` is a symlink to `AGENTS.md`.
- **Safety.** Narrow agent allowlist in `.claude/settings.json`; avoid relying on
  deny lists.
