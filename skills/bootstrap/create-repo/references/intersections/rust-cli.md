# Intersection: Rust CLI

Where `shapes/cli.md` and `languages/rust.md` meet: a compiled Rust binary shipped
to users. Use all three (plus `ci.md` and, when you cut versions, `releasing.md`).
[`allowlister`](https://github.com/nickderobertis/allowlister),
[`oneharness`](https://github.com/nickderobertis/oneharness), and
[`github-secrets`](https://github.com/nickderobertis/github-secrets) are worked
reference implementations of this intersection.

## Testing the compiled binary

- **Drive the built binary, not `main()`.** Use `assert_cmd` to spawn the compiled
  binary as a subprocess and assert on exit code, stdout, and stderr. In-process
  calls to `run()` are integration tests, not e2e.
- **Keep e2e a separate project, still in the gate.** The slow binary suite lives
  in its own `<bin>-e2e` crate — a `publish = false` Cargo workspace member
  holding only `tests/`, with a dev-dependency on the binary crate — and that
  crate is its own project whose `test` target `dependsOn` the binary crate's
  `build`. So `just test` runs the fast unit/integration tiers, `just test-e2e`
  (`nx run <bin>-e2e:test`) runs the binary journeys, and `just check` runs
  **both** through the graph, while a library-only change never rebuilds and
  respawns the binary. Don't `#[ignore]` e2e to keep it out of the default run:
  `cargo test` skips ignored tests, which silently makes realistic coverage
  opt-in — and unlike the project split, it saves nothing, since the tests are
  still compiled and collected.
- **Deterministic e2e is offline and tempdir-isolated.** A *live* tier that needs
  real services or credentials is the one sanctioned use of `#[ignore]` /
  env-gating — keep it compiling (don't `#[cfg]` it out), and run it in a
  dedicated workflow that requires its credential and fails fast without it (fork
  PRs gated by the require-approval repo setting). See the live/integration tier
  in `ci.md`.
- **`cargo nextest`** for fast, clear runs; snapshot stable output with `insta`
  where it helps.

## Toolchain, MSRV, and supply chain

- **Pin the toolchain in `rust-toolchain.toml`** at the workspace root — channel,
  `components` (`rustfmt`, `clippy`, `llvm-tools` for coverage), and the release
  `targets` — as the single source of truth for every crate, and have CI install
  from it.
- **Declare an MSRV and check it.** Set `rust-version` in `[workspace.package]`
  and inherit it per crate, set
  `msrv` in `clippy.toml`, and add a `just msrv` recipe
  (`cargo +<msrv> check --locked --all-targets --all-features`); run it in CI if
  the MSRV is a real promise.
- **Supply chain as a dedicated gate job.** Commit a `deny.toml` (advisories, an
  explicit `licenses` allow-list, `bans`, `sources`) and run `cargo deny check`
  plus `cargo machete` (unused deps) as a separate Linux-only CI job — not spread
  across the OS matrix.
- **Coverage:** `cargo llvm-cov` per project into the shared profile directory
  with `cargo llvm-cov report --fail-under-lines 95` on the aggregate — the
  binary crate's own number cannot see the lines its e2e crate covers (see
  `rust.md`).

## Distribution

- **Build per-platform archives in CI** with `taiki-e/upload-rust-binary-action`:
  set `bin`, an `archive` name like `<bin>-<tag>-<target>`, `checksum: sha256`,
  and `include: README.md,LICENSE,CHANGELOG.md`. Build each target on a **native
  runner** (`ubuntu-24.04-arm` for `aarch64-unknown-linux-gnu`, macOS runners for
  Darwin) rather than cross-compiling everything.
- **One asset-naming contract across every install surface.** A binary-only tool
  is usually installed several ways — GitHub Releases, an `install.sh`, a
  composite `action.yml`, a container image, optionally crates.io. Every surface
  that *downloads* a release asset must construct the **same** archive/`.sha256`
  name the release workflow produced; a CI job should exercise each surface (see
  the multi-surface section of `cli.md`). crates.io publication is separate and
  optional (`publish = false` for binary-only tools).
- **Releases** are tag-driven and decoupled from versioning; `release-plz` is the
  common driver. See `releasing.md`.

## Optional performance tier

Performance-sensitive CLIs add an **informational** bench tier (Criterion +
`hyperfine` + `critcmp`, optionally `samply`/cachegrind) that posts a PR comment
and never gates. Keep it out of `just check`. See the bench-tier note in `ci.md`.

## Command mapping

- `just check` → `nx affected -t format lint test build` plus the repo-level
  `coverage` target, so each crate's project runs `cargo fmt --check` + `cargo
  clippy -- -D warnings` + `cargo nextest run -p <crate>` (unit/integration) and
  the `<bin>-e2e` project runs the binary journeys; supply-chain (`cargo deny` +
  `cargo machete`) and `cargo doc -D warnings` are repo-level targets of their
  own.
- `just test-e2e` → `nx run <bin>-e2e:test`, the binary journeys in isolation
  (also run by `check`).
- `just msrv` → build under the declared MSRV.
- `just upgrade` → `cargo update`, then re-run `just check`.

## Verification

- [ ] **Drive the compiled binary.** E2E uses `assert_cmd` to spawn the compiled
  binary as a subprocess and asserts on exit code, stdout, and stderr — not
  in-process `run()` calls.
- [ ] **E2E a separate project, still gated.** The slow binary suite is its own
  `publish = false` `<bin>-e2e` crate and project, whose `test` target
  `dependsOn` the binary crate's `build` and which `just check` still runs
  (`just test-e2e` isolates it); e2e is not `#[ignore]`-d out of the default run.
- [ ] **Deterministic e2e isolated; live tier gated.** Deterministic e2e is
  offline and tempdir-isolated; a live tier needing real services/credentials is
  env-gated but still compiles, running in a dedicated workflow that requires its
  credential and fails fast without it (fork PRs gated by the require-approval
  repo setting).
- [ ] **Toolchain + supply chain.** Toolchain pinned in one workspace-root
  `rust-toolchain.toml`, MSRV declared in `[workspace.package]` and checked, and
  a Linux-only supply-chain job runs `cargo deny` + `cargo machete`.
- [ ] **Distribution contract.** Per-platform archives are built on native
  runners with `taiki-e/upload-rust-binary-action` (`checksum: sha256`), every
  install surface shares one asset-naming contract, and CI exercises each.
