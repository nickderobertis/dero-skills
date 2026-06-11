# Language: Python

Language-level conventions for any Python repo. Combine with a product shape
(`shapes/cli.md`, `shapes/library.md`, ...) and `ci.md`. For Python CLIs,
`intersections/python-cli.md` adds the packaging and e2e specifics.

- **Version and environment.** Use Python 3.14 unless something makes it
  impossible; manage the environment and dependencies with `uv`. Keep all
  project config in `pyproject.toml`.
- **Quality toolchain.** `ruff` for linting and formatting, `ty` for type
  checking, `pytest` for tests. All run under `just check` and fail on issues.
- **Warnings.** No warnings-only mode; treat warnings as errors where feasible.
- **Boundary validation.** Prefer Pydantic for every external / IO boundary
  (request bodies, config, env, third-party responses). Keep boundary code
  explicit and tested.
- **Clients.** Prefer official, async, well-typed libraries at boundaries;
  otherwise write a small async typed client yourself.
- **Coverage in the gate.** Measure coverage on every `pytest` run and fail the
  gate below the threshold — `pytest --cov --cov-fail-under=95` (configure
  `pytest-cov`). 95% line coverage is the default bar; lower it only with a
  documented reason in `AGENTS.md`. Coverage is a default gate, not opt-in: a
  repo that ships behavior its tests never execute has a hole, and the number
  makes the hole visible.
- **Command mapping.**
  - `just bootstrap` -> `uv sync`
  - `just check` -> `ruff format --check` + `ruff check` + `ty` +
    `pytest --cov --cov-fail-under=95`
  - `just upgrade` -> `uv lock --upgrade` then `uv sync`
- **CI.** Run `just check` on a clean checkout; the gate's coverage threshold
  travels with it.
- **Avoid baggage.** No `src` layout, asdf, direnv, or pre-commit unless clearly
  justified for this repo.
