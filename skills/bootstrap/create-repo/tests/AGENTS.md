# create-repo tests

Two tiers live here:

- **Deterministic script tests** (`test_*.py` + `e2e/`) for the skill's PEP 723
  scripts (`compose_repo_plan`, `check_repo_baseline`, `setup_github_governance`).
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

### Custom prompts

Set `SKILLTEST_PROMPT` to drive the skill with an arbitrary request instead of the
default Rust bootstrap. That activates `test_create_repo_with_custom_prompt`, which
reuses the same stealth harness + remote mocks but asserts only that a repo was
produced (a custom scenario may target another stack, or deliberately violate the
baseline), then copies the produced repo to `SKILLTEST_OUT_DIR` (default: a
persisted `/tmp` dir) and prints `PRODUCED_REPO=<path>` so a follow-up check can
run against the real artifact — e.g. pointing a buildout llmlint rule at it to
confirm it fires. `SKILLTEST_REPO` overrides the slug the mocked `gh`/push report.
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
