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
- **Testing.** Unit/integration tests plus **E2E covering critical user
  journeys** — sign-in, the core happy path, and key failure paths — not just
  smoke tests. E2E is your main visibility into real behavior.
- **Commands.** `just check` runs format/lint/typecheck/tests with minimal
  success output.
- **CI.** Build plus E2E on every PR; validate the production build, not just
  the dev server.
