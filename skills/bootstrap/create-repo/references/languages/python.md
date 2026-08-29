# Language: Python

Language-level conventions for any Python repo. Combine with a product shape
(`shapes/cli.md`, `shapes/library.md`, ...) and `ci.md`. For Python CLIs,
`intersections/python-cli.md` adds the packaging and e2e specifics.

- **Version and environment.** Use Python 3.14 unless something makes it
  impossible; manage the environment and dependencies with `uv`. Keep each
  project's config in that project's own `pyproject.toml` — see "Projects, the
  uv workspace, and the graph" below for how those manifests relate.
- **Quality toolchain.** `ruff` for linting and formatting, `ty` for type
  checking, `pytest` for tests. All run under `just check` and fail on issues.
- **Warnings.** No warnings-only mode; treat warnings as errors where feasible.
- **Boundary validation.** Prefer Pydantic for every external / IO boundary
  (request bodies, config, env, third-party responses). Keep boundary code
  explicit and tested.
- **No injection from untrusted input.** Run subprocesses without `shell=True` on
  external input, keep `eval`/`exec` and unsafe deserialization (`pickle`,
  `yaml.load` without `SafeLoader`) away from untrusted data, and build SQL and OS
  commands with parameterized APIs, not string interpolation.
- **Clients.** Prefer official, async, well-typed libraries at boundaries;
  otherwise write a small async typed client yourself.
- **Coverage in the gate.** Measure coverage on every `pytest` run (configure
  `pytest-cov`) and fail the gate below the threshold — with the suite split
  across projects that means `pytest --cov` per project and one aggregate
  `coverage report --fail-under=95`, per "Splitting the suite" below.
  95% line coverage is the default bar; lower it only with a
  documented reason in `AGENTS.md`. Coverage is a default gate, not opt-in: a
  repo that ships behavior its tests never execute has a hole, and the number
  makes the hole visible. But coverage is a *floor, not the target*: line
  coverage is satisfiable with mocks that prove nothing, so chase real e2e
  journeys and let the number follow — don't mock the layer under test to clear
  the bar.
- **Command mapping.** The root recipes delegate to the orchestrator, which runs
  the per-project targets named below.
  - `just bootstrap` -> `uv sync` at the workspace root (one resolve covering
    every project)
  - `just check` -> `nx affected -t format lint typecheck test` plus the
    repo-level `coverage` target
  - `just upgrade` -> `uv lock --upgrade` then `uv sync`
- **CI.** Run `just check` on a clean checkout; the gate's coverage threshold
  travels with it.
- **Avoid baggage.** No `src` layout, asdf, direnv, or pre-commit unless clearly
  justified for this repo.

## Projects, the uv workspace, and the graph

Nx runs the targets; **uv resolves the dependencies**. Both layers describe the
same projects and meet at the project directory.

- **One `pyproject.toml` per project, one `uv.lock` for the repo.** The root
  `pyproject.toml` carries `[tool.uv.workspace] members = [...]` naming each
  project's directory, and every project has its own `pyproject.toml` beside its
  package. `uv sync` at the root resolves them all into a single root `uv.lock` —
  never a lockfile per project, which would let two projects resolve the same
  dependency to different versions.
- **The project definition sits beside the manifest.** `project.json` lives in
  the same directory as that project's `pyproject.toml`, so Nx and uv agree on
  where a project starts and ends. A cross-project import is a real workspace
  dependency (`[tool.uv.sources] <dep> = { workspace = true }`), which is what
  makes the Nx edge true rather than merely declared.
- **Test-only projects are workspace members too.** A `<pkg>-e2e` project is a
  `pyproject.toml` with nothing to publish; it declares a workspace dependency on
  the package it drives, so it resolves against the same lock and exercises the
  installed package rather than importing it by path.

### Splitting the suite

- **`pytest` is invoked per project, rooted at that project.** A project's `test`
  target runs `uv run --project <dir> pytest` (or `pytest` with the target's
  `cwd` set to the project) so it collects only its own tests and reads its own
  `[tool.pytest.ini_options]`. One repo-wide `pytest` over a single top-level
  `tests/` directory is the thing the split replaces.
- **Fast tier with the code, expensive tiers in their own projects.** Unit tests
  live in the package project and are its `test` target. Anything slow or
  external — a subprocess-driven CLI journey, a suite needing a container, a
  live-service client suite — becomes its own project (`<pkg>-e2e`,
  `<pkg>-integration`) whose `test` target is that suite and which depends only on
  what it exercises. Do **not** express the tiers as pytest markers deselected by
  default (`-m "not slow"`): a marker leaves the slow tests inside the fast
  project, so an unrelated change still collects them and `nx affected` has
  nothing to skip.
- **Coverage survives the split by combining, not by lowering.** A per-project
  `--cov-fail-under` starts failing the moment a package's e2e tier moves into a
  sibling project, because the package's own run no longer sees the lines that
  tier covers. Measure per project and enforce once over the union:
  - set `[tool.coverage.run] parallel = true` and `relative_files = true` in the
    root config so data files written from different project directories combine;
  - each `test` target runs `pytest --cov=<the package it covers> --cov-report=`
    with `COVERAGE_FILE` pointing at a per-project data file under that project's
    declared target outputs, and **no** `--cov-fail-under`;
  - a repo-level `coverage` target `dependsOn` every project's `test`, runs
    `coverage combine` over those data files and then `coverage report
    --fail-under=95`, and `just check` runs it.

  The 95% bar and its failure behaviour are unchanged; only where the number is
  computed moved. Dropping the aggregate target, or lowering the bar because the
  split made a project's own number look bad, is losing the gate rather than
  splitting it.

### Target names

Each Python project declares the repo-uniform target names, each calling the
Python tool directly so `nx run-many -t lint` reaches it alongside a Rust or
TypeScript project: `format` -> `ruff format --check`, `lint` -> `ruff check`,
`typecheck` -> `ty`, `test` -> `pytest` as above, and `build` -> `uv build` on
the projects that publish a distribution (omit `build` on the ones that don't
rather than declaring an empty target). The repo-level `coverage` target is the
one aggregate and carries the same name in every language.

## Verification

- [ ] **Version + environment.** Python 3.14 (unless something makes it
  impossible); `uv` manages the environment and dependencies; each project's
  config lives in that project's own `pyproject.toml`.
- [ ] **Quality toolchain in the gate.** `ruff` (lint + format), `ty` (type
  check), and `pytest` all run under `just check` and fail on issues, with no
  warnings-only mode.
- [ ] **Boundary validation.** External / IO boundaries (request bodies, config,
  env, third-party responses) are validated with Pydantic; boundary code is
  explicit and tested.
- [ ] **Injection-safe boundaries.** No `shell=True` on external input, no
  `eval`/`exec` or unsafe deserialization of untrusted data, and SQL / OS commands
  are parameterized rather than string-built.
- [ ] **Coverage enforced.** Every `pytest` run measures coverage (`--cov`) and
  the gate fails below 95% lines — enforced by the aggregate `coverage report
  --fail-under=95` once the suite spans projects (95% default bar, lower only
  with a documented reason in `AGENTS.md`).
- [ ] **Command mapping wired.** `just bootstrap` → `uv sync` at the workspace
  root; `just check` → `nx affected -t format lint typecheck test` plus the
  `coverage` target, whose per-project targets run `ruff format --check` +
  `ruff check` + `ty` + `pytest --cov`; `just upgrade` → `uv lock --upgrade`
  then `uv sync`.
- [ ] **uv workspace under the graph.** The root `pyproject.toml` declares
  `[tool.uv.workspace]` members, each project has its own `pyproject.toml` with a
  `project.json` beside it, cross-project deps use `[tool.uv.sources]
  workspace = true`, and the repo has exactly one `uv.lock` — uv resolves, Nx
  runs targets.
- [ ] **Suite split into projects.** `pytest` is invoked per project rooted at
  that project; the fast unit tier is the package project's `test` target and
  every slow or external-service suite is its own project — not a pytest marker
  the default run deselects.
- [ ] **Coverage combined and still gating.** Per-project `test` targets write
  coverage data (`parallel` + `relative_files`, a per-project `COVERAGE_FILE`,
  no per-project `--cov-fail-under`) and a repo-level `coverage` target
  depending on every `test` runs `coverage combine` + `coverage report
  --fail-under=95`, failing the build below the bar.
- [ ] **Uniform target names.** Each Python project declares `format` / `lint` /
  `typecheck` / `test` (plus `build` where it publishes) calling ruff / ty /
  pytest / uv, so `nx affected` and `run-many` reach them by name in a polyglot
  repo.
