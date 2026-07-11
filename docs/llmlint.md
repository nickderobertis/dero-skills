# LLM lint (`llmlint`)


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
driver. `scripts/setup-llmlint.sh` is the source of truth for the installed
`llmlint-cli` floor and its compatible `oneharness-cli` dependency.

**Harness split — one fallback config, no env flip.** `oneharness.toml` is in
**fallback mode** (`run_mode = "fallback"`, `harnesses = ["codex", "claude-code"]`,
per-harness `[harness.<id>].model`): oneharness runs the harnesses in priority
order and stops at the first that can actually run, falling through only those
that cannot run at all — not installed, unspawnable, or rejected before any work
(auth / no-credit quota); a real task failure or timeout does *not* fall through.
So the single committed file works everywhere with no edit: a contributor with
the first authenticated harness uses the per-harness model recorded in
`oneharness.toml` (CI supplies the `OPENAI_API_KEY` secret for Codex), and a
Claude Code session falls through to its configured secondary harness when Codex
is unavailable. It works only because
`llmlint.yml` deliberately does **not** pin a harness/model (a pin's
`--harness`/`--model` flags would beat the config), while the per-harness `model`
tables keep each harness on its own model (a top-level `ONEHARNESS_MODEL` would
wrongly hit both).

Inside a Claude Code session the `SessionStart` hook runs
`scripts/session-setup.sh`, which provisions `just` and hands off to
`scripts/setup-llmlint.sh` (also called directly by `just bootstrap` and CI, where
`just` exists). `setup-llmlint.sh` installs llmlint (via `uv tool install
llmlint-cli` — one PyPI resolution that also fetches `oneharness-cli`, no Rust
toolchain or github.com reachability needed) and
persists only `PATH` — **no `ONEHARNESS_HARNESSES`/`ONEHARNESS_MODEL` override**,
since the fallback already selects claude-code here and an override would only
clobber it. `IS_SANDBOX` is injected into just the claude-code harness via
`oneharness.toml` so it runs under root without `--dangerously-skip-permissions`
refusing; that harness (the fallback order's first available entry) is also the
default the bundled `config_lint` plugin needs.

The **model tier** (`just lint-llm` / `just lint-llm-diff`) is **deliberately not
in `just check`**: it drives a real harness, so it is non-deterministic and needs
a harness token (in Claude Code the inherited session token; elsewhere
`CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`, or — for the codex default —
`OPENAI_API_KEY`). The **deterministic `validate` tier** (`just lint-llm-validate`)
IS a hard step of `just check` — model-free and token-free, so `bootstrap` installs
the `llmlint` binary (via `uv tool`) and the gate runs it. Caveat: loading
`llmlint.yml` resolves the hosted `config_lint@1` plugin, so a cold-cache run with
no `raw.githubusercontent` reachability fails (CI and warm caches are fine). Run
the model tier on demand with `just lint-llm`;
`just setup-llmlint` is the manual install path for a terminal (the hook covers
Claude Code sessions). Since the repo now runs all JS through bun (`bun install`,
`bunx nx`), the bun rule no longer fires on the authoring toolchain.

**Blocking CI check.** The `llmlint` job in `.github/workflows/ci.yml` runs two
steps on every PR. First — cheaply, with no model call or credential — `just
lint-llm-validate --diff-base origin/main` (`llmlint validate`; version floor owned by `scripts/setup-llmlint.sh`): the
deterministic gate that the config parses, every `llmlint: ignore` directive names
a real rule, and any edited versioned fragment bumped its `version:`. It fails
fast (cents, not dollars) on a config/suppression/version-bump slip before the
paid tier runs. Then the model tier: `just lint-llm-diff` — `llmlint --diff
--diff-base "origin/main"`, where a plain ref means three-dot/merge-base semantics
(no explicit `...HEAD`) and llmlint scopes both the target
set (only the changed files, skipping empty diffs) and the judge (only the *lines*
the branch changed) to the branch's **merge-base with main** (the fork point, not
main's current tip, so unrelated later commits on main are never linted, and the
judge doesn't flag pre-existing code a change merely sits near). llmlint does the changed-file selection itself, so no wrapper script is needed. CI installs
the committed Codex harness via bun and authenticates it with the `OPENAI_API_KEY`
repo secret; without that secret the model step fails (the validate step needs no
credential). It is a separate job (not folded into `check`) to keep the clean-clone
gate uv-only. Its context (`llmlint`, the job id) **must** be a branch-protection
required check, else auto-merge lands past a red run once `check`/`commitlint` go
green; apply with `uv run --script skills/bootstrap/create-repo/scripts/setup_github_governance.py check commitlint llmlint`, which now
refuses to omit it.
