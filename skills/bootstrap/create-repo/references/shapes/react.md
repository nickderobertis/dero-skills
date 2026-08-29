# Shape: React app

React specifics for any React app. This builds on `shapes/web-app.md` and
assumes `languages/typescript.md`; pull all three plus `ci.md`. It stays
framework-agnostic (Vite, TanStack Start, React Router, plain SPA) — for Next.js
also pull `shapes/nextjs.md`, which layers on top of this. The principles below
track [bulletproof-react](https://github.com/alan2207/bulletproof-react); apply
them, don't re-derive them.

- **Feature-based structure.** Organize each app project's `src/` by feature,
  not by file type: each feature owns its components, hooks, API calls, state,
  and types under `<project>/src/features/<feature>/`. Building blocks shared
  within one app live in that project's top-level `components/`, `hooks/`,
  `lib/`, `utils/`; the moment a second app consumes them they graduate to a
  `ui` project of their own rather than being imported across app boundaries.
  Colocate; don't scatter one feature across global `components/`, `hooks/`,
  `services/` folders.
- **Unidirectional architecture, enforced by lint.** Dependencies flow one way —
  shared → features → app; a feature never reaches into another feature's
  internals (compose them at the routes/app layer instead). Enforce it in the
  linter (ESLint `import/no-restricted-paths`, or Biome `noRestrictedImports`),
  not by convention — an unenforced boundary erodes. Inside a project that is
  the linter's job; *between* projects it is the module-boundary tag rule's, and
  both are on.
- **Rules of Hooks, enforced.** Call hooks unconditionally at the top level.
  Turn on `eslint-plugin-react-hooks` (`rules-of-hooks` **and** `exhaustive-deps`)
  or Biome's equivalents (`useHookAtTopLevel`, `useExhaustiveDependencies`) so
  the gate catches violations. Reach for `useEffect` only to synchronize with an
  external system, always clean up subscriptions/timers/listeners it starts, and
  derive values during render rather than mirroring props/state into an effect.
- **State by kind — right tool per kind.** Don't reach for one global store.
  Keep **server-cache state** in a data-fetching layer (TanStack Query / RTK
  Query) — never copy fetched data into a global client store as the source of
  truth; **local UI state** in the component; **shared client state** in a small
  store (Zustand/Jotai/Context) only when it is genuinely cross-cutting;
  **form state** in a form library; and **shareable state in the URL** (per
  `shapes/web-app.md`).
- **Declarative data layer.** Route data access through one typed client and
  colocate queries/mutations in the feature's `api/` module; don't scatter raw
  `fetch`/`axios` calls through components. Validate responses at that boundary
  with a runtime schema (per `languages/typescript.md`).
- **Composition over prop-drilling.** Keep components small and focused; prefer
  composition (children/slots) and a handful of well-scoped contexts over
  threading props through many layers or over-generalized "God" components.
- **Keep logic out of the view.** Extract data-fetching, mutations,
  subscriptions, and non-trivial derived state into custom hooks (`useX`) so the
  component stays mostly declarative markup wiring hook results to JSX. A
  component that inlines fetching + effects + branching business logic beside its
  render should split into a hook (the logic) and a presentational component (the
  view); reuse the logic by sharing the hook, not by copying the component.
- **Forms are validated.** Use a form library (e.g. React Hook Form) with a
  schema resolver (Zod) so the same schema validates the form and types its
  values — no hand-rolled `onChange` validation.
- **Error boundaries.** Wrap route/feature subtrees in error boundaries so one
  component's render error degrades locally instead of blanking the app, and
  surface async/query errors to the user rather than swallowing them.
- **Client-side security.** Avoid `dangerouslySetInnerHTML`; when unavoidable,
  sanitize the HTML (e.g. DOMPurify) and never feed it unsanitized user or
  third-party content. Authorization is still enforced server-side
  (`shapes/web-app.md`) — hiding a control in the UI is not a guard.
- **Performance, measured.** Split code at the route level (lazy-load routes)
  and virtualize large lists; add `memo`/`useMemo`/`useCallback` only where a
  profile shows a real cost, not preemptively.
- **Component/integration tests drive the real tree.** Test with Testing Library
  by accessible role / user-facing text (not `data-testid` or CSS), rendering
  the real component and mocking **only** the network boundary (e.g. MSW) — never
  the component under test. These sit alongside the shape's e2e journeys, they
  don't replace them.

## Verification

- [ ] **Feature-based structure.** Each app project's `src/` is organized by
  feature (`<project>/src/features/<feature>/` owning its
  components/hooks/api/state/types), with only genuinely shared code in that
  project's top-level `components/`/`hooks/`/`lib/`/`utils/` — and in a `ui`
  project once a second app consumes it.
- [ ] **Boundaries enforced at both scopes.** Unidirectional import rules
  (shared → features → app, no cross-feature internal imports) are enforced by
  the linter (`import/no-restricted-paths` or Biome `noRestrictedImports`), not
  convention — and *between* app projects by the module-boundary tag rule, so a
  second app depends on a shared `ui` project rather than reaching into the
  first's `src/`. Both are on.
- [ ] **Hooks rules in the gate.** `eslint-plugin-react-hooks`
  (`rules-of-hooks` + `exhaustive-deps`), or the Biome equivalents, run in
  `just check`; effects synchronize with external systems and clean up.
- [ ] **State separated by kind.** Server-cache state lives in a query layer (not
  duplicated into a global store); local, shared-client, form, and URL state each
  use the appropriate tool.
- [ ] **Declarative data layer.** Data access goes through one typed client with
  queries/mutations colocated per feature and responses validated at the
  boundary — no scattered raw `fetch`/`axios` in components.
- [ ] **Logic extracted to hooks.** Data-fetching, mutations, subscriptions, and
  non-trivial derived state live in custom hooks; components stay mostly
  declarative markup rather than inlining substantial business logic beside JSX.
- [ ] **Feature code colocated.** A feature's components/hooks/api/state live
  under `src/features/<feature>/`, not scattered across global type-based folders.
- [ ] **Forms validated by schema.** Forms use a form library with a schema
  resolver (Zod) rather than hand-rolled validation.
- [ ] **Error boundaries in place.** Route/feature subtrees are wrapped in error
  boundaries and async/query errors surface to the user.
- [ ] **Client-side XSS guarded.** `dangerouslySetInnerHTML` is avoided or fed
  only sanitized HTML; untrusted content is never injected raw.
- [ ] **Component/integration tests are real.** Tests render the real tree,
  select by accessible role / user-facing text, and mock only the network
  boundary (e.g. MSW) — never the component under test.
