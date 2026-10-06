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
one project. In a Rust workspace, `intersections/rust-cli.md` ("Journeys live
with the crate they exercise") states the layout: an e2e member on the terms of
the buildout rule `binary_e2e_is_its_own_crate`, one per crate exercised.

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
descriptions, tree scope, the single judge, direct commands and telemetry reuse.

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
