# Cross-cutting: llmlint (the LLM-judge tier)

Applies on top of every product shape and language. [llmlint](https://github.com/nickderobertis/llmlint)
is an LLM-as-judge linter: it enforces the code-quality checks a human reviewer
makes — adherence to architectural patterns, coding-style intent, organization
objectives — that deterministic linters can't express. It is **additive** to the
strict gate, not a replacement: keep ruff/clippy/biome/shellcheck/tsc for
everything they already check, and reach for llmlint only for the judgment calls.

- **It is NOT in `just check`.** llmlint drives a real coding harness through
  `oneharness`, so it is non-deterministic, needs an authenticated harness, and
  makes network calls — the opposite of the deterministic gate. Keep it out of the
  tight `just check` loop. It runs two ways: on demand over the configured set (or
  passed paths) with `just lint-llm`, and **diff-scoped** with `just lint-llm-diff`
  — which, via llmlint's `--diff --diff-base`, judges only the *lines* the branch
  changed since its merge-base with main (not whole files), so a PR is judged on
  what it introduced and the judge can't wander into untouched code.
  The diff-scoped run is the **blocking PR check** in its own CI workflow (separate
  from the `check` gate). It **requires** the harness credential and fails fast with
  a clear message when it is absent — never no-ops to a green pass, which would
  report unlinted files as clean. Fork PRs are handled at the repo level (GitHub's
  require-approval-for-fork-workflows setting, see `references/ci.md`), not by a
  no-op branch in the workflow; secrets stay restricted on `pull_request` from
  forks even after approval. Scoping CI to the diff keeps each PR paying for its own
  changes rather than a full-repo sweep on every push.
- **Config is composed, not hand-written.** The composer
  ([`scripts/compose_repo_plan.py`](../scripts/compose_repo_plan.py)) emits the
  repo's `llmlint.yml` for your stack with `--llmlint-config`, wiring a standard
  base rule set plus the per-reference rule fragments in as `@version`-pinned
  plugins. Bumping a hosted fragment's version pushes new rules to every repo that
  pins it; a repo *tunes* a standard rule in place with `override: true` (inherits
  the rule, changes only the fields it sets) rather than forking it.
- **Ongoing vs buildout — two configs.** The committed `llmlint.yml` carries the
  **ongoing** rules that judge code as it changes (e2e isn't mocked, inputs are
  validated at boundaries, no unjustified suppressions, ...). A second
  **buildout** config (`--llmlint-buildout-config`, e.g. `llmlint.buildout.yml`)
  carries one-time *structural* checks — the universal invariants the deterministic
  checker can't express (the gate wires every stage, coverage actually fails the
  build, no secret is committed, deps were upgraded) plus the per-stack ones
  (command surface wired the language-native way, toolchain pinned, the release
  pipeline has no manual step, CI runs the real gate, the monorepo delegates to
  its orchestrator, one asset-naming contract, the production build validated).
  Every composed reference — `base`, each language, each shape, and the
  cross-cutting pieces — contributes its buildout fragment. Run the buildout config
  **once** during creation, resolve findings, then **delete it — do not commit
  it**. Only the ongoing config stays and becomes the PR check. The easiest way to
  run it is `check_repo_baseline.py --buildout`, which composes the buildout config
  for the stack recorded in `AGENTS.md`, runs `llmlint`, and cleans up the temp
  config — so the compose/run/delete cycle isn't a manual dance to forget.
- **Rules are judge-level and scoped.** Each rule is a positive invariant judged
  `true` (holds) / `false` (a violation), scoped deterministically with `files`
  globs. Add a `relevance` clause whenever a file can match the globs but still
  not exercise the rule's premise (a `tests/` fixture with no test, a `.py` that
  makes no network call): relevance makes the judge **skip** the rule instead of
  returning a spurious `false` on an unrelated change. Reach for it by default
  unless the globs alone fully determine applicability. Don't restate a
  deterministic check llmlint can't improve on.
- **Install is automated.** llmlint needs `oneharness` and an authenticated
  harness (e.g. Claude Code). Bundle an idempotent `scripts/setup-llmlint.sh`
  (from `assets/setup-llmlint.sh.template`) that installs both, wire it into the
  Claude Code `SessionStart` hook in `.claude/settings.json` so web/cloud sessions
  are ready with no manual steps, and expose it as `just setup-llmlint` for a plain
  terminal. `llmlint doctor` confirms the harness is reachable; the `just lint-llm`
  recipe stays quiet on success and, on a missing binary, points at
  `just setup-llmlint`.
- **Suppress narrowly.** A one-off exception uses a strict inline directive in the
  source: the reserved `llmlint: ignore` prefix, then the specific configured rule
  name(s) in `[…]` and a reason. It must name real, configured rule(s) and give a
  reason — a bare, reason-less, or unknown-rule directive is a hard error (so don't
  write the literal `ignore`-bracket form in linted prose with a placeholder rule:
  llmlint parses it and rejects the unknown rule).

## Verification

- [ ] **Config composed, not hand-rolled.** `llmlint.yml` exists at the repo root,
  declares the bundled `config_lint` plugin plus the standard base and the
  per-reference rule fragments as `@version`-pinned plugins (composed via
  `compose_repo_plan.py --llmlint-config`), and omits `files.include` so llmlint
  lints the whole tree (add `files.exclude` globs for committed noise).
- [ ] **Recipes present, out of the gate.** A `just lint-llm` recipe runs llmlint
  on demand and a `just lint-llm-diff` recipe lints the merge-base diff; neither is
  wired into `just check` (the deterministic gate stays deterministic).
- [ ] **Install automated.** `scripts/setup-llmlint.sh` exists (idempotent
  toolchain install), `just setup-llmlint` runs it, and the Claude Code
  `SessionStart` hook in `.claude/settings.json` invokes it.
- [ ] **Blocking PR check.** A CI workflow runs `just lint-llm-diff` as its own
  job, separate from the `check` gate; it requires the harness credential and
  fails fast without it (fork PRs are gated by the repo's
  require-approval-for-fork-workflows setting, not a no-op), and `llmlint` is in
  the branch-protection required-checks set.
- [ ] **Buildout run once.** The buildout tier was run once during creation and
  its findings resolved — easiest via `check_repo_baseline.py --buildout` (it
  composes the buildout config for the recorded stack, runs `llmlint`, and cleans
  up), or by hand with `compose_repo_plan.py --llmlint-buildout-config` then
  deleting the file (never commit it).
- [ ] **Harness reachable.** `llmlint doctor` passes (oneharness + an
  authenticated harness installed), and `just lint-llm` was run once with its
  findings resolved.
