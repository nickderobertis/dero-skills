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
  below the threshold, on the merged report once the suite spans projects (see
  "Splitting the suite" below). Shell is the case most likely to justify lowering or
  dropping the bar (coverage tooling is coarser here); when you do, record the
  reason in `AGENTS.md` rather than silently skipping it.
- **Robustness.** `set -euo pipefail`; quote expansions; handle network and
  filesystem failures gracefully rather than assuming success.
- **Command mapping.** The root recipes delegate to the orchestrator, which runs
  the per-project targets named below.
  - `just check` -> `nx affected -t format lint test` plus the repo-level
    `coverage` target, with the per-project targets running `shfmt -d`,
    `shellcheck`, and `bats`
  - `just e2e` -> the integration project's `test` target in isolation
    (`nx run <plugin>-e2e:test`), which drives the real host tool — a convenience
    for a focused run, not the only place it runs
- **CI.** Matrix across the supported OSes; prove the scripts end-to-end, not
  just that they parse.

## Projects and the graph (shell has no workspace primitive)

Shell has no manifest, no dependency resolver, and no workspace primitive: there
is nothing for a "shell workspace" to own. That does not exempt a shell repo from
the project graph — it changes what fills each slot.

- **The project definition is the only manifest.** A shell project is a
  directory of scripts with a `project.json` in it declaring its targets. There
  is no `pyproject.toml`/`Cargo.toml` equivalent to sit beside, so `project.json`
  *is* where the project's identity, targets, inputs, and tags live. Where the
  host tool fixes the layout — an asdf plugin's `bin/` must be at the repo root —
  the project root is the repo root, and the other projects live in
  subdirectories beside it.
- **Dependency "resolution" is host tools, declared and checked.** A script's
  dependencies are executables on the host (`bash`, `curl`, `tar`, `jq`, a
  SHA-256 utility). Declare them explicitly (in `AGENTS.md`, and in
  `bin/help.deps` for an asdf plugin) and check for them at runtime with a clear
  failure naming the missing tool — that check is the shell analogue of a resolve
  step, and it is the only one that exists.
- **One lockfile per ecosystem still applies — to the ecosystems the *tooling*
  comes from.** Shell itself locks nothing, but `bats`, `shellcheck`, `shfmt`,
  and `kcov` come from somewhere. Pin those once for the repo — a root
  `.tool-versions`, or one root `package.json` + `bun.lock` if `bats` is
  installed from npm — never a copy per project. A second lockfile for the same
  ecosystem in a project subdirectory is the failure this rule exists to prevent.
- **Cross-project edges are explicit.** A shared `lib/` sourced by several script
  projects is its own project, and the consumers name it in `implicitDependencies`
  (there is no import statement for Nx to infer from), so editing a helper marks
  its consumers affected.

### Splitting the suite

- **`bats` is invoked per project, rooted at that project.** A project's `test`
  target runs `bats tests/` with the target's `cwd` set to the project, so it
  collects only that project's `.bats` files and loads that project's helpers.
  One repo-wide `bats` over a single top-level `tests/` directory is the thing
  the split replaces.
- **Fast tier with the scripts, host-tool integration in its own project.** The
  offline `bats` tests — version parsing, platform mapping, URL construction,
  failure messages — are the script project's `test` target. The suite that
  drives the real host tool and installs a real version over the network is slow
  and external, so it becomes its own project (`<plugin>-e2e`) whose `test`
  target is that suite and which depends only on the scripts it drives. Both run
  in `just check`; only the second is skippable by an unrelated change.
- **Coverage survives the split by merging, not by lowering.** Each project's
  `test` target wraps its `bats` run in `kcov` (`kcov --include-path=<project>
  <outdir> bats tests/`) writing into that project's declared target outputs; a
  repo-level `coverage` target `dependsOn` every `test` and runs
  `kcov --merge <merged> <outdir>...` over them. kcov has no fail-under flag, so
  the `coverage` target must then read the merged report's line rate (the
  generated `cobertura.xml`) and exit non-zero below the bar — a merge that
  nobody thresholds is not a gate. `bashcov` is the alternative: SimpleCov's
  `minimum_coverage` enforces the threshold directly, and its `.resultset.json`
  files merge across projects.
- **Shell is the case most likely to justify a lower bar** (the tooling is
  coarser here), and lowering it is allowed with a reason recorded in
  `AGENTS.md`. What is not allowed is letting the split be the reason nobody
  measures: a lower documented bar is still enforced on the merged report.

### Target names

Each shell project declares the repo-uniform target names, each calling the shell
toolchain directly so `nx run-many -t lint` reaches it alongside a Python or Rust
project: `format` -> `shfmt -d`, `lint` -> `shellcheck` (plus `actionlint` on a
project that owns workflows), and `test` -> `bats` as above. Shell has no
`typecheck` or `build` — declare neither rather than a no-op target. The
repo-level `coverage` target is the one aggregate and carries the same name in
every language.

## Verification

- [ ] **Target shell decided.** The target shell is chosen explicitly (POSIX `sh`
  without bashisms, or `bash` tested on macOS and Linux).
- [ ] **Quality gates.** `shellcheck` and `shfmt` are enforced and fail on
  issues — no warnings backlog.
- [ ] **Tests.** `bats` (or similar) covers unit-level behavior, plus real
  integration tests against the host tool when one exists.
- [ ] **Coverage decision.** 95% line coverage enforced in `just check` via
  `kcov`/`bashcov` — on the merged report once the suite spans projects — or a
  documented lower bar in `AGENTS.md` (shell is the most likely case to justify
  lowering it), still enforced.
- [ ] **Robustness.** `set -euo pipefail`, quoted expansions, and explicit
  handling of network/filesystem failures.
- [ ] **CI matrix.** CI runs across the supported OSes and proves the scripts
  end-to-end, not just that they parse.
- [ ] **Projects without a workspace primitive.** Each shell project has a
  `project.json` declaring its targets, tags, and inputs (there being no language
  manifest to sit beside); host-tool dependencies are declared and checked at
  runtime; the tooling's own ecosystem is pinned once at the repo root, not per
  project; and a shared `lib/` is its own project named in its consumers'
  `implicitDependencies`.
- [ ] **Suite split into projects.** `bats` is invoked per project rooted at that
  project; the offline unit tests are the script project's `test` target and the
  host-tool integration suite is its own project depending only on the scripts it
  drives — both run by `just check`.
- [ ] **Coverage merged and still gating.** Per-project `test` targets write
  `kcov`/`bashcov` data into their target outputs and a repo-level `coverage`
  target depending on every `test` merges them and fails below the bar (kcov has
  no fail-under flag, so the target reads the merged line rate itself) — a
  documented lower bar is still enforced on the merged report.
- [ ] **Uniform target names.** Each shell project declares `format` / `lint` /
  `test` calling shfmt / shellcheck / bats, so `nx affected` and `run-many` reach
  them by name in a polyglot repo.
