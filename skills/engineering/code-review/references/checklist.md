# Code review checklist

Work top to bottom; the earlier sections carry the most risk.

## Correctness

- [ ] The change does what the PR description claims.
- [ ] Edge cases handled: empty input, single element, max size, `None`/null.
- [ ] Boundary conditions correct (off-by-one, inclusive vs exclusive ranges).
- [ ] Error paths handled and surfaced, not swallowed.
- [ ] Concurrency: shared state guarded, no obvious races or deadlocks.
- [ ] No accidental behavior change to unrelated code paths.

## Safety and security

- [ ] Untrusted input is validated and bounded.
- [ ] Authentication and authorization checks are present and correct.
- [ ] No secrets, tokens, or credentials committed.
- [ ] No injection (SQL, shell, template) from unsanitized input.
- [ ] Resources (files, sockets, connections) are closed on all paths.

## Data and migrations

- [ ] Schema changes are backward compatible or gated behind a rollout.
- [ ] Migrations are reversible, or the risk is called out explicitly.
- [ ] Large backfills run out of band, not inline in a request.

## Tests

- [ ] New behavior is covered by tests.
- [ ] Failure and edge paths are tested, not only the happy path.
- [ ] Tests would actually fail if the change regressed.
- [ ] No flaky patterns (real sleeps, network calls, order dependence).

## Design and readability

- [ ] No needless duplication; reuse existing helpers where sensible.
- [ ] Abstractions are at the right level — not over- or under-engineered.
- [ ] Names are accurate; comments explain *why*, not *what*.
- [ ] No dead code, leftover debug logging, or commented-out blocks.

## Before approving

- [ ] CI is green.
- [ ] Findings are tagged by severity (blocking / should-fix / nit).
- [ ] The summary states an explicit verdict.
