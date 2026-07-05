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
  least one key failure/recovery path — not just smoke tests. Put e2e behind a
  `test-e2e` recipe that `just check` runs; e2e is your main visibility into real
  behavior. If a journey is too expensive for every run, document it and run it
  in CI (e.g. nightly) — never silently drop it.
- **Commands.** `just check` runs format/lint/typecheck/unit/e2e with minimal
  success output.
- **CI.** Bootstrap a clean checkout, then run the full gate (build + e2e) on
  every PR; validate the production build, not just the dev server. Start from
  `assets/ci.yml.template`.

## Verification

- [ ] **E2E covers critical journeys.** A `test-e2e` recipe (run by `just check`
  and CI) drives sign-in, the core happy path, and at least one
  failure/recovery path — not just smoke tests.
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
