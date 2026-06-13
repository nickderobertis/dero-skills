# Language: Rust

Language-level conventions for any Rust repo. Combine with a product shape
(`shapes/cli.md`, `shapes/library.md`, ...) and `ci.md`. For a compiled binary
shipped to users, prefer the `intersections/rust-cli.md` reference, which makes
the points below concrete.

- **Toolchain.** Stable Rust. Use `rustfmt` and `clippy` as strict gates (deny
  warnings); consider `cargo nextest` for faster, clearer test runs. Pin the
  toolchain in `rust-toolchain.toml` (channel, `components` —
  `rustfmt`/`clippy`/`llvm-tools` — and the release `targets`) as the single
  source of truth, and have CI install from it rather than from a separate action
  pin.
- **MSRV (when you promise one).** Declare `rust-version` in `Cargo.toml`, set
  `msrv` in `clippy.toml` so clippy flags too-new APIs, and add a `just msrv`
  recipe (`cargo +<msrv> check --locked --all-targets --all-features`). Run it in
  CI if the minimum is a real commitment, not just documentation.
- **Tests run in the gate, including e2e.** `cargo test` / `cargo nextest run`
  — and the integration/e2e tests under `tests/` — run in the default
  `just check` and in CI. Do **not** mark e2e tests `#[ignore]` to keep them out
  of the default run: `cargo test` skips ignored tests by default, so that
  quietly makes realistic coverage opt-in and defeats its purpose. `#[ignore]`
  is for the rare test that must be invoked explicitly (e.g. needs live
  credentials), not a way to speed up the gate. Split genuinely slow journeys
  into a separate target that CI still runs, never out of the gate entirely.
- **Coverage in the gate.** Measure coverage in the gate and fail below the
  threshold — `cargo llvm-cov --fail-under-lines 95` (or `cargo tarpaulin
  --fail-under 95`). 95% line coverage is the default bar; lower it only with a
  documented reason in `AGENTS.md`. Coverage is a default gate, not opt-in.
- **Supply chain as a dedicated gate job.** Commit a `deny.toml` (advisories, an
  explicit `licenses` allow-list, `bans`, `sources`) and run `cargo deny check`
  plus `cargo machete` (unused dependencies) as their own step / CI job — run
  once on Linux, not duplicated across the OS matrix. This is a default gate, not
  a "when you distribute" extra.
- **Boundary validation.** Validate external input at the edges; model invalid
  states out with the type system where practical.
- **Cross-platform.** Build and test on Linux, macOS, and Windows when the
  artifact ships to users; produce release binaries from CI.
- **Releases.** Tag-driven and decoupled from versioning (see `releasing.md`;
  `release-plz` is the common driver). Build per-platform archives in CI with
  `taiki-e/upload-rust-binary-action` (`bin`, an `archive` name like
  `<bin>-<tag>-<target>`, `checksum: sha256`, `include: README/LICENSE/CHANGELOG`)
  on **native runners** per target (`ubuntu-24.04-arm` for `aarch64` Linux,
  macOS runners for Darwin) rather than cross-compiling everything. For a
  binary-only tool, `publish = false` and ship via GitHub Releases +
  `cargo install --git` / `install.sh`; crates.io is a separate, optional surface.
- **Performance (optional, informational).** For hot paths, add a bench tier
  (Criterion + `hyperfine` + `critcmp`, optionally `samply`/cachegrind) that
  posts a PR comment and **never gates** — keep it out of `just check`. See the
  bench-tier note in `ci.md`.
- **Command mapping.**
  - `just bootstrap` -> fetch toolchain + `cargo fetch`
  - `just check` -> `cargo fmt --check` + `cargo clippy -- -D warnings` + tests
    with coverage enforced (e.g. `cargo llvm-cov --fail-under-lines 95`)
  - `just upgrade` -> `cargo update`, then re-run `just check`
