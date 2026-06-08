# Language: Rust

Language-level conventions for any Rust repo. Combine with a product shape
(`shapes/cli.md`, `shapes/library.md`, ...) and `ci.md`.

- **Toolchain.** Stable Rust. Use `rustfmt` and `clippy` as strict gates (deny
  warnings); consider `cargo nextest` for faster, clearer test runs.
- **Tests run in the gate, including e2e.** `cargo test` / `cargo nextest run`
  — and the integration/e2e tests under `tests/` — run in the default
  `just check` and in CI. Do **not** mark e2e tests `#[ignore]` to keep them out
  of the default run: `cargo test` skips ignored tests by default, so that
  quietly makes realistic coverage opt-in and defeats its purpose. `#[ignore]`
  is for the rare test that must be invoked explicitly (e.g. needs live
  credentials), not a way to speed up the gate. Split genuinely slow journeys
  into a separate target that CI still runs, never out of the gate entirely.
- **Security and licensing.** Run `cargo deny` (advisories + licenses) when you
  distribute binaries or libraries.
- **Boundary validation.** Validate external input at the edges; model invalid
  states out with the type system where practical.
- **Cross-platform.** Build and test on Linux, macOS, and Windows when the
  artifact ships to users; produce release binaries from CI.
- **Releases.** Use a modern, reliable distribution workflow (e.g., cargo-dist)
  when appropriate; publish checksums for binaries.
- **Command mapping.**
  - `just bootstrap` -> fetch toolchain + `cargo fetch`
  - `just check` -> `cargo fmt --check` + `cargo clippy -- -D warnings` + tests
  - `just upgrade` -> `cargo update`, then re-run `just check`
