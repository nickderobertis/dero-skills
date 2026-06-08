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
- **Commands.** `just check` runs fmt/lint/typecheck/tests plus the CLI e2e
  suite; `just e2e` may run the slower end-to-end journeys on their own.
