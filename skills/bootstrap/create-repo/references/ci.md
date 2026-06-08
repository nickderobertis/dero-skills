# Cross-cutting: GitHub Actions / CI

Applies on top of whichever product shape and language you chose. CI's job is to
prove the artifact the way a future maintainer or user would encounter it — not
to re-run a developer's warm local environment.

- **Clean checkout -> bootstrap -> full gate.** Every run starts from a clean
  checkout, runs `just bootstrap`, then `just check`. If bootstrap can't produce
  a working repo from scratch, that is the bug.
- **Realistic platform matrix.** Use an OS matrix when the artifact is
  cross-platform (CLIs, plugins, binaries). Test the versions you actually
  support.
- **Validate generated files.** Fail if committed generated files (lockfiles,
  schemas, formatted code) are out of date.
- **Cache for speed, never for correctness.** Cache dependencies, but never let
  a cache hide a broken clean build.
- **Artifacts after gates.** Upload build artifacts only once gates pass.
  Publish checksums for binaries; sign where appropriate.
- **Logs are context.** Keep logs minimal on success; emit detailed diagnostics
  only on failure, so a failed run points straight at the cause.
