# Shape: Next.js app

Next.js specifics. This builds on `shapes/react.md` (which itself builds on
`shapes/web-app.md`) and assumes `languages/typescript.md`; pull all four plus
`ci.md`. The React architecture principles — feature-based structure,
unidirectional imports, Rules of Hooks, state-by-kind — come from `react.md`;
this adds only the Next.js-specific concerns below.

- **Server/client boundary.** Be deliberate about server vs client components.
  Keep secrets and data access on the server; pass only what the client needs.
- **Trust boundaries.** Validate input in route handlers and server actions with
  a runtime schema (e.g., Zod) before using it. Never trust client-supplied data.
- **URL state.** Use `nuqs` (or equivalent) to keep and validate state in the
  URL, consistent with `shapes/web-app.md`.
- **UI.** Prefer shadcn/ui components; keep the design consistent and minimal.
- **Build validation.** Validate the production build (`next build`) in CI, not
  just `next dev`. Treat type errors and lint errors as build blockers.
- **Testing — not optional, and not a smoke test.** Ship all of:
  - Component/integration tests (e.g., Testing Library) for interactive UI.
  - A `test-e2e` recipe (e.g., Playwright) wired into `just check` and CI.
  - E2E covering, at minimum, the primary happy path **and** one meaningful
    failure/validation/recovery path through the rendered app.
  - If the app keeps state in the URL, an e2e test that loads a deep link and
    asserts the restored state, plus unit tests for parser defaults, invalid
    params, and serialization round-trips.
  - Drive e2e through accessible roles / user-facing selectors, not brittle CSS.

## Verification

- [ ] **Server/client boundary deliberate.** Secrets and data access stay on the
  server; only what the client needs is passed across.
- [ ] **Input validated with a runtime schema.** Route handlers and server
  actions validate input with a runtime schema (e.g. Zod) before using it.
- [ ] **Component + e2e tests ship.** Component/integration tests for interactive
  UI, plus a Playwright `test-e2e` recipe wired into `just check` and CI covering
  the primary happy path **and** one meaningful failure/validation/recovery path.
- [ ] **URL-state coverage (if applicable).** If the app keeps state in the URL,
  an e2e test loads a deep link and asserts the restored state, with unit tests
  for parser defaults, invalid params, and serialization round-trips.
- [ ] **Production build validated.** `next build` is validated in CI; type and
  lint errors are build blockers.
