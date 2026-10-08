# Tool: onebudgetspec (measured budgets)

Applies when the repo gates on measurable budgets — request counts, query counts,
cold-start time, gate wall clock — registered with
[onebudgetspec](https://github.com/nickderobertis/onebudgetspec). It is opt-in:
`compose_repo_plan.py --tool onebudgetspec` composes this reference and adopts the
budget lint rules, and `--wiring REPO_DIR` applies the setup step below. A repo
that does not opt in gets none of it.

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

Wiring and pinning are a setup step, checked deterministically by
`check_repo_baseline.py`, never a judged rule. `--wiring REPO_DIR` performs it:

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
and the two rules of "When is a requirement a budget" above.

## Verification

- [ ] **Pinned.** `@onebudgetspec/cli` (or `onebudgetspec-cli` in a uv project)
  is pinned to one exact version and recorded in the lockfile.
- [ ] **Every `budgets.yaml` is reached by `check`.** Each project's file through
  its `budgets`/`budgets-host` targets in the affected run; the root file on every
  run. `check_repo_baseline.py` reports any file nothing reaches.
- [ ] **The lint rules are adopted.** `llmlint.yml` lists the
  `tools/onebudgetspec.llmlint.yml@1` URL.
- [ ] **One project per budget domain**, its `budgets.yaml` at the root of the
  smallest tree holding everything specific to its measurements.
- [ ] **Measurements report and stop.** Each uses its SDK's `report`, none reads
  `budgets.yaml` or asserts a threshold, and no budget has a wrapper script of
  its own.
- [ ] **Cached by kind.** Deterministic `reported` budgets run in the cached
  `budgets` target; `elapsed` and host-reading ones carry the `host` label.
- [ ] **Figures come from existing tests where they can**, each standalone
  measurement covering a behaviour no gate exercises.
