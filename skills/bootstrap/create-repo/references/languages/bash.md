# Language: Bash / shell

Language-level conventions for shell-script repos. Combine with a product shape
(commonly `shapes/asdf-plugin.md`, or `shapes/cli.md`) and `ci.md`.

- **Portability.** Decide the target shell explicitly. If you target POSIX `sh`,
  avoid bashisms; otherwise require `bash` and test on both macOS and Linux.
- **Quality.** Enforce `shellcheck` and `shfmt`; fail on issues — never carry a
  warnings backlog.
- **Tests.** Use `bats` (or similar) for unit-level behavior, plus real
  integration tests against the host tool when one exists.
- **Coverage in the gate.** 95% line coverage is the default bar, enforced in
  `just check` — measure with `kcov` (wrap the `bats` run) or `bashcov` and fail
  below the threshold. Shell is the case most likely to justify lowering or
  dropping the bar (coverage tooling is coarser here); when you do, record the
  reason in `AGENTS.md` rather than silently skipping it.
- **Robustness.** `set -euo pipefail`; quote expansions; handle network and
  filesystem failures gracefully rather than assuming success.
- **Command mapping.**
  - `just check` -> `shellcheck` + `shfmt -d` + `bats` tests
  - `just e2e` -> integration tests that drive the real host tool
- **CI.** Matrix across the supported OSes; prove the scripts end-to-end, not
  just that they parse.

## Verification

- [ ] **Target shell decided.** The target shell is chosen explicitly (POSIX `sh`
  without bashisms, or `bash` tested on macOS and Linux).
- [ ] **Quality gates.** `shellcheck` and `shfmt` are enforced and fail on
  issues — no warnings backlog.
- [ ] **Tests.** `bats` (or similar) covers unit-level behavior, plus real
  integration tests against the host tool when one exists.
- [ ] **Coverage decision.** 95% line coverage enforced in `just check` via
  `kcov`/`bashcov`, or a documented lower bar in `AGENTS.md` (shell is the most
  likely case to justify lowering it).
- [ ] **Robustness.** `set -euo pipefail`, quoted expansions, and explicit
  handling of network/filesystem failures.
- [ ] **CI matrix.** CI runs across the supported OSes and proves the scripts
  end-to-end, not just that they parse.
