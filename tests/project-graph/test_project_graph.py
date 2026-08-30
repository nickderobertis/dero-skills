"""Tests that every part of this repo lands in a project `nx affected` can reach.

Code the graph does not cover is code affected selection can never choose: its
tests exist but nothing decides to run them. These cases drive the real `nx`
binary — the same one `just nx` and CI use — so what they assert is what
affected detection actually does, not a restatement of `project.json`.

This tier is its own project because it is the only suite that needs the
Node/Nx toolchain: a change to the Python tooling reruns `authoring-tools`
without paying for it. Nx is an authoring accelerator here, not a gate
dependency (the gate runs on uv alone), so every case skips when `node_modules`
has not been installed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NX = REPO_ROOT / "node_modules" / ".bin" / "nx"

pytestmark = pytest.mark.skipif(
    not NX.is_file(),
    reason="Nx is an authoring accelerator; run `just bootstrap` to install it",
)


def nx(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the workspace's own `nx`, the way `just nx` and CI do.

    The daemon is off so a run leaves no background process behind for the next
    suite to inherit.
    """
    return subprocess.run(
        [str(NX), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "NX_DAEMON": "false"},
    )


def affected_test_projects(*files: str) -> set[str]:
    """The projects whose `test` target `nx affected` selects for ``files``."""
    result = nx(
        "show", "projects", "--affected", "-t", "test", f"--files={','.join(files)}"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


# Every file under tools/ that a change can touch — the validator and its own
# tests — has to land inside a project, or a change to it selects nothing.
@pytest.mark.parametrize(
    "changed",
    ["tools/validate_skill.py", "tools/test_validate_skill.py", "tools/project.json"],
)
def test_a_change_to_the_authoring_tooling_selects_its_test_target(changed):
    assert "authoring-tools" in affected_test_projects(changed)


def test_a_change_to_this_tier_selects_only_this_tier():
    # The expensive, Nx-dependent suite sits behind its own project, so a change
    # to it reruns nothing else.
    assert affected_test_projects("tests/project-graph/test_project_graph.py") == {
        "project-graph-e2e"
    }


def test_a_change_to_the_shared_validator_still_reaches_the_skill_it_validates():
    # bootstrap-create-repo's validate/smoke targets run tools/validate_skill.py,
    # and nx.json's `tooling` input is what keeps that edge in the graph.
    assert affected_test_projects("tools/validate_skill.py") >= {
        "authoring-tools",
        "bootstrap-create-repo",
    }


def test_a_change_to_a_dogfooded_contract_reruns_the_drift_gate():
    # tools/test_oneharness_dogfood.py compares the repo's own oneharness.toml
    # against the create-repo template, and both files live outside tools/.
    # nx.json's `dogfoodedContracts` input is what keeps them in that project's
    # graph, so a cached pass cannot outlive an edit to either side.
    for contract in (
        "oneharness.toml",
        "skills/bootstrap/create-repo/assets/oneharness.toml.template",
    ):
        assert "authoring-tools" in affected_test_projects(contract), contract


def test_an_unrelated_change_selects_nothing():
    assert affected_test_projects("docs/authoring-skills.md") == set()


def test_a_change_to_a_skill_selects_only_that_skill():
    assert affected_test_projects("skills/bootstrap/create-repo/SKILL.md") == {
        "bootstrap-create-repo"
    }


def test_the_authoring_tooling_target_runs_its_suite_for_real():
    """`authoring-tools:test` must run this repo's pytest, not merely exist.

    Narrowed with `-k` to one case so the run is quick; the runner, the working
    directory and the collected tree are the real ones. The Nx cache is skipped
    so this executes the command rather than replaying a previous result.
    """
    result = nx(
        "run",
        "authoring-tools:test",
        "--skip-nx-cache",
        "--",
        "-k",
        "test_a_conformant_skill_passes",
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "1 passed" in output, output
