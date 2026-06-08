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
- **Package management.** Use pnpm; commit the lockfile. Keep dependencies
  current with a scripted upgrade path.
- **Command mapping.**
  - `just bootstrap` -> `pnpm install`
  - `just check` -> format check + lint + `tsc --noEmit` (typecheck) + tests
  - `just upgrade` -> upgrade dependencies, then re-run `just check`
- **Output.** `just check` should be quiet on success and specific on failure.
