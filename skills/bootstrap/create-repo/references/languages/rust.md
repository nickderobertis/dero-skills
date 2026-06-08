# Language: Rust

Language-level conventions for any Rust repo. Combine with a product shape
(`shapes/cli.md`, `shapes/library.md`, ...) and `ci.md`.

- **Toolchain.** Stable Rust. Use `rustfmt` and `clippy` as strict gates (deny
  warnings); consider `cargo nextest` for faster, clearer test runs.
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
