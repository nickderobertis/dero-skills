# create-repo tests

Two tiers live here:

- **Deterministic script tests** (`test_*.py` + `e2e/`) for the skill's PEP 723
  scripts (`compose_repo_plan`, `check_repo_baseline`, `setup_github_governance`)
  and for the suppression guard (`test_produced_repo_suppressions.py`).
  Fast, un-mocked, and part of the gate — they run under `just check` (and
  `just test`), embodying the "Tests are context engineering" mandate: real
  subprocesses over real temp files, never a mocked stand-in.
- **The skill eval** (`test_create_repo_skilltest.py`), documented below.

## The skilltest skill eval

`test_create_repo_skilltest.py` drives the whole `create-repo` skill through a
real harness (`skilltest-pytest`, a dev dep) so it bootstraps a hello-world Rust
CLI on a real filesystem, then judges the result. Its docstring covers the
mechanics; the load-bearing rules:

- **Deterministic-first.** The checks are in Python: the skill's own
  `check_repo_baseline.py` must pass against the produced repo, the expected
  files exist (`Cargo.toml`, `src/main.rs`, `AGENTS.md`, the `CLAUDE.md` symlink),
  and `cargo run` prints a greeting. The YAML-level `eval`s are deterministic
  mock-call assertions (no LLM judge): the skill runs no destructive command
  (`not_called`) and does self-verify by running its own baseline checker,
  including the one-time `--buildout` tier (`called`). Add new deterministic
  checks in code; reserve `evals` for what code genuinely can't check.
- **No gate may be bought with a suppression.** Every other check here is
  satisfiable two ways: fix the code, or silence the checker. `notignored` (via
  the `notignored-sdk` dev dep, which ships the matching binary) scans the
  produced repo and `produced_repo_suppressions.assert_none` rejects **every**
  directive that is not on its allowlist — default-deny, so a `# noqa`,
  `#[allow(...)]`, or `llmlint` ignore directive the agent added fails the test naming the
  directive, its location, and its reason (or its absence). See "The suppression
  guard" below.
- **The model must not know it is under test.** A short, natural prompt (no
  coaching); the env is scrubbed of every harness tell; a neutrally-named `/tmp`
  workspace (never pytest's `tmp_path`); bypass mode via a hidden
  `.oneharness.toml`; realistic `gh`/`git push` mock output with no "mock"/"test"
  strings. If you touch the run, re-audit stealth by reading the model's
  `/proc/<pid>/environ` mid-run — it must look like a normal sandboxed session.
- **The remote is never created.** skilltest `stub`s + a fake `gh` on `PATH` make
  `gh repo create`/`git push` return realistic success without touching GitHub.
- **The harness timeout is a generous ceiling.** The model stops when the task is
  done, so a high ceiling only bites a pathological run. On a timeout the on-disk
  artifact is still judged, but the mock-call evals (`called`/`not_called`) live in
  the report, which `run_skill` only returns on clean completion — so keep the
  ceiling high enough that the bootstrap finishes and those evals actually run.

### The suppression guard

`produced_repo_suppressions.py` holds the allowlist and the guard; its unit tests
are `test_produced_repo_suppressions.py`, which run in the normal gate (the guard
must be proven to work without a 30-minute harness run).

- **The allowlist is derived, not asserted.** Its three entries are exactly what
  the skill's own `assets/*.template` files emit, established by materialising
  every template under its documented target name and scanning the result. The
  gate test re-runs that derivation, so a template that grows a new directive
  fails until `ALLOWED` is updated — and one that drops a directive fails the
  companion "every entry is earned" test, so the list can't quietly widen.
  **Empty is the ideal**: shrink it by fixing a template, never grow it to make a
  run pass. The map from template to target name is itself gated against
  `assets/*.template`, so a new asset can't slip past the derivation unmapped.
- **Subset, not equality.** An entry permits a *non-empty subset* of its named
  rules on its path glob: narrowing a template's rule list suppresses less and
  stays allowed; adding an unlisted rule, or dropping to a blanket directive that
  names none, does not. `require_reason` is on for every entry whose template
  writes a justification, so stripping the "why" out is caught too.
- **Stealth.** The scan runs only after the harness has exited, against a
  `notignored` resolved at import (before `_stealth_env` strips this repo's venv
  from `PATH`) and handed over via `NOTIGNORED_BIN` for the duration of the scan
  alone. Keep it that way: nothing notignored-related may be visible to the model
  mid-run, or the check measures a model that knows it is being measured. That
  invariant has its own gate test, which drives the real `_stealth_env` in a
  subprocess and asserts the binary is unreachable *and* the guard still scans —
  so a lazy `PATH` lookup can't creep back in.
- **Order matters** in the Rust case: scan *before* `cargo build`, so a `target/`
  of vendored crates can never be what the guard reports on.

### Custom prompts

Set `SKILLTEST_PROMPT` to drive the skill with an arbitrary request instead of the
default Rust bootstrap. That activates `test_create_repo_with_custom_prompt`, which
reuses the same stealth harness + remote mocks but asserts only that a repo was
produced (a custom scenario may target another stack, or deliberately violate the
baseline), then copies the produced repo to `SKILLTEST_OUT_DIR` (default: a
persisted `/tmp` dir) and prints `PRODUCED_REPO=<path>` plus the suppression
report so a follow-up check can run against the real artifact — e.g. pointing a
buildout llmlint rule at it to confirm it fires. The suppression guard *reports*
here instead of asserting, matching this entry point's looser contract: an
adversarial scenario may have asked for the suppression, and judging that is the
reader's call. `SKILLTEST_REPO` overrides the slug the mocked `gh`/push report.
All three are read at import (before `_stealth_env` strips `SKILLTEST_*`). Setting
`SKILLTEST_PROMPT` skips the default Rust test, so exactly one of the two runs.

### Running it

Opt-in only — it drives a real ~20-30 min bootstrap and is never in `just check`:

```
just skilltest
```

It needs a provider (`oneharness` on `PATH`, or a custom `SKILLTEST_PROVIDER`)
and a sandbox it can write in (it runs the harness in bypass mode). Without the
`--skilltest-e2e` flag / `SKILLTEST_E2E=1` it is skipped (the marker gate lives in
the rootdir `conftest.py`); without a provider it is skipped too. The opt-in
lifts the marker skip but the provider skip still applies, so a plain
`just skilltest` in a provider-less shell is a no-op.
