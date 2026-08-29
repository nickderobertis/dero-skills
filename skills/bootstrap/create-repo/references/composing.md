# Composing references

These references mix and match. There is no single "Python template" or
"Next.js template"; instead you assemble guidance from three axes and combine
them for the repo in front of you.

## How to compose

1. **Pick one product shape** — what the repo *is*: `shapes/cli.md`,
   `shapes/web-app.md`, `shapes/react.md`, `shapes/nextjs.md`,
   `shapes/library.md`, `shapes/skills-repo.md`, or `shapes/asdf-plugin.md`.
   Shapes are language-agnostic wherever possible — `cli.md` is about building a
   good CLI regardless of language. Some shapes build on others: `react.md`
   layers on `web-app.md`, and `nextjs.md` layers on `react.md` — pick the
   most specific one and the composer pulls in the shapes beneath it.
2. **Pull in the language(s)** the repo is built in: `languages/python.md`,
   `languages/typescript.md`, `languages/rust.md`, `languages/bash.md`,
   `languages/terraform.md`. The
   language reference carries the toolchain (formatter, linter, type checker,
   test runner) and boundary-validation conventions.
3. **Always pull in `ci.md`** — it applies on top of every shape. When the repo
   ships a *versioned artifact* (binary, package, plugin, image), also pull in
   `releasing.md` — the Conventional-Commits → automated-release pipeline that
   pairs with `ci.md`'s squash-merge governance.
4. **Always pull in `monorepo.md`** — the Nx project graph, which every repo
   this skill stands up has. It layers on top of the shapes and languages: each
   project inside picks its own. A one-deliverable repo composes it too, because
   a project is a unit of the target/test graph rather than a publishable
   package — the graph is where its test tiers and its expensive suites split
   apart.
5. **Prefer an intersection reference when one exists.** Where a shape and a
   language meet often enough to need their own guidance, that lives in
   `intersections/` (for example `intersections/python-cli.md`). If you hit an
   intersection that has no reference yet and you need guidance there, **create
   one** rather than stretching a single-axis reference.

## Worked examples

Each example names its references and then its project graph — the units the
repo's targets run over.

- **A TypeScript web app.** Start from `shapes/web-app.md` +
  `languages/typescript.md` + `ci.md` + `monorepo.md`. If it is a React app, pick
  `shapes/react.md` instead — the composer pulls in `web-app.md` beneath it and
  assumes TypeScript. If you then choose Next.js as the framework, pick
  `shapes/nextjs.md` (it builds on `react.md`, which builds on `web-app.md`, all
  assuming TypeScript). Graph: `web` (the app, fast unit tests alongside it),
  `web-e2e` (the browser suite, depends on `web`), and `ui` for shared
  components once a second consumer exists. The browser suite is the expensive
  one, so it is its own project and a change to a server route that no page
  renders never starts a browser.
- **A Python CLI.** `shapes/cli.md` gives the language-agnostic CLI principles;
  `languages/python.md` gives the toolchain. Their overlap — packaging a console
  entry point, e2e-testing the installed command — is concrete enough to have
  its own reference: `intersections/python-cli.md`. Use those three plus `ci.md`
  and `monorepo.md`. Graph: `cli` (the package and its fast unit tests) and
  `cli-e2e` (the suite that installs the console script and drives it as a
  subprocess, depending on `cli`). Add a project per external system the CLI
  talks to — an API client with its own live-service suite — so a change to
  argument parsing does not go out to the network.
- **A Rust CLI.** `shapes/cli.md` + `languages/rust.md` +
  `intersections/rust-cli.md` + `ci.md` + `monorepo.md` (+ `releasing.md` when
  you cut versions). The intersection captures the overlap: driving the compiled
  binary in e2e, MSRV + toolchain pinning, the supply-chain gate job, and
  per-platform release archives on one asset-naming contract. Graph: `cli` (the
  crate, `cargo test` unit tier) and `cli-e2e` (the suite that drives the built
  binary); the Cargo workspace resolves the crates against one `Cargo.lock`
  while Nx runs the targets, and the repo carries the Node/bun toolchain for Nx
  even though nothing in it is written in TypeScript.
- **An asdf plugin.** `shapes/asdf-plugin.md` + `languages/bash.md` + `ci.md` +
  `monorepo.md`. Graph: `plugin` (the `bin/` scripts, shellcheck + `bats` unit
  tier) and `plugin-e2e` (the suite that installs real tool versions through the
  plugin — slow and network-touching, so it is a project of its own and unrelated
  script edits skip it).
- **A skills repo.** `shapes/skills-repo.md` + the language(s) its scripts are
  written in + `ci.md` + `monorepo.md`. Graph: one project per skill, each with
  the same `validate` / `smoke` / `test` target names so `nx affected -t test`
  fans out by name, plus the shared validation tooling as its own project the
  skills depend on. Editing one skill runs that skill's targets, not the whole
  catalog.
- **An app with infrastructure.** Compose the app's shape and implementation
  language, then add `languages/terraform.md` for its `infra/` subtree; the same
  language reference also works with a standalone infrastructure repo shape.
  Graph: the app's projects as above plus `infra` (its own project, `fmt` /
  `validate` / `plan` targets), so an app-only change never runs a plan and a
  Terraform-only change never rebuilds the app.
- **A multi-app / polyglot monorepo.** Compose each deliverable from its own
  shape + language as usual (e.g. a Next.js app via `shapes/nextjs.md` +
  `languages/typescript.md`, alongside a Python service via `languages/python.md`),
  with `monorepo.md` + `ci.md` once on top. Graph: a project per deliverable plus
  the same tier and cost splits inside each, all in one graph; each ecosystem
  keeps one lockfile, the root command surface delegates to Nx, and CI runs only
  affected projects.

## Catalog

| Axis | References |
| --- | --- |
| Product shape | `shapes/cli.md`, `shapes/web-app.md`, `shapes/react.md`, `shapes/nextjs.md`, `shapes/library.md`, `shapes/skills-repo.md`, `shapes/asdf-plugin.md` |
| Language | `languages/python.md`, `languages/typescript.md`, `languages/rust.md`, `languages/bash.md`, `languages/terraform.md` |
| Always applied | `base.md`, `ci.md`, `monorepo.md` |
| Cross-cutting (flagged) | `releasing.md` (when shipping a versioned artifact) |
| Intersection | `intersections/python-cli.md`, `intersections/rust-cli.md` |

The core principles in `SKILL.md` always apply; references specialize them and
never override the non-negotiable invariants (strict gates, boundary validation,
portability, security).
