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
- **Command mapping.**
  - `just bootstrap` -> `uv sync`
  - `just check` -> `ruff format --check` + `ruff check` + `ty` + `pytest`
  - `just upgrade` -> `uv lock --upgrade` then `uv sync`
- **CI.** Run `just check` on a clean checkout. Add coverage only if it
  materially helps; don't make it a vanity gate.
- **Avoid baggage.** No `src` layout, asdf, direnv, or pre-commit unless clearly
  justified for this repo.
