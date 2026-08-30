"""Tests that this repo's project graph puts every part of it in the right tier.

Two things have to be true of the graph and neither is visible by reading
`project.json`:

* **Nothing is orphaned.** Code the graph does not cover is code affected
  selection can never choose: its tests exist but nothing decides to run them.
* **The expensive tiers are out of reach.** The judged llmlint tier and the
  harness-driven skilltest eval must sit behind graph edges an unrelated change
  cannot reach, or the split bought nothing.

These cases drive the real `nx` binary — the same one `just check` and CI use —
so what they assert is what affected detection actually does.

This tier is its own project because it is the only suite that shells out to Nx
itself: a change to the Python tooling reruns `authoring-tools` without paying
for it. The gate now runs *through* Nx, so `node_modules` is always present when
these run under `just check`; the skip below only covers a bare `pytest` sweep in
a clone that has not been bootstrapped.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NX = REPO_ROOT / "node_modules" / ".bin" / "nx"

pytestmark = [
    pytest.mark.skipif(
        not NX.is_file(),
        reason="the orchestrator is not installed; run `just bootstrap`",
    ),
    pytest.mark.skipif(
        shutil.which("just") is None,
        reason="the command surface needs `just`; run `just session-setup`",
    ),
]

# The `-t` list of an orchestrator invocation, up to the next flag.
_TARGETS_RE = re.compile(r"-t\s+((?:[\w-]+\s*)+)")


def gate_targets() -> tuple[str, ...]:
    """The target names `just check` actually fans out over.

    Read off the real command surface rather than restated here: `just -n` is the
    user-facing way to ask a recipe what it will run, and because the tier is
    resolved in just rather than in a shell `if`, its output IS the one
    orchestrator command that would execute.
    """
    result = subprocess.run(
        ["just", "-n", "check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    match = _TARGETS_RE.search(result.stdout + result.stderr)
    assert match is not None, "`just -n check` printed no `-t` target list"
    return tuple(match.group(1).split())


GATE_TARGETS = gate_targets()


def nx(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the workspace's own `nx`, the way `just check` and CI do.

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


def affected_projects(targets: tuple[str, ...], *files: str) -> set[str]:
    """The projects `nx affected` selects for ``files`` across ``targets``."""
    target_args = [arg for target in targets for arg in ("-t", target)]
    result = nx(
        "show", "projects", "--affected", *target_args, f"--files={','.join(files)}"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def affected_test_projects(*files: str) -> set[str]:
    """The projects whose `test` target `nx affected` selects for ``files``."""
    return affected_projects(("test",), *files)


def gate_projects(*files: str) -> set[str]:
    """Everything `just check` would run for ``files`` — the whole gate fan-out."""
    return affected_projects(GATE_TARGETS, *files)


# Every file under tools/ that a change can touch — the validator and its own
# tests — has to land inside a project, or a change to it selects nothing.
@pytest.mark.parametrize(
    "changed",
    ["tools/validate_skill.py", "tools/test_validate_skill.py", "tools/project.json"],
)
def test_a_change_to_the_authoring_tooling_selects_its_test_target(changed):
    assert "authoring-tools" in affected_test_projects(changed)


def test_a_change_to_this_tier_selects_only_this_tier():
    # The Nx-dependent suite sits behind its own project, so a change to it
    # reruns nothing else.
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


def test_a_change_to_a_skill_selects_that_skill_and_the_tier_that_drives_it():
    # The e2e tier depends on the skill it exercises, so a skill edit pays for
    # both. Nothing else is reached — in particular not the repo-wide baseline
    # audit, which is keyed to the checker script rather than to the whole skill.
    assert affected_test_projects("skills/bootstrap/create-repo/SKILL.md") == {
        "bootstrap-create-repo",
        "bootstrap-create-repo-e2e",
    }


def test_a_change_to_the_baseline_checker_reruns_the_audit_of_this_repo():
    # `just baseline` dogfoods that script against this repo, so editing it has
    # to rerun the audit — `nx.json`'s `repoBaseline` input is the edge.
    assert "repo-baseline" in affected_test_projects(
        "skills/bootstrap/create-repo/scripts/check_repo_baseline.py"
    )


def test_a_change_to_the_skill_e2e_tier_selects_only_that_tier():
    # The e2e suite is a project of its own nested under the skill, so editing a
    # journey does not rerun the skill's fast unit tier.
    assert affected_test_projects(
        "skills/bootstrap/create-repo/tests/e2e/test_templates_e2e.py"
    ) == {"bootstrap-create-repo-e2e"}


def test_a_change_to_the_root_command_surface_reruns_the_baseline_audit():
    # The baseline checker reads the justfile, so `nx.json`'s `repoBaseline`
    # input has to keep it in that project's graph — otherwise a cached pass
    # would outlive an edit to the gate itself.
    assert "repo-baseline" in affected_test_projects("justfile")


# --- the expensive tiers, and the edges that keep them out of the gate --------


def test_the_gate_never_fans_out_over_the_expensive_target_names():
    """`skilltest` and `lint-llm` are declared on projects, but on no gate tier.

    This is the load-bearing half of "expensive work sits behind an unreachable
    edge": the graph edges below keep an unrelated change from selecting those
    projects at all, and this keeps even a *related* change from paying for the
    tier inside `just check`. `GATE_TARGETS` is read off `just -n check`, so
    this fails the moment somebody adds an expensive name to the real gate.
    """
    for expensive in ("skilltest", "lint-llm"):
        assert expensive not in GATE_TARGETS, GATE_TARGETS
        # The target exists — it is promoted out of the gate, not deleted.
        result = nx("show", "projects", "-t", expensive)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip(), f"no project declares a `{expensive}` target"


@pytest.mark.parametrize(
    "unrelated",
    [
        "tools/validate_skill.py",
        "tests/project-graph/test_project_graph.py",
        "consumer-bootstrap/scripts/install-agent-skills.sh",
    ],
)
def test_an_unrelated_change_never_reaches_the_judged_llmlint_tier(unrelated):
    # `llmlint-tier` depends on nothing in this repo, so affected detection
    # starting anywhere else stops before it.
    assert "llmlint-tier" not in gate_projects(unrelated)


def test_the_judged_llmlint_tier_runs_when_its_own_config_changes():
    # It is unreachable, not unreachable-and-dead: editing the composed config
    # or a rule fragment is exactly when it can tell you something.
    for own in (
        "llmlint.yml",
        "skills/bootstrap/create-repo/assets/llmlint/base.llmlint.yml",
    ):
        assert "llmlint-tier" in gate_projects(own), own


@pytest.mark.parametrize(
    "unrelated",
    [
        "tools/check_tool_versions.py",
        "tests/project-graph/test_project_graph.py",
        "consumer-bootstrap/scripts/install-agent-skills.sh",
    ],
)
def test_an_unrelated_change_never_runs_the_harness_driven_eval(unrelated):
    assert "bootstrap-create-repo-skilltest" not in affected_projects(
        ("skilltest",), unrelated
    )


def test_the_eval_still_depends_on_the_skill_it_evaluates():
    # The edge the worked example prescribes: the expensive tier depends on what
    # it actually tests, so it runs when that changes and never otherwise.
    assert "bootstrap-create-repo-skilltest" in affected_projects(
        ("skilltest",), "skills/bootstrap/create-repo/SKILL.md"
    )


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
