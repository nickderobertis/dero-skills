"""The generated budgets job and its own budget, held to the released action's contract.

`fixtures/onebudgetspec-github-action/<tag>/` holds the pinned release's `action.yml` and
`telemetry --help`, byte for byte; `external/test_budget_report_job_external.py` holds them to
the release itself. Moving the composer's pin without refreshing them fails here, offline.
"""

from __future__ import annotations

import json
import re

import pytest
import yaml

from ci_cd_shape import ASSETS, COMPOSER, FIXTURES, GOVERNANCE, NAME, compose, run

ACTION = "nickderobertis/onebudgetspec-github-action"
POLICY = ASSETS / "budgets" / "budget-policy.md"
REFERENCE = ASSETS.parent / "references" / "tools" / "onebudgetspec.md"
CI_REFERENCE = ASSETS.parent / "references" / "ci.md"


def pinned_tag() -> str:
    source = COMPOSER.read_text(encoding="utf-8")
    match = re.search(r'ONEBUDGETSPEC_ACTION_VERSION = "(v\d+\.\d+\.\d+)"', source)
    assert match, "the composer pins no exact action release"
    return match.group(1)


def released() -> dict:
    path = FIXTURES / "onebudgetspec-github-action" / pinned_tag() / "action.yml"
    assert path.is_file(), f"refresh the release's fixtures: {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def telemetry_help() -> str:
    path = (
        FIXTURES / "onebudgetspec-github-action" / pinned_tag() / "telemetry-help.txt"
    )
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return compose(tmp_path_factory.mktemp("contract") / "repo")


@pytest.fixture(scope="module")
def job(repo) -> dict:
    workflow = yaml.safe_load((repo / ".github/workflows/ci.yml").read_text())
    return workflow["jobs"]["budgets"]


@pytest.fixture(scope="module")
def action_step(job) -> dict:
    steps = [s for s in job["steps"] if s.get("uses", "").startswith(f"{ACTION}@")]
    assert len(steps) == 1
    return steps[0]


def test_the_job_pins_the_release_the_fixtures_hold(action_step):
    assert action_step["uses"] == f"{ACTION}@{pinned_tag()}"
    assert released()["name"]


def test_each_input_the_job_passes_is_one_the_action_declares(action_step):
    declared = released()["inputs"]
    assert set(action_step["with"]) <= set(declared)
    required = {name for name, spec in declared.items() if spec.get("required")}
    assert required <= set(action_step["with"])


def test_the_job_passes_every_other_input_at_the_action_default(action_step):
    """Saying nothing must keep meaning what the generated budgets rely on."""
    declared = released()["inputs"]
    assert declared["budget-files"]["default"] == "**/budgets.yaml"
    assert declared["labels"]["default"] == "ci-report"
    assert declared["artifact-name"]["default"] == "budget-results"
    assert not {"budget-files", "labels", "artifact-name"} & set(action_step["with"])
    assert declared["enforcement-exclude-labels"]["default"] == ""


def test_only_labels_the_root_budgets_carry_are_report_only(repo, action_step):
    labels = action_step["with"]["enforcement-exclude-labels"].split(",")
    budgets = yaml.safe_load((repo / "budgets.yaml").read_text())["budgets"]
    carried = {label for budget in budgets for label in budget["labels"]}
    assert set(labels) == carried


def test_the_own_budget_command_matches_the_released_reader(repo):
    budget = yaml.safe_load((repo / ".github/reporting/budgets.yaml").read_text())
    assert budget["schema_version"] == 1
    entries = budget["budgets"]
    assert [entry["id"] for entry in entries] == [
        f"{NAME}-budget-report-command-seconds"
    ]
    entry = entries[0]
    assert entry["labels"] == [released()["inputs"]["labels"]["default"]]
    command = entry["command"]
    assert command[:2] == ["onebudgetspec-github-action", "telemetry"]
    shown = telemetry_help()
    assert "Usage: onebudgetspec-github-action telemetry" in shown
    for flag in (arg for arg in command[2:] if arg.startswith("--")):
        assert f"{flag} <" in shown, flag
    assert "[default: ci-report]" in shown
    assert entry["measure"] == "reported"
    assert entry["direction"] == "max"
    assert entry["unit"] == "seconds"


def test_the_governance_record_requires_budgets(repo):
    record = json.loads((repo / ".github/governance.json").read_text())
    assert record["required_checks"] == [
        "lint",
        "test-linux",
        "test-windows",
        "test-macos",
        "budgets",
    ]
    done = run(
        ["uv", "run", "--script", str(GOVERNANCE), "--repo", "a/b", "--branch", "main", "--dry-run"],
        cwd=repo,
    )  # fmt: skip
    assert "budgets" in done.stdout.splitlines()[1], done.stdout


def test_the_policy_is_generated_from_the_one_asset(repo):
    policy = POLICY.read_text(encoding="utf-8")
    agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.count(policy) == 1
    assert agents.count("## Budget reports") == 1


def test_the_policy_holds_the_five_instructions(repo):
    policy = POLICY.read_text(encoding="utf-8")
    numbered = re.findall(r"^(\d)\. (.+)$", policy, re.M)
    assert [number for number, _ in numbered] == ["1", "2", "3", "4", "5"]
    first, second, third, fourth, fifth = (text for _, text in numbered)
    assert "read the PR report first" in first
    assert "skip the duplicate read" in first
    assert "Never time base versus branch on a shared host" in second
    assert "at least one standard deviation" in third
    assert "profiling to find where" in third
    assert "never raise limits or timeouts" in fourth
    assert "Suspect the current change first" in fifth


@pytest.mark.parametrize("path", [POLICY, REFERENCE, CI_REFERENCE])
def test_no_budget_text_contradicts_the_policy(path):
    text = " ".join(path.read_text(encoding="utf-8").split()).lower()
    assert "toward its threshold" not in text
    assert "never stored" not in text
    assert "no previous measurement" not in text
