# Cross-cutting: GitHub Actions / CI

Applies on top of whichever product shape and language you chose. CI's job is to
prove the artifact the way a future maintainer or user would encounter it — not
to re-run a developer's warm local environment.

- **Required, and it must run the gate.** A CI workflow is non-negotiable, and a
  workflow that doesn't invoke `just check` proves nothing. Start from
  [`assets/ci.yml.template`](../assets/ci.yml.template).
- **Clean checkout -> bootstrap -> full gate.** Every run starts from a clean
  checkout, runs `just bootstrap`, then `just check` (which includes e2e). If
  bootstrap can't produce a working repo from scratch, that is the bug. The
  baseline checker fails CI that never references `just check`.
- **Realistic platform matrix.** Use an OS matrix when the artifact is
  cross-platform (CLIs, plugins, binaries). Test the versions you actually
  support. When the matrix includes Windows, commit a `.gitattributes` with
  `* text=auto eol=lf` (and an `.editorconfig`) so line endings don't flip to
  CRLF on checkout and break tests or formatters — a failure mode that only ever
  shows up on the Windows runner.
- **Prove the end-user install path, on the real platforms.** Bootstrapping the
  *dev* toolchain (`just bootstrap`) is not the same as installing the shipped
  artifact. When the repo produces something users install — a CLI, a binary, a
  published package, a plugin — add a CI job, separate from the dev gate, that
  installs it via the **recommended end-user method** (the README install
  one-liner, `uvx`/`pipx`, `npm i -g`, `cargo install`, `brew install`, asdf
  `plugin add`, ...) on the supported OS matrix, then runs the installed entry
  point (e.g. `tool --version`) as a smoke test. Run it against the artifact the
  release will ship — the built wheel / binary / tarball — so the path is proven
  on every PR, not only after a release. Prefer a single cross-platform install
  script or command so the docs, CI, and what users actually run never drift; if
  CI installs differently than the docs tell users to, CI is proving the wrong
  thing. This drift is invisible until a user hits it.
- **Coverage is a default gate.** The gate measures test coverage and fails
  below the threshold; 95% line coverage is the default bar. Because CI runs the
  same `just check`, the threshold is proven on every PR, not tracked as a
  vanity badge. Measure it with the language's tool (`pytest --cov-fail-under`,
  Vitest `coverage.thresholds`, `cargo llvm-cov --fail-under-lines`, ...). Lower
  the bar — or drop it for a stack where coverage tooling genuinely doesn't fit
  — only with a documented reason in `AGENTS.md`, never by silently leaving
  coverage unmeasured.
- **Least privilege and no script injection.** Give the workflow's `GITHUB_TOKEN`
  the minimum `permissions:` each job needs — default the top level to
  `contents: read` and widen per-job only where required (e.g. a release job
  adding `contents: write`). Never interpolate untrusted event data
  (`${{ github.event.* }}` — a PR title, body, or branch name) straight into a
  `run:` shell; pass it through `env:` and reference it as a quoted variable, so a
  crafted PR can't inject commands.
- **Validate generated files.** Fail if committed generated files (lockfiles,
  schemas, formatted code) are out of date.
- **Cache for speed, never for correctness.** Cache dependencies, but never let
  a cache hide a broken clean build.
- **Artifacts after gates.** Upload build artifacts only once gates pass.
  Publish checksums for binaries; sign where appropriate.
- **Logs are context.** Keep logs minimal on success; emit detailed diagnostics
  only on failure, so a failed run points straight at the cause.

## The live / integration test tier

The deterministic e2e in `just check` must stay offline and reproducible — but
offline does **not** mean "mock everything." Drive the real artifact against real
*local* resources (real temp files, a real local server/DB, the real binary as a
subprocess); mocking the layer under test to keep the gate fast proves the mock,
not the product. Behavior that can only be proven against a *real* external
service (a real API, harness, or credential store) is a tier **above** the gate,
not a relaxation of it — the place where stubbing a third party is replaced by
the genuine call. Structure it the way these repos do:

- **Out of `just check`, in its own workflow.** Live tests never run in the
  default gate (they would make it non-deterministic and credential-dependent).
  They run in dedicated CI workflows — often **one workflow per integration** so
  a flaky provider fails in isolation and its secrets stay scoped.
- **Compile-but-skip — never `#[cfg]` it out.** The live test must still
  *compile and type-check* in the deterministic gate even when its secret/env is
  unset; keep the code in the build and skip it at *runtime* there (`#[ignore]` /
  an env guard) rather than excluding it, so the live code can't rot while
  execution stays opt-in. (Contrast with deterministic e2e, which must never be
  `#[ignore]`-d out of the gate.) The *dedicated live workflow* is where it runs
  for real — and there the credential is required, not optional (next bullet).
- **Require the credential — fail fast, never no-op.** The live workflow
  **requires** its secret and fails fast with a clear, actionable message when it
  is absent. Do **not** make a credential-less run skip or no-op to a green pass:
  that reports an untested path as covered — false confidence worse than a red X.
- **Handle fork PRs at the repo level, not in workflow logic.** Forks legitimately
  can't hold your secrets, but the fix is a repository setting, not a no-op branch
  in the workflow. Enable **Settings → Actions → General → Fork pull request
  workflows → Require approval for all (or first-time) contributors**, so a
  maintainer reviews and approves before any fork CI runs. Secrets stay restricted
  on `pull_request` from forks even after approval, so keep credential-gated live
  checks out of the required-checks set for fork PRs and run them from a
  maintainer-pushed branch or after merge.
- **Prove the published shape, not the dev build.** When the live/install test
  exercises a packaged artifact, install the *publish-shape* package in a fresh
  project and unset whatever dev escape-hatch points at your local binary (e.g.
  a `*_BIN` env var) so the test is forced through the artifact users get — not
  the one sitting in `target/`.

## Informational performance tier

Performance-sensitive artifacts (CLIs, hot libraries) benefit from a benchmark
workflow — but keep it **informational, never a gate**. The pattern these repos
share: a `bench.yml` running Criterion micro-benchmarks plus `hyperfine` CLI
timings, comparing against the PR base with `critcmp`, `continue-on-error: true`,
posting a single sticky PR comment (and degrading to an artifact + summary on
forks, which can't comment). It lives outside `just check` so a noisy or slow
benchmark can never block a correct change; the strict gate stays strict.

## Repository settings: merge model & branch protection

CI only proves the artifact if the platform actually *blocks* a merge until the
gate is green. Configure the GitHub repo so the protected default branch can only
take changes that passed the same checks CI runs. These settings are
deterministic, so script them with `gh` rather than clicking through the UI; the
exact commands below are the ones this skill's own repo uses.

- **Squash-merge only.** Disable merge commits and rebase-merging so history on
  the default branch stays linear and one PR is one commit. Set the squash
  commit subject to the **PR title** and the body to the **PR description**, so
  the PR title is what lands (and what a Conventional-Commits / release pipeline
  reads).
- **Lint the PR title as a required check.** Because the PR title *becomes* the
  release-driving commit, add a CI check that validates it against Conventional
  Commits (`amannn/action-semantic-pull-request`, or `commitlint` over the
  title) and put it in the required set below. Without it, a malformed title
  merges and the release tool computes a wrong (or no) version. See
  `releasing.md` for the release pipeline this feeds.
- **Auto-merge enabled.** Turn on auto-merge at the repo level so a PR can be
  queued with `gh pr merge --auto --squash` and merges itself the moment every
  required check goes green — no babysitting, and nothing merges early.
- **Delete head branches after merge.** Keep the branch list to live work only.
- **Branch protection on the default branch, requiring *all* CI checks.** Every
  status check that gates correctness must be required — including the
  **full-e2e gate job** (the one that runs `just check`), not just a fast lint
  job. List every required context by name; a check that is not *required* is
  advisory and a red one can still be merged past. Add the standard protections:
  require a PR before merging, linear history, no force-pushes, no branch
  deletion, and conversation resolution.
- **Leave "up to date before merging" off (non-strict checks).** Don't require a
  PR to be rebased onto the latest default branch before it can merge (GitHub's
  `strict` flag) — it forces a re-sync and a full CI re-run every time the base
  moves, which is real friction for little gain on a fast-moving or
  solo-maintained repo. Turn it on (`--strict`) only if independently-green PRs
  routinely break when combined (semantic conflicts) and that risk outweighs the
  churn.
- **Admins can still override.** Leave admin enforcement off
  (`enforce_admins: false`) so a maintainer can break the glass in an emergency;
  the protection is the default path, not a cage. (Bump the required approval
  count above zero for a team; zero is reasonable for a solo maintainer who
  still wants every check enforced.)
- **Require approval for fork-PR workflows.** Credential-gated checks (the
  live/llmlint tiers) fail fast without their secret, so a fork's CI must not
  auto-run unreviewed. Set the fork-PR approval policy (GitHub's *Settings →
  Actions → General → Fork pull request workflows*) so a maintainer approves
  before a fork's workflows run; default it to all external contributors. Secrets
  stay restricted on `pull_request` from forks even after approval.
- **Commit a required PR template.** Add `.github/pull_request_template.md`
  (start from
  [`assets/pull_request_template.md.template`](../assets/pull_request_template.md.template))
  so GitHub auto-populates every new PR with the structure the repo expects:
  **What** — a terse, high-level description of the *behavior* change; **Why** —
  the driver, i.e. the impact and what motivated it; and an optional **Additional
  info** third section, usually omitted, for the rare thing that fits neither
  above. Keep it pithy and resist describing the code changes line by line — the
  diff already shows the *how*; the template captures the *what* and *why*. This
  is also what lands in history, since the squash body is taken from the PR
  description. Unlike the merge-model settings, this is a committed file, so the
  baseline checker enforces its presence and that it names both What and Why.

Apply it (replace the contexts with *your* required job names):

```bash
# Merge model: squash only, auto-merge on, delete head branches.
gh repo edit --enable-squash-merge --enable-auto-merge \
  --enable-merge-commit=false --enable-rebase-merge=false \
  --delete-branch-on-merge
# PR title -> squash subject, PR body -> squash body (no gh flag; use the API).
gh api -X PATCH repos/{owner}/{repo} \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY

# Branch protection: require every gating check (the e2e-inclusive gate + any
# others), standard protections, admins can override.
gh api -X PUT repos/{owner}/{repo}/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": { "strict": false, "contexts": ["check", "commitlint", "llmlint"] },
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "required_linear_history": true,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "restrictions": null
}
JSON

# Require maintainer approval before a fork's workflows run (so credential-gated
# checks never auto-run unreviewed on forks).
gh api -X PUT repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval \
  -f approval_policy=all_external_contributors
```

The skill bundles this as a runnable, idempotent script — it applies the merge
model, branch protection, *and* the fork-PR approval policy. Pass the required
check contexts and preview with `--dry-run` first:

```bash
uv run --script scripts/setup_github_governance.py check commitlint llmlint --dry-run
uv run --script scripts/setup_github_governance.py check commitlint llmlint
# tune who is gated on forks (default: all_external_contributors):
uv run --script scripts/setup_github_governance.py check commitlint llmlint \
  --fork-pr-approval first_time_contributors
```

The `llmlint` context is the **LLM-judge tier** (see
[`references/llmlint.md`](llmlint.md)) — a required PR check that runs *outside*
the deterministic `just check` gate and requires its harness credential (fork
PRs are gated by the repo's require-approval-for-fork-workflows setting, not a
no-op). List it among the required contexts so a red llmlint run blocks merge,
the same as the `check` gate.

These are repo-side settings, so the filesystem baseline checker cannot verify
them — record the intended model in `AGENTS.md` ("Commits, releases, and
merging") so the decision is auditable and the next maintainer can re-apply it.

## Verification

- [ ] **CI proves the artifact.** A workflow runs `just bootstrap` then
  `just check` on a clean checkout, on the supported platform matrix.
- [ ] **End-user install path.** If the repo ships an installable artifact, a CI
  job (separate from the dev gate) installs it via the recommended end-user
  method on the platform matrix and smoke-tests the installed entry point — the
  path users actually take, not just `just bootstrap`.
- [ ] **CI least privilege + no injection.** Workflows set a minimal
  `permissions:` on the token (read-only by default, widened only where a job
  needs it) and never interpolate untrusted `${{ github.event.* }}` data directly
  into `run:` scripts — it is passed via `env:` and quoted instead.
- [ ] **Generated files validated.** CI fails if committed generated files
  (lockfiles, schemas, formatted code) are out of date, and logs stay minimal on
  success.
- [ ] **Repo governance configured.** The default branch is protected with
  squash-merge only, auto-merge on, head branches deleted on merge, and *every*
  gating CI check (including the full-e2e gate job) required before merge, with
  admins able to override. The model is recorded in the "Commits, releases, and
  merging" section of `AGENTS.md` (the filesystem checker cannot see repo-side
  settings, so this is verified by hand against the "Repository settings" section
  above).
- [ ] **Live tier requires its credential.** Any live/integration workflow keeps
  the test compiling, requires its secret, and fails fast with a clear message
  when it is absent (no skip/no-op to green). Fork PRs are gated by the fork-PR
  approval policy (applied by `setup_github_governance.py`, or **Settings →
  Actions → General → Fork pull request workflows → Require approval**), not by
  workflow no-op logic.
- [ ] **PR template.** A GitHub pull-request template exists
  (`.github/pull_request_template.md`) with **What** and **Why** sections
  (**Additional info** optional).
