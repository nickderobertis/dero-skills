"""Judged proof that the onebudgetspec llmlint rules rule the right way.

Each case writes a small consumer repo to disk — a `budgets.yaml`, the code its
command runs, and an `llmlint.yml` adopting the in-tree fragment as a plugin, the
way a budgets-registering repo adopts it by URL — then runs the real `llmlint`
over it with one rule selected and reads the verdict from its JSON report. Every
true and false clause a rule states gets a tree of its own, so each verdict is
proven independently; the minimal-tree rule also gets the non-pedantic case, a
measuring script that calls a shared harness and the code it measures, both
outside the budget's tree.

Nothing is mocked: the judge is the harness `oneharness.toml` selects, which is
why this sits in the `skilltest` project rather than the gate — it needs a
credential and its verdicts are a model's. The cases run concurrently, since each
is one independent judge call. Run with `just skilltest -k onebudgetspec`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2]
FRAGMENT = SKILL / "assets" / "llmlint" / "tools" / "onebudgetspec.llmlint.yml"
# What the composer writes beside a consumer's llmlint.yml: the harness selection.
ONEHARNESS_TEMPLATE = SKILL / "assets" / "oneharness.toml.template"

# `just bootstrap` installs llmlint via `uv tool`, into ~/.local/bin.
_INSTALLED = Path.home() / ".local" / "bin" / "llmlint"
_LLMLINT = str(_INSTALLED) if _INSTALLED.is_file() else shutil.which("llmlint")

pytestmark = [
    pytest.mark.skilltest_e2e,
    pytest.mark.skipif(
        _LLMLINT is None, reason="needs llmlint on PATH (`just setup-llmlint`)"
    ),
]


# llmlint: ignore[contracts_have_one_source_or_a_drift_gate] llmlint publishes no schema for its report's outcome values, and no offline run can emit `pass`, `fail` or `not_relevant` — only a judge does. The drift gate for these three spellings is this module's cases, each of which expects one and fails on a misspelling against the real engine; the report envelope around them is held offline by test_eval_wiring.py.
class Expected(StrEnum):
    """The verdicts these cases expect, spelled as llmlint reports them. Not the
    report's whole vocabulary: any other outcome simply fails to match."""

    PASS = "pass"
    FAIL = "fail"
    NOT_RELEVANT = "not_relevant"


# The baseline every tree varies from: one budget counting the Linear requests a
# full issue sync makes, reported through ONEBUDGETSPEC_RESULT, all under
# packages/linear-sync/. It conforms to all five rules: no test runs the sync,
# so the budget's standalone run is the only gate exercising it.
ROOT = "packages/linear-sync"

_TERSE_DESCRIPTION = (
    "Linear API requests one full issue sync makes. Protects the sync from "
    "sliding back into per-issue fetches that exhaust Linear's rate limit."
)


def _budget(
    command: str,
    *,
    budget_id: str = "linear-sync-requests",
    description: str = _TERSE_DESCRIPTION,
    measure: str = "reported",
    unit: str = "requests",
    threshold: int = 14,
) -> str:
    indented = "\n".join(f"      {line}" for line in description.splitlines())
    return f"""\
  - id: {budget_id}
    description: |
{indented}
    measure: {measure}
    command: {command}
    unit: {unit}
    direction: max
    threshold: {threshold}
"""


def _budgets_yaml(*budgets: str) -> str:
    return "schema_version: 1\nbudgets:\n" + "".join(budgets)


_MEASURE_COMMAND = '["uv", "run", "python", "budgets/measure_sync_requests.py"]'

_MEASURE = '''\
"""Count the Linear requests one full issue sync makes, for onebudgetspec."""

import json
import os
from pathlib import Path

from linear_sync.client import RecordingTransport
from linear_sync.sync import sync_issues

FIXTURE = Path(__file__).parent / "fixtures" / "issues_two_pages.json"

transport = RecordingTransport.replaying(FIXTURE)
sync_issues(transport)
Path(os.environ["ONEBUDGETSPEC_RESULT"]).write_text(
    json.dumps({"value": len(transport.requests)})
)
'''

_ISSUES_FIXTURE = (
    '{"pages": [{"issues": [{"id": "LIN-1"}, {"id": "LIN-2"}], "next": "p2"},'
    ' {"issues": [{"id": "LIN-3"}], "next": null}]}\n'
)

_SYNC = '''\
"""Pull every Linear issue, a page at a time."""


def sync_issues(transport):
    cursor = None
    issues = []
    while True:
        page = transport.post("/graphql", {"query": "issues", "after": cursor})
        issues.extend(page["issues"])
        cursor = page["next"]
        if cursor is None:
            return issues
'''

_CLIENT = '''\
"""A transport that replays recorded pages and keeps every request it served."""

import json


class RecordingTransport:
    def __init__(self, pages):
        self._pages = iter(pages)
        self.requests = []

    @classmethod
    def replaying(cls, path):
        return cls(json.loads(path.read_text())["pages"])

    def post(self, path, body):
        self.requests.append((path, body))
        return next(self._pages)
'''


def _conforming() -> dict[str, str]:
    """Everything the budget needs lives under packages/linear-sync/."""
    return {
        f"{ROOT}/budgets.yaml": _budgets_yaml(_budget(_MEASURE_COMMAND)),
        f"{ROOT}/budgets/measure_sync_requests.py": _MEASURE,
        f"{ROOT}/budgets/fixtures/issues_two_pages.json": _ISSUES_FIXTURE,
        f"{ROOT}/src/linear_sync/sync.py": _SYNC,
        f"{ROOT}/src/linear_sync/client.py": _CLIENT,
    }


def _with(**overrides: str) -> dict[str, str]:
    """The conforming tree with files replaced or added, keyed relative to ROOT."""
    tree = _conforming()
    tree.update({f"{ROOT}/{rel}": body for rel, body in overrides.items()})
    return tree


def _description(text: str) -> dict[str, str]:
    return {"budgets.yaml": _budgets_yaml(_budget(_MEASURE_COMMAND, description=text))}


def _narrative_description() -> dict[str, str]:
    return _with(
        **_description(
            "Base (c9e75a8): 18 requests. Journey 1 fetched the first page (1),\n"
            "journey 2 refetched each issue (14), journey 3 paged comments (3).\n"
            "Lowered to 14 after the batching change in #212."
        )
    )


def _field_restating_description() -> dict[str, str]:
    return _with(
        **_description(
            "Runs budgets/measure_sync_requests.py and reports requests; must "
            "stay at or below a maximum of 14 requests."
        )
    )


def _shared_harness_outside() -> dict[str, str]:
    """The budget's own files live under budgets/linear/; the replay harness it
    drives (also used by the unit tests) and the code it measures live elsewhere."""
    measure = '''\
"""Count the Linear requests one full issue sync makes, for onebudgetspec."""

import json
import os
from pathlib import Path

from linear_sync.sync import sync_issues
from testkit.replay import ReplayTransport

PAGES = Path(__file__).parent / "fixtures" / "issues_two_pages.json"

transport = ReplayTransport.from_file(PAGES)
sync_issues(transport)
Path(os.environ["ONEBUDGETSPEC_RESULT"]).write_text(
    json.dumps({"value": len(transport.requests)})
)
'''
    replay = '''\
"""Replays recorded GraphQL pages and records each request; every suite's harness."""

import json


class ReplayTransport:
    def __init__(self, pages):
        self._pages = iter(pages)
        self.requests = []

    @classmethod
    def from_file(cls, path):
        return cls(json.loads(path.read_text())["pages"])

    def post(self, path, body):
        self.requests.append((path, body))
        return next(self._pages)
'''
    unit_test = """\
from linear_sync.sync import sync_issues
from testkit.replay import ReplayTransport


def test_sync_follows_every_page():
    transport = ReplayTransport(
        [{"issues": [{"id": "A"}], "next": "2"}, {"issues": [{"id": "B"}], "next": None}]
    )
    assert [i["id"] for i in sync_issues(transport)] == ["A", "B"]
"""
    return {
        "budgets/linear/budgets.yaml": _budgets_yaml(
            _budget('["uv", "run", "python", "measure_sync_requests.py"]')
        ),
        "budgets/linear/measure_sync_requests.py": measure,
        "budgets/linear/fixtures/issues_two_pages.json": _ISSUES_FIXTURE,
        "src/linear_sync/sync.py": _SYNC,
        "testkit/testkit/replay.py": replay,
        "tests/test_sync.py": unit_test,
    }


def _budget_only_script_outside() -> dict[str, str]:
    """The budget sits in packages/linear-sync/, but its measuring script and its
    fixture — used by nothing else — sit in the repo-root e2e suite."""
    tree = _conforming()
    del tree[f"{ROOT}/budgets/measure_sync_requests.py"]
    del tree[f"{ROOT}/budgets/fixtures/issues_two_pages.json"]
    tree[f"{ROOT}/budgets.yaml"] = _budgets_yaml(
        _budget('["uv", "run", "python", "../../tests/e2e/linear_budget.py"]')
    )
    tree["tests/e2e/linear_budget.py"] = _MEASURE
    tree["tests/e2e/fixtures/issues_two_pages.json"] = _ISSUES_FIXTURE
    return tree


def _budget_only_fixture_outside() -> dict[str, str]:
    """The measuring script is in the tree, but the fixture only it reads is not."""
    tree = _conforming()
    del tree[f"{ROOT}/budgets/fixtures/issues_two_pages.json"]
    tree[f"{ROOT}/budgets/measure_sync_requests.py"] = _MEASURE.replace(
        'Path(__file__).parent / "fixtures" / "issues_two_pages.json"',
        'Path(__file__).parents[3] / "fixtures" / "linear_sync_budget_pages.json"',
    )
    tree["fixtures/linear_sync_budget_pages.json"] = _ISSUES_FIXTURE
    return tree


def _elapsed_budget() -> dict[str, str]:
    return _with(
        **{
            "budgets.yaml": _budgets_yaml(
                _budget(
                    '["uv", "run", "python", "budgets/sync_fixture_pages.py"]',
                    budget_id="linear-sync-seconds",
                    description=(
                        "Wall clock of one full issue sync against recorded pages. "
                        "Protects the sync loop from quadratic slowdowns."
                    ),
                    measure="elapsed",
                    unit="seconds",
                    threshold=5,
                )
            ),
            "budgets/sync_fixture_pages.py": '''\
"""One full issue sync against recorded pages; onebudgetspec times the run."""

from pathlib import Path

from linear_sync.client import RecordingTransport
from linear_sync.sync import sync_issues

sync_issues(
    RecordingTransport.replaying(
        Path(__file__).parent / "fixtures" / "issues_two_pages.json"
    )
)
''',
        }
    )


def _asserts_threshold() -> dict[str, str]:
    return _with(
        **{
            "budgets/measure_sync_requests.py": _MEASURE
            + """
assert len(transport.requests) <= 14, (
    f"{len(transport.requests)} Linear requests is over the budget of 14"
)
"""
        }
    )


def _reads_budgets_yaml() -> dict[str, str]:
    return _with(
        **{
            "budgets/measure_sync_requests.py": _MEASURE.replace(
                "transport = RecordingTransport.replaying(FIXTURE)\n",
                """\
import yaml

BUDGETS = yaml.safe_load((Path(__file__).parents[1] / "budgets.yaml").read_text())
(BUDGET,) = [b for b in BUDGETS["budgets"] if b["id"] == "linear-sync-requests"]
print(f"measuring {BUDGET['id']} in {BUDGET['unit']}")

transport = RecordingTransport.replaying(FIXTURE)
""",
            )
        }
    )


_RUNNER = '''\
"""Run one measurement module by name and report its figure, for every budget."""

import importlib
import json
import os
import sys
from pathlib import Path

measurement = importlib.import_module(f"measurements.{sys.argv[1]}")
Path(os.environ["ONEBUDGETSPEC_RESULT"]).write_text(
    json.dumps({"value": measurement.measure()})
)
'''


def _generic_runner() -> dict[str, str]:
    runner_command = '["uv", "run", "python", "budgets/run.py", "{name}"]'
    tree = _conforming()
    del tree[f"{ROOT}/budgets/measure_sync_requests.py"]
    tree.update(
        {
            f"{ROOT}/budgets.yaml": _budgets_yaml(
                _budget(runner_command.format(name="sync_requests")),
                _budget(
                    runner_command.format(name="sync_pages"),
                    budget_id="linear-sync-pages",
                    description=(
                        "Issue pages one full sync fetches. Protects against "
                        "shrinking the page size Linear allows."
                    ),
                    unit="pages",
                    threshold=3,
                ),
            ),
            f"{ROOT}/budgets/run.py": _RUNNER,
            f"{ROOT}/budgets/measurements/__init__.py": "",
            f"{ROOT}/budgets/measurements/sync_requests.py": '''\
"""Linear requests one full issue sync makes."""

from pathlib import Path

from linear_sync.client import RecordingTransport
from linear_sync.sync import sync_issues

FIXTURE = Path(__file__).parents[1] / "fixtures" / "issues_two_pages.json"


def measure():
    transport = RecordingTransport.replaying(FIXTURE)
    sync_issues(transport)
    return len(transport.requests)
''',
            f"{ROOT}/budgets/measurements/sync_pages.py": '''\
"""Issue pages one full sync fetches."""

from pathlib import Path

from linear_sync.client import RecordingTransport
from linear_sync.sync import sync_issues

FIXTURE = Path(__file__).parents[1] / "fixtures" / "issues_two_pages.json"


def measure():
    transport = RecordingTransport.replaying(FIXTURE)
    sync_issues(transport)
    return sum(1 for _, body in transport.requests if body["query"] == "issues")
''',
        }
    )
    return tree


def _wrapper(body: str) -> dict[str, str]:
    return _with(
        **{
            "budgets.yaml": _budgets_yaml(
                _budget('["sh", "budgets/linear-sync-requests.sh"]')
            ),
            "budgets/linear-sync-requests.sh": "#!/bin/sh\nset -eu\n" + body,
        }
    )


_WRAPPER_RELISTS_IDS = _wrapper("""\
budget_id=linear-sync-requests
case "$budget_id" in
  linear-sync-requests|linear-write-requests) ;;
  *) echo "unknown budget id: $budget_id" >&2; exit 2 ;;
esac
exec uv run python budgets/measure_sync_requests.py
""")

_WRAPPER_RECHECKS_RESULT = _wrapper("""\
uv run python budgets/measure_sync_requests.py
if [ ! -s "$ONEBUDGETSPEC_RESULT" ]; then
  echo "linear-sync-requests wrote no result" >&2
  exit 1
fi
""")


_JOURNEY_TEST = """\
from pathlib import Path

from linear_sync.client import RecordingTransport
from linear_sync.sync import sync_issues

PAGES = Path(__file__).parent / "fixtures" / "issues_two_pages.json"


def test_sync_pulls_every_issue_across_pages():
    transport = RecordingTransport.replaying(PAGES)
    issues = sync_issues(transport)
    assert [i["id"] for i in issues] == ["LIN-1", "LIN-2", "LIN-3"]
"""

_PROJECT_JSON = """\
{
  "name": "linear-sync",
  "targets": {
    "test": {
      "command": "uv run pytest -n auto tests",
      "options": {"cwd": "packages/linear-sync"},
      "outputs": ["{projectRoot}/.telemetry"]
    },
    "budgets": {
      "command": "onebudgetspec check",
      "options": {"cwd": "packages/linear-sync"},
      "dependsOn": ["test"]
    }
  }
}
"""


def _with_journey_test() -> dict[str, str]:
    """The baseline plus an existing gate: a journey test that already runs a full
    sync over the same recorded pages, in the project's `test` target."""
    return _with(
        **{
            "project.json": _PROJECT_JSON,
            "tests/test_sync_journey.py": _JOURNEY_TEST,
            "tests/fixtures/issues_two_pages.json": _ISSUES_FIXTURE,
        }
    )


def _analyses_test_telemetry() -> dict[str, str]:
    tree = _with_journey_test()
    del tree[f"{ROOT}/budgets/measure_sync_requests.py"]
    del tree[f"{ROOT}/budgets/fixtures/issues_two_pages.json"]
    tree[f"{ROOT}/tests/test_sync_journey.py"] = (
        _JOURNEY_TEST.replace(
            "from linear_sync.sync import sync_issues\n",
            "from linear_sync.sync import sync_issues\nfrom telemetry import record\n",
        )
        + '    record("sync_journey", requests=len(transport.requests))\n'
    )
    tree[f"{ROOT}/tests/telemetry.py"] = '''\
"""Save a journey's figures under .telemetry/, the `test` target's output."""

import json
from pathlib import Path

TELEMETRY = Path(__file__).parents[1] / ".telemetry"


def record(journey, **figures):
    TELEMETRY.mkdir(exist_ok=True)
    (TELEMETRY / f"{journey}.json").write_text(json.dumps(figures))
'''
    tree[f"{ROOT}/budgets.yaml"] = _budgets_yaml(
        _budget('["uv", "run", "python", "budgets/analyse_sync_requests.py"]')
    )
    tree[f"{ROOT}/budgets/analyse_sync_requests.py"] = '''\
"""Report the requests the sync journey test recorded, for onebudgetspec."""

import json
import os
from pathlib import Path

recorded = json.loads(
    (Path(__file__).parents[1] / ".telemetry" / "sync_journey.json").read_text()
)
Path(os.environ["ONEBUDGETSPEC_RESULT"]).write_text(
    json.dumps({"value": recorded["requests"]})
)
'''
    return tree


def _reruns_test_scenario() -> dict[str, str]:
    """The journey test already syncs these pages; the budget syncs them again
    only to count the requests."""
    return _with_journey_test()


def _standalone_cheaper_than_recording() -> dict[str, str]:
    """The journey test runs the sync, but under `pytest -n auto` beside the whole
    suite, where its wall clock measures contention rather than the sync."""
    tree = _with_journey_test()
    timed = _elapsed_budget()
    tree[f"{ROOT}/budgets.yaml"] = timed[f"{ROOT}/budgets.yaml"]
    tree[f"{ROOT}/budgets/sync_fixture_pages.py"] = timed[
        f"{ROOT}/budgets/sync_fixture_pages.py"
    ].replace(
        '"""One full issue sync against recorded pages; onebudgetspec times the run."""',
        '"""One full issue sync against recorded pages; onebudgetspec times the run.\n\n'
        "Timed alone: the journey test shares its gate run with every other test\n"
        "under pytest-xdist, so a duration recorded there would need repeated runs\n"
        'to mean anything — dearer than this one standalone sync."""',
    )
    del tree[f"{ROOT}/budgets/measure_sync_requests.py"]
    return tree


def _shared_fixture_outside() -> dict[str, str]:
    """The measuring script is in the tree; the recorded pages it reads sit in the
    repo-root fixtures/, where a repo-level sync test reads them too."""
    tree = _budget_only_fixture_outside()
    del tree["fixtures/linear_sync_budget_pages.json"]
    tree[f"{ROOT}/budgets/measure_sync_requests.py"] = _MEASURE.replace(
        'Path(__file__).parent / "fixtures" / "issues_two_pages.json"',
        'Path(__file__).parents[3] / "fixtures" / "issues_two_pages.json"',
    )
    tree["fixtures/issues_two_pages.json"] = _ISSUES_FIXTURE
    tree["tests/test_sync_pages.py"] = _JOURNEY_TEST.replace(
        'Path(__file__).parent / "fixtures"', 'Path(__file__).parents[1] / "fixtures"'
    )
    return tree


def _telemetry_outside() -> dict[str, str]:
    """The budget is rooted at budgets/; the recording it analyses was added to
    the existing journey test under tests/, outside that tree."""
    tree = _analyses_test_telemetry()
    del tree[f"{ROOT}/budgets.yaml"]
    tree[f"{ROOT}/budgets/budgets.yaml"] = _budgets_yaml(
        _budget('["uv", "run", "python", "analyse_sync_requests.py"]')
    )
    return tree


def _no_budgets() -> dict[str, str]:
    """A repo registering no budgets whose files still match every rule's globs:
    a release script, a unit test, and a rate limiter named for its budget."""
    return {
        "src/linear_sync/sync.py": _SYNC,
        "src/linear_sync/rate_budget.py": '''\
"""Linear's request budget: a token bucket refilled once a minute."""

import time


class RateBudget:
    def __init__(self, per_minute):
        self.per_minute = per_minute
        self.tokens = per_minute
        self.refilled_at = time.monotonic()

    def take(self):
        if time.monotonic() - self.refilled_at >= 60:
            self.tokens, self.refilled_at = self.per_minute, time.monotonic()
        if self.tokens == 0:
            return False
        self.tokens -= 1
        return True
''',
        "scripts/release.sh": (
            '#!/bin/sh\nset -eu\ngit tag "v$1"\ngit push origin "v$1"\n'
        ),
        "tests/test_rate_budget.py": """\
from linear_sync.rate_budget import RateBudget


def test_refuses_once_spent():
    budget = RateBudget(per_minute=1)
    assert budget.take() is True
    assert budget.take() is False
""",
    }


@dataclass(frozen=True)
class Case:
    rule: str
    fixture: str
    tree: dict[str, str]
    expected: Expected
    # For a FAIL: a file the judge must attribute a violation to.
    culprit: str | None = None


DESCRIPTIONS = "budget_descriptions_durable_and_terse"
MINIMAL_TREE = "budgets_scoped_to_minimal_tree"
ONLY_JUDGE = "onebudgetspec_is_the_only_judge"
DIRECT = "budget_commands_measure_directly"
TELEMETRY = "budgets_reuse_gate_telemetry"

CASES = [
    Case(DESCRIPTIONS, "terse", _conforming(), Expected.PASS),
    Case(
        DESCRIPTIONS,
        "changelog-narrative",
        _narrative_description(),
        Expected.FAIL,
        f"{ROOT}/budgets.yaml",
    ),
    Case(
        DESCRIPTIONS,
        "restates-fields",
        _field_restating_description(),
        Expected.FAIL,
        f"{ROOT}/budgets.yaml",
    ),
    Case(MINIMAL_TREE, "self-contained", _conforming(), Expected.PASS),
    Case(
        MINIMAL_TREE,
        "shared-harness-and-measured-code-outside",
        _shared_harness_outside(),
        Expected.PASS,
    ),
    Case(
        MINIMAL_TREE, "shared-fixture-outside", _shared_fixture_outside(), Expected.PASS
    ),
    Case(
        MINIMAL_TREE,
        "telemetry-in-existing-test-outside",
        _telemetry_outside(),
        Expected.PASS,
    ),
    Case(
        MINIMAL_TREE,
        "budget-only-script-outside",
        _budget_only_script_outside(),
        Expected.FAIL,
    ),
    Case(
        MINIMAL_TREE,
        "budget-only-fixture-outside",
        _budget_only_fixture_outside(),
        Expected.FAIL,
    ),
    Case(ONLY_JUDGE, "reports-figure", _conforming(), Expected.PASS),
    Case(ONLY_JUDGE, "elapsed-run", _elapsed_budget(), Expected.PASS),
    Case(
        ONLY_JUDGE,
        "asserts-threshold",
        _asserts_threshold(),
        Expected.FAIL,
        f"{ROOT}/budgets/measure_sync_requests.py",
    ),
    Case(
        ONLY_JUDGE,
        "reads-budgets-yaml",
        _reads_budgets_yaml(),
        Expected.FAIL,
        f"{ROOT}/budgets/measure_sync_requests.py",
    ),
    Case(DIRECT, "direct", _conforming(), Expected.PASS),
    Case(DIRECT, "generic-runner", _generic_runner(), Expected.PASS),
    Case(DIRECT, "wrapper-relists-ids", _WRAPPER_RELISTS_IDS, Expected.FAIL),
    Case(DIRECT, "wrapper-rechecks-result", _WRAPPER_RECHECKS_RESULT, Expected.FAIL),
    Case(
        TELEMETRY, "analyses-test-telemetry", _analyses_test_telemetry(), Expected.PASS
    ),
    Case(TELEMETRY, "no-gate-exercises-it", _conforming(), Expected.PASS),
    Case(
        TELEMETRY,
        "standalone-cheaper-than-recording",
        _standalone_cheaper_than_recording(),
        Expected.PASS,
    ),
    Case(TELEMETRY, "reruns-test-scenario", _reruns_test_scenario(), Expected.FAIL),
    # Every rule's relevance clause drops matched files no budget reaches.
    *(
        Case(rule, "no-budgets", _no_budgets(), Expected.NOT_RELEVANT)
        for rule in (DESCRIPTIONS, MINIMAL_TREE, ONLY_JUDGE, DIRECT, TELEMETRY)
    ),
]


@dataclass(frozen=True)
class Verdict:
    outcome: str
    violation_files: list[str]
    report: str


def _judge(case: Case, root: Path) -> Verdict:
    root.mkdir(parents=True)
    for rel, body in case.tree.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (root / "llmlint.yml").write_text(f'plugins:\n  - "{FRAGMENT}"\n', encoding="utf-8")
    shutil.copy(ONEHARNESS_TEMPLATE, root / "oneharness.toml")
    subprocess.run(["git", "init", "-q", str(root)], check=True)

    assert _LLMLINT is not None
    result = subprocess.run(
        [
            _LLMLINT,
            "--format",
            "json",
            "--progress",
            "never",
            "--no-history",
            "--rule",
            case.rule,
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return _verdict(case.rule, result)


def _verdict(rule_name: str, result: subprocess.CompletedProcess[str]) -> Verdict:
    """Read one rule's verdict out of llmlint's JSON report, checking its shape."""
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"llmlint printed no JSON report:\n{result.stderr}")
    assert isinstance(report, dict), report
    assert report.get("errors") == [], report.get("errors")
    rules = report.get("rules")
    assert isinstance(rules, list), report
    matches = [r for r in rules if isinstance(r, dict) and r.get("name") == rule_name]
    assert len(matches) == 1, f"no single verdict for {rule_name}: {rules}"
    (rule,) = matches
    violations = rule.get("violations", [])
    assert isinstance(violations, list), rule
    assert all(
        isinstance(v, dict) and isinstance(v.get("file"), str) for v in violations
    ), rule
    assert isinstance(rule.get("outcome"), str), rule
    return Verdict(
        outcome=rule["outcome"],
        violation_files=[v["file"] for v in violations],
        report=json.dumps(rule, indent=2),
    )


def _id(case: Case) -> str:
    return f"{case.rule}/{case.fixture}"


@pytest.fixture(scope="module")
def verdicts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Verdict]:
    base = tmp_path_factory.mktemp("onebudgetspec-consumers")
    with ThreadPoolExecutor(max_workers=len(CASES)) as pool:
        futures = {
            _id(c): pool.submit(_judge, c, base / _id(c).replace("/", "--"))
            for c in CASES
        }
        return {key: future.result() for key, future in futures.items()}


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_rule_judges_its_fixture(case: Case, verdicts: dict[str, Verdict]) -> None:
    verdict = verdicts[_id(case)]
    assert verdict.outcome == case.expected, (
        f"{case.rule} judged the {case.fixture!r} fixture {verdict.outcome!r}, "
        f"expected {case.expected!r}:\n{verdict.report}"
    )
    if case.culprit is not None:
        assert case.culprit in verdict.violation_files, verdict.report
