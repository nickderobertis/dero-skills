# Language: TypeScript

Language-level conventions for any TypeScript repo. Combine with a product shape
(`shapes/web-app.md`, `shapes/cli.md`, ...) and `ci.md`. For Next.js apps,
`shapes/nextjs.md` builds on this plus `shapes/web-app.md`.

- **Strictness.** Enable `strict` mode. Treat type errors as build blockers, not
  warnings.
- **One toolchain.** Prefer a single tool for lint and format (e.g., Biome) to
  reduce drift and noise. Avoid stacking overlapping linters.
- **Boundary validation.** Parse and validate all external / IO input at the
  boundary with a runtime schema (e.g., Zod or equivalent); never trust raw
  `unknown` from the network, env, or storage.
- **Package management.** Use bun; commit the `bun.lock` lockfile. bun doubles
  as the runtime and test runner, so prefer it over a separate runner/loader
  (no tsx/ts-node) unless a dependency forces otherwise. Keep dependencies
  current with a scripted upgrade path. pnpm or npm are acceptable fallbacks
  when a constraint rules bun out — document the reason in `AGENTS.md`.
- **Coverage in the gate.** Run the test suite with coverage and fail below the
  threshold. Prefer bun's built-in runner (`bun test --coverage` with
  `coverageThreshold` in `bunfig.toml`); Vitest `coverage.thresholds` is a fine
  alternative if you need its ecosystem. 95% line coverage is the default bar;
  lower it only with a documented reason in `AGENTS.md`. Coverage is a default
  gate, not opt-in.
- **Command mapping.**
  - `just bootstrap` -> `bun install`
  - `just check` -> format check + lint + `tsc --noEmit` (typecheck — bun does
    not typecheck) + tests with coverage enforced (≥95%)
  - `just upgrade` -> `bun update` (refresh dependencies), then re-run
    `just check`
- **Output.** `just check` should be quiet on success and specific on failure.
