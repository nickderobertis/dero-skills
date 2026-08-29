# Intersection: Python + CLI

Use this alongside `languages/python.md` and `shapes/cli.md` (and `ci.md`). It
covers only what is specific to the overlap — packaging a Python CLI and
testing it as users run it. This file is the model for how to add an
intersection reference when a shape and a language meet often enough to need
shared guidance.

- **Console entry point.** Define the CLI as a `project.scripts` entry point in
  the CLI project's own `pyproject.toml` (the workspace member, not the
  workspace-root manifest) so it installs as a real command, not a `python -m`
  afterthought. A repo shipping several commands declares each in its own
  project's manifest.
- **Argument boundary.** Parse and validate arguments at the boundary — a typed
  parser, or Pydantic settings for env/config. Reject bad input with a clear
  message and a non-zero exit code.
- **E2E the installed command.** Install into a clean environment with `uv` and
  invoke the actual console script as a subprocess; assert on exit code, stdout,
  and stderr. In-process `main()` calls miss packaging, entry-point, and
  argument-parsing bugs; `monkeypatch`/`unittest.mock` stubbing the filesystem,
  subprocess, or network the command uses proves the mock, not the tool. Use real
  files in `tmp_path` and a real local server; mock only a genuinely external
  third party you can't run, gated to the live tier.
- **Run-without-install.** Document `uvx <tool>` / `uv run` usage so users can
  try it without a global install.
- **Distribution.** Build a wheel and sdist; publish with checksums. Keep
  startup fast (lazy imports) since CLI users feel cold-start latency. Have CI
  install the built wheel the recommended end-user way (`uvx`/`pipx install`) on
  the real OS matrix and run `<tool> --version` as a smoke test, so the install
  path users take is proven — not just the dev `uv sync` (see `ci.md`).
- **Where the e2e suite lives.** The install-and-drive suite is its own
  `<cli>-e2e` project: a `publish = false` uv workspace member holding only
  `tests/`, depending on the CLI package so it resolves against the same
  `uv.lock` and installs the real distribution. Its `test` target `dependsOn` the
  CLI project's `build`, so it drives a freshly built wheel rather than the
  source tree.
- **Commands.** The subprocess-level CLI e2e suite runs in the default `just
  check` and in CI — never opt-in; `just check` reaches it as that project's
  `test` target. `just e2e` only isolates the slower install-and-run journeys for
  a focused run (`nx run <cli>-e2e:test`); it is not where they exclusively live.
  Don't gate e2e behind a pytest marker that the default run deselects — the
  project split is what makes the tier skippable; a marker only makes it
  invisible, leaving the slow tests collected by every run.

## Verification

- [ ] **Console entry point.** The CLI is a `project.scripts` entry point in the
  CLI project's own `pyproject.toml` (a real installed command, not a `python -m`
  afterthought), one per command the repo ships.
- [ ] **Argument boundary.** Arguments are parsed and validated at the boundary;
  bad input is rejected with a clear message and a non-zero exit code.
- [ ] **E2E the installed command.** E2E installs into a clean environment with
  `uv` and invokes the actual console script as a subprocess, asserting on exit
  code, stdout, and stderr — real files in `tmp_path`, no `monkeypatch`/
  `unittest.mock` stubbing the filesystem, subprocess, or network.
- [ ] **E2E lives in its own project.** The install-and-drive suite is a
  `publish = false` uv workspace member (`<cli>-e2e`) depending on the CLI
  package, whose `test` target `dependsOn` the CLI project's `build` — not a
  pytest marker the default run deselects.
- [ ] **Run-without-install documented.** `uvx <tool>` / `uv run` usage is
  documented.
- [ ] **Distribution proven.** A wheel and sdist are built with checksums, and CI
  installs the built wheel the recommended end-user way (`uvx`/`pipx install`) on
  the OS matrix and runs `<tool> --version` as a smoke test.
