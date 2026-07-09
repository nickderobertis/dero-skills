# Base: always-applied invariants

The shape- and language-agnostic core that applies to *every* repo this skill
stands up, regardless of what it produces. The composer always includes this
reference first; its guidance restates the SKILL.md principles only enough to
anchor the verification items below, which are the universal checks every
composed plan carries before any shape/language/cross-cutting items are added.

- **Agent layer first.** `AGENTS.md` (terse, always-loaded context), `CLAUDE.md`
  symlinked to it, and a narrow `.claude/settings.json` allowlist — laid down
  before the rest so the build runs with fewer approval prompts.
- **Don't point at context that's already loaded.** The harness auto-loads every
  `AGENTS.md`/`CLAUDE.md` colocated with, or in an ancestor of, the files in scope,
  and every markdown doc reachable from one of those by reference (directly, or
  nested through other docs) is thereby surfaced as available to load. Within that
  set, don't redirect the reader to a file already present: a nested `AGENTS.md`
  saying "see the root `AGENTS.md`", or a referenced doc pointing back at the parent
  that linked it, is dead weight that rots when the target moves. State the rule
  where it belongs and let the layering do the rest. Introducing a genuinely new
  resource (not yet reachable here) is fine; so is a plain note about a file's role
  (e.g. that `CLAUDE.md` is a symlink of `AGENTS.md`).
- **One command surface.** A `just` recipe set (`bootstrap`, `check`, `test`,
  `lint`, `format`, `upgrade`) with real bodies; `just bootstrap` works from a
  clean clone and `just check` is the full gate.
- **Strict, deterministic gate.** Format, lint, type check, and tests fail on
  issues — no warnings-only mode — with coverage measured and enforced.
- **Realistic e2e is an invariant, not a preference.** The suite is the only QA
  loop, so it drives the real artifact across real boundaries and covers every
  user journey; mocking the layer under test is a fail.
- **Security is a baseline invariant, not a follow-up.** Secrets never enter the
  tree — they live in the platform's secret store and are referenced by name;
  every external input is validated at its trust boundary before use; and every
  grant (the agent allowlist, a CI token, a service role) is least-privilege.
  Hold these at gate level, the same as the strict gate and real e2e.
- **Compose deliberately and record it.** Build the repo up from the reference
  pieces and write the decision into the AGENTS.md "Stack and composition"
  section so it is auditable, not silently skipped.
- **Land on current dependencies.** Run `just upgrade` as one of the last steps
  so the repo starts life on the latest deps, with the gate re-run and lockfiles
  committed.

## Verification

- [ ] **Agent layer.** `AGENTS.md` exists; `CLAUDE.md` is a symlink to it (not a
  copy); `.claude/settings.json` has a narrow allowlist.
- [ ] **Composition recorded.** `AGENTS.md` has a filled-in "Stack and
  composition" section naming the product shape, the language(s), the references
  composed, and what was excluded and why — no `<...>`/`TODO` placeholders left.
- [ ] **Security baseline.** No secret, credential, or key is committed (values
  live in a secret store, referenced by name); external inputs are validated at
  the trust boundary; and grants — the `.claude` allowlist, the CI token, service
  roles — are least-privilege, not blanket.
- [ ] **Command surface.** The `justfile` defines `bootstrap`, `check`, `test`,
  `lint`, `format`, `upgrade`, each with a real body (no placeholder recipe
  survives), and `just bootstrap` works from a clean clone.
- [ ] **Strict gate.** `just check` runs format check + lint + type check + unit
  tests + e2e and fails on any issue (no warnings-only mode); `check` actually
  invokes `test` — confirm the wiring, don't assume it.
- [ ] **Coverage enforced.** Coverage is measured and the gate fails below the
  threshold (95% line coverage by default, or a documented lower bar in
  `AGENTS.md`).
- [ ] **Real e2e of every journey.** E2E drives the real artifact across real
  boundaries — not mocking the layer under test — and covers every user-facing
  journey, happy path **and** failure/recovery, running inside `just check`
  (a too-expensive case is a documented exception CI still runs, never silently
  skipped).
- [ ] **Upgraded to latest, then gated.** `just upgrade` was run as one of the
  last steps, the refreshed lockfiles are committed, and the gate it re-runs
  passed.
