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
scripts/actions-budget-figures.sh release-cycle-time        [--release TAG] [--repo OWNER/NAME] [--now TIMESTAMP]
scripts/actions-budget-figures.sh release-read-requests     [--release TAG] [--repo OWNER/NAME] [--exclude-workflow NAME]...
scripts/actions-budget-figures.sh pr-read-requests          [--release TAG] [--repo OWNER/NAME]
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
  change, from the earliest `created_at` of its runs to the latest end of their
  counted jobs, plus `merged_at` to `--now`; detail `#<n>: checks <s>s + merge to
  release <s>s`. `release-read-requests` (requests): the `cost.rest_requests` of
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
- [ ] **Actions figures are the skill's copy.** Any `scripts/actions-budget-figures.sh`
  is an unedited copy of the source at its latest version; the checker reports
  one that is not.
- [ ] **Figures come from existing tests where they can**, each standalone
  measurement covering a behaviour no gate exercises.
