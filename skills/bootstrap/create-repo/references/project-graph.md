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
- **By what a change can reach.** Where the product already has a seam — a
  plug-in interface, a codegen contract, a client for one external system, the
  persistence or storage schema and data model something else reads and writes,
  the boundary deciding what one internal package, module, or library owns and
  what another may assume of it — make the seam a project, so the graph reflects
  it and affected detection can use it. Every one of those is a **contract**: an
  agreement two parties must both hold to where one can change independently.
  That criterion, not this list, is what makes a seam a candidate — a store, a
  serialization, or an internal boundary it names is treated no differently from
  one it does not.

## Boundaries follow domains, not kinds of code

Tier and cost say *why* one product becomes several units. When it is split
further for modularity, the boundaries between those units follow **domains,
never kinds of code**: prefer domain-driven boundaries that would actually
isolate related changes; minimize central packages that result in many
dependent changes; avoid unnecessary grouping that leads to dependencies of
unrelated code — types and data-access packages are domain-specific, not
centralized.

The test of a boundary is that a change to one concern stays inside one unit.
So a domain's types, its store interface, and its logic belong together, in a
unit named for the concern it serves (`provider-a`, `supervision`), not spread
across a `types`, a `store-api`, and a `core`.

The shape to refuse is a package named for a *kind* of code — `types`,
`models`, `store-api`, `data-access`, `utils`, `common` — that several domains
depend on, unless it is a genuine cross-domain contract. The `type:contract`
criterion decides that: a shared identity, a wire or storage format, or an
interface every domain must agree on is a contract; a bag of every domain's
structs is not, and a domain's own records living in a central package do not
become a contract by being served or persisted, since the contract is the
format, not the package that happens to declare it. The **contract test** is
how the criterion is applied — ask of a candidate central package whether one
domain adding a concept would have to edit it:

> A shared package is a contract only if adding or changing one domain's
> concept does not require editing it. A contract holds what every domain must
> agree on and never enumerates the domains: a closed sum type, registry, or
> table in a central package with an entry per domain's things is a bag with a
> type signature, however it is persisted or served.

Worked example: a supervisor with two printer providers. The layered shape:

| Project | Holds | `dependsOn` / graph edges |
| --- | --- | --- |
| `types` | every domain's structs: both providers' wire types, the supervision records, and one event enum with a variant per domain's events | — |
| `store-api` | the store interface over all of them | `store-api -> types` |
| `core` | the supervision logic | `core -> store-api`, `core -> types` |
| `provider-a`, `provider-b` | each provider's port implementation | `provider-a -> core`, `provider-b -> core` |
| `app` | the composition root | `app -> provider-a`, `app -> provider-b` |

A change to provider A's wire format, or to the events it writes, edits `types`
and rebuilds everything: `nx affected` from that one change reaches
`store-api`, `core`, both providers, and `app`. The domain shape:

| Project | Holds | `dependsOn` / graph edges |
| --- | --- | --- |
| `contract` | only what every domain agrees on: identities, timestamps, and the event **envelope** every domain writes under (a kind name and an opaque payload) — never an enum with a variant per domain's events | — |
| `provider-a` | provider A's wire types, its event kinds, and its port implementation | `provider-a -> contract` |
| `provider-b` | the same for provider B | `provider-b -> contract` |
| `core` | the supervision domain's records, its event kinds, and the store interfaces *it* needs | `core -> contract` |
| `app` | the composition root | `app -> core`, `app -> provider-a`, `app -> provider-b` |

(The port a provider implements is a contract project on the plug-in example's
terms, elided here.) The same change edits `provider-a` and rebuilds it and
`app`, and adding provider C edits nothing in `contract`. That is how the graph
benefits: `nx affected` from a change in one domain reaches that domain, the
contracts it changed, and the roots — not every unit.

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

`type:contract` is not reserved for a plug-in interface. A project that owns the
**persistence or storage schema** other projects read and write — the migrations,
the table and column names, the document or key shape, the on-disk or wire
format — or one that owns the **interface between internal packages, modules, or
libraries**, earns the same project and the same tag on the same terms: it is an
agreement its consumers hold to, and it passes the contract test above — a schema
or seam that one domain adding a concept has to edit is not on its own schedule,
and belongs to that domain. When both hold, give it a project, tag it
`type:contract`, and let the module-boundary rule keep it from importing the
consumers that depend on it. Judged by that criterion, a seam none of these
examples names qualifies too. Land such a schema and its data model
before what consumes them, and change either only the way the same section
requires of a generated contract: non-breaking, with the drift check below
keeping every restatement of a column, key, or field name aligned.

## Nx and the language's own workspace

Both layers are present and they own different things. **Nx owns running
targets** across projects — the project graph, affected detection, caching, and
target ordering. **The language's own workspace primitive owns dependency
resolution** — a uv workspace, a Cargo workspace, bun/pnpm workspaces — and
keeps **one lockfile per ecosystem**, not one per project. Declare a project to
both: to Nx so its targets can be run and skipped, to the language workspace so
its dependencies resolve against the same lock.

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
  developers so a green target built once is never rebuilt elsewhere. Cache for
  speed, never for correctness — a cache must never hide a broken clean build.
- **Declare the task graph.** Express cross-project order with task-pipeline
  dependencies (`dependsOn` — e.g. a project's `build` depends on its
  dependencies' `build`) so Nx parallelizes safely and correctly instead of you
  sequencing builds by hand.
- **Keep target names uniform.** The same target name means the same thing in
  every project (`test` always runs tests, `lint` always lints).
  `run-many`/`affected` fan out *by name*, so this consistency is what lets one
  root command cover the whole repo.
- **Enforce project boundaries.** Tag projects and enforce allowed dependencies
  (e.g. Nx's module-boundary lint rule) so the graph stays acyclic and
  dependencies run one way: domains depend on contracts, never on each other's
  internals, and the roots depend on the domains. Boundaries are what keep a
  repo from collapsing into a big ball of mud — and, per the worked example,
  what keep an expensive suite out of reach of changes that have nothing to do
  with it.
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

**Versioning & cross-project contracts.** Two graph-level concerns layer on top of
`releasing.md`:

- **Lockstep versioning through one script.** When a single logical version spans
  many manifests (a Cargo workspace + `pyproject.toml` + `package.json` +
  per-platform carrier packages + every lockfile), never hand-edit one of N. Make
  a single `scripts/set-version.sh` the source of truth — it writes every
  manifest, lockfile, and cross-package pin — and have the release tool call it
  (`semantic-release`'s `exec`, or `release-plz`). Publish each registry in
  dependency order and keep it **idempotent** (skip a version already live).
- **Cross-project contracts are generated, never hand-written, and drift-checked.**
  When one language's types are the source of truth for a contract other packages
  consume (e.g. `schemars` Rust types → JSON Schema → Python/TS model codegen),
  generate the downstream models and add a `--check` mode to the generator that
  fails the gate if regenerating would change a committed file. This is the
  concrete form of `ci.md`'s "validate generated files," run at the workspace
  level inside `just check`. The rule is the contract, not the codegen: a
  **storage schema** restated across projects (a migration's column names and the
  model, query, or fixture repeating them) and an **internal package interface**
  restated by its consumers get the same treatment — one authoritative source the
  others reference, generated copies derived from it, or a reconciling check in
  the gate that fails on drift. Whichever of the three a seam uses, nothing may be
  restated across it with only convention holding the copies together.

## Verification

- [ ] **The repo has an Nx project graph.** `nx.json` plus project definitions
  are present and the root command surface runs targets through them — including
  in a repo with a single deliverable, where the graph exists for the target/test
  split rather than for packaging.
- [ ] **Split by test tier and by cost.** The fast unit tier lives inside the
  project whose code it covers; the integration and e2e tiers are projects of
  their own depending on what they test, and every slow or
  external-service-touching suite is a project of its own rather than riding
  along inside a broadly-depended-on project.
- [ ] **Expensive work sits behind an unreachable edge.** Each expensive project
  depends only on what it actually tests (no edge back to the core), so `nx
  affected` from an unrelated change does not reach it, and a boundary tag
  enforces that the edge cannot be drawn back.
- [ ] **Contract seams are projects, and their restatements are gated.** Every
  seam the repo has that is a contract — a plug-in interface, a codegen contract,
  the persistence or storage schema and data model other projects read and write,
  the boundary deciding what an internal package, module, or library owns, or any
  other seam meeting the criterion — is a project of its own, tagged
  `type:contract` with the module-boundary rule keeping it from depending on its
  consumers; and every fact restated across it has one authoritative source, a
  generated copy, or a gate check that fails on drift.
- [ ] **Boundaries follow domains.** Each project holds one concern's types,
  interfaces, and logic together; the only projects several others depend on are
  genuine cross-domain contracts, none of which a domain adding a concept has to
  edit; and no project grouped by kind of code (`types`, `models`, `store-api`,
  `data-access`, `utils`, `common`) has several domains depending on it.
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
