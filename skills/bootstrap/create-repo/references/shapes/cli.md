# Shape: CLI

Language-agnostic principles for building a good command-line tool. Pair with
the implementation language (`languages/python.md`, `languages/rust.md`,
`languages/typescript.md`, `languages/bash.md`) and `ci.md`. Where an
intersection reference exists (e.g. `intersections/python-cli.md`), prefer it.

- **E2E from the user's side.** Test the built/installed CLI the way a user runs
  it — invoke the real entry point as a subprocess and assert on exit codes,
  stdout, and stderr. Snapshot/golden-file tests are ideal for stable output.
  In-process `main()` calls are not enough — nor is mocking the
  process/network/filesystem the command touches: that proves the mock, not the
  CLI. Cover every command and flag a user reaches, happy path and
  failure/recovery, not one smoke test.
- **Boundaries.** Validate and bound arguments and stdin at the edge. Handle
  process, network, and filesystem failures explicitly; fail with a non-zero
  exit code and a clear message.
- **Output is a contract.** Be minimal on success; on error, print the exact
  problem and a suggested next action to stderr. Keep machine-readable output
  (e.g. `--json`) stable and separate from human prose.
- **Exit codes.** Use distinct, documented exit codes; `0` only on success.
- **Help and discoverability.** `--help` should be accurate and complete; keep
  the command surface small and predictable.
- **Distribution.** If users install it, ship packaging + release automation,
  document supported platforms, and have CI exercise the recommended end-user
  install method on the real platform matrix (see `ci.md`, `releasing.md`, and
  the language reference) — proving the install path users take, not just the dev
  bootstrap.
- **One asset-naming contract across every install surface.** A CLI is often
  installed several ways — GitHub Releases, an `install.sh` one-liner, a
  composite GitHub Action (`action.yml`), a container image, a registry. Every
  surface that *downloads* a release asset must construct the **same**
  archive/`.sha256` name the release workflow produced; the moment they drift, an
  install path 404s in a way local testing never catches. Pin the naming once and
  have CI test each surface (e.g. one job that installs the action via `source`
  and another via `download`). When a published package *carries* the binary,
  smoke-test the publish-shape package with the dev escape-hatch unset so CI
  proves the shipped artifact, not `target/` (see the live/install tier in
  `ci.md`).
- **Commands.** The CLI e2e suite runs in the default `just check` and in CI —
  it is never opt-in or excluded by default. `just e2e` is only a convenience
  for running the slow end-to-end journeys *in isolation*; it must not be the
  one place they run. If a journey is genuinely too expensive for every gate
  run, that is a deliberate, documented exception that CI still executes (e.g.
  nightly) — not silent exclusion from the gate.

## Verification

- [ ] **E2E drives the real entry point.** E2E invokes the built/installed CLI as
  a subprocess and asserts on exit code, stdout, and stderr — not in-process
  `main()` calls, and not mocking the process/network/filesystem the command
  touches. It covers every command and flag a user reaches, happy path and
  failure/recovery.
- [ ] **Boundaries validated.** Arguments and stdin are validated at the edge;
  process/network/filesystem failures fail with a non-zero exit code and a clear
  message.
- [ ] **Output is a contract.** Minimal on success; on error the exact problem
  plus a suggested next action go to stderr; machine-readable output (e.g.
  `--json`) is stable and separate from human prose. Exit codes are distinct and
  documented (`0` only on success), and `--help` is accurate and complete.
- [ ] **One asset-naming contract across install surfaces.** Every surface that
  downloads a release asset constructs the same archive/`.sha256` name the
  release workflow produced, and CI exercises each surface (so an install path
  can't 404 unnoticed).
