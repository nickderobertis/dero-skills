# Cross-cutting: GitHub Actions / CI

Applies on top of whichever product shape and language you chose. CI's job is to
prove the artifact the way a future maintainer or user would encounter it — not
to re-run a developer's warm local environment.

- **Required, and it must run the gate.** A CI workflow is non-negotiable, and a
  workflow that doesn't invoke `just check` proves nothing. Start from
  [`assets/ci.yml.template`](../assets/ci.yml.template).
- **Clean checkout -> bootstrap -> full gate.** Every run starts from a clean
  checkout, runs `just bootstrap`, then `just check` (which includes e2e). If
  bootstrap can't produce a working repo from scratch, that is the bug. The
  baseline checker fails CI that never references `just check`.
- **Realistic platform matrix.** Use an OS matrix when the artifact is
  cross-platform (CLIs, plugins, binaries). Test the versions you actually
  support.
- **Prove the end-user install path, on the real platforms.** Bootstrapping the
  *dev* toolchain (`just bootstrap`) is not the same as installing the shipped
  artifact. When the repo produces something users install — a CLI, a binary, a
  published package, a plugin — add a CI job, separate from the dev gate, that
  installs it via the **recommended end-user method** (the README install
  one-liner, `uvx`/`pipx`, `npm i -g`, `cargo install`, `brew install`, asdf
  `plugin add`, ...) on the supported OS matrix, then runs the installed entry
  point (e.g. `tool --version`) as a smoke test. Run it against the artifact the
  release will ship — the built wheel / binary / tarball — so the path is proven
  on every PR, not only after a release. Prefer a single cross-platform install
  script or command so the docs, CI, and what users actually run never drift; if
  CI installs differently than the docs tell users to, CI is proving the wrong
  thing. This drift is invisible until a user hits it.
- **Coverage is a default gate.** The gate measures test coverage and fails
  below the threshold; 95% line coverage is the default bar. Because CI runs the
  same `just check`, the threshold is proven on every PR, not tracked as a
  vanity badge. Measure it with the language's tool (`pytest --cov-fail-under`,
  Vitest `coverage.thresholds`, `cargo llvm-cov --fail-under-lines`, ...). Lower
  the bar — or drop it for a stack where coverage tooling genuinely doesn't fit
  — only with a documented reason in `AGENTS.md`, never by silently leaving
  coverage unmeasured.
- **Validate generated files.** Fail if committed generated files (lockfiles,
  schemas, formatted code) are out of date.
- **Cache for speed, never for correctness.** Cache dependencies, but never let
  a cache hide a broken clean build.
- **Artifacts after gates.** Upload build artifacts only once gates pass.
  Publish checksums for binaries; sign where appropriate.
- **Logs are context.** Keep logs minimal on success; emit detailed diagnostics
  only on failure, so a failed run points straight at the cause.
