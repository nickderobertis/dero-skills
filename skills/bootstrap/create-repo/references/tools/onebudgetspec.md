# Tool: onebudgetspec (measured budgets)

Every repo built from this baseline registers its measurable budgets — request
counts, query counts, cold-start time, gate wall clock — with
[onebudgetspec](https://github.com/nickderobertis/onebudgetspec), from its first
commit, so every non-functional requirement with a number is auditable,
trackable and adjustable in one place. Budgets are part of the baseline:
`compose_repo_plan.py` composes this reference into every plan and adopts the
budget lint rules in every composed `llmlint.yml`, and `--wiring REPO_DIR`
applies the setup step below.

The file format, the result protocol and the command line are onebudgetspec's to
state: read the
[README at the pinned release](https://github.com/nickderobertis/onebudgetspec/blob/v0.2.0/README.md)
rather than a copy of it here. This reference states how a repo built from this
baseline lays budgets out, so a new consumer writes a `budgets.yaml` and a
measurement and nothing else.

## When is a requirement a budget

This section is the one statement of the rule; the generated `AGENTS.md` and
the `tests_hold_no_nonfunctional_thresholds` and
`budgets_track_product_owner_outcomes` lint rules restate it. A number in a
test falls into one of three cases.

**1. A cost figure is a budget.** The test's subject is how much a behaviour
costs at a realistic workload — wall clock, request or query count, API points,
payload or artifact size, memory, money — and the bound is a tolerance someone
could reasonably raise or lower. That figure goes in a `budgets.yaml` budget. A
test may record it as telemetry for the budget's command (see "Measure in the
tests that already run") and never compares it with a threshold itself.

Before — the bound is buried in the test:

```python
def test_sync_pulls_every_issue_across_pages():
    transport = RecordingTransport.replaying(PAGES)
    issues = sync_issues(transport)
    assert [i["id"] for i in issues] == ["LIN-1", "LIN-2", "LIN-3"]
    assert len(transport.requests) <= 14
```

After — the test records the figure, and a budget owns the bound:

```python
def test_sync_pulls_every_issue_across_pages():
    transport = RecordingTransport.replaying(PAGES)
    issues = sync_issues(transport)
    assert [i["id"] for i in issues] == ["LIN-1", "LIN-2", "LIN-3"]
    record("sync_journey", requests=len(transport.requests))
```

```yaml
budgets:
  - id: linear-sync-requests
    description: |
      Linear API requests one full issue sync makes. Protects the sync from
      sliding back into per-issue fetches that exhaust Linear's rate limit.
    measure: reported
    command: ["uv", "run", "python", "budgets/analyse_sync_requests.py"]
    unit: requests
    direction: max
    threshold: 14
```

**2. A wall-clock discriminator is rewritten to wait on an event, with no
number.** The test uses elapsed time to tell two behaviours apart — "returned in
under 2 s, so it did not wait", "still running after 2 s, so it is blocked".
That is neither a budget nor a sound test: a loaded host flips it. The test
instead holds its double until the test releases it and asserts the observable
event — returned while the double is still held and its job still alive, or the
process seen as a blocked waiter on the lock. Any timeout left is a hang guard
only, never an asserted property.

Before:

```python
def test_enqueue_returns_without_waiting_for_the_export(exporter):
    started = time.monotonic()
    job = enqueue_export(exporter, board="ops")
    assert time.monotonic() - started < 2, "enqueue waited for the export"
```

After:

```python
def test_enqueue_returns_without_waiting_for_the_export(exporter):
    exporter.hold()
    job = enqueue_export(exporter, board="ops")
    assert exporter.is_held() and job.is_alive()
    exporter.release()
    assert job.result(timeout=30) == "done"  # hang guard, not the property
```

**3. A time-feature contract stays a test assertion.** The test's subject is a
time feature itself, where the clock is the event: a configured timeout or
deadline firing near its value, or a retry's backoff schedule. So do an exact
count of an action ("exactly one board check"), the absence of an operation
("no whole-board walk") and a limit the product itself enforces (a truncation
bound, a host's body-size limit). None of these is a tolerance anyone tunes, so
none is ever a finding.

Before — the contracts are mistaken for cost figures: the tests only record
them, and budgets own the bounds, so a figure inside its budget no longer proves
the timeout fires near its deadline or the board is checked exactly once:

```python
def test_request_times_out_at_its_configured_deadline(stalled_server):
    client = Client(stalled_server.url, timeout=0.5)
    started = time.monotonic()
    with pytest.raises(RequestTimeout):
        client.get("/issues")
    record("client_timeout", seconds=time.monotonic() - started)


def test_a_move_checks_the_board(board):
    move_card(board, "LIN-1", to="done")
    record("move_card", board_checks=board.checks)
```

After — the contracts stay assertions in the tests that check them:

```python
def test_request_times_out_at_its_configured_deadline(stalled_server):
    client = Client(stalled_server.url, timeout=0.5)
    started = time.monotonic()
    with pytest.raises(RequestTimeout):
        client.get("/issues")
    assert 0.5 <= time.monotonic() - started < 1.5


def test_a_move_checks_the_board_exactly_once(board):
    move_card(board, "LIN-1", to="done")
    assert board.checks == 1
```

**The level a budget sits at.** A budget is a product-owner-level outcome: a
quota's headroom on a realistic run, a latency someone waits through, the size
of what reaches a reader. Each carries the threshold the user approves.
Per-step, per-phase and per-operation figures, and a second unit of the same
concern (requests beside points), are telemetry, not budgets: still recorded on
every run, and reported by the budget's analysis as the breakdown of its figure
in the SDK reporter's `detail` (see "onebudgetspec is the only judge"), so a
failed budget says which part grew. A budget's command analyses telemetry the
gate's tests already record, never running a scenario of its own just to
measure, except where "Measure in the tests that already run" allows a
standalone measurement.

Before — a run's total registered beside its phases, and one quota in two units
(each entry's `measure`, `direction` and `threshold` elided):

```yaml
budgets:
  - id: linear-sync-points
    description: Linear API points one full sync spends against the hourly quota.
    command: ["uv", "run", "python", "budgets/analyse_sync.py", "total"]
    unit: points
  - id: linear-sync-issues-points
    description: Linear API points the issues phase of a sync spends.
    command: ["uv", "run", "python", "budgets/analyse_sync.py", "issues"]
    unit: points
  - id: linear-sync-comments-points
    description: Linear API points the comments phase of a sync spends.
    command: ["uv", "run", "python", "budgets/analyse_sync.py", "comments"]
    unit: points
  - id: linear-sync-requests
    description: Linear API requests one full sync makes.
    command: ["uv", "run", "python", "budgets/analyse_sync.py", "requests"]
    unit: requests
```

After — the single total, its phases and requests reported as `detail`:

```yaml
budgets:
  - id: linear-sync-points
    description: |
      Linear API points one full sync spends. Protects the hourly quota the
      sync shares with every other integration.
    measure: reported
    command: ["uv", "run", "python", "budgets/analyse_sync.py"]
    unit: points
    direction: max
    threshold: 400
```

```python
"""Report the points the sync journey test recorded, broken down by phase."""

import json
import sys
from pathlib import Path

from onebudgetspec_sdk import report

TELEMETRY = Path(__file__).parents[1] / ".telemetry" / "sync_journey.json"
recorded = json.loads(TELEMETRY.read_text())
phases = recorded.get("phases") if isinstance(recorded, dict) else None
if not isinstance(phases, dict) or not all(
    isinstance(p, dict) and all(type(p.get(k)) is int for k in ("points", "requests"))
    for p in phases.values()
):
    sys.exit(f"{TELEMETRY}: expected per-phase integer points and requests")
report(
    sum(p["points"] for p in phases.values()),
    detail=", ".join(
        f"{name}: {p['points']} points in {p['requests']} requests"
        for name, p in phases.items()
    ),
)
```

## Measure in the tests that already run

Prefer recording a budget's figure where a test already exercises the behaviour.
The tests keep checking end-to-end behaviour in realistic scenarios as they
already do; one of them also records the figure a budget needs as temporary
telemetry, written to a test output that is never committed (`dist/telemetry/`,
say). The budget's command only analyses that data and reports it. A standalone
measurement is for a behaviour no existing gate exercises, or one whose recording
there would cost more than measuring it on its own.

## Where a budget lives

A `budgets.yaml` sits at the root of the smallest tree that holds everything
specific to its measurements: the commands it names, the tests they run, and
budget-only fixtures. Measuring code may call shared code anywhere else — a common
test harness, the code under measurement, a fixture other tests use too. Only
something that exists solely to serve one of its budgets must not live outside
that tree.

## One Nx project per budget domain

Each budget domain is one Nx project, holding its `budgets.yaml`, its measuring
code and its budget targets. As the release lays it out, that is two targets:

- **`budgets`** — `onebudgetspec check <project>/budgets.yaml --exclude-label host
  --save-results target/budget-results/<project>.json`, the deterministic budgets,
  cached (see caching below). It declares that file in its Nx `outputs`
  (`{workspaceRoot}/target/budget-results/<project>.json`), so a cache hit restores it
  for `test-linux` to upload. When a test records the telemetry it analyses, it
  `dependsOn` that test.
- **`budgets-host`** — `onebudgetspec check <project>/budgets.yaml --label host`,
  the `elapsed` and host-reading budgets, never cached.

The affected run is `nx affected -t budgets budgets-host`, and `check` depends on
it, so a change runs the budgets of the projects it can reach. A root
`budgets.yaml` holds what every change must stay within (the gate's wall clock,
say) and is checked on every run with `onebudgetspec check budgets.yaml`.

## onebudgetspec is the only judge

A `reported` budget's measuring code reports its figure with its SDK's reporter
and stops there. It never reads `budgets.yaml` and never compares the figure with
the threshold; `onebudgetspec check` does both, once. Each reporter writes to the
file `ONEBUDGETSPEC_RESULT` names and writes nothing (returning false) outside a
check, so a test that records a figure behaves the same either way:

| SDK | Reporter |
| --- | --- |
| Rust (`onebudgetspec-core`) | `onebudgetspec_core::report(value: f64, detail: Option<&str>) -> std::io::Result<bool>` |
| Python (`onebudgetspec-sdk`) | `onebudgetspec_sdk.report(value: float, detail: str \| None = None) -> bool` |
| TypeScript (`@onebudgetspec/sdk`) | `report(value: number, detail?: string): boolean`, synchronous |

## No per-budget wrappers

A budget's `command` runs its measurement or analysis directly, or through one
generic runner per file that picks the measurement by `ONEBUDGETSPEC_BUDGET_ID`,
which onebudgetspec sets to the budget's `id` for every budget's command. No
script exists per budget, and no runner keeps its own list of the file's ids to
dispatch on.

## Budget descriptions

A budget's `description` says, in one or two sentences, what is measured and what
it protects. It carries no base figures from an old commit, no history of the
figure, and no account of what each journey did, and it does not restate the
budget's own fields (command, unit, direction, threshold).

## Caching by measure kind

Whether a budget's target is cached follows from how it is measured. This is
convention and the wiring's defaults, not a lint rule.

- **A deterministic `reported` budget is cached**: its figure depends only on code
  and fixtures. The `budgets` target's inputs are its tree (`{projectRoot}/**/*`),
  the production sources of what it measures (`^production`), the telemetry a
  dependent test wrote (`{ "dependentTasksOutputFiles": "**/telemetry/*.json" }`)
  and the pinned release (`{ "externalDependencies": ["@onebudgetspec/cli"] }`,
  its lockfile entry). That last input needs an Nx that reads the repo's
  lockfile — Nx 20 does not read `bun.lock` and refuses the target, Nx 23 does.
- **An `elapsed` or host-reading budget is not**: anything reading the host's
  load, the clock, the network or a credential. Label it `host`, so the uncached
  `budgets-host` target runs it.

## End-to-end journeys live with the crate they exercise

A budget domain's project can only contain its budgets if the journeys feeding
them sit beside the code they exercise. A single e2e suite exercising every
crate or package would pull every domain's telemetry, and so its budgets, into
one project. In a Rust workspace the layout is the one the buildout rule
`binary_e2e_is_its_own_crate` already holds: one test-only e2e member per crate
exercised, sharing a support crate for the harness.

## The setup step: pinned and wired

Wiring and pinning are a setup step every repo takes, checked deterministically
by `check_repo_baseline.py` in every repo whatever its recorded composition,
never a judged rule. `compose_repo_plan.py --wiring REPO_DIR`, which needs no
other flag, performs it:

- pins `@onebudgetspec/cli` exactly in `package.json`, so the next install
  records it in the lockfile like any other dev tool;
- adds the `budgets` and `budgets-host` target defaults above to `nx.json`
  (with the conventional `default`/`production` named inputs where none exist);
- adds a `budgets` recipe to the justfile that `check` depends on, running
  `nx affected -t budgets budgets-host` at the gate's tier and the root
  `budgets.yaml` on every run.

Each project then declares its two targets' `command` (and the `dependsOn` of a
telemetry-reading `budgets`); the defaults supply the rest. The composed
`llmlint.yml` adopts the budget rules
(`assets/llmlint/tools/onebudgetspec.llmlint.yml`, pinned `@1`), which judge
descriptions, tree scope, the single judge, direct commands and telemetry reuse,
and the two rules of "When is a requirement a budget" above. The fragment is
listed by URL in the repo's own `llmlint.yml`, never named by `base.llmlint.yml`,
so a config adopts the budget rules only by listing them.

## The budget report job

`compose_repo_plan.py --ci-cd` generates one `budgets` job in `ci.yml` from
[`onebudgetspec-github-action`](https://github.com/nickderobertis/onebudgetspec-github-action),
pinned to the exact release the composer names. Its `action.yml` states the inputs, and
`tests/test_budget_report_contract.py` reconciles the job and its budget to a vendored
copy of it. The action publishes its crate last, so a crate at a version means that
release's whole payload is usable.

- **When it runs.** Every pull request, draft or not and whatever it touches, and every
  push to main, after the other jobs however they ended. It adds no build or test.
- **What it checks.** Every budget without an omitted label appears once in the report. One
  with a result `test-linux` saved shows that figure with its verdict, and its command does
  not run here. Any other runs once through ordinary `onebudgetspec check --id`, with its
  original command, labels and threshold, unless its own argv shows it cannot run: its
  program is missing, or a relative path it names is absent. Then it is listed as not
  measured, with `no saved result was found on this run` as its reason. A missing
  `ci-report` label is never a reason, and nothing is rerun to fill a figure. So name a
  budget's saved telemetry in its `command`: an absent file then reads as unavailable rather
  than as a failed measurement.
- **How the comment reads.** The layout is the action's contract, and its README at the
  pinned tag is its one source; this reference does not restate it.
  `tests/external/test_budget_report_job_external.py` reads the comment the released action
  posts for a composed repository, so a pin bump that moves the layout fails there.
- **The saved-results flow.** Each `budgets` target passes `--save-results
  target/budget-results/<project>.json` and declares that file in its Nx `outputs`, so a
  cache hit restores it. `test-linux` uploads `target/budget-results/` as
  `budget-check-results-test-linux` under `if: always()`, and the job's
  `saved-results-artifacts: budget-check-results-*` reads every match of this run.
- **What `not-measured-on-pr` means.** A pull request cannot measure this budget or move its
  figure, so `omit-labels` leaves it out of the report and one line in Details counts it.
  Its check elsewhere is unchanged: the release tail and `just gate` still run it, under its other
  label. `<name>-ci-check-time` and `<name>-ci-job-count` do not carry it: on a pull request
  they read that run's own jobs. Neither does `<name>-cache-headroom`, which reads the
  repository's cache listing with the job's token on any event.
- **A base push takes the merged pull request's figures.** A push to main reads no saved
  results. Each row it would leave unavailable, and each whose command would run, takes the
  figure of the pull request that merged it, so a pull request's value and its baseline are
  one measurement. A commit with no pull request, or an expired artifact, leaves those rows
  as they would have been, with a diagnostic.
- **What fails it.** An enforced budget over its threshold or with a measurement error, or
  an invalid budgets file. A missing baseline, a refused credential or a failed comment or
  upload is a stated reason and changes nothing. Forks get the summary and the artifact,
  with no comment and no credential.
- **What is report-only.** `enforcement-exclude-labels` names `release-tail` and
  `host-variable`, whose commands need a release or a developer's machine. Those not omitted
  still run once and keep their verdict and flags; one whose command fails, as the CI-time
  figures' do on a fork or a push, has no verdict and is listed as not measured, with the
  failure's message as its reason.
- **Its own budget.** `.github/reporting/budgets.yaml` holds
  `<name>-budget-report-command-seconds`, read from the job's own telemetry by the released
  `onebudgetspec-github-action telemetry`. The interval runs from cold install through the
  checks, the merged-run and history reads and the main result upload, so it ends before the
  comment post; the check of this budget and the saving of its verdict come after it, and
  queueing and runner setup are outside it. Only that job holds the telemetry, so the `report-job` project's `budgets`
  target leaves it out with `--exclude-label ci-report`, and it is enforced in the job.
- **The history.** Each push to main saves the same result artifact and, when it measured at
  least one budget, one rolling file, and a pull request compares with it. A push that measured
  nothing saves no rolling file and says so, and the next push reads the one before it. Every
  budget compares the same way, with no baseline per kind of budget, and the action's README
  states how many samples a comparison needs.

**The policy** generated repositories carry has one statement,
`assets/budgets/budget-policy.md`, which `--ci-cd` adds to `AGENTS.md`.
Approved thresholds alone decide failures, and the stored measurements are informational, so
an improvement is never flagged and a stored figure never becomes a line to hold.

## GitHub Actions budget figures

The figures CI and CD time budgets need — the longest check, the jobs per pull
request and per release, a change's time from its final checks to its release,
and what reading them costs — are measured the same way in every repository by
one script this skill owns,
[`assets/budgets/actions-budget-figures.sh`](../../assets/budgets/actions-budget-figures.sh).
onevcs's read-only `onevcs host` queries select the runs, jobs and pull requests;
the script only applies the counting rules and the arithmetic. This section is
the contract every repository's budget nodes name. The usage block below is the
script's own header usage, line for line; `test_actions_budget_figures.py` fails
when the two differ.

```
scripts/actions-budget-figures.sh longest-job --stage pr|cd [--release TAG] [--repo OWNER/NAME] [--now TIMESTAMP] [--exclude-workflow NAME]...
scripts/actions-budget-figures.sh job-count   --stage pr|cd [--release TAG] [--repo OWNER/NAME] [--now TIMESTAMP] [--exclude-workflow NAME]...
scripts/actions-budget-figures.sh release-cycle-time        [--release TAG] [--repo OWNER/NAME] [--now TIMESTAMP] --published-paths FILE
scripts/actions-budget-figures.sh release-read-requests     [--release TAG] [--repo OWNER/NAME] [--exclude-workflow NAME]...
scripts/actions-budget-figures.sh pr-read-requests          [--release TAG] [--repo OWNER/NAME]
scripts/actions-budget-figures.sh cache-use                 [--release TAG] [--repo OWNER/NAME]
scripts/actions-budget-figures.sh cache-headroom [--family PREFIX]... [--repo OWNER/NAME]
```

- **A `measure: reported` budget's `command`.** It writes `{"value", "detail"}`
  to the file `ONEBUDGETSPEC_RESULT` names and exits 0, printing the detail to
  stderr too. With `ONEBUDGETSPEC_RESULT` unset it exits 2 saying to run it under
  `onebudgetspec check`, writing nothing.
- **A pull request's own run.** When `GITHUB_EVENT_NAME` is `pull_request` and
  `GITHUB_RUN_ID` and `GITHUB_REPOSITORY` are set, `longest-job --stage pr` and
  `job-count --stage pr` read that run's jobs with one `gh api
  repos/R/actions/runs/ID/jobs` read (paging only past 100 jobs), needing `gh`
  rather than onevcs and no release, so a `--release` is not read. The counting
  rules are the same, except that a job still in progress, which is the budgets
  job itself, counts toward the job count and not toward the longest job. The
  detail is one unheaded block. A failed read or a listing shorter than its
  `total_count` ends the figure (exit 1). Every other invocation, the release
  tail included, is unaffected by the rest of this item and reads as below.
- **What it needs.** bash, jq, and `onevcs` on `PATH` at a release carrying
  `onevcs host` (`ONEVCS` may name the binary). It reads only those answers and:
  the token variables, passed to onevcs untouched; `GITHUB_REPOSITORY`, the
  default `--repo`; `GITHUB_RUN_ID` and `RUNNER_NAME`, for the job running it;
  `GITHUB_EVENT_NAME`, for a pull request's own run; `GITHUB_EVENT_PATH`;
  `GITHUB_REF_TYPE` and `GITHUB_REF_NAME`.
- **Saved answers.** Each answer is saved whole under
  `$RUNNER_TEMP/actions-budget-figures/<OWNER>__<NAME>/<TAG>/` (the tag
  percent-encoded, so `release/v1` and `release_v1` never share one), one file per
  query and argument, before it is used, and every later figure in the job reads
  it, so a release tail makes each read once. A failed read saves nothing. With
  `RUNNER_TEMP` unset nothing is saved; nothing is shared across jobs.
- **Which release.** `--release TAG`; else a `release` payload's
  `release.tag_name`; else `GITHUB_REF_NAME` when `GITHUB_REF_TYPE` is `tag`;
  else exit 1 naming `--release`. `--now` defaults to the moment it starts, in
  RFC 3339 UTC.
- **A counted job** is `completed`, not `skipped`, with a `runner_name` (a job
  cancelled before a runner took it is not), lasting `completed_at − started_at`,
  never below 0. The job running the script — run `GITHUB_RUN_ID`'s
  `in_progress` job on runner `RUNNER_NAME` — counts too, timed to `--now`; no
  other running job does.
- **Selections.** The release is `onevcs host release-changes --tag`, read once.
  `--stage cd` takes `commit-runs --commit <release commit> --event push --event
  release`, plus run `GITHUB_RUN_ID` when not among them, plus a `workflow_run`
  payload's `workflow_run.id`, each run's jobs from `run-jobs`; nothing is
  attributed by a timing window. `--stage pr` takes each change's runs and jobs
  from `change-checks --change --head --merged-at`, and the figure is the largest
  over the pull requests.
- **Delivery only: `--exclude-workflow NAME`.** A push workflow on the default
  branch runs at the release commit whether or not it delivers anything, so the
  CD figures (`--stage cd` and `release-read-requests`) leave out every run of a
  workflow `--exclude-workflow` names, by the `workflow_name` onevcs's answers
  carry; the option repeats, and its jobs are never read, nor added back as
  `GITHUB_RUN_ID` or a payload's run. A repository passes its push workflows that
  make and publish nothing — a cache warmer among them — in its CD budgets'
  commands, and never the release-plz or
  semantic-release run or the release workflow it triggers. The script names no
  workflow of its own: without the option every push or release run counts.
  With the option given, a run at the release commit whose `workflow_name` is
  missing or empty ends the figure (exit 1), since it could not be told apart.
  `--stage pr` refuses the option (exit 2), and the detail of `longest-job` and
  `job-count` ends with one `left out by --exclude-workflow: <workflow> (run
  <id>, <event>)` line per run it left out.
- **Delivery only: a run concluded `skipped` ran nothing.** A run the
  `commit-runs` listing reports concluded `skipped` (every job skipped by its
  condition), whatever its workflow, counts toward no CD figure: its jobs are
  never read, so it costs `release-read-requests` no request and changes no job
  count or longest job, nor is it added back as `GITHUB_RUN_ID` or a payload's
  run. A run that concluded any other way — `success`, `failure`, `cancelled` —
  is read and counted, as is one still running (a null conclusion). A run whose
  `conclusion` is absent or neither a string nor null ends every CD figure (exit
  1), since a skipped run could not be told apart. The detail of `longest-job`
  and `job-count` ends with one
  `left out as concluded skipped: <workflow> (run <id>, <event>)` line per such
  run, after the `--exclude-workflow` lines.
- **Figures.** `longest-job` (seconds): the longest counted job; detail lists
  every counted job as `<workflow> / <job name>: <s>s`, longest first, then the
  longest per runner OS. `job-count` (jobs): counted jobs, each matrix leg one;
  detail by workflow and runner OS. Under `--stage pr` each detail is one block
  per pull request headed `#<number>`. `release-cycle-time` (seconds): per
  published change, selected by matching its local merge-commit diff to the required
  `--published-paths FILE` git pathspecs (one per line, ignoring blank and comment
  lines), from the earliest `created_at` of its runs to the latest end of their
  counted jobs, plus `merged_at` to `--now`; detail `#<n>: checks <s>s + merge to
  release <s>s`, followed by `#<n>: left out, changes no published path` for
  every excluded PR. A missing, unreadable or empty paths file, absent merge
  commit/parent (checkout needs `fetch-depth: 0`), or no published change is
  missing data, exit 1. Missing `--published-paths` is usage, exit 2.
  `release-read-requests` (requests): the `cost.rest_requests` of
  the release's `release-changes`, `commit-runs` and `--stage cd` `run-jobs`
  answers; `pr-read-requests` (requests): the most one pull request's
  `change-checks` answer cost. Their detail lists every request under the query
  that made it, read from the answers' `cost`, so saved answers report what each
  read cost when it was made.
- **Missing data is an error, never 0.** It exits 1 with `missing: …` and a
  `next: …` line saying what to do about it, writing nothing, when the release
  commit has no `push` or `release` run (or every one concluded `skipped` or is
  a workflow `--exclude-workflow` names), a commit maps to no merged pull request
  (`change: null`: a rebase-merged repository is refused), a pull request has no
  `pull_request` run, a selected run lists no job, or `longest-job` has no
  counted job. A run whose listed jobs were all skipped
  counts 0. A non-zero onevcs exit ends it with exit 1 and onevcs's reason.
  `onebudgetspec check` records either as the budget's `error` (exit 3).

- **`cache-headroom` (gigabytes, a minimum).** Like `cache-use` it runs in
  `nx-remote-cache.mjs` and needs node, not onevcs, reading `GH_TOKEN` or
  `GITHUB_TOKEN`, `GITHUB_API_URL` and `GITHUB_REPOSITORY`. It reads
  `actions/cache/storage-limit`, then every page of `actions/caches` sorted by
  `created_at`, because a small cache can sit below any number of results. GitHub sorts by
  no unique field and reorders ties between pages, so when those pages fall short of the
  total it reads the listing again narrowed by `key` prefix, until each answer holds its
  whole total in one response, counting entries by id. A listing still short of its total,
  such as over 100 entries of one key that the reads never all show, writes no figure. It never reads `actions/cache/usage`, measured lagging the listing
  by up to 715 MB. The figure is the allowance less the bytes of every entry, results
  included because they take real capacity, in gigabytes of 1,000,000,000 bytes. The
  allowance is `max_cache_size_gb` × 1,073,741,824 bytes, since GitHub's line is 10 GiB
  (10.737 of these gigabytes) and not 10 GB. A job holding `actions: read`, as every
  pull-request `budgets` job and the release tail does, is answered 403 on the storage
  limit, so a 403 there uses GitHub's stated 10 GiB (10,737,418,240 bytes). A family `--family` names counts only its
  newest entry, its older copies' bytes going to the detail; every other entry counts in
  full. A named family holding more than two entries reads 0, naming it in the detail,
  because two is the most a working cleanup leaves. Bytes above the line read negative,
  an over-budget reading and no error: a listing can stand above the line for minutes
  before anything is removed. The detail gives the allowance and its source, non-result bytes per family
  (the key without its trailing hashes), result entries and bytes, the oldest result's
  age, the requests made and the lowest `X-RateLimit-Remaining` any response carried,
  since these endpoints draw a core counter `gh api rate_limit` does not report. Any other
  failed read, a listing's 403 or a storage limit's 429 among them, writes nothing and
  exits 1.

**The copy rule.** A repository copies the script to
`scripts/actions-budget-figures.sh` and names that copy as its budgets'
`command`; nothing fetches it at run time. Its header says it is edited only
here and carries `# source:`, `# version:` and `# sha256:` (the digest of every
line after the header), and
[`actions-budget-figures.versions`](../../assets/budgets/actions-budget-figures.versions)
lists every version with its digest. A change to the body adds the next version
there, which the skill's tests enforce against what is released. The
baseline checker reports a copy whose body does not match its `sha256` line
(edited in place), whose version and digest the versions file does not list
(unknown), or that is older than the source (outdated, naming both versions);
the fix is always to copy the source again.

## The CI and CD budgets every repository starts with

`compose_repo_plan.py --ci-cd` writes the root `budgets.yaml` from
[`assets/budgets.yaml.template`](../../assets/budgets.yaml.template): the ten
budgets, the eight established by onepipeline, onetaskgraph and onepipeline-ui
plus the shared-cache replay and cache headroom figures, each id under the
project name the composer fills in. Every CI check and CD job is held to 30
minutes and a change's last-commit pull-request checks plus merge to release to
90; job counts are budgeted per pull-request push and per release because the
account's 20 concurrent jobs are shared.

| id | unit | threshold | label | command |
| --- | --- | --- | --- | --- |
| `<name>-ci-check-time` | seconds | 1800 | `release-tail` | `bash scripts/actions-budget-figures.sh longest-job --stage pr` |
| `<name>-cd-job-time` | seconds | 1800 | `release-tail`, `not-measured-on-pr` | `bash scripts/actions-budget-figures.sh longest-job --stage cd --exclude-workflow ci` |
| `<name>-release-cycle-time` | seconds | 5400 | `release-tail`, `not-measured-on-pr` | `bash scripts/actions-budget-figures.sh release-cycle-time --published-paths .github/release-published-paths` |
| `<name>-ci-job-count` | jobs | the CI jobs the shape runs: 5 cross-platform, 6 with a second visual-docs architecture, 3 Linux-only | `release-tail` | `bash scripts/actions-budget-figures.sh job-count --stage pr` |
| `<name>-cd-job-count` | jobs | 6 cross-platform, 4 Linux-only | `release-tail`, `not-measured-on-pr` | `bash scripts/actions-budget-figures.sh job-count --stage cd --exclude-workflow ci` |
| `<name>-release-read-requests` | requests | 6 | `release-tail`, `not-measured-on-pr` | `bash scripts/actions-budget-figures.sh release-read-requests --exclude-workflow ci` |
| `<name>-pr-read-requests` | requests | 2 | `release-tail`, `not-measured-on-pr` | `bash scripts/actions-budget-figures.sh pr-read-requests` |
| `<name>-nx-cache-use` | percent | 100 (minimum) | `release-tail`, `not-measured-on-pr` | `bash scripts/actions-budget-figures.sh cache-use` |
| `<name>-cache-headroom` | gigabytes | 3.05 (minimum) | `release-tail` | `bash scripts/actions-budget-figures.sh cache-headroom`, with a `--family` per warm leg |
| `<name>-gate-time` | seconds | 1800 | `host-variable`, `not-measured-on-pr` | `node scripts/gate-time-budget.mjs` |

A push workflow delivering nothing is passed to the CD budgets' commands with
`--exclude-workflow`. A Rust composition has one, its warm workflow, so its three CD
commands name `"rust cache warm"` as well as `ci`.

`<name>-cache-headroom`'s 3.05 is onetaskgraph's measured figure, which a new repository
starts from; once its warm runs have saved, replace it by one whole warm run's saves plus
each family's largest growth. A Rust composition's command names the family each warm leg
cleans up, and a TypeScript one names none.

- **Where each is enforced.** `timeout-minutes: 30` holds every CI job at pull-
  request time, and `budgets` reports the two CI-time figures from its own run and the
  cache headroom, and omits the seven labelled `not-measured-on-pr`. The nine `release-tail` budgets are the last step of
  release.yml's `publish` — `onebudgetspec check budgets.yaml --label
  release-tail`, after it installs `onevcs-cli` at the version the skill pins —
  whose result nothing reads. `<name>-gate-time` is `just gate`'s own wall clock,
  recorded by `scripts/gate-time.sh` to `.gate-time.json` (gitignored) and read
  back by `scripts/gate-time-budget.mjs` through the SDK: over its line it warns
  and the gate passes (a development machine's timing varies too much to refuse
  on); a missing record or an invalid budgets file fails it.
- **The strict check leaves both labels out.** The `budgets` recipe `check` runs
  passes `--exclude-label release-tail --exclude-label host-variable`, so the
  gate neither reads GitHub nor fails on a machine's speed.
- **Cut work, never shard.** A figure over its line is met by affected-only
  selection, caches keyed on their inputs, no check run twice and no needless
  serialization; a coverage-reducing alternative is the owner's decision.
- **`llmlint: ignore[budgets_track_product_owner_outcomes]`** sits on each of the
  eight existing operational budgets, with its reason: the brief fixes these units — a line per check and per
  job beside the cycle that contains them, job counts, GitHub reads — rather than
  one product-owner outcome.

## Verification

- [ ] **Pinned.** `@onebudgetspec/cli` (or `onebudgetspec-cli` in a uv project)
  is pinned to one exact version and recorded in the lockfile.
- [ ] **Every `budgets.yaml` is reached by `check`.** Each project's file through
  its `budgets`/`budgets-host` targets in the affected run; the root file on every
  run. `check_repo_baseline.py` reports any file nothing reaches.
- [ ] **Wired.** `compose_repo_plan.py --wiring <repo>` has run: the
  `budgets`/`budgets-host` target defaults are in `nx.json` and `check` depends
  on the `budgets` recipe, at its tier.
- [ ] **The lint rules are adopted.** `llmlint.yml` lists the
  `tools/onebudgetspec.llmlint.yml@1` URL.
- [ ] **One project per budget domain**, its `budgets.yaml` at the root of the
  smallest tree holding everything specific to its measurements.
- [ ] **Measurements report and stop.** Each uses its SDK's `report`, none reads
  `budgets.yaml` or asserts a threshold, and no budget has a wrapper script of
  its own.
- [ ] **Cached by kind.** Deterministic `reported` budgets run in the cached
  `budgets` target; `elapsed` and host-reading ones carry the `host` label.
- [ ] **The ten CI/CD budgets are registered.** The root `budgets.yaml`
  carries them as the table above states, the `release-tail` nine run by
  `publish`'s last step and `<name>-gate-time` by `just gate`; the checker
  reports a missing one, or budgets naming the figures script with no copy.
- [ ] **Actions figures are the skill's copy.** Any `scripts/actions-budget-figures.sh`
  is an unedited copy of the source at its latest version; the checker reports
  one that is not.
- [ ] **Figures come from existing tests where they can**, each standalone
  measurement covering a behaviour no gate exercises.

## Shared Nx cache

Copy the sources unchanged: [nx-remote-cache.mjs](../../assets/ci/nx-remote-cache.mjs),
[nx-version-free-inputs.mjs](../../assets/ci/nx-version-free-inputs.mjs) and, where a
warm workflow runs, [actions-cache-prune.mjs](../../assets/ci/actions-cache-prune.mjs).
The source owns the command interface, telemetry schemas, validation and readers;
consult it for fields and statuses rather than maintaining a second contract here.
The adjacent versions files and baseline checker detect edited or stale copies;
`nx-remote-cache.mjs` is at version 10.
The composer reads the sidecar pin and promotion branch pattern from its exports.

Each existing CI job decides trust, starts the pinned sidecar at the workspace
root, waits for its recording proxy, runs Nx and reports its results even after
failure. Only trusted PR reports are uploaded. Forks and Dependabot cannot write
shared entries. Cache outages warn and fall back to local execution; missing
telemetry remains unavailable evidence rather than a successful measurement.
Reports preserve both accepted uploads and remote replay bodies for promotion.

A Rust composition's warm workflow is the only saver of Rust build caches, and each of its
legs deletes its family's superseded copies through `actions-cache-prune.mjs`, holding
`actions: write` while it builds merged source. That risk is accepted because anything merged
can already run in every later job, and it is narrowed: only a push to main runs a leg, its
checkout persists no credentials, and the cleanup step alone is handed the token. GitHub removes the least recently read entries once a save finds
the listing over 10 GiB, so room for a run's saves is what keeps results, and cleanup does
not rescue them: that room is what `<name>-cache-headroom` holds.

Promotion runs before release-plz because the upstream cache only publishes a
shared store from a default-branch push: a PR's own cache scope cannot serve
another PR. The merge job promotes the latest trusted PR run into the base
scope. Manual promotion uses a push to the dedicated branch pattern exported by
the script, naming the source PR. A demonstration PR based on that branch can
read that scope. There is no dispatch write or fabricated GitHub event.
Release-plz's own steps run only on main pushes and authenticate with the release
PAT; the job's default token needs only read access. Promotion failures warn so
release-plz can proceed. Receipts retain failed attempts as well as successful
writes, because later re-execution of an expected result must count as a miss.

The cache-use figure reads production reports and receipts without running tests.
Its eligibility and trust joins are implemented alongside the writers in the
shared script. Local hits and previously unseen hashes are excluded; failed
store or promotion attempts still establish expectations for later matching
executions. Missing or expired required evidence cannot produce a passing figure.
The release-tail budget's full-replay threshold is a design target supported by
the spike's three OS replays, not a measured release baseline. Revisit it after
real release observations. The conservative workload exceeds the spike's upload
ceiling; no rate control is promised, and test results are never deleted:
`actions-cache-prune.mjs` refuses an `nx-cache-` family, and `<name>-cache-headroom`
holds the room left for them.

`executions.json` is schema 2, two additive fields over schema 1, which a reader still
accepts with both unknown. Each task's `remote_read` is what the recording proxy saw:
`hit` (200), `miss` (404), `failed` (any other status, or no answer, the sidecar gone or
the connection dropped) or `not_attempted` (no read). The pinned sidecar answers its own
store faults with a 404, so GitHub answering 500 reads as `miss`, which can only understate
replays. The document's `base_sha` is the first parent of the merge commit the run tested,
null when that cannot be read.

The figure counts `base_moved` too. A release pull request's execution whose task and OS
appear in a carried pull request's latest run at a different hash, where that run's
`base_sha` is not the merge commit's first parent, joins the denominator as a miss: that
run proved the task against a base its merge did not land on. A changed hash without a
moved base stays excluded, as new work. Evidence whose base is unknown stays excluded and
is counted in the detail.

Evidence that ordinary conditions removed is reported rather than refused. An attempt whose
artifacts a whole-workflow re-run removed, and a pull request whose CI predates adoption
(its runs created before the commit adding `scripts/nx-remote-cache.mjs`, read from a full
checkout), are counted as unavailable in the detail, and the figure is taken over what is
measurable. An artifact that is present but invalid still errors, one job's artifact missing
beside another's present still errors, and no measurable execution at all still reports
missing, never a pass.

Version-free ecosystem inputs keep release version and changelog edits from
invalidating proven tests while retaining dependency and source changes. Targets
that inspect versions keep them in their inputs. Separate ecosystem digests avoid
cross-ecosystem invalidation, and platform inputs prevent cross-OS replay.
Unparseable files retain their raw content digest instead of a constant.
