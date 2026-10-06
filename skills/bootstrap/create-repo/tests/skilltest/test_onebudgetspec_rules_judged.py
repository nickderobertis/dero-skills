"""Judged proof that the onebudgetspec llmlint rules rule the right way.

Each case writes a small consumer repo to disk — a `budgets.yaml`, the code its
command runs, and an `llmlint.yml` adopting the in-tree fragment as a plugin, the
way a budgets-registering repo adopts it by URL — then runs the real `llmlint`
over it with one rule selected and reads the verdict from its JSON report. Every
rule gets a conforming tree it must pass and a nonconforming one it must fail;
the minimal-tree rule also gets the non-pedantic case, a measuring script that
calls a shared harness and the code it measures, both outside the budget's tree.

Nothing is mocked: the judge is the harness `oneharness.toml` selects, which is
why this sits in the `skilltest` project rather than the gate — it needs a
credential and its verdicts are a model's. The cases run concurrently, since each
is one independent judge call. Run with
`just skilltest -k onebudgetspec`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2]
FRAGMENT = SKILL / "assets" / "llmlint" / "tools" / "onebudgetspec.llmlint.yml"
# What the composer writes beside a consumer's llmlint.yml: the harness selection.
ONEHARNESS_TEMPLATE = SKILL / "assets" / "oneharness.toml.template"

_LLMLINT = shutil.which(
    "llmlint",
    path=f"{Path.home() / '.local' / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
)

pytestmark = [
    pytest.mark.skilltest_e2e,
    pytest.mark.skipif(
        _LLMLINT is None, reason="needs llmlint on PATH (`just setup-llmlint`)"
    ),
]


# --- fixture trees ------------------------------------------------------------
# Shared pieces: a conforming budget measuring the Linear requests one issue sync
# makes, reported through ONEBUDGETSPEC_RESULT.

_TERSE_DESCRIPTION = (
    "Linear API requests one full issue sync makes. Protects the sync from "
    "sliding back into per-issue fetches that exhaust Linear's rate limit."
)


def _budgets_yaml(command: str, description: str = _TERSE_DESCRIPTION) -> str:
    indented = "\n".join(f"      {line}" for line in description.splitlines())
    return f"""\
schema_version: 1
budgets:
  - id: linear-sync-requests
    description: |
{indented}
    measure: reported
    command: {command}
    unit: requests
    direction: max
    threshold: 14
"""


_MEASURE_SELF_CONTAINED = '''\
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


def _self_contained_tree() -> dict[str, str]:
    """Everything the budget needs lives under packages/linear-sync/."""
    return {
        "packages/linear-sync/budgets.yaml": _budgets_yaml(
            '["uv", "run", "python", "budgets/measure_sync_requests.py"]'
        ),
        "packages/linear-sync/budgets/measure_sync_requests.py": (
            _MEASURE_SELF_CONTAINED
        ),
        "packages/linear-sync/budgets/fixtures/issues_two_pages.json": (
            _ISSUES_FIXTURE
        ),
        "packages/linear-sync/src/linear_sync/sync.py": _SYNC,
        "packages/linear-sync/src/linear_sync/client.py": _CLIENT,
    }


def _shared_harness_tree() -> dict[str, str]:
    """The budget's own files live under budgets/linear/; the harness it drives
    (also used by the unit tests) and the code it measures live elsewhere."""
    measure = '''\
"""Count the Linear requests one full issue sync makes, for onebudgetspec."""

import json
import os
from pathlib import Path

from linear_sync.sync import sync_issues
from testkit.recording_server import RecordingServer

PAGES = Path(__file__).parent / "fixtures" / "issues_two_pages.json"

with RecordingServer.replaying(PAGES) as server:
    sync_issues(server.transport())
Path(os.environ["ONEBUDGETSPEC_RESULT"]).write_text(
    json.dumps({"value": len(server.requests)})
)
'''
    recording_server = '''\
"""A loopback server replaying recorded GraphQL pages; shared by every suite."""

import json


class RecordingServer:
    def __init__(self, pages):
        self._pages = iter(pages)
        self.requests = []

    @classmethod
    def replaying(cls, path):
        return cls(json.loads(path.read_text())["pages"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transport(self):
        return self

    def post(self, path, body):
        self.requests.append((path, body))
        return next(self._pages)
'''
    unit_test = """\
from linear_sync.sync import sync_issues
from testkit.recording_server import RecordingServer


def test_sync_follows_every_page():
    server = RecordingServer([{"issues": [{"id": "A"}], "next": "2"},
                              {"issues": [{"id": "B"}], "next": None}])
    assert [i["id"] for i in sync_issues(server.transport())] == ["A", "B"]
"""
    return {
        "budgets/linear/budgets.yaml": _budgets_yaml(
            '["uv", "run", "python", "measure_sync_requests.py"]'
        ),
        "budgets/linear/measure_sync_requests.py": measure,
        "budgets/linear/fixtures/issues_two_pages.json": _ISSUES_FIXTURE,
        "src/linear_sync/sync.py": _SYNC,
        "testkit/testkit/recording_server.py": recording_server,
        "tests/test_sync.py": unit_test,
    }


def _budget_only_files_outside_tree() -> dict[str, str]:
    """The budget sits in packages/linear-sync/, but its measuring script and its
    fixture — used by nothing else — sit in the repo-root e2e suite."""
    measure = _MEASURE_SELF_CONTAINED.replace(
        'Path(__file__).parent / "fixtures" / "issues_two_pages.json"',
        'Path(__file__).parent / "fixtures" / "linear_budget_issues.json"',
    )
    return {
        "packages/linear-sync/budgets.yaml": _budgets_yaml(
            '["uv", "run", "python", "../../tests/e2e/linear_budget.py"]'
        ),
        "packages/linear-sync/src/linear_sync/sync.py": _SYNC,
        "packages/linear-sync/src/linear_sync/client.py": _CLIENT,
        "tests/e2e/linear_budget.py": measure,
        "tests/e2e/fixtures/linear_budget_issues.json": _ISSUES_FIXTURE,
    }


def _narrative_descriptions() -> dict[str, str]:
    tree = _self_contained_tree()
    tree["packages/linear-sync/budgets.yaml"] = _budgets_yaml(
        '["uv", "run", "python", "budgets/measure_sync_requests.py"]',
        description=(
            "Base (c9e75a8): 18 requests. Journey 1 fetched the first page (1),\n"
            "journey 2 refetched each issue (14), journey 3 paged comments (3).\n"
            "Lowered to 14 after the batching change in #212. Runs\n"
            "measure_sync_requests.py and fails above 14 requests."
        ),
    )
    return tree


def _measure_judges_itself() -> dict[str, str]:
    tree = _self_contained_tree()
    tree["packages/linear-sync/budgets/measure_sync_requests.py"] = '''\
"""Count the Linear requests one full issue sync makes, for onebudgetspec."""

import json
import os
import sys
from pathlib import Path

import yaml

from linear_sync.client import RecordingTransport
from linear_sync.sync import sync_issues

HERE = Path(__file__).parent
FIXTURE = HERE / "fixtures" / "issues_two_pages.json"
BUDGETS = yaml.safe_load((HERE.parent / "budgets.yaml").read_text())
THRESHOLD = next(
    b["threshold"] for b in BUDGETS["budgets"] if b["id"] == "linear-sync-requests"
)

transport = RecordingTransport.replaying(FIXTURE)
sync_issues(transport)
count = len(transport.requests)
Path(os.environ["ONEBUDGETSPEC_RESULT"]).write_text(json.dumps({"value": count}))
if count > THRESHOLD:
    sys.exit(f"linear-sync-requests: {count} requests is over the {THRESHOLD} budget")
'''
    return tree


def _per_budget_wrapper() -> dict[str, str]:
    tree = _self_contained_tree()
    tree["packages/linear-sync/budgets.yaml"] = _budgets_yaml(
        '["sh", "budgets/linear-sync-requests.sh", "linear-sync-requests"]'
    )
    tree["packages/linear-sync/budgets/linear-sync-requests.sh"] = """\
#!/bin/sh
# Wrapper for the linear-sync-requests budget.
set -eu
case "$1" in
  linear-sync-requests|linear-write-requests) ;;
  *) echo "unknown budget id: $1" >&2; exit 2 ;;
esac
uv run python budgets/measure_sync_requests.py
if [ ! -s "$ONEBUDGETSPEC_RESULT" ]; then
  echo "linear-sync-requests wrote no result" >&2
  exit 1
fi
"""
    return tree


# --- the cases ----------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    rule: str
    fixture: str
    tree: dict[str, str]
    expected: str  # llmlint's rule outcome: "pass" or "fail"
    # For a "fail": a file the judge must attribute a violation to.
    culprit: str | None = None


CASES = [
    Case(
        "budget_descriptions_durable_and_terse",
        "terse",
        _self_contained_tree(),
        "pass",
    ),
    Case(
        "budget_descriptions_durable_and_terse",
        "changelog-narrative",
        _narrative_descriptions(),
        "fail",
        "packages/linear-sync/budgets.yaml",
    ),
    Case(
        "budgets_scoped_to_minimal_tree",
        "self-contained",
        _self_contained_tree(),
        "pass",
    ),
    Case(
        "budgets_scoped_to_minimal_tree",
        "shared-harness-and-measured-code-outside",
        _shared_harness_tree(),
        "pass",
    ),
    Case(
        "budgets_scoped_to_minimal_tree",
        "budget-only-script-outside",
        _budget_only_files_outside_tree(),
        "fail",
    ),
    Case(
        "onebudgetspec_is_the_only_judge",
        "reports-only",
        _self_contained_tree(),
        "pass",
    ),
    Case(
        "onebudgetspec_is_the_only_judge",
        "reads-budgets-and-asserts",
        _measure_judges_itself(),
        "fail",
        "packages/linear-sync/budgets/measure_sync_requests.py",
    ),
    Case(
        "budget_commands_measure_directly",
        "direct",
        _self_contained_tree(),
        "pass",
    ),
    Case(
        "budget_commands_measure_directly",
        "per-budget-wrapper",
        _per_budget_wrapper(),
        "fail",
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
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"llmlint printed no JSON report:\n{result.stderr}")
    assert not report["errors"], report["errors"]
    (rule,) = [r for r in report["rules"] if r["name"] == case.rule]
    return Verdict(
        outcome=rule["outcome"],
        violation_files=[v["file"] for v in rule.get("violations") or []],
        report=json.dumps(rule, indent=2),
    )


@pytest.fixture(scope="module")
def verdicts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Verdict]:
    base = tmp_path_factory.mktemp("onebudgetspec-consumers")
    with ThreadPoolExecutor(max_workers=len(CASES)) as pool:
        futures = {
            _id(c): pool.submit(_judge, c, base / _id(c).replace("/", "--"))
            for c in CASES
        }
        return {key: future.result() for key, future in futures.items()}


def _id(case: Case) -> str:
    return f"{case.rule}/{case.fixture}"


@pytest.mark.parametrize("case", CASES, ids=_id)
def test_rule_judges_its_fixture(case: Case, verdicts: dict[str, Verdict]) -> None:
    verdict = verdicts[_id(case)]
    assert verdict.outcome == case.expected, (
        f"{case.rule} judged the {case.fixture!r} fixture {verdict.outcome!r}, "
        f"expected {case.expected!r}:\n{verdict.report}"
    )
    if case.culprit is not None:
        assert case.culprit in verdict.violation_files, verdict.report
