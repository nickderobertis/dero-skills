# Composing references

These references mix and match. There is no single "Python template" or
"Next.js template"; instead you assemble guidance from three axes and combine
them for the repo in front of you.

## How to compose

1. **Pick one product shape** — what the repo *is*: `shapes/cli.md`,
   `shapes/web-app.md`, `shapes/nextjs.md`, `shapes/library.md`,
   `shapes/skills-repo.md`, or `shapes/asdf-plugin.md`. Shapes are
   language-agnostic wherever possible — `cli.md` is about building a good CLI
   regardless of language.
2. **Pull in the language(s)** the repo is built in: `languages/python.md`,
   `languages/typescript.md`, `languages/rust.md`, `languages/bash.md`. The
   language reference carries the toolchain (formatter, linter, type checker,
   test runner) and boundary-validation conventions.
3. **Always pull in `ci.md`** — it applies on top of every shape.
4. **Pull in `monorepo.md` when the repo holds more than one deliverable** —
   multiple apps, multiple publishable packages, or more than one language. It
   is cross-cutting and *conditional*: it layers on top of the per-project
   shapes and languages (each project inside still picks its own). Skip it for a
   single-artifact repo.
5. **Prefer an intersection reference when one exists.** Where a shape and a
   language meet often enough to need their own guidance, that lives in
   `intersections/` (for example `intersections/python-cli.md`). If you hit an
   intersection that has no reference yet and you need guidance there, **create
   one** rather than stretching a single-axis reference.

## Worked examples

- **A TypeScript web app.** Start from `shapes/web-app.md` +
  `languages/typescript.md` + `ci.md`. If you then choose Next.js as the
  framework, also pull in `shapes/nextjs.md` (it builds on `web-app.md` and
  assumes TypeScript).
- **A Python CLI.** `shapes/cli.md` gives the language-agnostic CLI principles;
  `languages/python.md` gives the toolchain. Their overlap — packaging a console
  entry point, e2e-testing the installed command — is concrete enough to have
  its own reference: `intersections/python-cli.md`. Use all three.
- **A Rust CLI.** `shapes/cli.md` + `languages/rust.md` + `ci.md`. No
  intersection reference exists yet; if the overlap (snapshot-testing a compiled
  binary, cross-platform release artifacts) grows, add `intersections/rust-cli.md`.
- **An asdf plugin.** `shapes/asdf-plugin.md` + `languages/bash.md` + `ci.md`.
- **A multi-app / polyglot monorepo.** Compose each deliverable from its own
  shape + language as usual (e.g. a Next.js app via `shapes/nextjs.md` +
  `languages/typescript.md`, alongside a Python service via `languages/python.md`),
  then add `monorepo.md` + `ci.md` once on top. The root command surface
  delegates to the orchestrator (Nx) and CI runs only affected projects.

## Catalog

| Axis | References |
| --- | --- |
| Product shape | `shapes/cli.md`, `shapes/web-app.md`, `shapes/nextjs.md`, `shapes/library.md`, `shapes/skills-repo.md`, `shapes/asdf-plugin.md` |
| Language | `languages/python.md`, `languages/typescript.md`, `languages/rust.md`, `languages/bash.md` |
| Cross-cutting | `ci.md` (always), `monorepo.md` (when >1 app/package/language) |
| Intersection | `intersections/python-cli.md` |

The core principles in `SKILL.md` always apply; references specialize them and
never override the non-negotiable invariants (strict gates, boundary validation,
portability, security).
