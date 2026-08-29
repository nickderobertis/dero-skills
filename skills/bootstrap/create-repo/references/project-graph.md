# Cross-cutting: Project graph & Nx orchestration

Applies to **every** repo this skill stands up. An Nx-orchestrated project graph
is mandatory: the repo is laid out as a set of projects so its targets — above
all its tests — run only when a change can actually reach them. This layers on
top of the per-project shapes and languages: every project inside still picks
its own shape + language references.

**Nx is the orchestrator, and there is no native-workspace substitute.** A pure
Rust repo and a pure Python repo carry the Node/bun toolchain for Nx the same as
a TypeScript one. The trade is deliberate: there is essentially always a test
split worth making, and one uniform `nx affected` surface — the same project
graph, the same affected detection, the same cache, in every repo — is worth the
toolchain that buys it. The decision is settled: compose it and move on rather
than re-weighing the tradeoff per repo.

## A project is a unit of the target/test graph

**A project is a unit of the target/test graph, not necessarily a publishable
package.** The mandate does not ask a repo to ship more artifacts than it has,
invent packages nobody consumes, or split its public API. A project is the
smallest thing you want to lint, test, or build on its own — and therefore the
smallest thing an unrelated change can *skip*.

So a repo with exactly one deliverable still has a project graph. Split it:

- **By test tier.** The fast unit suite lives with the code it covers; the
  integration and e2e suites are projects of their own (`<app>-integration`,
  `<app>-e2e`) that depend on the app. A change inside the app reruns the fast
  tier immediately and the slow tiers behind it; a change to an e2e test reruns
  only that tier.
- **By cost and externality.** A suite that is slow, or that touches an external
  service (a live API, a container, a browser, a real database), becomes its own
  project so unrelated changes stop paying for it. Cost is a reason to split on
  its own — it needs no packaging story behind it.
- **By what a change can reach.** Where the product already has an internal seam
  — a plug-in interface, a codegen contract, a client for one external system —
  make the seam a project, so the graph reflects it and affected detection can
  use it.

## Worked example: the plug-in interface

A repo whose one deliverable is a tool with a plug-in system. The interface's
conformance suite is the expensive part: it loads real plug-ins and talks to the
service they wrap. Extract the interface into its own project that **does not
depend on the core**:

| Project | Holds | `dependsOn` / graph edges |
| --- | --- | --- |
| `plugin-api` | the interface a plug-in implements plus its own conformance logic | — (depends on nothing in this repo) |
| `core` | the product logic | — |
| `app` | the deliverable | `app -> core`, `app -> plugin-api` |
| `plugin-api-e2e` | the expensive conformance suite | `plugin-api-e2e -> plugin-api` |

There is deliberately **no edge from `plugin-api` to `core`**, so `nx affected`
starting at a change in `core` reaches `app` and stops — the expensive suite does
not run. It runs when `plugin-api` or its own logic changes, which is exactly
when it can tell you something.

Tags hold that shape in place once it exists: tag `plugin-api` `type:contract`,
`core` and `app` `type:app`, and enforce with the module-boundary rule that
`type:contract` may depend only on other `type:contract` projects. Without the
tag, one convenient `core` import inside `plugin-api` silently re-attaches the
expensive suite to every change in the repo, and nothing fails to say so.

The general rule this instances: **put the expensive thing behind a graph edge
that unrelated changes cannot reach**, and make a boundary tag the thing that
keeps the edge from being drawn back.

## Nx and the language's own workspace

Both layers are present and they own different things. **Nx owns running
targets** across projects — the project graph, affected detection, caching, and
target ordering. **The language's own workspace primitive owns dependency
resolution** — a uv workspace, a Cargo workspace, bun/pnpm workspaces — and
keeps **one lockfile per ecosystem**, not one per project. Declare a project to
both: to Nx so its targets can be run and skipped, to the language workspace so
its dependencies resolve against the same lock. The per-language mechanics
belong to the language reference.

## Running the graph

- **Each project declares its own targets.** Give every project a definition
  with locally-declared targets (`build`, `lint`, `test`, `typecheck`, ...). Nx
  owns *running* targets across projects; it does not own *what* a target does —
  that stays with the project and calls its language-native tool (ruff in one
  project, biome in another).
- **Root commands delegate; they don't reimplement.** Keep the same memorable
  surface at the repo root (`just bootstrap/check/test/lint/format/upgrade`), but
  each recipe shells out to Nx instead of hand-rolling a loop over projects.
  `just check` -> `nx affected -t lint test typecheck build` on a PR;
  `nx run-many -t ...` for a full sweep. No bespoke for-each-package bash.
- **Run only what changed.** Use affected-only execution (`nx affected`) keyed
  off the merge base, so a PR lints and tests just the projects its diff can
  reach — not the whole tree. This is what turns the splits above into saved
  wall-clock.
- **Cache target outputs.** Enable computation caching so unchanged inputs replay
  a cached result instead of recomputing. Share a remote cache between CI and
  developers so a green target built once is never rebuilt elsewhere. Mirror
  `ci.md`: cache for speed, never for correctness — a cache must never hide a
  broken clean build.
- **Declare the task graph.** Express cross-project order with task-pipeline
  dependencies (`dependsOn` — e.g. a project's `build` depends on its
  dependencies' `build`) so Nx parallelizes safely and correctly instead of you
  sequencing builds by hand.
- **Keep target names uniform.** The same target name means the same thing in
  every project (`test` always runs tests, `lint` always lints).
  `run-many`/`affected` fan out *by name*, so this consistency is what lets one
  root command cover the whole repo.
- **Enforce project boundaries.** Tag projects and enforce allowed dependencies
  (e.g. Nx's module-boundary lint rule) so the graph stays acyclic and the
  layering (app -> feature -> shared) holds. Boundaries are what keep a repo from
  collapsing into a big ball of mud — and, per the worked example, what keep an
  expensive suite out of reach of changes that have nothing to do with it.
- **Localize the instruction layer.** Add a nested `AGENTS.md` in each project
  for subtree-specific rules; the root `AGENTS.md` keeps only repo-wide
  constraints. Use `CODEOWNERS` so changes route to the right reviewers.
- **Polyglot, one graph.** Multiple languages live in a single project graph,
  each project running its own toolchain, each ecosystem keeping its one
  lockfile, and Nx caching across them uniformly.

**CI.** PRs run the repo's gate recipe, which delegates to `nx affected` against
the merge base — derive the base/head SHAs explicitly (e.g. `nx-set-shas`) so
detection is deterministic — for fast, scoped feedback. The same recipe run as a
full sweep on the main branch (and/or nightly) catches anything affected
detection or a stale cache could miss. CI calls the command surface, never the
orchestrator directly, so local and CI runs cannot drift. Bundled skill *scripts*
still stay orchestrator-independent (PEP 723 / Node built-ins): Nx orchestrates
targets, it is never a runtime dependency of the scripts themselves.

**Versioning & generated contracts.** Two graph-level concerns layer on top of
`releasing.md`:

- **Lockstep versioning through one script.** When a single logical version spans
  many manifests (a Cargo workspace + `pyproject.toml` + `package.json` +
  per-platform carrier packages + every lockfile), never hand-edit one of N. Make
  a single `scripts/set-version.sh` the source of truth — it writes every
  manifest, lockfile, and cross-package pin — and have the release tool call it
  (`semantic-release`'s `exec`, or `release-plz`). Publish each registry in
  dependency order and keep it **idempotent** (skip a version already live).
- **Cross-language contracts are generated, never hand-written, and drift-checked.**
  When one language's types are the source of truth for a contract other packages
  consume (e.g. `schemars` Rust types → JSON Schema → Python/TS model codegen),
  generate the downstream models and add a `--check` mode to the generator that
  fails the gate if regenerating would change a committed file. This is the
  concrete form of `ci.md`'s "validate generated files," run at the workspace
  level inside `just check`.

## Verification

- [ ] **The repo has an Nx project graph.** `nx.json` plus project definitions
  are present and the root command surface runs targets through them — including
  in a repo with a single deliverable, where the graph exists for the target/test
  split rather than for packaging.
- [ ] **Split by test tier and by cost.** The fast unit tier, the integration
  tier, and the e2e tier are separate projects, and every slow or
  external-service-touching suite is a project of its own rather than riding
  along in the project it tests.
- [ ] **Expensive work sits behind an unreachable edge.** Each expensive project
  depends only on what it actually tests (no edge back to the core), so `nx
  affected` from an unrelated change does not reach it, and a boundary tag
  enforces that the edge cannot be drawn back.
- [ ] **Each deliverable is its own project.** Every app/package has a project
  definition with locally-declared targets (`build`, `lint`, `test`,
  `typecheck`, ...) calling its own language-native tool.
- [ ] **Nx and the language workspace both wired.** Nx runs targets; the
  language's own workspace primitive (uv/Cargo/bun/pnpm) resolves dependencies
  and keeps one lockfile per ecosystem, not one per project.
- [ ] **Root commands delegate.** The `just bootstrap/check/test/lint/format/
  upgrade` recipes shell out to the orchestrator (`nx affected` / `nx run-many`),
  not a bespoke for-each-package loop.
- [ ] **Affected-only in CI.** PRs run `nx affected` keyed off an explicitly
  derived merge base; a full `run-many` runs on the main branch and/or nightly.
- [ ] **Caching never hides a broken clean build.** Computation caching is on for
  speed, but a cache never masks a broken clean build (mirrors `ci.md`).
- [ ] **Project boundaries enforced.** Projects are tagged and allowed
  dependencies enforced (e.g. the module-boundary lint rule); target names are
  uniform across projects.
- [ ] **Instruction layer localized.** Each project has a nested `AGENTS.md` for
  subtree rules, with `CODEOWNERS` routing reviews; the root `AGENTS.md` keeps
  only repo-wide constraints.
- [ ] **Scripts stay orchestrator-independent.** Bundled skill scripts remain
  self-contained (PEP 723 / Node built-ins) — Nx orchestrates targets, it is
  never a runtime dependency of the scripts.
