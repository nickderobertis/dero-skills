# Stack-specific add-ons

Apply the core principles in `SKILL.md` first, then layer on exactly one product
add-on (Python / TypeScript / Rust / Bash / skills repo) plus the CI add-on. The
add-ons specialize the principles for a stack; they never override the
non-negotiable invariants (strict gates, boundary validation, portability,
security).

## Python repo (package / service / CLI)

- Python: use 3.14 unless impossible; use `uv` for env and deps; keep config in
  `pyproject.toml`.
- Quality: `ruff` for lint and format; `ty` for type checking; `pytest` for
  tests.
- Boundary validation: prefer Pydantic for all external / IO boundaries.
- Clients: prefer official async and well-typed libraries; otherwise write a
  small async typed client.
- Commands: `just bootstrap` (uv sync), `just check` (ruff / ty / pytest),
  `just upgrade` (uv update).
- Warnings: no warnings-only; treat warnings as errors where feasible.
- CI: run `just check` on a clean checkout; include coverage only if it
  materially helps.

## TypeScript / Next.js app

- TS strict: enable strict mode; treat type errors as build blockers.
- Lint / format: prefer a single toolchain (e.g., Biome) to reduce drift and
  noise.
- URL state: keep state in the URL where reasonable; validate and parse with
  `nuqs` (or equivalent).
- UI: prefer shadcn/ui components where relevant; keep design consistent and
  minimal.
- Architecture: enforce boundaries (server / client separation, feature / module
  boundaries).
- Testing: unit / integration plus E2E covering critical user journeys (not just
  smoke).
- Commands: `just check` should run format / lint / typecheck / tests with
  minimal success output.
- CI: build plus E2E on PR; ensure the production build is validated.

## Rust CLI repo

- Toolchain: stable Rust; use `rustfmt` and `clippy` as strict gates; consider
  `cargo nextest`.
- Security: `cargo deny` (licenses / advisories) when distributing binaries.
- E2E: run the compiled CLI in tests (golden files, snapshot tests, exit codes,
  stderr / stdout).
- Cross-platform: build and test on Linux / macOS / Windows; produce release
  binaries.
- Releases: use a modern, reliable distribution workflow (e.g., cargo-dist) when
  appropriate.
- Commands: `just check` => fmt / clippy / tests / E2E; `just release` or a
  CI-driven release pipeline.

## Bash / asdf plugin-style repo

- Portability: avoid bashisms if targeting sh; otherwise explicitly require bash
  and test on macOS and Linux.
- Quality: `shellcheck` and `shfmt` enforced; fail on issues (no warnings
  backlog).
- Tests: `bats` (or similar) plus real-host-tool integration tests (e.g.,
  `asdf plugin test`).
- Behavior: test install / uninstall / rehash / version resolution; handle
  network failures gracefully.
- Commands: `just check` includes shellcheck / shfmt / tests; `just e2e` runs
  host-tool integration.
- CI: matrix across OS; validate the plugin works end-to-end via the host tool.

## Skills repo / multi-skill tooling repo

- Determinism: anything deterministic in a skill should be a script; instructions
  only where judgment is needed.
- Install model: project-based installs (no multiple profiles); keep
  dependencies updated regularly.
- Multi-tool compatibility: make setup work across Cursor / Claude Code /
  VS Code.
- JS tooling: pnpm where applicable; Python tooling: uv; keep boundaries clear.
- Hooks: use husky where JS exists; the hook should call `just check`; minimal
  success output.
- Docs: root and nested `AGENTS.md`; include `tests/AGENTS.md`; symlink
  `CLAUDE.md` -> `AGENTS.md`.
- Safety: narrow allowlist for agent actions; avoid deny-list reliance.

## GitHub Actions / CI patterns

Applies on top of whichever product add-on you chose.

- Always include: clean checkout -> bootstrap -> full quality gate.
- Prefer an OS matrix when the artifact is cross-platform.
- Cache intelligently, but never at the expense of correctness.
- Upload artifacts only after gates pass; include checksums for binaries when
  relevant.
- Keep logs minimal on success; emit detailed diagnostics only on failure.
