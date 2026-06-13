# Cross-cutting: Monorepo orchestration

Applies **only when the repo holds more than one deliverable** — multiple apps,
multiple publishable packages, or more than one implementation language. A
single-artifact repo does not need this, and adopting an orchestrator there is
the kind of template baggage the skill warns against. When it *does* apply,
layer this on top of the per-project shapes and languages: every app/package
inside the monorepo still picks its own shape + language references.

**Recommended orchestrator: Nx.** It provides the project graph, affected-only
execution, and computation caching the points below rely on. (Turborepo or Bazel
can fill the same role; the principles transfer.)

- **Each app/package is its own project.** Give every deliverable a project
  definition with locally-declared targets (`build`, `lint`, `test`,
  `typecheck`, ...). The orchestrator owns *running* targets across projects; it
  does not own *what* a target does — that stays with the project and calls its
  language-native tool (ruff in one project, biome in another).
- **Root commands delegate; they don't reimplement.** Keep the same memorable
  surface at the repo root (`just bootstrap/check/test/lint/format/upgrade`), but
  each recipe shells out to the orchestrator instead of hand-rolling a loop over
  packages. `just check` -> `nx affected -t lint test typecheck build` on a PR;
  `nx run-many -t ...` for a full sweep. No bespoke for-each-package bash.
- **Run only what changed.** Use affected-only execution (`nx affected`) keyed
  off the merge base, so a PR lints and tests just the projects its diff can
  reach — not the whole tree. This is the main reason a large repo stays fast.
- **Cache target outputs.** Enable computation caching so unchanged inputs replay
  a cached result instead of recomputing. Share a remote cache between CI and
  developers so a green target built once is never rebuilt elsewhere. Mirror
  `ci.md`: cache for speed, never for correctness — a cache must never hide a
  broken clean build.
- **Declare the task graph.** Express cross-project order with task-pipeline
  dependencies (`dependsOn` — e.g. a project's `build` depends on its
  dependencies' `build`) so the orchestrator parallelizes safely and correctly
  instead of you sequencing builds by hand.
- **Keep target names uniform.** The same target name means the same thing in
  every project (`test` always runs tests, `lint` always lints).
  `run-many`/`affected` fan out *by name*, so this consistency is what lets one
  root command cover the whole repo.
- **Enforce project boundaries.** Tag projects and enforce allowed dependencies
  (e.g. Nx's module-boundary lint rule) so the graph stays acyclic and the
  layering (app -> feature -> shared) holds. Boundaries are what keep a monorepo
  from collapsing into a big ball of mud.
- **Localize the instruction layer.** Add a nested `AGENTS.md` in each project
  for subtree-specific rules; the root `AGENTS.md` keeps only repo-wide
  constraints. Use `CODEOWNERS` so changes route to the right reviewers.
- **Polyglot, one graph.** Multiple languages live in a single project graph,
  each project running its own toolchain. Keep one lockfile per language
  ecosystem (not one per package) and let the orchestrator cache across them
  uniformly.

**CI.** PRs run `nx affected` against the merge base — derive the base/head SHAs
explicitly (e.g. `nx-set-shas`) so detection is deterministic — for fast, scoped
feedback. A full `nx run-many` on the main branch (and/or nightly) catches
anything affected-detection or a stale cache could miss. Bundled skill *scripts*
still stay orchestrator-independent (PEP 723 / Node built-ins): Nx orchestrates
targets, it is never a runtime dependency of the scripts themselves.

**Versioning & generated contracts.** Two monorepo-specific concerns layer on top
of `releasing.md`:

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
