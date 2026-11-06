# Cross-cutting: GitHub Actions / CI

Applies on top of whichever product shape and language you chose. CI's job is to
prove the artifact the way a future maintainer or user would encounter it — not
to re-run a developer's warm local environment.

- **Required, and it must run the gate.** A CI workflow is non-negotiable, and a
  workflow that doesn't invoke the gate proves nothing. Write it with
  `compose_repo_plan.py --ci-cd` from
  [`assets/ci.yml.template`](../assets/ci.yml.template): its jobs run the gate's
  parts — `just lint` once, `just ci-test` per platform — as "CI and CD time
  budgets" below lays out.
- **Clean checkout -> bootstrap -> the gate.** Every run starts from a clean
  checkout, runs `just bootstrap`, then `just check` (which includes e2e) — at
  the tier that run is for ("Staged gates" below: the affected tier on a pull
  request, the broader sweep at its one lifecycle point). If bootstrap can't
  produce a working repo from scratch, that is the bug. The baseline checker
  fails CI that never references `just check` (or the composed `just ci-test`).
- **Pins stay in lockstep.** A toolchain version pinned in more than one place
  (`.tool-versions`, a CI `setup-*` action or matrix entry, a `Dockerfile`, the
  docs) must hold the same value everywhere, or be reconciled by a single check —
  otherwise local and CI silently run different toolchains and the gate stops
  meaning what a contributor sees.
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

## CI and CD time budgets

Every repository this skill composes starts inside the CI and CD shape, and the
budgets, that onepipeline, onetaskgraph and onepipeline-ui proved — so it is held
to the lines below from its first pull request rather than growing into hours-long
checks, repeated sweeps and job sprawl. `compose_repo_plan.py --ci-cd REPO_DIR
--language rust|typescript --name NAME [--platforms cross-platform|linux]
[--visual-docs] [--visual-docs-arch arm64]` writes it; `check_repo_baseline.py`
holds it in place.

**The lines.** Every CI check and every CD job is held to **30 minutes**, and a
change's last-commit pull-request checks plus its merge to release to **90
minutes**. Jobs are counted too, per pull-request push and per release, because the
account's **20 concurrent jobs** are shared by every repository: a job a pull
request did not need is a slot another repository waited for.

**CI** (`.github/workflows/ci.yml`, on `pull_request`, and on push to main for
`budgets` alone):

- `lint` — once, on Linux, everything platform-independent: the PR-title check,
  the suppressions comment (notignored), format/lint/type checks over the
  affected projects (`just lint`), the supply chain and (Rust) the MSRV
  (`just supply-chain`), the judged lint (llmlint), and the visual docs through
  screencomp's composite actions as steps, never its reusable workflow's jobs.
- `test-linux`, `test-windows`, `test-macos` — one per platform the repository
  supports, each running `just ci-test` (tests, builds, e2e and per-project
  budgets over the affected projects); a Linux-only repository runs `lint` and
  `test-linux`.
- `visual-docs-<arch>` — only for a repository that captures its visual docs on a
  second architecture, natively on that architecture's runner.
- `budgets` — the released report action, once its predecessors have ended however
  they ended: `needs` every other job, `if: always()`, no path or draft filter. It
  builds and tests nothing.
- Every job `timeout-minutes: 30`. Only `budgets` has a `needs` edge, so the rest all
  start at once.
- **A push to main runs only `budgets`,** which saves that commit's results as the
  history pull requests compare with. The other jobs are guarded to `pull_request`:
  the merged tree is the one its pull request's jobs proved, and nothing re-runs them
  afterwards.
- **Release PRs run every job.** Cached test inputs exclude changelogs and
  replace raw manifest/lockfile inputs with the per-ecosystem digest from
  `scripts/nx-version-free-inputs.mjs`. Only workspace package versions are
  blanked; dependencies, source and toolchain edits still change the hash.
  Use `--keep-version` for a target whose tests read the version. Every target
  shared by OS legs includes `node -p process.platform`. Cached outputs name
  small files only; terminal output is cached by Nx itself.
- **Use the shared remote cache in the four Nx jobs.** Run `trust`, start the
  pinned op-nx sidecar as a background `uses:` step at GITHUB_WORKSPACE, then
  `ready` before existing Nx commands. A loopback proxy records accepted
  uploads and actual remote replays; a failed upload cannot fail a passing task.
  `report` runs with `if: always()` and uploads bodies plus task telemetry for
  trusted same-repository PRs, retaining seven days. Fork and Dependabot runs
  read through the sidecar's read-only mode and never write reusable entries.
  Sidecar teardown uses a bare `cancel: nx-cache` step with no other keys.
  The pinned sidecar reports
  upstream throttling or outages as HTTP 409 or 500, so failed-store detail can
  include hidden throttling. A 409 is not proof of presence; promotion confirms it
  with a GET. Only a directly observed cache-API 429 is labelled throttled.
- **Promote before release-plz.** A PR writes only its merge-ref Actions-cache
  scope. The pinned library publishes its shared store only on default-branch
  pushes, so create-repo bridges that gap: the existing release-plz job promotes
  the latest complete trusted successful PR run's accepted and replayed bodies
  into the base scope, before opening/updating the release PR. Faults warn and
  leave release-plz running; promotion never deletes source entries.
- **Manual promotion is a real push.** The dedicated branch pattern is exported
  by `nx-remote-cache.mjs` as `PROMOTION_BRANCH_PATTERN`, currently
  `cache-promote/**`. Push `cache-promote/<PR>` to promote that trusted PR into
  the branch's scope; a demonstration PR based on it can read those entries.
  The pinned sidecar deliberately refuses workflow_dispatch writes, so there is
  no dispatch trigger and no GitHub event variable override. Release-plz steps
  run only on pushes to main. No PR or release job is added.
  The shared scripts' versioned contracts, telemetry and release-tail cache-use
  measurement are in [onebudgetspec.md](tools/onebudgetspec.md#shared-nx-cache).

**CD:** `release-plz` (`.github/workflows/release-plz.yml`, push to main) opens
the release pull request and arms auto-merge on it, so it merges itself once its
checks pass; on merge it tags and cuts the GitHub Release. (release-plz versions
Cargo workspaces only, so a TypeScript repository's `release-plz` job runs
release-please in pull-request mode; the job id is what the CD budgets count.)
The Release fires `.github/workflows/release.yml`: `build-linux`,
`build-linux-arm64` (GitHub's hosted ARM runner, aarch64-unknown-linux-gnu),
`build-macos` (Apple Silicon only) and `build-windows`, one per supported
platform, each building every artifact for its targets once, test-installing those
files, publishing its platform's artifacts and verifying them from the registries
on its own OS; then `publish`, which needs the build jobs, publishes the
platform-independent artifacts and confirms registry resolution. There is no
release-time test re-run and no separate published-smoke workflow. That is six CD
jobs cross-platform, four Linux-only.

**The budgets** (root `budgets.yaml`, from
[`assets/budgets.yaml.template`](../assets/budgets.yaml.template), each id under
the project name):

| budget | threshold | label |
| --- | --- | --- |
| `<name>-ci-check-time` | 1800 s | `release-tail` |
| `<name>-cd-job-time` | 1800 s | `release-tail` |
| `<name>-release-cycle-time` | 5400 s | `release-tail` |
| `<name>-ci-job-count` | the CI jobs the shape runs: 5 cross-platform, 6 with a second visual-docs architecture, 3 Linux-only | `release-tail` |
| `<name>-cd-job-count` | 6 cross-platform, 4 Linux-only | `release-tail` |
| `<name>-release-read-requests` | 6 requests | `release-tail` |
| `<name>-pr-read-requests` | 2 requests | `release-tail` |
| `<name>-nx-cache-use` | 100% remote replay of eligible executions | `release-tail` |
| `<name>-gate-time` | 1800 s | `host-variable` |

The eight GitHub figures run the repository's verbatim copy of the figures script
(`tools/onebudgetspec.md`, "GitHub Actions budget figures"). The CD figures pass
`--exclude-workflow ci`: `budgets`' push run at the release commit delivers nothing.

Beside these, `.github/reporting/budgets.yaml` holds the `budgets` job's own budget,
`<name>-budget-report-command-seconds` at a provisional 120 s, labelled `ci-report`.

**Where each line is enforced.** At pull-request time, by each job's
`timeout-minutes: 30`, and by `budgets` for every budget its job can measure. After a
release, by the release tail: `publish` installs
`onevcs-cli` at the version the skill pins and ends with `onebudgetspec check
budgets.yaml --label release-tail`, whose result nothing reads — an over figure
turns that step red with every breakdown in its log, and never blocks, demotes or
reverts a release. Locally, as a warning: `just gate` records its wall clock to
`.gate-time.json` and checks `<name>-gate-time` (`--label host-variable`),
warning on an over reading (exit 1) and failing on an invalid budgets file or a
failed measurement (2 or 3). The strict budget check `just check` runs excludes
both labels, and `budgets` reports them without enforcing them. No pre-push hook runs the gate: CI owns it ("Push hooks" below).

**Meet the line by cutting work, never by sharding.** Affected-only selection,
caches keyed on their inputs, no check run twice, no needless serialization
(`needs`). Splitting a job into shards buys wall-clock with job slots the account
does not have. An alternative that reduces coverage — skipping a platform,
dropping a suite from a job — is the owner's decision, never a fix applied to meet
a budget.

**Required checks** are exactly `lint`, the test jobs and `budgets`, which the composer
records in `.github/governance.json` and `setup_github_governance.py` applies by
default. Branches need not be up to date before they merge (non-strict): that
trades catching a semantic conflict between two independently green pull requests
for not re-running every open pull request each time main moves, and with no test
run on push to main nothing catches such a conflict until the next pull request
touching that code. Up-to-date branches stay an explicit opt-in (`--strict`).

**Staged gates under this shape.** Every pull request runs the affected tier. The
broader `just check all` sweep is not a CI job here — a push runs only `budgets` and the
release pull request's tree is a tested main — so it runs where a change reaches
everything (`just upgrade`) and by hand.

## Staged gates: the affected tier and the broader tier

The cost that matters is wall-clock from idea to production, and the way to cut
it without proving less is to run each check at the one point in the lifecycle
where it can still tell you something new. `project-graph.md` makes that
possible — the mandatory project graph is what lets a change reach some targets
and not others. This section names the two tiers that graph buys; every other
reference, script, and `AGENTS.md` note restates them from here rather than
re-deriving them.

- **The affected tier** — what development and review run. The gate recipe
  delegates to `nx affected -t lint test typecheck build` keyed off an
  **explicitly derived merge base** (`nx-set-shas`, or an explicit
  `--base=$(git merge-base origin/main HEAD)` — never Nx's implicit default,
  which is not deterministic across a shallow CI checkout). A change pays for
  the projects its diff can reach and nothing else. This is the **default**
  tier: a target belongs to it unless a rule below promotes it out.
- **The broader tier** — one full `nx run-many -t ...` sweep over every project,
  plus every target promoted out of the affected tier (the live/integration
  suites, and whatever the promotion rule below moved). It exists to catch what
  affected detection or a stale cache could miss, and it runs at **exactly one**
  point in the lifecycle — see "Where the broader tier runs" below.

Both tiers run the same recipe surface (`just check`), so local and CI cannot
drift: the tier is a *flag on the same command*, never a second implementation
of the gate. The informational benchmark tier below is in neither — it is not a
gate at all.

### Gate a given commit once

The rule is a property of a **commit**, not of a place in the pipeline: **each
commit is gated once at each tier.** A commit that has already been swept must
not be swept again later under another job's name — sweeping post-merge and then
sweeping the same code again at release-prep pays twice for one answer.

To find the duplicates in your own pipeline, list every job that runs targets
and write down two things about each: the **commit it runs against** and the
**tier it runs**. Then:

- Two jobs running the same tier against the **same SHA** are gating that commit
  twice. Delete the later one.
- Two jobs running the same tier against **different SHAs over an identical
  tree** — nothing landed between them, as with a tag build, a re-tag, or a
  release-prep job that follows the merge that already swept it — are also
  gating the same code twice. The tree is what gets proven; a fresh SHA over an
  unchanged tree proves nothing new.
- A squash-merge commit is **not** a duplicate of the PR head it came from: its
  tree never existed before (the base moved under it), so the first run against
  it is a first gating, not a second.
- A build is not a gate. A tag-triggered workflow that compiles and publishes an
  already-swept commit re-gates nothing; re-running that commit's lint,
  type-check, or test targets does.

### Where the broader tier runs, and what decides it

Do not inherit a location — derive it from the repo's release model. The
question to answer is: **between a commit landing on the default branch and the
artifact shipping, can any other commit land?**

- **No — the repo releases on merge** (the push-to-main driver `releasing.md`
  teaches: `semantic-release`, or `release-please` in non-PR mode). The merged
  commit *is* the released commit, so a later sweep would re-sweep the same
  tree. **The broader tier runs at merge-to-main**, and release-prep, tagging,
  and publishing re-run nothing.
- **Yes — the repo batches releases** behind a release train, a release-PR gate
  that accumulates several merges, or a manual cut. The commit that ships is not
  a commit any merge job swept, so **the broader tier runs at release-prep** —
  on the release PR or the pre-tag job — and **merge-to-main stays on the
  affected tier**.

When both readings are defensible (a release-PR gate merged immediately after
every single change effectively releases per-commit), answer the question
literally: if the release PR *can* accumulate more than one change, the repo is
batched. Absent a reason to batch, prefer release-on-merge and sweep at merge —
it puts the broader answer closest to the change that caused it. Record the
choice in the "Commits, releases, and merging" section of `AGENTS.md`: it is the
fact a later reader needs to tell a legitimate sweep from a duplicate.

### Measure, then derive your own threshold

Which targets sit in which tier is a measurement, not a taste. Collect, per
target, over a rolling window of recent CI runs:

- **wall-clock on a cold cache** — p50 and p95, not an average;
- **cache hit rate** — the share of runs replayed from cache instead of executed
  (Nx reports it per run; a remote cache shared with developers raises it);
- **affected rate** — how often the target is actually in the affected set for a
  typical change.

Compare targets on **expected cost per change = affected rate x (1 - cache hit
rate) x p95 wall-clock**, never on raw runtime: a ten-minute suite affected by
one change in twenty costs less per change than a one-minute suite affected by
every one of them.

**The promotion rule.** The affected tier must return its verdict inside the
time a contributor will wait without switching context. That budget is the rule,
and it is measured: when the affected tier's measured p95 exceeds it, promote
the target with the largest expected cost per change into the broader tier, and
repeat until the tier is back under budget. Before promoting anything, try the
two fixes that cost nothing in what gets proven — **split the project** so the
expensive part sits behind a graph edge unrelated changes cannot reach
(`project-graph.md`), and **raise the cache hit rate** (stable inputs, a shared
remote cache).

**Starting defaults, and the rule that supersedes them.** Begin at **10 minutes
p95 for the whole affected tier** and **5 minutes p95 for its lint/unit
targets** — the ones a contributor watches before switching away. Those numbers
are justified only by what a contributor will sit through; they are not measured
facts about *your* repo. Re-derive them from the measurements above whenever the
numbers move, and record the threshold you landed on, with the measurement
behind it, in `AGENTS.md`. Where your measurement and these defaults disagree,
the promotion rule wins and the defaults go.

**Promotion never buys speed with coverage.** A target that covers the behavior
a change alters stays in the affected tier however long it takes: you make it
cheaper — split it, cache it, narrow its inputs — or you keep paying for it. No
threshold above licenses promoting the suite that covers the diff in front of
you, and cheaper is acceptable only where it does not weaken what the change
proves.

### External contact promotes unconditionally

A suite that contacts an external service — a live third-party API, a hosted
model harness, a registry, a payment sandbox — leaves the affected tier
**because of what it touches, not because of how long it takes**. A *fast*
external-hitting suite still moves. This is not a runtime judgment call and no
measurement above applies to it: the reasons are non-determinism, a credential
fork PRs cannot hold, somebody else's rate limit and bill, and an outage over
there turning into a red PR over here.

That tier is the one the next section describes from the other side. The staged
model only says where it sits: out of the affected tier, into its own
credential-gated workflow. Promotion is a change of *place*, never a relaxation
— the live tier still requires its secret and still fails fast when it is
absent.

## The live / integration test tier

The deterministic e2e in `just check` must stay offline and reproducible — but
offline does **not** mean "mock everything." Drive the real artifact against real
*local* resources (real temp files, a real local server/DB, the real binary as a
subprocess); mocking the layer under test to keep the gate fast proves the mock,
not the product. Behavior that can only be proven against a *real* external
service (a real API, harness, or credential store) is a tier **above** the gate,
not a relaxation of it — the place where stubbing a third party is replaced by
the genuine call. It is the same tier the staged model promotes into, seen from
the other side: "External contact promotes unconditionally" says *why* it leaves
the affected tier; this section says how to build it. Structure it the way these
repos do:

- **Out of the affected tier, in its own workflow.** Live tests never run in the
  gate a PR runs (they would make it non-deterministic and credential-dependent):
  external contact promotes them unconditionally, per the section above. They run
  in dedicated CI workflows — often **one workflow per integration** so a flaky
  provider fails in isolation and its secrets stay scoped.
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

## The suppressions review comment (notignored)

The strict gate and the llmlint judge both decide *pass or fail*. Neither shows a
reviewer the one thing a high-level review of agent-written code most needs: the
checks the change **switched off**. A `# noqa`, an `#[allow(dead_code)]`, an
`llmlint: ignore` each cost one line to add and scroll past unremarked — and
silencing a rule is usually easier than fixing it, so that is where debt hides.

[notignored](https://github.com/nickderobertis/notignored) closes it. Its GitHub
Action posts one sticky comment listing the suppressions *this* pull request
introduced — each with its tool, its silenced rules, its stated reason (or the
conspicuous absence of one), a link to the line, and a collapsed block holding the
code it silences. So a human reviewing at a high level sees every bypass and its
justification without reading the diff line by line. Add it to every repo. In the
composed CI/CD shape it is a step of the `lint` job, guarded by the fork check
below; a repository without that shape starts from
[`assets/notignored.yml.template`](../assets/notignored.yml.template) →
`.github/workflows/notignored.yml`.

- **Drop-in, with defaults.** No config file and nothing to install — the action
  fetches its own binary. `diff-base` defaults to the PR's base branch and `paths`
  to the whole repo, so a repo wanting the default artifact passes no inputs.
  Consume the **floating major tag** (`uses: nickderobertis/notignored@v0`), which
  follows every `0.x` release and moves only once that release's artifacts have
  published; pin exactly only where a reproducible build beats picking up fixes.
- **Three things it needs.** A `pull_request` trigger (the comment lives on the
  PR, so a push-only workflow has nothing to comment on),
  `permissions: pull-requests: write` (plus `contents: read`) so the comment can
  be upserted, and `fetch-depth: 0` on the checkout so the base branch exists to
  diff against. Each failure is silent — the run goes green having posted
  nothing — and a shallow checkout is the usual culprit.
- **It is deliberately NOT a required check of its own.** (As a `lint` step it
  is skipped on forks while `lint` itself still reports.) Skip it on fork pull requests
  (`if: github.event.pull_request.head.repo.full_name == github.repository`),
  whose read-only token cannot upsert a comment — and a required context that
  never reports would block those PRs forever. So keep it out of the required set
  above: it is a *review artifact*, not a gate. Adding a suppression is
  legitimate; hiding it is not, and the comment is what makes hiding it hard.
  (Gate on the count instead — the action's `count` output — only if the repo has
  a real reason to forbid new suppressions outright.)

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
  required check goes green — no babysitting, and nothing merges early. A release
  workflow arms it on the release PR it opens, so that PR merges itself too
  unless `AGENTS.md` records the release-PR opt-out phrase.
- **Delete head branches after merge.** Keep the branch list to live work only.
- **Branch protection on the default branch, requiring *all* CI checks.** Every
  status check that gates correctness must be required — including the
  **full-e2e gate job** (the one that runs `just check`), not just a fast lint
  job. List every required context by name; a check that is not *required* is
  advisory and a red one can still be merged past. Add the standard protections:
  require a PR before merging, linear history, no force-pushes, no branch
  deletion, and conversation resolution.
- **Required checks are the PR-context jobs only.** In the composed CI/CD shape
  those are exactly `lint` (which carries the PR-title check and the llmlint
  judge as steps), the test jobs and `budgets`, recorded in `.github/governance.json`. A
  repository on the older shape requires its affected-tier gate (`check`), the
  PR-title lint (`commitlint`) and the llmlint judge (`llmlint`). The **broader-tier sweep runs after the PR
  is gone** (at merge-to-main, or at release-prep), so it has no pull request to
  report on and must never join the required set: a required context that never
  reports blocks every PR forever, the same trap as `notignored` below. It gates
  the *release* instead — make the release workflow depend on the sweep job so a
  red sweep stops the release rather than a merge. If you rename the gate job
  (say to `check-affected`) or add a PR-reporting job, the required contexts, the
  `setup_github_governance.py` invocation, and `AGENTS.md` all move together —
  re-apply, then `--verify`.
- **Leave "up to date before merging" off (non-strict checks).** Don't require a
  PR to be rebased onto the latest default branch before it can merge (GitHub's
  `strict` flag) — it forces a re-sync and a full CI re-run every time the base
  moves, which is real friction for little gain on a fast-moving or
  solo-maintained repo. The trade: a semantic conflict between two independently
  green PRs is not caught at merge, and with no CI run on push to main, not until
  a later PR touches that code. Turn it on (`--strict`) only as an explicit
  opt-in, if independently-green PRs routinely break when combined and that risk
  outweighs the churn.
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
  stay restricted on `pull_request` from forks even after approval. GitHub offers
  this setting on public repositories only; on a private or internal one it is
  not applicable and is skipped, not failed.
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
# checks never auto-run unreviewed on forks). Public repositories only: GitHub
# refuses this on a private or internal repository.
gh api -X PUT repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval \
  -f approval_policy=all_external_contributors
```

The skill bundles this as a runnable, idempotent script — it applies the merge
model, branch protection, *and* — on a public repository — the fork-PR approval
policy, reading the visibility from GitHub and reporting the policy as `not
applicable (private)` or `(internal)` elsewhere. In a composed repository it reads
the required checks — `lint`, the test jobs and `budgets`, non-strict — from
`.github/governance.json`, so it needs no contexts; elsewhere pass them. Preview
with `--dry-run` first:

```bash
uv run --script scripts/setup_github_governance.py --dry-run   # the composed shape
uv run --script scripts/setup_github_governance.py
uv run --script scripts/setup_github_governance.py check commitlint llmlint --dry-run
uv run --script scripts/setup_github_governance.py check commitlint llmlint
# tune who is gated on forks (default: all_external_contributors):
uv run --script scripts/setup_github_governance.py check commitlint llmlint \
  --fork-pr-approval first_time_contributors
# preview a private or internal repo fully offline (no `gh` to read visibility):
uv run --script scripts/setup_github_governance.py check commitlint llmlint \
  --repo OWNER/NAME --branch main --visibility private --dry-run
```

The `llmlint` context is the **LLM-judge tier** (see
[`references/llmlint.md`](llmlint.md)) — a required PR check that runs *outside*
the deterministic `just check` gate and requires its harness credential (fork
PRs are gated by the repo's require-approval-for-fork-workflows setting, not a
no-op). It **must** be among the required contexts so a red llmlint run blocks
merge, the same as the `check` gate — otherwise auto-merge lands a PR the moment
`check`/`commitlint` go green, past a still-running or failed llmlint. The setup
script **enforces** this: it refuses to apply governance without an `llmlint`
context — or a required `lint` whose job in `ci.yml` runs `just lint-llm-diff`,
as the composed shape's does — unless you pass `--allow-missing-llmlint` (only
for a repo with no llmlint tier).

**Adding one check to a repository already governed.** The commands above apply the
whole profile, and their protection PUT replaces what is there. To require one more
context while keeping everything else — other contexts, their app bindings,
approvals, strictness, admins, restrictions and the merge and fork settings — use
the additive mode instead:

```bash
uv run --script scripts/setup_github_governance.py --add-required-check budgets \
  --repo OWNER/NAME --branch main [--dry-run | --verify]
```

It sends one POST to the required-status-checks subresource, naming only the missing
contexts, so a repeated call writes nothing, and it reads the protection back and
fails if anything but the addition moved. It writes classic protection only, which
is what every repository it has been applied to uses: a branch governed by a
ruleset, classic or not, is refused by name before any write, as is a failed read.
Never run it before the job reporting that context has landed on the base branch,
because every open pull request would then wait on a check nothing reports.

These are repo-side settings, so the filesystem baseline checker cannot verify
them — record the intended model in `AGENTS.md` ("Commits, releases, and
merging") so the decision is auditable and the next maintainer can re-apply it.

## Verification

- [ ] **The CI/CD shape.** `compose_repo_plan.py --ci-cd` wrote it, and
  `check_repo_baseline.py` passes it: `lint` plus one `test-<platform>` job (and
  any `visual-docs-<arch>`), each `timeout-minutes: 30` with no `needs`; `budgets`
  after them under `if: always()`, the released action's one job, unfiltered; no
  other pull-request job; only `budgets` on push to main; `release-plz`, a `build-<platform>`
  job per supported platform (Linux arm64 with Linux) and `publish` ending in the
  release tail; the nine budgets in the root `budgets.yaml` with the figures
  script copied unedited; the report job's own budget in
  `.github/reporting/budgets.yaml`; the budget policy in `AGENTS.md`;
  `.github/governance.json` requiring exactly `lint`, the test jobs and `budgets`,
  non-strict unless the owner opted in.

- [ ] **Two tiers, named and wired.** Pull requests run the **affected tier** —
  the gate recipe delegating to `nx affected` keyed off an explicitly derived
  merge base — and one job runs the **broader tier**, a full `run-many` sweep
  (except in the composed CI/CD shape, which runs no sweep in CI: see "CI and CD
  time budgets"). Both go through the same recipe surface, so no tier is a
  second implementation of the gate.
- [ ] **The broader tier runs at exactly one lifecycle point.** Exactly one job
  sweeps: merge-to-main for a repo that releases the merged commit, release-prep
  for a repo that batches releases. The placement, and the release model it was
  derived from, are recorded in the "Commits, releases, and merging" section of
  `AGENTS.md`.
- [ ] **No commit is gated twice.** Every job that runs targets is listed with
  the commit it runs against and the tier it runs, and no two of them run the
  same tier over the same SHA — or over an identical tree with nothing landed
  between. The tag/build/publish workflow re-gates nothing.
- [ ] **Thresholds are measured, not inherited.** Per-target cold-cache
  wall-clock (p50/p95), cache hit rate, and affected rate are measured over
  recent runs; promotion follows the highest expected cost per change; and the
  threshold the repo settled on — with the measurement behind it — is written in
  `AGENTS.md` rather than carried over from another repo's numbers.
- [ ] **External contact promotes unconditionally.** Every suite that touches an
  external service sits outside the affected tier because of what it touches,
  not its runtime — a fast external-hitting suite too — and it still requires its
  credential and fails fast (no skip to green).
- [ ] **Promotion never weakened coverage.** No target was promoted out of the
  affected tier to hit a time budget while it was the thing covering the changed
  behavior; expensive targets were split, cached, or narrowed instead.
- [ ] **Required set matches the PR-context jobs.** The required contexts name
  the affected-tier gate, the PR-title lint, and the llmlint tier — the jobs that
  report on a pull request — and the broader-tier sweep is not among them
  (it blocks the release instead). Renaming a gate job updates the contexts, the
  `setup_github_governance.py` invocation, and `AGENTS.md` together.
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
  squash-merge only, auto-merge on (and armed on any release PR the release
  workflow opens), head branches deleted on merge, and *every*
  gating CI check (including the full-e2e gate job *and* the `llmlint` tier, which
  the setup script enforces) required before merge, with admins able to override. The model is recorded in the "Commits, releases, and
  merging" section of `AGENTS.md`. The filesystem checker cannot see repo-side
  settings, so verify them against the live repo with
  `setup_github_governance.py --verify <every-required-check>` — it reads branch
  protection, the merge model, and fork-PR approval back and fails on any
  divergence (a required check missing, force-pushes allowed, the wrong merge
  model), rather than trusting a by-hand read of the "Repository settings" section
  above.
- [ ] **Live tier requires its credential.** Any live/integration workflow keeps
  the test compiling, requires its secret, and fails fast with a clear message
  when it is absent (no skip/no-op to green). Fork PRs are gated by the fork-PR
  approval policy (applied by `setup_github_governance.py`, or **Settings →
  Actions → General → Fork pull request workflows → Require approval**), not by
  workflow no-op logic.
- [ ] **PR template.** A GitHub pull-request template exists
  (`.github/pull_request_template.md`) with **What** and **Why** sections
  (**Additional info** optional).
- [ ] **Suppressions review comment.** The composed `lint` job runs it as a
  step, or `.github/workflows/notignored.yml` exists
  (from `assets/notignored.yml.template`), runs
  `uses: nickderobertis/notignored@v0` on `pull_request` with
  `pull-requests: write` and a `fetch-depth: 0` checkout, and skips fork PRs via
  the `head.repo.full_name == github.repository` guard. It is **not** in the
  required-checks set — it is a review artifact, not a gate.

## Push hooks

A repository with PR CI installs no duplicate pre-push check: CI owns the gate,
model-free validation and visual checks or captures. Keep manual check, capture,
baseline and review commands available, and preserve pre-commit hooks. Without CI,
keep the existing pre-push gate. Hook setup reruns must remove obsolete generated
pre-push dispatchers owned by the repository without disabling other hooks or
unsetting a shared hooksPath.
