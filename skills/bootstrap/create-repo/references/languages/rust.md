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
- **MSRV (when you promise one).** Declare `rust-version` once in
  `[workspace.package]` and inherit it per crate, set
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
  into their own project, which `just check` still runs — see "Splitting the
  suite" below — never out of the gate entirely.
- **Coverage in the gate.** Measure coverage in the gate and fail below the
  threshold — `cargo llvm-cov` (or `cargo tarpaulin`), with the enforcing
  `--fail-under-lines 95` on the aggregate report once the suite spans projects,
  per "Splitting the suite" below. 95% line coverage is the default bar; lower
  it only with a
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
- **Command mapping.** The root recipes delegate to the orchestrator, which runs
  the per-project targets named below.
  - `just bootstrap` -> fetch toolchain + `cargo fetch` at the workspace root
    (one resolve covering every crate)
  - `just check` -> `nx affected -t format lint test build` plus the repo-level
    `coverage` and supply-chain targets, with the per-project targets running
    `cargo fmt --check`, `cargo clippy -- -D warnings`, and the tests, and
    coverage enforced on the aggregate (`cargo llvm-cov report
    --fail-under-lines 95`)
  - `just upgrade` -> `cargo update`, then re-run `just check`

## Projects, the Cargo workspace, and the graph

Nx runs the targets; **Cargo resolves the dependencies**. Both layers describe
the same projects and meet at the crate directory.

- **One `Cargo.toml` per crate, one `Cargo.lock` for the repo.** The root
  `Cargo.toml` is a virtual manifest carrying `[workspace] members = [...]` and
  a `[workspace.dependencies]` table every member inherits from
  (`serde = { workspace = true }`); each crate has its own `Cargo.toml`. Cargo
  resolves the whole workspace into a single root `Cargo.lock` — never a lockfile
  per crate. `rust-toolchain.toml` is likewise workspace-wide, not per crate.
- **The project definition sits beside the manifest.** `project.json` lives in
  the same directory as that crate's `Cargo.toml`, so Nx and Cargo agree on where
  a project starts and ends. A cross-project edge is a real path dependency
  (`foo = { path = "../foo" }`, or `workspace = true` against a workspace member),
  which is what makes the Nx edge true rather than merely declared.
- **Inherit the shared metadata.** Put `version`, `edition`, `rust-version`
  (MSRV), and `license` in `[workspace.package]` and inherit them per crate
  (`rust-version.workspace = true`) so one logical version spans every manifest —
  which is what makes a single `set-version.sh` possible when you cut releases.
- **Test-only crates are workspace members too.** A `<bin>-e2e` crate is a
  `publish = false` member holding only `tests/`; it takes a dev-dependency on
  the crate it drives, so it resolves against the same lock.

### Splitting the suite

- **The runner is invoked per crate.** A project's `test` target runs
  `cargo nextest run -p <crate>` (or `cargo test -p <crate>`), so it builds and
  runs only that crate's tests. One workspace-wide `cargo test --workspace` is
  the thing the split replaces: it rebuilds and reruns everything for any change.
- **Fast tier with the code, the binary tier in its own crate.** Unit tests and
  the crate's own `tests/` integration tests are the library crate's `test`
  target. The suite that spawns the compiled binary (`assert_cmd`) moves into its
  own `<bin>-e2e` crate/project whose `test` target `dependsOn` the binary
  crate's `build`, so it runs against a real freshly-built artifact and an
  unrelated library change cannot reach it. Same for any suite that needs a
  container or a live service.
- **`#[ignore]` is still not a tiering mechanism.** The split is what makes the
  slow tier skippable; `#[ignore]` only makes it invisible. Keep `#[ignore]` for
  the live tier that genuinely needs credentials (see `intersections/rust-cli.md`).
- **Coverage survives the split by combining, not by lowering.** A per-crate
  `cargo llvm-cov --fail-under-lines 95` starts failing the moment the binary's
  e2e tier moves into a sibling crate, because the binary crate's own run no
  longer sees the lines that tier covers. Measure per project and enforce once
  over the union:
  - each `test` target runs `cargo llvm-cov --no-report nextest -p <crate>`,
    which writes raw profile data into the shared `target/llvm-cov-target`
    directory instead of reporting;
  - a repo-level `coverage` target `dependsOn` every project's `test` and runs
    `cargo llvm-cov report --fail-under-lines 95` over the accumulated profiles;
  - `just check` runs it, and the profile directory is declared as those targets'
    outputs so caching replays it rather than silently reporting on a partial set.

  The 95% bar and its failure behaviour are unchanged; only where the number is
  computed moved. Dropping the aggregate target, or lowering the bar because the
  split made a crate's own number look bad, is losing the gate rather than
  splitting it.

### Target names

Each Rust project declares the repo-uniform target names, each calling cargo
directly so `nx run-many -t lint` reaches it alongside a Python or TypeScript
project: `format` -> `cargo fmt -p <crate> --check`, `lint` ->
`cargo clippy -p <crate> --all-targets -- -D warnings`, `test` -> nextest as
above, and `build` -> `cargo build -p <crate>`. Rust has no separate `typecheck`
target — `cargo clippy`/`build` type-check as they go, so declare no empty
`typecheck` rather than a no-op one. The repo-level `coverage` target is the one
aggregate and carries the same name in every language; the supply-chain check
(`cargo deny` + `cargo machete`) is workspace-wide by nature and stays a
repo-level target too.

## Verification

- [ ] **Toolchain.** Stable Rust with `rustfmt` and `clippy -D warnings` as
  strict gates; the toolchain is pinned in `rust-toolchain.toml` (channel,
  components, release targets) and CI installs from it.
- [ ] **MSRV (if promised).** `rust-version` in `[workspace.package]` and
  inherited per crate, `msrv` in `clippy.toml`, and a `just msrv` recipe — run in
  CI when the minimum is a real commitment.
- [ ] **Tests incl. e2e in the gate.** `cargo test` / `cargo nextest run` and the
  `tests/` integration/e2e run in `just check` and CI — via their projects'
  `test` targets; e2e is not `#[ignore]`-d out of the default run.
- [ ] **Coverage enforced.** `cargo llvm-cov` (or tarpaulin) runs in the gate and
  `--fail-under-lines 95` fails it below the bar, on the aggregate report once
  the suite spans projects.
- [ ] **Supply-chain gate.** A committed `deny.toml` plus `cargo deny check` and
  `cargo machete` run as their own Linux-only step/job.
- [ ] **Release archives.** Per-platform archives are built in CI on native
  runners (see `releasing.md` / `intersections/rust-cli.md`).
- [ ] **Cargo workspace under the graph.** A root virtual `Cargo.toml` declares
  `[workspace] members` with shared `[workspace.dependencies]` /
  `[workspace.package]` metadata inherited per crate, each crate has its own
  `Cargo.toml` with a `project.json` beside it, cross-crate edges are real path /
  workspace dependencies, and the repo has exactly one `Cargo.lock` and one
  workspace-wide `rust-toolchain.toml`.
- [ ] **Suite split into projects.** `cargo nextest run -p <crate>` runs per
  project (not one `--workspace` sweep); unit and `tests/` integration are the
  library crate's `test` target, and the compiled-binary suite is its own
  `<bin>-e2e` crate whose `test` `dependsOn` the binary crate's `build`.
- [ ] **Coverage combined and still gating.** Per-project `test` targets run
  `cargo llvm-cov --no-report` into the shared profile directory and a
  repo-level `coverage` target depending on every `test` runs `cargo llvm-cov
  report --fail-under-lines 95`, failing the build below the bar.
- [ ] **Uniform target names.** Each Rust project declares `format` / `lint` /
  `test` / `build` calling cargo, so `nx affected` and `run-many` reach them by
  name in a polyglot repo.
