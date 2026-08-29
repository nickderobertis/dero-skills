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
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from test_check_repo_baseline import SCRIPT, make_repo

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


# The command surface, driven end to end: what each recipe actually invokes is
# read back from the stub's argv, so the tier is observed rather than matched.


def test_check_runs_the_affected_tier_by_default(command_surface):
    # The gate's targets, then the e2e tier — both scoped to what this change can
    # reach, so e2e is gated on every change without a second tier of its own.
    result = run_just(command_surface, "check")
    assert result.returncode == 0, result.stderr
    assert invocations(result) == [
        f"bunx nx affected -t {GATE_TARGETS} --base=origin/main",
        "bunx nx affected -t e2e --base=origin/main",
    ]


def test_check_all_runs_the_broader_sweep(command_surface):
    result = run_just(command_surface, "check", "all")
    assert result.returncode == 0, result.stderr
    assert invocations(result) == [
        f"bunx nx run-many -t {GATE_TARGETS}",
        "bunx nx run-many -t e2e",
    ]


def test_an_unknown_tier_is_refused_rather_than_quietly_downgraded(command_surface):
    # A typo must not buy the weaker tier while reporting success.
    result = run_just(command_surface, "check", "affcted")
    assert result.returncode != 0
    assert "unknown tier" in result.stdout + result.stderr
    assert invocations(result) == []


def test_the_affected_tier_keys_off_the_explicitly_derived_merge_base(
    command_surface,
):
    # CI derives the base (nx-set-shas exports NX_BASE) instead of leaving the
    # orchestrator to its non-deterministic implicit default.
    result = run_just(command_surface, "check", NX_BASE="0ff1ce")
    assert result.returncode == 0, result.stderr
    assert invocations(result) == [
        f"bunx nx affected -t {GATE_TARGETS} --base=0ff1ce",
        "bunx nx affected -t e2e --base=0ff1ce",
    ]


@pytest.mark.parametrize(
    ("recipe", "expected"),
    [
        ("test", "bunx nx affected -t test --base=origin/main"),
        ("lint", "bunx nx affected -t lint typecheck --base=origin/main"),
        ("format", "bunx nx run-many -t format"),
        ("test-e2e", "bunx nx affected -t e2e --base=origin/main"),
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


def repo_from_templates(root: Path, **overrides) -> Path:
    """An otherwise-conformant repo whose command surface IS the shipped template."""
    root.mkdir(parents=True, exist_ok=True)
    justfile = (ASSETS / "justfile.template").read_text(encoding="utf-8")
    return make_repo(root, justfile=justfile, **overrides)


def run_baseline(repo: Path) -> subprocess.CompletedProcess[str]:
    """Audit `repo` with the real checker, the way its docs tell an author to."""
    return subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), str(repo)],
        capture_output=True,
        text=True,
    )


def test_the_baseline_checker_accepts_the_template_command_surface(tmp_path):
    # The checker is what audits a produced repo, so run it: it must find the
    # whole surface and read the delegating gate as running the test suite. The
    # only thing left for it to report is the two stack-specific bodies the
    # template deliberately leaves for the new repo to fill in.
    result = run_baseline(repo_from_templates(tmp_path / "repo"))
    assert result.returncode == 1, result.stdout
    assert "still hold template placeholders: bootstrap, upgrade" in result.stderr
    assert "1 invariant(s) failed" in result.stderr
    assert "missing required recipe" not in result.stderr
    assert "does not run `test`" not in result.stderr


def test_the_checker_reads_the_template_surface_as_reaching_the_graph(tmp_path):
    # With no nx.json, the advisory must be about the missing graph — never about
    # recipes that bypass it, which these delegate through.
    repo = repo_from_templates(tmp_path / "repo", project_graph=False)
    result = run_baseline(repo)
    (warning,) = [
        line for line in result.stdout.splitlines() if line.startswith("WARN")
    ]
    assert "no project graph" in warning
    assert "bypass" not in warning


# AGENTS.md is the file a produced repo is audited on, so the template has to
# leave behind a section the checker accepts once it is filled in.


def agents_section(text: str, heading: str) -> str:
    """The body under `heading`, up to the next section."""
    body = text.split(f"\n{heading}\n", 1)[1]
    return body.split("\n## ", 1)[0]


def test_the_agents_template_records_the_graph_and_the_staged_gate():
    agents = (ASSETS / "AGENTS.md.template").read_text(encoding="utf-8")
    section = agents_section(agents, "## Stack and composition")
    # The composition the composer prints, and the graph it always composes.
    assert "References composed" in section
    assert "project-graph.md" in section
    assert "project graph" in section
    # The command surface teaches both tiers of the staged gate.
    surface = agents_section(agents, "## Command surface")
    assert "`just check all`" in surface
    assert "affected tier" in surface and "broader tier" in surface


def test_a_filled_in_agents_template_passes_the_baseline_checker(tmp_path):
    # The template ships placeholders on purpose — catching one left unfilled is
    # the checker's job — so fill them the way a new repo does and run the real
    # checker over the result: the composition it records must be accepted.
    agents = (ASSETS / "AGENTS.md.template").read_text(encoding="utf-8")
    filled = re.sub(r"<[^>]+>", "python cli, one deliverable", agents, flags=re.S)
    repo = repo_from_templates(tmp_path / "repo", composition=filled)
    result = run_baseline(repo)
    assert "composition" not in result.stderr, result.stderr


# GitHub runs the workflow, so it can't be executed here; it is read as the text
# GitHub parses — the jobs, the tier each runs, and the safety rules ci.md sets.


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
