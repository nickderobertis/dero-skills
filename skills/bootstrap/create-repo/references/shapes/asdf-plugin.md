# Shape: asdf plugin

Principles for an asdf (or similar host-tool) plugin — a repo whose deliverable
is a set of shell scripts the host tool invokes to list, download, and install
versions of some managed tool. Almost always pair with `languages/bash.md` and
`ci.md`. [`asdf-allowlister`](https://github.com/nickderobertis/asdf-allowlister)
is a worked reference for the clean case (a tool that ships consistent,
checksummed release assets);
[`asdf-prusaslicer`](https://github.com/nickderobertis/asdf-prusaslicer) is the
worked reference for the messy upstream — inconsistent asset names, no published
checksums, uneven platform coverage, and runtime-library dependencies.

## Script contract

The plugin *is* its `bin/` scripts; treat their interface as the product. Follow
the current asdf plugin-script contract (check the official asdf docs rather than
relying on memory — script names and contracts drift).

- **Implement the required scripts:** `bin/list-all` (print installable
  versions) and `bin/install` (install into `ASDF_INSTALL_PATH`).
- **Implement the recommended scripts** unless there is a documented reason not
  to: `bin/download` (fetch/prepare the artifact, separate from install) and
  `bin/latest-stable` (resolve one latest stable version).
- **Add `bin/help.*`** (`overview`, `deps`, `config`, `links`) when they carry
  real user-facing information.
- **Add other scripts only for a concrete need** — `bin/list-bin-paths`,
  `bin/exec-env`, `bin/uninstall`, legacy-file hooks, lifecycle hooks. Don't
  ship empty stubs.
- **Plugin scripts must never call `asdf`.** They run *inside* asdf; shelling
  back out is circular. In particular, do not call `asdf reshim` from `install`.
- **Output is the contract.** Each script prints only what asdf expects and
  nothing else — `list-all` a space-separated list, `latest-stable` exactly one
  version. Stay quiet on success; on failure print the exact problem and a
  concrete next action. Make every script executable.

## Version, stable, and download policy

- **List from the authoritative upstream.** Pull versions from a structured API
  or release index, not brittle HTML scraping. Filter out yanked, malformed, and
  irrelevant tags. `list-all` prints newest *last* (per current asdf
  convention). **Do not use `sort -V`** — it is not portable across the BSD/macOS
  and GNU toolchains; sort versions with a portable comparator.
- **Prerelease policy is explicit and documented.** Exclude prereleases, RCs,
  nightlies, and dev builds from stable flows by default; `latest-stable` returns
  exactly one stable version and exits non-zero with a concise error when none
  matches.
- **GitHub-sourced versions** support `GITHUB_API_TOKEN` (falling back to
  `GITHUB_TOKEN`) to dodge rate limits, but never *require* a token for normal
  public use, never send it on binary downloads, and never commit a real token.
- **Download safely.** Use the asdf-provided env vars (`ASDF_INSTALL_TYPE`,
  `ASDF_INSTALL_VERSION`, `ASDF_INSTALL_PATH`, …). Pin exact version URLs over
  HTTPS — never a mutable `latest` URL for a specific install. Use robust `curl`
  flags (fail on HTTP error, follow redirects, quiet on success). Verify upstream
  checksums when published; a mismatch aborts. When upstream publishes **no**
  checksums, say so in `AGENTS.md` and lean on HTTPS plus atomic download (fetch
  to a `.part` sidecar and rename only on success) so a partial download never
  looks installed. Work in a safe temp dir and clean up on failure.
- **Match assets by pattern when names are inconsistent.** A well-behaved
  upstream lets you build the asset name from `<name>-<version>-<triple>`; a messy
  one varies names across releases (build timestamps, `macOS` vs `MacOS`, GTK
  variants, distro qualifiers). For those, select the asset with an **ordered list
  of regex patterns** per `(os, arch)` — most-specific first, falling back to
  looser matches — rather than string templating, and cover the selection logic
  with `bats` fixtures drawn from real release data.

## Install and platform support

- **Install only into `ASDF_INSTALL_PATH`** (plus temp files you clean up).
  Don't write outside it and don't mutate user shell profiles. Ensure the
  expected executable lands and is runnable, then verify with `<tool> --version`
  (or the closest equivalent).
- **Map platforms explicitly and test the map.** Support Linux and macOS, arm64
  and amd64, wherever upstream ships artifacts. Normalize `Darwin`/`Linux`,
  `x86_64`/`amd64`, `aarch64`/`arm64` with portable shell. Fail clearly on
  unsupported OS/arch combinations, and document anything unsupported (commonly
  Windows).
- **Keep dependencies small and explicit** (e.g. `bash`, `curl`, `tar`, a
  SHA-256 utility, `jq`). For source builds, document build deps in
  `bin/help.deps` and the README, keep build commands deterministic, and fail —
  never silently install system packages.
- **Turn missing runtime libraries into actionable advice.** When the installed
  binary needs system libraries the host may lack (GUI tools especially —
  WebKitGTK, OpenGL), don't let the smoke test fail with a raw loader error.
  Parse the missing-`.so` name, map it to the package that provides it on the
  common distros, and fail with the exact `apt`/`dnf`/`pacman` install command.
  Keep the mapping a pure function and test it offline. Document uneven platform
  coverage (an arch/version upstream simply doesn't ship) explicitly rather than
  failing opaquely.

## Testing, gate, and CI

- **Real host-tool integration, not only unit tests.** Drive the plugin through
  the host tool itself (`asdf plugin test`), simulating a user installing a
  version, in addition to fast behavior tests.
- **Test the parsing and mapping logic** with `bats`: version parsing,
  prerelease filtering, latest-stable selection, OS/arch mapping, download-URL
  construction, checksum behavior, install-path behavior, unsupported-platform
  errors, and concise failure output. Cover every shared `lib/` helper if `lib/`
  exists (add helpers only when they cut duplication without hiding behavior).
- **The natural project split.** asdf fixes the plugin's layout — `bin/` must sit
  at the repo root — so the `plugin` project's root *is* the repo root, holding
  the scripts, any shared `lib/`, and the offline `bats` tests as its `format`,
  `lint`, and `test` targets. The host-tool integration is slow and reaches the
  network, so `plugin-e2e` is a project of its own beside it (its own directory,
  its own `project.json`) whose `test` target runs `asdf plugin test` and which
  depends on `plugin` — editing a `bats` fixture then never installs a real tool
  version. See `languages/bash.md` for how a shell project is declared when there
  is no language manifest to sit beside.
- **Commands.** The root recipes delegate to the orchestrator, which runs the
  per-project targets.
  - `just check` -> `nx affected -t format lint test` plus the repo-level
    `coverage` target, so the `plugin` project's `shfmt` format check,
    `shellcheck`, `actionlint`, and `bats` unit tests and the `plugin-e2e`
    project's `asdf plugin test` all run — quiet on success.
  - `just plugin-test` / `just e2e` -> `nx run plugin-e2e:test`, the host-tool
    integration in isolation; it must also run inside the default gate, not only
    on demand.
- **CI.** Matrix across `ubuntu-latest` and `macos-latest`; run the gate and the
  current recommended `asdf-vm/actions/plugin-test`, validating the installed
  tool (`<tool> --version`). Support `GITHUB_API_TOKEN` for release-API calls but
  never require it for forks.

## Verification

- [ ] **Script contract implemented.** Required scripts (`bin/list-all`,
  `bin/install`) and recommended ones (`bin/download`, `bin/latest-stable`) follow
  the current asdf plugin-script contract; each is executable and prints only
  what asdf expects.
- [ ] **Never calls asdf.** Plugin scripts never shell back out to `asdf` (e.g.
  no `asdf reshim` from `install`).
- [ ] **Version/stable/download policy.** Versions come from an authoritative
  upstream with a portable version sort (no `sort -V`); prerelease policy is
  documented; downloads use pinned HTTPS URLs with robust `curl`, checksum verify
  when published or atomic `.part` download otherwise, and temp-dir cleanup.
- [ ] **Install + platform support.** Install writes only into
  `ASDF_INSTALL_PATH`, verifies `<tool> --version`, maps platforms explicitly
  (Linux/macOS, arm64/amd64) with a tested map, and fails clearly on unsupported
  combinations; missing runtime libraries become an actionable install command.
- [ ] **Real host-tool integration in CI.** `asdf plugin test` plus `bats` unit
  tests run in the gate, on an `ubuntu-latest` + `macos-latest` matrix.
- [ ] **Project split matches the cost.** The `plugin` project is rooted at the
  repo root (asdf fixes `bin/` there) with the scripts and offline `bats` tests;
  the host-tool integration is its own `plugin-e2e` project depending on it, so
  editing a fixture never installs a real tool version.
