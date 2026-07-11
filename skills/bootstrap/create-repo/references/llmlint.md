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
  — which runs `llmlint --diff --diff-base "origin/main"`, so llmlint scopes both
  the target set (only the changed files, skipping empty diffs) and the judge (only
  the *lines* the branch changed) to the merge-base with main. A plain `--diff-base
  <ref>` means three-dot/merge-base semantics (llmlint >= 0.3.15), so no explicit
  `...HEAD` is needed. A PR is judged on what it introduced and the judge can't
  wander into untouched code. (llmlint >= 0.3.11 does the changed-file selection
  itself — no wrapper script needed.)
  The diff-scoped run is the **blocking PR check** in its own CI workflow (separate
  from the `check` gate). It **requires** the harness credential and fails fast with
  a clear message when it is absent — never no-ops to a green pass, which would
  report unlinted files as clean. Fork PRs are handled at the repo level (GitHub's
  require-approval-for-fork-workflows setting, see `references/ci.md`), not by a
  no-op branch in the workflow; secrets stay restricted on `pull_request` from
  forks even after approval. Scoping CI to the diff keeps each PR paying for its own
  changes rather than a full-repo sweep on every push.
- **A deterministic gate runs first — `just lint-llm-validate`.** `llmlint
  validate` (>= 0.3.17) runs every model-free check in one pass — the config
  structure parses, each inline `llmlint: ignore` directive names a real configured
  rule, and any edited versioned fragment bumped its `version:` — with no harness
  call and no credential. Wire it as a `just lint-llm-validate` recipe (`llmlint
  validate {{args}}`) and run it in the CI llmlint job **before** the model-based
  `lint-llm-diff` step: it catches a broken config, a stale suppression, or a
  forgotten fragment bump in milliseconds, so the paid model tier never runs
  against a config that can't pass. Pass `--diff-base origin/main` to scope the
  version-bump check to the branch's changes. Because it is fast and token-free, it
  also makes a good `pre-push` hook (husky where JS exists) that skips without
  blocking when the toolchain isn't installed — a local safety net before the
  blocking CI check, not a slow model call on every push.
- **Config is composed, not hand-written.** The composer
  ([`scripts/compose_repo_plan.py`](../scripts/compose_repo_plan.py)) emits the
  repo's `llmlint.yml` for your stack with `--llmlint-config`, wiring a standard
  base rule set plus the per-reference rule fragments in as `@version`-pinned
  plugins. A repo *tunes* a standard rule in place with `override: true` (inherits
  the rule, changes only the fields it sets) rather than forking it.
- **Harness selection is a fallback `oneharness.toml`, not a pin in `llmlint.yml`.**
  The composed `llmlint.yml` deliberately pins **no** harness/model (no
  `agents.default.harness`), so an `oneharness.toml` at the repo root decides which
  harness llmlint drives. Compose it alongside the config — `--llmlint-config` also
  writes `oneharness.toml` beside the config (override the path with
  `--oneharness-config`), or copy `assets/oneharness.toml.template`. It runs
  oneharness in **fallback mode**: `run_mode = "fallback"` with
  `harnesses = ["codex", "claude-code"]` and a per-harness `[harness.<id>].model`
  (`codex` → `gpt-5.5`, `claude-code` → `claude-opus-4-8`). oneharness tries the
  harnesses in priority order and stops at the first that can actually run, falling
  through only candidates that cannot run at all (not installed, unspawnable, or
  rejected before any work — auth / no-credit quota; a real task failure or timeout
  does *not* fall through). So the **one** committed file works everywhere with no
  edit: a contributor with Codex authenticated runs the primary (codex + gpt-5.5),
  and a Claude Code session — where `oneharness detect` reports codex
  `available: false` — falls through to the secondary (claude-code + opus-4.8), the
  only harness authenticated there. This is why `setup-llmlint.sh` no longer exports
  an `ONEHARNESS_HARNESSES` override: the fallback already selects claude-code, and
  an override would clobber the list and mis-apply a single model to both harnesses.
  Inject `IS_SANDBOX = "1"` into the `[harness.claude-code]` `env` so it may run
  under root without `--dangerously-skip-permissions` refusing.
- **Fragment versioning is semver; consumers pin the major.** Each fragment
  carries a `version:` (`MAJOR.MINOR.PATCH`). Bump **minor** for an added or
  tightened rule, **patch** for a wording fix, **major** only for a breaking
  change — a removed or renamed rule, or one whose meaning flips such that
  previously-passing code now fails or a consumer's `override` dangles. The
  composer pins a consumer to the **major only** (`@1`), which llmlint reads as
  "any `1.x`", so non-breaking bumps reach pinned repos on their next (cold-cache
  or CI) run automatically while a major bump stays opt-in. Because the pin is
  also llmlint's cache key, a warm-cached consumer forces a refetch with
  `LLMLINT_PLUGIN_REFRESH=1` or by moving to the new major.
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
  cross-cutting pieces — contributes its buildout fragment. A reference can
  contribute to **both** tiers: the composer maps it to an ongoing fragment at
  `assets/llmlint/<ref>.llmlint.yml` and a buildout one at
  `assets/llmlint/buildout/<ref>.llmlint.yml` independently, so put a rule that
  should re-run on every PR in the ongoing fragment and a one-time wiring check in
  the buildout one (`monorepo` does exactly this — an ongoing
  `instruction_layer_localized` alongside its buildout orchestration checks). Run
  the buildout config **once** during creation, resolve findings, then **delete it
  — do not commit it**. Only the ongoing config stays and becomes the PR check. The easiest way to
  run it is `check_repo_baseline.py --buildout`, which composes the buildout config
  for the stack recorded in `AGENTS.md`, runs `llmlint`, and cleans up the temp
  config — so the compose/run/delete cycle isn't a manual dance to forget. It merges
  the committed ongoing config in alongside (llmlint preflight-validates inline
  ignore directives against the *configured* rules, so run in isolation the buildout
  config would hard-error on directives naming ongoing rules).
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
  (from `assets/setup-llmlint.sh.template`) that installs both, and expose it as
  `just setup-llmlint` for a plain terminal. Wire it into web/cloud sessions
  through the `SessionStart` hook — point the hook at `scripts/session-setup.sh`
  (Principle 1), which provisions `just` and then hands off to `setup-llmlint.sh`,
  so one hook readies the whole session with no manual steps. (`setup-llmlint.sh`
  is still called directly by `just bootstrap` and CI, where `just` already
  exists.) `llmlint doctor` confirms the harness is reachable; the `just lint-llm`
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
- [ ] **Harness selection is a fallback `oneharness.toml`.** `oneharness.toml`
  exists at the repo root in fallback mode (`run_mode = "fallback"`,
  `harnesses = ["codex", "claude-code"]` with per-harness models), and `llmlint.yml`
  pins no harness — so a Claude Code session falls through to claude-code with no
  env override. Composed beside `llmlint.yml` by `--llmlint-config`.
- [ ] **Recipes present, out of the gate.** A `just lint-llm` recipe runs llmlint
  on demand, a `just lint-llm-diff` recipe lints the merge-base diff, and a `just
  lint-llm-validate` recipe runs the deterministic `validate` gate; none is wired
  into `just check` (the deterministic gate stays uv-only and credential-free).
- [ ] **Deterministic gate runs first in CI.** The CI llmlint job runs `just
  lint-llm-validate --diff-base origin/main` (no credential, no model) before the
  model-based `lint-llm-diff` step, so a config/suppression/version-bump slip fails
  fast without spending a harness call.
- [ ] **Install automated.** `scripts/setup-llmlint.sh` exists (idempotent
  toolchain install) and `just setup-llmlint` runs it. The Claude Code
  `SessionStart` hook in `.claude/settings.json` invokes it — directly, or (the
  recommended layout) via `scripts/session-setup.sh`, which provisions `just`
  first, then hands off to `setup-llmlint.sh`.
- [ ] **Blocking PR check.** A CI workflow runs `just lint-llm-diff` as its own
  job, separate from the `check` gate; it requires the harness credential and
  fails fast without it (fork PRs are gated by the repo's
  require-approval-for-fork-workflows setting, not a no-op), and `llmlint` is in
  the branch-protection required-checks set — `setup_github_governance.py`
  enforces its presence, so auto-merge cannot land a PR past a red llmlint run.
- [ ] **Buildout run once.** The buildout tier was run once during creation and
  its findings resolved — easiest via `check_repo_baseline.py --buildout` (it
  composes the buildout config for the recorded stack, runs `llmlint`, and cleans
  up), or by hand with `compose_repo_plan.py --llmlint-buildout-config` then
  deleting the file (never commit it).
- [ ] **Harness reachable.** `llmlint doctor` passes (oneharness + an
  authenticated harness installed), and `just lint-llm` was run once with its
  findings resolved.
