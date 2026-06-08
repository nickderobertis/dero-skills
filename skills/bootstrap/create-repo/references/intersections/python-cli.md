# Intersection: Python + CLI

Use this alongside `languages/python.md` and `shapes/cli.md` (and `ci.md`). It
covers only what is specific to the overlap — packaging a Python CLI and
testing it as users run it. This file is the model for how to add an
intersection reference when a shape and a language meet often enough to need
shared guidance.

- **Console entry point.** Define the CLI as a `project.scripts` entry point in
  `pyproject.toml` so it installs as a real command, not a `python -m` afterthought.
- **Argument boundary.** Parse and validate arguments at the boundary — a typed
  parser, or Pydantic settings for env/config. Reject bad input with a clear
  message and a non-zero exit code.
- **E2E the installed command.** Install into a clean environment with `uv` and
  invoke the actual console script as a subprocess; assert on exit code, stdout,
  and stderr. Don't settle for calling `main()` in-process — that misses
  packaging, entry-point, and argument-parsing bugs.
- **Run-without-install.** Document `uvx <tool>` / `uv run` usage so users can
  try it without a global install.
- **Distribution.** Build a wheel and sdist; publish with checksums. Keep
  startup fast (lazy imports) since CLI users feel cold-start latency.
- **Commands.** The subprocess-level CLI e2e suite runs in the default `just
  check` and in CI — never opt-in. `just e2e` only isolates the slower
  install-and-run journeys for a focused run; it is not where they exclusively
  live. Don't gate e2e behind a pytest marker that the default run deselects.
