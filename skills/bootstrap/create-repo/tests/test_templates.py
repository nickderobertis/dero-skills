"""Static checks on the shipped assets that no command can execute here.

These are deliberately NOT end-to-end tests, and they do not pretend to be. The
CI workflow is run by GitHub, and the AGENTS.md template is prose a maintainer
reads; neither has a local entry point to drive. What each one is read for is the
structure a produced repo depends on — the jobs and the tier each runs, and the
sections the baseline checker and references/ci.md require.

The parts of these assets that DO have a local interface are exercised there
instead: ``e2e/test_templates_e2e.py`` drives the justfile with the real `just`
and hands the produced repo — CI workflow and filled-in AGENTS.md included — to
the real baseline checker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def agents_section(text: str, heading: str) -> str:
    """The body under `heading`, up to the next section."""
    body = text.split(f"\n{heading}\n", 1)[1]
    return body.split("\n## ", 1)[0]


@pytest.fixture
def agents_template() -> str:
    return (ASSETS / "AGENTS.md.template").read_text(encoding="utf-8")


def test_the_agents_template_records_the_graph_and_the_staged_gate(agents_template):
    section = agents_section(agents_template, "## Stack and composition")
    # The composition the composer prints, and the graph it always composes.
    assert "References composed" in section
    assert "project-graph.md" in section
    assert "project graph" in section
    # The command surface teaches both tiers of the staged gate.
    surface = agents_section(agents_template, "## Command surface")
    assert "`just check all`" in surface
    assert "affected tier" in surface and "broader tier" in surface


# GitHub runs the workflow, so it is read as the text GitHub parses — the jobs,
# the tier each runs, and the safety rules references/ci.md sets.


@pytest.fixture
def ci_workflow() -> str:
    return (ASSETS / "ci.yml.template").read_text(encoding="utf-8")


def job_block(workflow: str, name: str) -> str:
    """The lines of one job, from its `  <name>:` header to the next job header."""
    lines = workflow.splitlines()
    start = lines.index(f"  {name}:")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("  ") and not line.startswith("   ") and line.endswith(":"):
            break
        body.append(line)
    return "\n".join(body)


def test_pull_requests_run_the_affected_tier_with_a_derived_merge_base(ci_workflow):
    job = job_block(ci_workflow, "check")
    assert "github.event_name == 'pull_request'" in job
    # Affected detection needs the history the merge base is derived from...
    assert "fetch-depth: 0" in job
    # ...and the base derived explicitly, not left to the implicit default.
    assert "nrwl/nx-set-shas@" in job
    assert "run: just check" in job
    assert "just check all" not in job


def test_the_broader_sweep_runs_at_one_lifecycle_point(ci_workflow):
    job = job_block(ci_workflow, "check-all")
    assert "github.event_name == 'push'" in job
    assert "run: just check all" in job
    # One sweep, at merge-to-main: nothing downstream re-gates the same tree.
    assert ci_workflow.count("just check all") == 1


def test_both_tiers_run_the_same_recipe_surface(ci_workflow):
    # The tier is a flag on `just check`, never a second gate implementation, so
    # local and CI cannot drift.
    assert "nx affected" not in ci_workflow
    assert "nx run-many" not in ci_workflow


def test_the_llmlint_tier_survives_alongside_the_staged_gate(ci_workflow):
    job = job_block(ci_workflow, "llmlint")
    assert "just lint-llm-validate --diff-base origin/main" in job
    assert "just lint-llm-diff origin/main" in job


def test_the_workflow_is_least_privilege(ci_workflow):
    # A read-only default token, widened per job only where one needs it.
    assert "\npermissions:\n  contents: read\n" in ci_workflow


def run_step_lines(workflow: str) -> list[str]:
    """Every line that is part of a `run:` step (inline or a block scalar)."""
    lines = workflow.splitlines()
    collected: list[str] = []
    block_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if block_indent is not None:
            indent = len(line) - len(line.lstrip())
            if stripped and indent <= block_indent:
                block_indent = None
            else:
                collected.append(line)
                continue
        if stripped.startswith("run:"):
            collected.append(line)
            if stripped in ("run: |", "run: >"):
                block_indent = len(line) - len(line.lstrip())
    return collected


def test_no_untrusted_event_data_is_interpolated_into_a_shell(ci_workflow):
    # `${{ github.event.* }}` in a `run:` is a command-injection vector; event
    # data may only reach a step through an action input or `env:`.
    offenders = [
        line
        for line in run_step_lines(ci_workflow)
        if "${{ github.event" in line or "${{ inputs" in line
    ]
    assert offenders == []
