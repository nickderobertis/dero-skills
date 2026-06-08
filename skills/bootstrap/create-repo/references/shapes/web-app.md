# Shape: Web app

Language-agnostic principles for a web application. Pair with the implementation
language (usually `languages/typescript.md`) and `ci.md`. If you pick a
framework with its own reference (e.g. `shapes/nextjs.md`), pull that in too.

- **State in the URL.** Keep shareable, navigable state in the URL where
  reasonable; parse and validate it rather than trusting it raw.
- **Enforce boundaries.** Keep a clear server/client split and feature/module
  boundaries. Validate all external input (requests, params, third-party
  responses) at the trust boundary.
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
