# Shape: CLI

Language-agnostic principles for building a good command-line tool. Pair with
the implementation language (`languages/python.md`, `languages/rust.md`,
`languages/typescript.md`, `languages/bash.md`) and `ci.md`. Where an
intersection reference exists (e.g. `intersections/python-cli.md`), prefer it.

- **E2E from the user's side.** Test the built/installed CLI the way a user runs
  it — invoke the real entry point as a subprocess and assert on exit codes,
  stdout, and stderr. Snapshot/golden-file tests are ideal for stable output.
  In-process calls to `main()` are not enough.
- **Boundaries.** Validate and bound arguments and stdin at the edge. Handle
  process, network, and filesystem failures explicitly; fail with a non-zero
  exit code and a clear message.
- **Output is a contract.** Be minimal on success; on error, print the exact
  problem and a suggested next action to stderr. Keep machine-readable output
  (e.g. `--json`) stable and separate from human prose.
- **Exit codes.** Use distinct, documented exit codes; `0` only on success.
- **Help and discoverability.** `--help` should be accurate and complete; keep
  the command surface small and predictable.
- **Distribution.** If users install it, ship packaging + release automation and
  document supported platforms (see `ci.md` and the language reference).
- **Commands.** The CLI e2e suite runs in the default `just check` and in CI —
  it is never opt-in or excluded by default. `just e2e` is only a convenience
  for running the slow end-to-end journeys *in isolation*; it must not be the
  one place they run. If a journey is genuinely too expensive for every gate
  run, that is a deliberate, documented exception that CI still executes (e.g.
  nightly) — not silent exclusion from the gate.
