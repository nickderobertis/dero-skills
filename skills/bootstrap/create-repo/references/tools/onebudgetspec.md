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
[README at the pinned release](https://github.com/nickderobertis/onebudgetspec/blob/v0.1.3/README.md)
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

- **`budgets`** — `onebudgetspec check <project>/budgets.yaml --exclude-label host`,
  the deterministic budgets, cached (see caching below). When a test records the
  telemetry it analyses, it `dependsOn` that test.
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
```

- **A `measure: reported` budget's `command`.** It writes `{"value", "detail"}`
  to the file `ONEBUDGETSPEC_RESULT` names and exits 0, printing the detail to
  stderr too. With `ONEBUDGETSPEC_RESULT` unset it exits 2 saying to run it under
  `onebudgetspec check`, writing nothing.
- **What it needs.** bash, jq, and `onevcs` on `PATH` at a release carrying
  `onevcs host` (`ONEVCS` may name the binary). It reads only those answers and:
  the token variables, passed to onevcs untouched; `GITHUB_REPOSITORY`, the
  default `--repo`; `GITHUB_RUN_ID` and `RUNNER_NAME`, for the job running it;
  `GITHUB_EVENT_PATH`; `GITHUB_REF_TYPE` and `GITHUB_REF_NAME`.
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
[`assets/budgets.yaml.template`](../../assets/budgets.yaml.template): the nine
budgets, the eight established by onepipeline, onetaskgraph and onepipeline-ui
plus the shared-cache replay figure, each id under the
project name the composer fills in. Every CI check and CD job is held to 30
minutes and a change's last-commit pull-request checks plus merge to release to
90; job counts are budgeted per pull-request push and per release because the
account's 20 concurrent jobs are shared.

| id | unit | threshold | label | command |
| --- | --- | --- | --- | --- |
| `<name>-ci-check-time` | seconds | 1800 | `release-tail` | `bash scripts/actions-budget-figures.sh longest-job --stage pr` |
| `<name>-cd-job-time` | seconds | 1800 | `release-tail` | `bash scripts/actions-budget-figures.sh longest-job --stage cd` |
| `<name>-release-cycle-time` | seconds | 5400 | `release-tail` | `bash scripts/actions-budget-figures.sh release-cycle-time --published-paths .github/release-published-paths` |
| `<name>-ci-job-count` | jobs | the CI jobs the shape runs: 4 cross-platform, 5 with a second visual-docs architecture, 2 Linux-only | `release-tail` | `bash scripts/actions-budget-figures.sh job-count --stage pr` |
| `<name>-cd-job-count` | jobs | 6 cross-platform, 4 Linux-only | `release-tail` | `bash scripts/actions-budget-figures.sh job-count --stage cd` |
| `<name>-release-read-requests` | requests | 6 | `release-tail` | `bash scripts/actions-budget-figures.sh release-read-requests` |
| `<name>-pr-read-requests` | requests | 2 | `release-tail` | `bash scripts/actions-budget-figures.sh pr-read-requests` |
| `<name>-nx-cache-use` | percent | 100 (minimum) | `release-tail` | `bash scripts/actions-budget-figures.sh cache-use` |
| `<name>-gate-time` | seconds | 1800 | `host-variable` | `node scripts/gate-time-budget.mjs` |

A repository that adds a push workflow delivering nothing (a cache warmer) passes
it to the CD budgets' commands with `--exclude-workflow`, as the three
repositories do; the composed shape has none, so its commands name none.

- **Where each is enforced.** `timeout-minutes: 30` holds every CI job at pull-
  request time. The eight `release-tail` budgets are the last step of
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
- [ ] **The nine CI/CD budgets are registered.** The root `budgets.yaml`
  carries them as the table above states, the `release-tail` eight run by
  `publish`'s last step and `<name>-gate-time` by `just gate`; the checker
  reports a missing one, or budgets naming the figures script with no copy.
- [ ] **Actions figures are the skill's copy.** Any `scripts/actions-budget-figures.sh`
  is an unedited copy of the source at its latest version; the checker reports
  one that is not.
- [ ] **Figures come from existing tests where they can**, each standalone
  measurement covering a behaviour no gate exercises.

## Shared Nx cache

The verbatim sources are `assets/ci/nx-remote-cache.mjs` and
`assets/ci/nx-version-free-inputs.mjs`, each version 1. Their headers digest
every byte after the sha256 line; their adjacent `.versions` files and the
baseline checker detect stale or edited copies. `OP_NX_COMMIT` exports the
full pinned op-nx commit; the composer reads it for every sidecar step.

`node scripts/nx-remote-cache.mjs trust` reads the actual event payload,
repository and PR author, and writes `read_only` and `promotable` to
GITHUB_OUTPUT. Forks and Dependabot are read-only and not promotable; other
same-repository PRs are promotable; other events are not. The workflow translates
read_only true to a nonempty CACHE_READ_ONLY; false leaves it empty, because the
pinned library treats even the string "false" as read-only.

`ready [--timeout-seconds N]` defaults to 60 seconds. An unauthenticated 401
proves liveness without consulting the store. It starts a loopback recording
proxy before appending NX_SELF_HOSTED_REMOTE_CACHE_SERVER and its access token
to GITHUB_ENV. Failure appends neither, warns and exits 0, leaving Nx local.
The proxy forwards cache requests unchanged and records GET/PUT statuses and
accepted bodies. A failed PUT receives a nonfatal status so a passing task stays
passing. No cache inventory or deletion is used.

`report --job NAME --out DIR` runs after the existing Nx commands, always.
It stops the proxy and writes:

- `manifest.json`: schema 1, job, entries with hash, bytes and source
  (`stored` or `replayed`), and stored_bytes. Each `<hash>.tar` is the
  exact accepted Nx request body or the exact body served from the remote.
  Accepted uploads and replays are both retained for later promotion.
- `executions.json`: schema 1, repository, ref, pr (integer or null),
  head_sha, run_id, attempt, job, os (`process.platform`), tasks and
  store_attempts. Each task has task identifier, exact hash, cacheable, result
  (`remote_hit`, `local_hit`, `executed`, `failed`), remote_status,
  started_at and finished_at (RFC3339 UTC from Nx task timing).
  `selection` records mode affected with base_sha/head_sha, the exact
  NX_BASE/NX_HEAD values compared; non-affected all-project runs record mode all.
  PR workflows explicitly select the event's base/head SHAs.
- Store attempts have task, hash, os, attempted_at (RFC3339 UTC), status
  (`stored`, `throttled`, `failed`), and nullable http_status. Every PUT
  is recorded before forwarding and updated with its result. A successful
  cacheable execution with no usable remote records a failed/null attempt.
  Failed tasks never prove reusable results.

The same script exports Nx pre/post execution hooks, registered in nx.json.
They append one record per existing invocation, so later commands cannot
overwrite earlier evidence. Hooks only collect telemetry: failures warn and
never alter Nx's exit status or run a task again. Missing or incomplete
invocations produce unavailable evidence, never an empty success. Remote hits
require an observed GET 200 joined to Nx's own result by hash; terminal wording,
local hits and passing tasks never stand in for a remote hit. Distinct task
identifiers sharing a hash remain distinct executions. The reporter logs and
summarizes stored entry count and bytes separately from replay count, and
exits 0 for operational faults (2 for usage).

`promote (--sha SHA | --pr N) --workflow FILE [--repo OWNER/NAME]` runs in the
existing release-plz job after the sidecar/ready and before release-plz.
On main it resolves the merged PR from the commit's associated pulls; on a push
to `cache-promote/<PR>` it validates the branch's named PR.
PROMOTION_BRANCH_PATTERN is defined by the shared script, and the composer uses
that constant in the trigger. The pinned sidecar deliberately denies dispatch
writes: there is no workflow_dispatch trigger or fabricated GitHub event.
Release-plz itself runs only on main pushes.

Promotion admits only same-repository, non-Dependabot sources. It selects the
latest successful PR run on the head, validates affected selection against the
source run's base/head, downloads every expected job's seven-day report artifact,
and writes each distinct accepted or replayed body through the sidecar's own
`PUT /v1/cache/<hash>` API into the current readable scope. Source entries
are retained. No source, untrusted source, missing/expired artifacts or a GitHub
or store fault warns and exits 0 so release-plz still runs; usage exits 2.
The job has actions:read and uploads `nx-cache-promotion-<merge_sha>` from
RUNNER_TEMP/nx-cache-promotion. Its receipt.json schema 1 has repository, scope,
merge_sha, pr, source_run_id, source_attempt, promoted_at (RFC3339 UTC),
entries (task/hash/os and promoted or already_present status), and attempts.
Each attempt has task/hash/os, attempted_at, status (promoted, already_present,
throttled, failed or never_promoted), and nullable http_status. Only accepted or
confirmed existing entries enter entries; all known source expectations enter
attempts, including recorded failures. Missing source evidence remains missing
measurement.

`cache-use [--release TAG] [--repo OWNER/NAME]` reads only release metadata
and the counted PRs' run/artifact evidence through paginated read-only GitHub API
calls, memoized under RUNNER_TEMP for the job. It never runs tests or a sidecar.
The observation interval starts after the previous release's published_at and
ends at this release's published_at, inclusive. With no previous release it
starts at the first CI run. A run completing after the end is left out and named.
The release PR contributes its final successful pre-merge CI attempt and later
same-PR reruns in the interval; ordinary counted PRs contribute retries and later
pushes. Earlier attempts establish expectations.

Deduplicate (run_id, attempt, job, task, os, hash). A cacheable execution is eligible
only if a matching task/OS/hash had a preceding store attempt in an earlier run
of the same PR, or in the merged PR's latest run or its promotion into the readable
base scope. A validated replayed body in that latest run establishes the same
expectation as a stored body: without a recorded promotion, a later matching
re-execution is never_promoted; after promotion, a 404/re-execution is evicted. Attempts, including failed/null and throttled writes, establish
eligibility; successful receipts are not a precondition. The execution's own
store cannot qualify it. New hashes and local hits are outside both numerator
and denominator. A qualifying GET 200 remote replay counts as a hit even after
an earlier failed store; re-execution, failure and remote unavailability count
as misses. The value is 100 * remote_hits / eligible_executions.

Detail includes aggregate and separate release-PR and same-PR retry/push rates,
every miss's task/OS/hash and age since attempt, raw statuses and causes:
429 throttled; transport/5xx or disabled store failed; accepted/promoted then
404 evicted; no promotion attempted never_promoted. It reports skipped local
hits, unavailable artifacts, promotion failures, stored bytes and request counts.
Repository, PR, source head/run/attempt, selection, OS and readable scope are
validated against GitHub metadata; artifact names alone confer no trust. GitHub
can return empty run.pull_requests after merge. In that case the reader uses
commits/{head_sha}/pulls to associate the same trusted merged PR/head repository
and author, then verifies selection.base_sha is an ancestor of the merge's first
parent through compare. An unassociable run is missing data named by run/attempt,
not silently omitted. These extra read-only responses use the same memoization. The
writer and reader schemas are owned and validated by the same script. The pinned
sidecar can turn hidden throttling or service outages into cache-API 409
(save failed, then its existence probe found nothing) or 500. See the
[pinned backend's save/probe handling](https://github.com/op-nx/github-cache/blob/91839c24885842cac86567c30e23b37fb85f7e33/packages/github-cache/src/backend/actions-cache-backend.ts#L376).
Source store_attempts records a 409 as failed with its raw status. Only promotion
can report already_present, after a confirming GET of the same hash/body.
Only a directly observed cache-API 429 is labelled throttled; statuses are never
guessed from logs. Zero
eligible executions, invalid schema, missing expected job/attempt telemetry and
expired required artifacts exit 1 without a figure; usage exits 2.

The composed `<name>-nx-cache-use` budget is reported percent, direction min,
threshold 100, labelled release-tail. This is a design target supported by the
spike's three successful OS replays, not a measured release baseline; revisit it
after real release-tail observations. At the conservative envelope there are
2,540 stores plus 2,540 promotion writes in a minute, 25.4 times the spike's
200 uploads/minute ceiling. There is no claim of aggregate quota headroom and
no second budget, rate-control job or deletion mechanism.

`node scripts/nx-version-free-inputs.mjs --ecosystem cargo|npm|python|files
[--keep-version]` prints one sha256 of tracked paths and contents. Cargo covers
Cargo.toml/Cargo.lock; npm covers package.json and bun/package-lock/pnpm locks;
Python covers pyproject.toml/uv.lock; files reads the tracked literal version
files named by .github/nx-version-files. Only workspace packages' own version
fields and their own lock entries are blanked, or each current workspace-version
occurrence in literal files. Changelogs are never read. Keep-version blanks
nothing for tests that read the version. Unparseable input warns and hashes the
same raw files, never a constant, exiting 0 because Nx hashes runtime stdout
regardless of exit status. Ecosystem digests prevent a Cargo edit from invalidating
npm-only targets; OS runtime inputs prevent cross-platform replay.
