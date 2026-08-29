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
- **Package management.** Use bun; commit one root `bun.lock` covering every
  project in the workspace (see below). bun doubles
  as the runtime and test runner, so prefer it over a separate runner/loader
  (no tsx/ts-node) unless a dependency forces otherwise. Keep dependencies
  current with a scripted upgrade path. pnpm or npm are acceptable fallbacks
  when a constraint rules bun out — document the reason in `AGENTS.md`.
- **Coverage in the gate.** Run the test suite with coverage and fail below the
  threshold. Prefer bun's built-in runner (`bun test --coverage` with
  `coverageThreshold` in the project's `bunfig.toml`); Vitest
  `coverage.thresholds` is a fine alternative if you need its ecosystem. With
  the suite split across projects, see "Splitting the suite" below for where the
  threshold lives. 95% line coverage is the default bar;
  lower it only with a documented reason in `AGENTS.md`. Coverage is a default
  gate, not opt-in.
- **Command mapping.** The root recipes delegate to the orchestrator, which runs
  the per-project targets named below.
  - `just bootstrap` -> `bun install` at the workspace root (one resolve
    covering every project)
  - `just check` -> `nx affected -t format lint typecheck test build` plus the
    repo-level `coverage` target, with the per-project targets running the
    format check, lint, `tsc --noEmit` (bun does not typecheck), and tests with
    coverage enforced (≥95%)
  - `just upgrade` -> `bun update` (refresh dependencies), then re-run
    `just check`
- **Output.** `just check` should be quiet on success and specific on failure.

## Projects, the workspace, and the graph

Nx runs the targets; **the package manager resolves the dependencies**. Both
layers describe the same projects and meet at the project directory.

- **One `package.json` per project, one `bun.lock` for the repo.** The root
  `package.json` declares `"workspaces": ["packages/*", "apps/*", ...]` (bun's
  and pnpm's workspace primitive; pnpm uses `pnpm-workspace.yaml`), and each
  project has its own `package.json`. `bun install` at the root hoists and
  resolves every member into a single root lockfile — never a lockfile per
  project, and never a second package manager's lockfile beside it.
- **The project definition sits beside the manifest.** `project.json` lives in
  the same directory as that project's `package.json` (or the package's `nx`
  block carries the targets). A cross-project import is a real workspace
  dependency — depend on the sibling by its package name with the
  `workspace:*` protocol, not by a deep relative path — which is what makes the
  Nx edge true rather than merely declared.
- **`tsconfig` mirrors the graph.** Each project has its own `tsconfig.json`
  extending a shared base, and cross-project edges are TypeScript project
  references, so `typecheck` is per project and incremental rather than one
  whole-repo `tsc`.
- **Test-only projects are workspace members too.** A `<app>-e2e` project is a
  `package.json` with nothing to publish that depends on the app it drives, so
  its Playwright/bun dependencies resolve against the same lock.

### Splitting the suite

- **The runner is invoked per project, rooted at that project.** A project's
  `test` target runs `bun test` with the target's `cwd` set to the project, so
  it collects only that project's tests and reads that project's `bunfig.toml`
  (Vitest: `vitest run` against the project's own config). One repo-wide `bun
  test` over a single top-level `tests/` directory is the thing the split
  replaces.
- **Fast tier with the code, browser and integration tiers in their own
  projects.** Unit and component tests live in the package/app project and are
  its `test` target. The browser suite is the expensive one: put Playwright in
  its own `<app>-e2e` project whose `test` target is that suite and which
  depends on the app it drives (its `test` `dependsOn` the app's `build`, so it
  runs against the production bundle). Same for any suite that needs a container
  or a live service.
- **Coverage survives the split by keeping a threshold on every project.** bun
  enforces `coverageThreshold` from the `bunfig.toml` it reads, which is now the
  project's — so every project that has tests sets it (95% default), and a
  project shipping code with no threshold configured is a hole, not a pass.
  Where a package's coverage genuinely comes partly from a sibling project (the
  `<app>-e2e` suite covering app code), emit LCOV instead
  (`coverageReporter = ["lcov"]`, or Vitest `coverage.reporter`) into each
  project's declared target outputs and add a repo-level `coverage` target that
  `dependsOn` every `test`, merges the LCOV files, and fails below 95% over the
  union. Enforce in one place or the other — never in neither.

### Target names

Each TypeScript project declares the repo-uniform target names, each calling the
JS toolchain directly so `nx run-many -t lint` reaches it alongside a Python or
Rust project: `format` -> the formatter's check mode (e.g. `biome format`),
`lint` -> `biome check` (or ESLint), `typecheck` -> `tsc --noEmit -p .` (bun
does not typecheck), `test` -> the runner as above, and `build` -> the project's
bundler/`tsc` build on the projects that produce an artifact. The repo-level
`coverage` target is the one aggregate and carries the same name in every
language.

## Verification

- [ ] **Strictness.** `strict` mode is on and type errors are build blockers, not
  warnings.
- [ ] **One toolchain.** A single tool handles lint and format (e.g. Biome); no
  stacked overlapping linters.
- [ ] **Boundary validation.** All external / IO input is parsed and validated at
  the boundary with a runtime schema (e.g. Zod); raw `unknown` from the network,
  env, or storage is never trusted.
- [ ] **Package management.** bun is used with one root `bun.lock` committed for
  the whole workspace (a documented pnpm/npm fallback only when a constraint
  rules bun out).
- [ ] **Coverage enforced.** The suite runs with coverage and fails below the
  threshold (95% default; `bun test --coverage` with `coverageThreshold`, or
  Vitest `coverage.thresholds`) — per project, or over merged per-project LCOV.
- [ ] **Command mapping wired.** `just bootstrap` → `bun install`; `just check` →
  `nx affected -t format lint typecheck test build` plus the `coverage` target,
  whose per-project targets run the format check + lint + `tsc --noEmit` + tests
  with coverage; `just upgrade` → `bun update` then re-run `just check`.
- [ ] **Workspace under the graph.** The root `package.json` declares
  `workspaces` (or `pnpm-workspace.yaml`), each project has its own
  `package.json` with a `project.json` beside it and its own `tsconfig.json`
  wired by project references, cross-project deps use `workspace:*` rather than
  deep relative paths, and the repo has exactly one lockfile.
- [ ] **Suite split into projects.** The runner is invoked per project rooted at
  that project; unit/component tests are the app or package project's `test`
  target and the browser suite is its own `<app>-e2e` project depending on the
  app (its `test` `dependsOn` the app's `build`).
- [ ] **Coverage still gating after the split.** Every project with tests
  enforces the 95% threshold in its own config (`coverageThreshold` /
  `coverage.thresholds`), or per-project LCOV is merged by a repo-level
  `coverage` target that fails below 95% — never neither.
- [ ] **Uniform target names.** Each TypeScript project declares `format` /
  `lint` / `typecheck` / `test` (plus `build` where it produces an artifact) so
  `nx affected` and `run-many` reach them by name in a polyglot repo.
