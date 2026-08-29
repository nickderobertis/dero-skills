"""End-to-end tests for the assets a produced repo starts from.

The justfile template is driven the way a contributor drives it: materialised
under its real name and run with the *real* `just`, with only the genuinely
external orchestrator stubbed (a `bunx` on PATH that echoes its argv), so the
tier each recipe invokes is observed from the command that actually ran — not
matched out of the file. The materialised command surface is then handed to the
real baseline checker, which is what a produced repo is audited by.

The CI template can't be executed locally (GitHub runs it), so it is read as the
text GitHub parses: the jobs it defines, the tier each runs, and the
least-privilege / no-injection rules `references/ci.md` requires of it.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from test_check_repo_baseline import crb

SKILL_DIR = Path(__file__).resolve().parents[2]
ASSETS = SKILL_DIR / "assets"

# The gate's target list, as references/ci.md's staged model names it.
GATE_TARGETS = "lint typecheck test build"


@pytest.fixture
def command_surface(tmp_path: Path) -> Path:
    """The justfile template under its real name, with a stub orchestrator on PATH."""
    shutil.copy(ASSETS / "justfile.template", tmp_path / "justfile")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "bunx"
    stub.write_text('#!/usr/bin/env bash\necho "bunx $*"\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return tmp_path


def run_just(repo: Path, *args: str, **env_overrides: str):
    """Run a recipe with the real `just`, resolving `bunx` to the stub."""
    env = {**os.environ, "PATH": f"{repo / 'bin'}:{os.environ['PATH']}"}
    env.update(env_overrides)
    return subprocess.run(
        ["just", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def invocations(result: subprocess.CompletedProcess[str]) -> list[str]:
    """The orchestrator invocations the recipe actually made (via the stub)."""
    return [line for line in result.stdout.splitlines() if line.startswith("bunx nx ")]


# --- the command surface ---------------------------------------------------


def test_check_runs_the_affected_tier_by_default(command_surface):
    result = run_just(command_surface, "check")
    assert result.returncode == 0, result.stderr
    assert invocations(result) == [
        f"bunx nx affected -t {GATE_TARGETS} --base=origin/main"
    ]


def test_check_all_runs_the_broader_sweep(command_surface):
    result = run_just(command_surface, "check", "all")
    assert result.returncode == 0, result.stderr
    assert invocations(result) == [f"bunx nx run-many -t {GATE_TARGETS}"]


def test_the_affected_tier_keys_off_the_explicitly_derived_merge_base(
    command_surface,
):
    # CI derives the base (nx-set-shas exports NX_BASE) instead of leaving the
    # orchestrator to its non-deterministic implicit default.
    result = run_just(command_surface, "check", NX_BASE="0ff1ce")
    assert result.returncode == 0, result.stderr
    assert invocations(result) == [f"bunx nx affected -t {GATE_TARGETS} --base=0ff1ce"]


@pytest.mark.parametrize(
    ("recipe", "expected"),
    [
        ("test", "bunx nx affected -t test --base=origin/main"),
        ("lint", "bunx nx affected -t lint typecheck --base=origin/main"),
        ("format", "bunx nx run-many -t format"),
        ("test-e2e", "bunx nx run-many -t test -p tag:type:e2e"),
    ],
)
def test_each_gate_recipe_delegates_to_the_orchestrator(
    command_surface, recipe, expected
):
    result = run_just(command_surface, recipe)
    assert result.returncode == 0, result.stderr
    assert invocations(result) == [expected]


def test_placeholder_recipes_still_name_what_a_new_repo_must_fill_in(
    command_surface,
):
    # bootstrap and upgrade are the stack-specific ones the template can't write.
    result = run_just(command_surface, "bootstrap")
    assert result.returncode == 0, result.stderr
    assert "TODO" in result.stdout


def test_the_template_surface_satisfies_the_baseline_checker(command_surface):
    # The checker is what audits a produced repo: it must find the whole command
    # surface, and must read the delegating gate as running the test suite. The
    # only thing left for it to report is the two stack-specific bodies the
    # template deliberately leaves for the new repo to fill in.
    findings = crb.check_justfile(command_surface)
    errors = [f.message for f in findings if f.level == "ERROR"]
    assert errors == [
        "justfile recipe(s) still hold template placeholders: bootstrap, upgrade"
    ]
    names = crb.parse_just_recipes(
        (command_surface / "justfile").read_text(encoding="utf-8")
    )
    assert {
        "bootstrap",
        "check",
        "test",
        "test-e2e",
        "lint",
        "format",
        "upgrade",
        "lint-llm",
        "lint-llm-diff",
        "lint-llm-validate",
    } <= names


def test_the_template_surface_reads_as_delegating_to_the_graph(command_surface):
    # No nx.json here (the template is a command surface, not a whole repo), so
    # the project-graph check still advises — but about the missing graph only,
    # never about recipes that bypass it.
    (finding,) = crb.check_project_graph(command_surface)
    assert finding.level == "WARN"
    assert "bypass" not in finding.message


# --- the CI workflow -------------------------------------------------------


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


def test_no_untrusted_event_data_is_interpolated_into_a_shell(ci_workflow):
    # `${{ github.event.* }}` in a `run:` is a command-injection vector; event
    # data may only reach a step through an action input or `env:`.
    offenders = [
        line
        for line in run_step_lines(ci_workflow)
        if "${{ github.event" in line or "${{ inputs" in line
    ]
    assert offenders == []


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
