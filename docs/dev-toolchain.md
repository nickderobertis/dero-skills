# Dev toolchain

`just check` runs through Nx, so this repo's gate needs three runtimes, not one:
`uv` (Python), `node` (the runtime Nx and semantic-release run on) and `bun` (the
package manager and script runner). Two further binaries are installed by `just
bootstrap` through `uv tool`, because gate targets shell out to them directly:

- **`llmlint`** — the deterministic, model-free `llmlint validate` that the
  `llmlint-tier` project's `validate` target runs. No harness token needed.
- **`shellcheck`** (from `shellcheck-py`) — the `lint` target of the two shell
  projects, `authoring-scripts` and `consumer-bootstrap`.

## Pins

`.tool-versions` is the source of truth for `just`/`uv`/`node`/`bun`. Provision
them with asdf (or a compatible manager such as mise) via `asdf install`, then
run `just bootstrap`. The `python` pin records the targeted version — uv supplies
Python per `requires-python`. `just check-versions` (the `authoring-tools:validate`
target) fails the gate if any pin disagrees with what the CI workflows install.

## Cloud and web sessions

A Claude Code web/cloud session has no asdf, and its image ships uv/node/bun but
not `just`. The `SessionStart` hook therefore runs `scripts/session-setup.sh`,
which installs `just` via `uv tool install rust-just` (PyPI-only, so it needs
nothing but uv) before the first `just ...` call, then runs `setup-llmlint`.
`just session-setup` is the manual entry point; it is idempotent and no-ops in CI.
