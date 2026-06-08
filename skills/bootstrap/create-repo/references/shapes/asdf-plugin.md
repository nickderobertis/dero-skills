# Shape: asdf plugin

Principles for an asdf (or similar host-tool) plugin. Almost always pair with
`languages/bash.md` and `ci.md`.

- **Cover the plugin lifecycle.** Test install, uninstall, rehash, and version
  resolution. Handle network failures during downloads gracefully.
- **Real host-tool integration.** Run the plugin through the host tool itself
  (e.g., `asdf plugin test`), not only isolated unit tests of helper functions.
- **Portability.** Decide the target shell and test on macOS and Linux; avoid
  bashisms if you target POSIX `sh` (see `languages/bash.md`).
- **Commands.**
  - `just check` -> `shellcheck` + `shfmt` + `bats` unit tests
  - `just e2e` -> host-tool integration (the plugin working end to end)
- **CI.** Matrix across the supported OSes; validate the plugin works end to end
  via the host tool, simulating a real user installing a version.
