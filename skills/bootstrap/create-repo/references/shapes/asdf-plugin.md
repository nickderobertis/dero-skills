# Shape: asdf plugin

Principles for an asdf (or similar host-tool) plugin — a repo whose deliverable
is a set of shell scripts the host tool invokes to list, download, and install
versions of some managed tool. Almost always pair with `languages/bash.md` and
`ci.md`. [`asdf-allowlister`](https://github.com/nickderobertis/asdf-allowlister)
is a worked reference implementation of this shape.

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
  checksums when published; a mismatch aborts. Work in a safe temp dir, clean up
  on failure, and never leave a partial artifact that looks installed.

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

## Testing, gate, and CI

- **Real host-tool integration, not only unit tests.** Drive the plugin through
  the host tool itself (`asdf plugin test`), simulating a user installing a
  version, in addition to fast behavior tests.
- **Test the parsing and mapping logic** with `bats`: version parsing,
  prerelease filtering, latest-stable selection, OS/arch mapping, download-URL
  construction, checksum behavior, install-path behavior, unsupported-platform
  errors, and concise failure output. Cover every shared `lib/` helper if `lib/`
  exists (add helpers only when they cut duplication without hiding behavior).
- **Commands.**
  - `just check` -> `shfmt` format check + `shellcheck` + `actionlint` + `bats`
    unit tests + `asdf plugin test` — quiet on success.
  - `just plugin-test` / `just e2e` -> the host-tool integration in isolation;
    it must also run inside the default gate, not only on demand.
- **CI.** Matrix across `ubuntu-latest` and `macos-latest`; run the gate and the
  current recommended `asdf-vm/actions/plugin-test`, validating the installed
  tool (`<tool> --version`). Support `GITHUB_API_TOKEN` for release-API calls but
  never require it for forks.
