# Shape: Web app

Language-agnostic principles for a web application. Pair with the implementation
language (usually `languages/typescript.md`) and `ci.md`. If it is a React app,
pick the more specific `shapes/react.md` (or `shapes/nextjs.md` for Next.js) —
each builds on this shape and the composer pulls this one in beneath it.

- **State in the URL.** Keep shareable, navigable state in the URL where
  reasonable; parse and validate it rather than trusting it raw.
- **Enforce boundaries.** Keep a clear server/client split and feature/module
  boundaries. Validate all external input (requests, params, third-party
  responses) at the trust boundary.
- **Authorize on the server.** Enforce authentication and authorization on every
  privileged action and data-access path server-side; hiding a control in the UI
  is not a guard. Keep secrets and privileged data access server-only (see
  `shapes/nextjs.md` for the Next.js specifics).
- **UI consistency.** Prefer a consistent, minimal component system (e.g.,
  shadcn/ui) over bespoke one-off UI. Keep design tokens and patterns shared.
- **Testing — part of the gate, not opt-in.** Unit/integration tests plus
  **E2E covering critical user journeys** — sign-in, the core happy path, and at
  least one key failure/recovery path — not just smoke tests. Keep e2e in its own
  project whose `test` target `just check` reaches through the graph (a
  `just test-e2e` recipe is a convenience for focused runs, not the only place it
  runs); e2e is your main visibility into real behavior. If a journey is too
  expensive for every run, document it and run it in CI (e.g. nightly) — never
  silently drop it.
- **The natural project split.** The app is one project holding its own routes,
  components, and fast unit/component tests. The browser suite is the expensive
  one, so `<app>-e2e` is a project of its own that depends on the app's build — a
  change to a server route no page renders never starts a browser. Shared UI
  graduates into a `ui` project the moment a second consumer exists, and each
  backend service the app calls is its own project rather than a folder inside
  it. The language reference says where each project's manifest and targets live.
- **Commands.** `just check` runs format/lint/typecheck/unit/e2e across the
  affected projects, with minimal success output.
- **CI.** Bootstrap a clean checkout, then run the full gate (build + e2e) on
  every PR; validate the production build, not just the dev server. Start from
  `assets/ci.yml.template`.

## Verification

- [ ] **E2E covers critical journeys.** The e2e project's `test` target (reached
  by `just check` and CI, exposed as a `test-e2e` recipe for focused runs) drives
  sign-in, the core happy path, and at least one failure/recovery path — not just
  smoke tests.
- [ ] **Project split matches the cost.** The app and its fast unit/component
  tests are one project; the browser suite is its own `<app>-e2e` project
  depending on the app's build; shared UI is a `ui` project once a second
  consumer exists; each backend service is its own project.
- [ ] **Boundaries enforced.** A clear server/client split holds, and all
  external input (requests, params, third-party responses) is validated at the
  trust boundary.
- [ ] **Server-side authorization.** Every privileged action and data-access path
  checks authn/authz on the server; UI-only gating is never the sole guard, and
  secrets and privileged data access stay server-only.
- [ ] **URL state validated.** Shareable/navigable state kept in the URL is
  parsed and validated rather than trusted raw.
- [ ] **Production build validated in CI.** CI validates the production build,
  not just the dev server.
