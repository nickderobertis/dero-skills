"""The baseline checker's onebudgetspec section, over repos the composer wired.

A repo declares onebudgetspec by recording `tools/onebudgetspec.md` among the
references it composed. Each fixture starts from the conformant baseline repo,
has the real composer (`--tool onebudgetspec --wiring`) apply the setup step,
adds budget domains the way a consumer would, and then breaks one of the three
things the checker holds: the pin, a file's reach from `check`, the lint rules.
Whether the wired repo actually measures is the e2e tier's
(e2e/test_onebudgetspec_wiring_e2e.py).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_check_repo_baseline import (
    CONFORMANT_AGENTS,
    FULL_JUSTFILE,
    crb,
    levels,
    make_repo,
)

SKILL_DIR = Path(__file__).resolve().parents[1]
COMPOSER = SKILL_DIR / "scripts" / "compose_repo_plan.py"

# The wiring runs the budgets against the merge base the template assigns.
BASED_JUSTFILE = 'base := "origin/main"\n\n' + FULL_JUSTFILE
DECLARING_AGENTS = CONFORMANT_AGENTS.replace(
    "+ ci.md", "+ ci.md + tools/onebudgetspec.md"
)
# What `bun install` records for the pinned release (trimmed to its entry).
BUN_LOCK = (
    '{\n  "lockfileVersion": 1,\n  "packages": {\n'
    '    "@onebudgetspec/cli": ["@onebudgetspec/cli@0.1.3", "", {}, "sha512-x"],\n'
    "  }\n}\n"
)
API_BUDGETS = """\
schema_version: 1
budgets:
  - id: api-requests-per-sync
    description: "Upstream requests one sync of the recorded fixture makes"
    measure: reported
    command: ["node", "budgets/measure.mjs"]
    unit: requests
    direction: max
    threshold: 40
"""
ROOT_BUDGETS = """\
schema_version: 1
budgets:
  - id: gate-time
    description: "Wall clock of the full gate"
    measure: elapsed
    command: ["just", "check"]
    unit: seconds
    direction: max
    threshold: 1800
"""


def api_project(budgets_target: str = "budgets") -> dict[str, object]:
    return {
        "name": "api",
        "targets": {
            budgets_target: {
                "command": "onebudgetspec check services/api/budgets.yaml "
                "--exclude-label host"
            },
            "budgets-host": {
                "command": "onebudgetspec check {projectRoot}/budgets.yaml --label host"
            },
        },
    }


def wired_repo(tmp_path: Path) -> Path:
    """A baseline repo declaring onebudgetspec, set up by the composer's wiring."""
    repo = make_repo(tmp_path, composition=DECLARING_AGENTS, justfile=BASED_JUSTFILE)
    result = subprocess.run(
        [
            "uv",
            "run",
            "--script",
            str(COMPOSER),
            "--shape",
            "cli",
            "--language",
            "python",
            "--tool",
            "onebudgetspec",
            "-o",
            str(tmp_path / "plan.md"),
            "--llmlint-config",
            str(repo / "llmlint.yml"),
            "--wiring",
            str(repo),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    (repo / "bun.lock").write_text(BUN_LOCK, encoding="utf-8")
    api = repo / "services" / "api"
    api.mkdir(parents=True)
    (api / "budgets.yaml").write_text(API_BUDGETS, encoding="utf-8")
    (api / "project.json").write_text(json.dumps(api_project()), encoding="utf-8")
    (repo / "budgets.yaml").write_text(ROOT_BUDGETS, encoding="utf-8")
    return repo


def budget_findings(repo: Path) -> list[crb.Finding]:
    return crb.check_onebudgetspec(repo)


def test_a_wired_repo_passes(tmp_path):
    repo = wired_repo(tmp_path)
    findings = budget_findings(repo)
    assert levels(findings, "ERROR") == []
    assert levels(findings, "OK") == [
        "onebudgetspec pinned, 2 budgets file(s) reached by check, lint rules adopted"
    ]
    assert levels(crb.audit(repo), "ERROR") == []


def test_no_pin_fails(tmp_path):
    repo = wired_repo(tmp_path)
    (repo / "package.json").write_text('{"private": true}', encoding="utf-8")
    [error] = levels(budget_findings(repo), "ERROR")
    assert error == (
        "onebudgetspec is not pinned: neither package.json nor uv.lock pins "
        "onebudgetspec"
    )


def test_a_version_range_is_not_a_pin(tmp_path):
    repo = wired_repo(tmp_path)
    package = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    package["devDependencies"]["@onebudgetspec/cli"] = "^0.1.3"
    (repo / "package.json").write_text(json.dumps(package), encoding="utf-8")
    [error] = levels(budget_findings(repo), "ERROR")
    assert (
        "declares @onebudgetspec/cli '^0.1.3', which is not the exact version "
        "bun.lock resolves (0.1.3)"
    ) in error


def test_a_pin_no_lockfile_records_fails(tmp_path):
    repo = wired_repo(tmp_path)
    (repo / "bun.lock").unlink()
    [error] = levels(budget_findings(repo), "ERROR")
    assert "but bun.lock does not record it" in error


def uv_repo(tmp_path: Path, dist: str = "", dev: str = "") -> Path:
    """A wired repo whose onebudgetspec pin lives in uv.lock rather than bun.lock.

    ``dist`` and ``dev`` are the root project's requirement tables as uv.lock
    records them; both onebudgetspec packages resolve to 0.1.3.
    """
    repo = wired_repo(tmp_path)
    (repo / "package.json").write_text('{"private": true}', encoding="utf-8")
    (repo / "bun.lock").unlink()
    (repo / "uv.lock").write_text(
        f"""\
version = 1

[[package]]
name = "app"
version = "0.1.0"
source = {{ virtual = "." }}

[package.metadata]
requires-dist = [{dist}]

[package.metadata.requires-dev]
dev = [{dev}]

[[package]]
name = "onebudgetspec-cli"
version = "0.1.3"

[[package]]
name = "onebudgetspec-sdk"
version = "0.1.3"
""",
        encoding="utf-8",
    )
    return repo


CLI_PINNED = '{ name = "onebudgetspec-cli", specifier = "==0.1.3" }'


def test_a_uv_pin_recorded_in_uv_lock_passes(tmp_path):
    repo = uv_repo(tmp_path, dev=CLI_PINNED)
    assert levels(budget_findings(repo), "ERROR") == []


@pytest.mark.parametrize(
    ("dist", "dev", "problem"),
    [
        (
            "",
            '{ name = "onebudgetspec-cli", specifier = "==0.1.2" }',
            "uv.lock records onebudgetspec-cli ==0.1.2, not `==` the version it "
            "resolves (0.1.3)",
        ),
        (
            '{ name = "onebudgetspec-sdk", specifier = ">=0.1" }',
            CLI_PINNED,
            "uv.lock records onebudgetspec-sdk >=0.1, not `==` the version it "
            "resolves (0.1.3)",
        ),
        (
            '{ name = "onebudgetspec-sdk" }',
            CLI_PINNED,
            "uv.lock records onebudgetspec-sdk unbounded",
        ),
    ],
    ids=["stale pin", "an unpinned sdk beside a pinned cli", "no specifier"],
)
def test_a_uv_requirement_that_is_not_the_resolved_version_fails(
    tmp_path, dist, dev, problem
):
    repo = uv_repo(tmp_path, dist=dist, dev=dev)
    [error] = levels(budget_findings(repo), "ERROR")
    assert problem in error


def test_a_uv_requirement_the_lock_does_not_resolve_fails(tmp_path):
    repo = uv_repo(tmp_path, dev=CLI_PINNED)
    lock = repo / "uv.lock"
    text = lock.read_text(encoding="utf-8")
    lock.write_text(text.split('[[package]]\nname = "onebudgetspec-cli"')[0])
    [error] = levels(budget_findings(repo), "ERROR")
    assert "uv.lock requires onebudgetspec-cli but does not resolve it" in error


def test_a_project_file_checked_by_a_target_check_does_not_run_fails(tmp_path):
    repo = wired_repo(tmp_path)
    project = api_project(budgets_target="measure")
    del project["targets"]["budgets-host"]
    (repo / "services" / "api" / "project.json").write_text(
        json.dumps(project), encoding="utf-8"
    )
    [error] = levels(budget_findings(repo), "ERROR")
    assert error == (
        "a budgets file is not in the gate: services/api/budgets.yaml is checked "
        "by services/api's measure target(s), which `check` does not run"
    )


def test_a_project_file_no_target_checks_fails(tmp_path):
    repo = wired_repo(tmp_path)
    (repo / "services" / "api" / "project.json").write_text(
        json.dumps({"name": "api", "targets": {"test": {"command": "node t.mjs"}}}),
        encoding="utf-8",
    )
    [error] = levels(budget_findings(repo), "ERROR")
    assert "no target in services/api/project.json runs `onebudgetspec check`" in error


def test_a_file_outside_every_project_fails(tmp_path):
    repo = wired_repo(tmp_path)
    stray = repo / "scripts" / "budgets.yaml"
    stray.write_text(API_BUDGETS, encoding="utf-8")
    [error] = levels(budget_findings(repo), "ERROR")
    assert "scripts/budgets.yaml sits in no Nx project" in error


def test_a_root_file_check_never_reads_fails(tmp_path):
    repo = wired_repo(tmp_path)
    justfile = repo / "justfile"
    text = justfile.read_text(encoding="utf-8")
    root_line = "    [ ! -f budgets.yaml ] || bunx onebudgetspec check budgets.yaml\n"
    assert root_line in text
    justfile.write_text(text.replace(root_line, ""), encoding="utf-8")
    [error] = levels(budget_findings(repo), "ERROR")
    assert error == (
        "a budgets file is not in the gate: budgets.yaml is checked by no "
        "`onebudgetspec check` in a recipe `check` runs"
    )


def test_a_check_that_no_longer_depends_on_budgets_leaves_every_file_out(tmp_path):
    repo = wired_repo(tmp_path)
    justfile = repo / "justfile"
    text = justfile.read_text(encoding="utf-8")
    justfile.write_text(text.replace(" (budgets tier)", ""), encoding="utf-8")
    errors = levels(budget_findings(repo), "ERROR")
    assert len(errors) == 2
    assert all(e.startswith("a budgets file is not in the gate") for e in errors)


def test_lint_rules_not_adopted_fails(tmp_path):
    repo = wired_repo(tmp_path)
    config = repo / "llmlint.yml"
    text = config.read_text(encoding="utf-8")
    kept = [line for line in text.splitlines() if "onebudgetspec" not in line]
    config.write_text("\n".join(kept) + "\n", encoding="utf-8")
    [error] = levels(budget_findings(repo), "ERROR")
    assert "the onebudgetspec lint rules are not adopted" in error


def test_a_pin_the_lockfile_records_at_another_version_fails(tmp_path):
    repo = wired_repo(tmp_path)
    lock = repo / "bun.lock"
    lock.write_text(
        lock.read_text(encoding="utf-8").replace("cli@0.1.3", "cli@0.1.2"),
        encoding="utf-8",
    )
    [error] = levels(budget_findings(repo), "ERROR")
    assert "'0.1.3', which is not the exact version bun.lock resolves (0.1.2)" in error


@pytest.mark.parametrize(
    "lock",
    [
        "not json at all\n",
        '{"packages": {"other": ["other@1.0.0", "", {"note": '
        '"@onebudgetspec/cli@0.1.3"}, "sha512-y"]}}\n',
        '{"packages": {"@onebudgetspec/cli": "@onebudgetspec/cli@0.1.3"}}\n',
    ],
    ids=["not JSON", "named only inside another package", "entry not an array"],
)
def test_a_bun_lock_that_resolves_no_such_package_fails(tmp_path, lock):
    repo = wired_repo(tmp_path)
    (repo / "bun.lock").write_text(lock, encoding="utf-8")
    [error] = levels(budget_findings(repo), "ERROR")
    assert "pins @onebudgetspec/cli 0.1.3 but bun.lock does not record it" in error


@pytest.mark.parametrize(
    ("command", "reached"),
    [
        ("onebudgetspec check --label=host services/api/budgets.yaml", True),
        ("onebudgetspec check --json services/api/budgets.yaml", True),
        ("onebudgetspec check services/api/budgets.yaml --label", False),
        ("onebudgetspec check --bogus services/api/budgets.yaml", False),
        ("onebudgetspec check --label --bogus services/api/budgets.yaml", False),
        ("onebudgetspec check --help", False),
    ],
    ids=[
        "--flag=value",
        "a switch",
        "a value missing",
        "unknown option",
        "an option as a value",
        "--help",
    ],
)
def test_check_arguments_the_cli_refuses_reach_no_file(tmp_path, command, reached):
    repo = wired_repo(tmp_path)
    project = {"name": "api", "targets": {"budgets": {"command": command}}}
    (repo / "services" / "api" / "project.json").write_text(
        json.dumps(project), encoding="utf-8"
    )
    errors = levels(budget_findings(repo), "ERROR")
    assert len(errors) == (0 if reached else 1), errors


@pytest.mark.parametrize(
    "budgets_target",
    [
        {
            "options": {
                "command": "onebudgetspec check --exclude-label host",
                "cwd": "services/api",
            }
        },
        {
            "options": {
                "commands": [
                    "echo measuring",
                    {"command": "onebudgetspec check services/api/budgets.yaml"},
                ]
            }
        },
    ],
    ids=["options.command from options.cwd", "options.commands"],
)
def test_a_target_running_check_through_its_options_reaches_the_file(
    tmp_path, budgets_target
):
    repo = wired_repo(tmp_path)
    project = {"name": "api", "targets": {"budgets": budgets_target}}
    (repo / "services" / "api" / "project.json").write_text(
        json.dumps(project), encoding="utf-8"
    )
    assert levels(budget_findings(repo), "ERROR") == []


def test_a_target_checking_from_another_directory_misses_the_file(tmp_path):
    repo = wired_repo(tmp_path)
    budgets = {"options": {"command": "onebudgetspec check", "cwd": "services/web"}}
    project = {"name": "api", "targets": {"budgets": budgets}}
    (repo / "services" / "api" / "project.json").write_text(
        json.dumps(project), encoding="utf-8"
    )
    [error] = levels(budget_findings(repo), "ERROR")
    assert "no target in services/api/project.json runs `onebudgetspec check`" in error


def test_an_inline_plugins_list_adopts_the_rules(tmp_path):
    repo = wired_repo(tmp_path)
    url = next(
        line.strip().strip("- ").strip('"')
        for line in (repo / "llmlint.yml").read_text(encoding="utf-8").splitlines()
        if "onebudgetspec" in line
    )
    (repo / "llmlint.yml").write_text(
        f'plugins: ["https://example.com/base.llmlint.yml@1", "{url}"]  # composed\n',
        encoding="utf-8",
    )
    assert levels(budget_findings(repo), "ERROR") == []


@pytest.mark.parametrize(
    "lock",
    ["package = 1\n", '[[package]]\nname = ["onebudgetspec-sdk"]\nversion = "0.1.3"\n'],
    ids=["package not a list", "entry name not a string"],
)
def test_a_uv_lock_it_cannot_read_records_nothing(tmp_path, lock):
    repo = uv_repo(tmp_path, dev=CLI_PINNED)
    (repo / "uv.lock").write_text(lock, encoding="utf-8")
    [error] = levels(budget_findings(repo), "ERROR")
    assert "neither package.json nor uv.lock pins onebudgetspec" in error


def test_the_uv_pin_reading_agrees_with_a_uv_generated_lock():
    # This repository pins `onebudgetspec-cli` in its dev group, and uv wrote its
    # uv.lock: the checker must read that pair as pinned.
    assert crb.onebudgetspec_pin_problem(SKILL_DIR.parents[2]) is None


def test_an_unterminated_quoted_plugin_is_not_adoption(tmp_path):
    repo = wired_repo(tmp_path)
    config = repo / "llmlint.yml"
    lines = config.read_text(encoding="utf-8").splitlines()
    broken = [line.rstrip('"') if "onebudgetspec" in line else line for line in lines]
    config.write_text("\n".join(broken) + "\n", encoding="utf-8")
    [error] = levels(budget_findings(repo), "ERROR")
    assert "the onebudgetspec lint rules are not adopted" in error


@pytest.mark.parametrize(
    "malformed",
    [
        lambda url: f'plugins:\n  - "{url}" trailing\n',
        lambda url: f'plugins: ["https://example.com/base.llmlint.yml@1", "{url}"\n',
    ],
    ids=["content after the quoted url", "an unclosed flow list"],
)
def test_a_malformed_plugins_entry_is_not_adoption(tmp_path, malformed):
    repo = wired_repo(tmp_path)
    config = repo / "llmlint.yml"
    url = next(
        line.strip().strip("- ").strip('"')
        for line in config.read_text(encoding="utf-8").splitlines()
        if "onebudgetspec" in line
    )
    config.write_text(malformed(url), encoding="utf-8")
    [error] = levels(budget_findings(repo), "ERROR")
    assert "the onebudgetspec lint rules are not adopted" in error


def test_a_commented_out_plugin_is_not_adoption(tmp_path):
    repo = wired_repo(tmp_path)
    config = repo / "llmlint.yml"
    lines = config.read_text(encoding="utf-8").splitlines()
    commented = [
        line.replace("  - ", "  # - ") if "onebudgetspec" in line else line
        for line in lines
    ]
    config.write_text("\n".join(commented) + "\n", encoding="utf-8")
    [error] = levels(budget_findings(repo), "ERROR")
    assert "the onebudgetspec lint rules are not adopted" in error


def test_the_value_taking_check_options_are_the_clis(tmp_path):
    # `onebudgetspec check --help` (the dev dependency, the same release the
    # composer pins) is the source of which options take a value.
    binary = Path(sys.executable).parent / "onebudgetspec"
    found = str(binary) if binary.is_file() else shutil.which("onebudgetspec")
    assert found is not None, "onebudgetspec is not installed — run `just bootstrap`"
    help_text = subprocess.run(
        [found, "check", "--help"], capture_output=True, text=True, check=True
    ).stdout
    taking_values = set(re.findall(r"^\s+(--[a-z-]+) <[A-Z_]+>", help_text, re.M))
    assert taking_values == crb.ONEBUDGETSPEC_VALUE_FLAGS
    switches = set(re.findall(r"^\s+(--[a-z-]+)$", help_text, re.M))
    assert switches == crb.ONEBUDGETSPEC_SWITCHES


def test_a_repo_that_does_not_declare_it_is_not_checked(tmp_path):
    # Every one of the three broken at once, and still nothing to report.
    repo = make_repo(tmp_path)
    api = repo / "services" / "api"
    api.mkdir(parents=True)
    (api / "budgets.yaml").write_text(API_BUDGETS, encoding="utf-8")
    (repo / "budgets.yaml").write_text(ROOT_BUDGETS, encoding="utf-8")
    assert budget_findings(repo) == []
    assert levels(crb.audit(repo), "ERROR") == []


ROOT = "budgets.yaml"
API = "services/api/budgets.yaml"


@pytest.mark.parametrize(
    ("command", "cwd", "target", "reaches"),
    [
        ("onebudgetspec check", ".", ROOT, True),
        ("onebudgetspec check", "services/api", API, True),
        ("onebudgetspec check --label host budgets.yaml", ".", ROOT, True),
        ("onebudgetspec check --label budgets.yaml", ".", ROOT, True),  # a value
        ("onebudgetspec check other.yaml", ".", ROOT, False),
        ("onebudgetspec check budgets.yaml", ".", API, False),
        ("onebudgetspec check --recursive .", ".", API, True),
        ("onebudgetspec check --recursive services", ".", API, True),
        ("onebudgetspec check --recursive web", ".", API, False),
        ("onebudgetspec validate services/api/budgets.yaml", ".", API, False),
    ],
)
def test_onebudgetspec_reaches(command, cwd, target, reaches):
    assert crb.onebudgetspec_reaches(command, cwd, target) is reaches
