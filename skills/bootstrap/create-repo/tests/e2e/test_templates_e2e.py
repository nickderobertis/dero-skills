"""End-to-end tests for the command surface a produced repo starts from.

Nothing here is stubbed. The justfile template is materialised under its real
name and driven with the *real* `just`, the way a contributor drives it:

* `just --dry-run <recipe>` is a real user-facing flag — it is how you ask a
  justfile what a recipe will run — and because the template resolves the tier in
  just rather than in a shell `if`, its output IS the one orchestrator command
  that would execute. That is what these tests read the tier off, so the tier is
  observed from `just` itself rather than matched out of the file.
* The paths that must fail before anything runs — a mistyped tier, an NX_BASE
  that is not a git ref — are driven as ordinary `just` runs and asserted on the
  real exit code and message.

The materialised repo is then handed to the real baseline checker as a
subprocess, which is the interface a produced repo is actually audited by.

Static assertions about the shipped assets that no command exercises (the
AGENTS.md prose, the GitHub-run CI workflow) live in ``../test_templates.py``.
"""

from __future__ import annotations

import os
import re
import shutil
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
    """The justfile template under its real name, in a directory of its own."""
    shutil.copy(ASSETS / "justfile.template", tmp_path / "justfile")
    return tmp_path


def run_just(repo: Path, *args: str, **env_overrides: str):
    """Run `just` for real in `repo`, optionally overriding environment input."""
    # NX_BASE is input to the template under test, so it comes from the test
    # rather than from whatever exported it around us: this repo's own CI exports
    # one (nx-set-shas), and inheriting it would make the cases that assert the
    # template's *default* base read that value and fail only in CI.
    ambient = {k: v for k, v in os.environ.items() if k != "NX_BASE"}
    env = {**ambient, **env_overrides}
    return subprocess.run(
        ["just", *args], cwd=repo, capture_output=True, text=True, env=env
    )


def planned(repo: Path, *args: str, **env_overrides: str) -> list[str]:
    """The commands `just` reports it will run for `args` (`just --dry-run`)."""
    result = run_just(repo, "--dry-run", *args, **env_overrides)
    assert result.returncode == 0, result.stderr
    # --dry-run prints each resolved recipe line to stderr.
    return [line.strip() for line in result.stderr.splitlines() if line.strip()]


# The command surface, driven end to end: what each recipe runs is read back from
# `just`, so the tier is observed rather than matched.


def test_check_runs_the_affected_tier_by_default(command_surface):
    # The gate's targets, then the e2e tier — both scoped to what this change can
    # reach, so e2e is gated on every change without a second tier of its own.
    assert planned(command_surface, "check") == [
        f"bunx nx affected --base=origin/main -t {GATE_TARGETS}",
        "bunx nx affected --base=origin/main -t e2e",
    ]


def test_check_all_runs_the_broader_sweep(command_surface):
    assert planned(command_surface, "check", "all") == [
        f"bunx nx run-many -t {GATE_TARGETS}",
        "bunx nx run-many -t e2e",
    ]


def test_an_unknown_tier_is_refused_rather_than_quietly_downgraded(command_surface):
    # A typo must not buy the weaker tier while reporting success.
    result = run_just(command_surface, "check", "affcted")
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "unknown tier 'affcted'" in output
    assert "bunx nx" not in result.stdout


def test_the_affected_tier_keys_off_the_explicitly_derived_merge_base(command_surface):
    # CI derives the base (nx-set-shas exports NX_BASE) instead of leaving the
    # orchestrator to its non-deterministic implicit default.
    assert planned(command_surface, "check", NX_BASE="0ff1ce") == [
        f"bunx nx affected --base=0ff1ce -t {GATE_TARGETS}",
        "bunx nx affected --base=0ff1ce -t e2e",
    ]


@pytest.mark.parametrize(
    "hostile_base",
    ['origin/main"; rm -rf /; echo "', "$(id)", "main; touch pwned"],
    ids=["quote-and-chain", "command substitution", "trailing command"],
)
def test_an_nx_base_that_is_not_a_git_ref_is_refused_at_the_boundary(
    command_surface, hostile_base
):
    # NX_BASE is environment input that ends up inside a command, so the recipes
    # must never see a value that is not a plain ref/SHA — and the run must stop
    # there rather than pass it on.
    result = run_just(command_surface, "check", NX_BASE=hostile_base)
    assert result.returncode != 0
    assert "NX_BASE must be a plain git ref or SHA" in result.stderr
    assert not (command_surface / "pwned").exists()


@pytest.mark.parametrize(
    ("recipe", "expected"),
    [
        ("test", "bunx nx affected --base=origin/main -t test"),
        ("lint", "bunx nx affected --base=origin/main -t lint typecheck"),
        ("format", "bunx nx run-many -t format"),
        ("test-e2e", "bunx nx affected --base=origin/main -t e2e"),
    ],
)
def test_each_gate_recipe_delegates_to_the_orchestrator(
    command_surface, recipe, expected
):
    assert planned(command_surface, recipe) == [expected]


def test_placeholder_recipes_still_name_what_a_new_repo_must_fill_in(command_surface):
    # bootstrap is the stack-specific one the template can't write, so it runs for
    # real and says so.
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


def test_the_checker_accepts_the_shipped_ci_workflow(tmp_path):
    # GitHub is the only thing that can run the workflow, but the checker audits
    # it in every produced repo — so the shipped template must satisfy it. The
    # second half is the control: the same repo with a workflow that never runs
    # the gate does fail, so the first half is not passing vacuously.
    workflow = (ASSETS / "ci.yml.template").read_text(encoding="utf-8")
    shipped = run_baseline(repo_from_templates(tmp_path / "shipped", ci=workflow))
    assert "CI workflow" not in shipped.stderr, shipped.stderr

    ungated = run_baseline(
        repo_from_templates(
            tmp_path / "ungated",
            ci="name: ci\non: [push]\njobs:\n  noop:\n    runs-on: ubuntu-latest\n",
        )
    )
    assert "CI workflow(s) never run the gate" in ungated.stderr


def test_a_filled_in_agents_template_passes_the_baseline_checker(tmp_path):
    # The template ships placeholders on purpose — catching one left unfilled is
    # the checker's job — so fill them the way a new repo does and run the real
    # checker over the result: the composition it records must be accepted.
    agents = (ASSETS / "AGENTS.md.template").read_text(encoding="utf-8")
    filled = re.sub(r"<[^>]+>", "python cli, one deliverable", agents, flags=re.S)
    repo = repo_from_templates(tmp_path / "repo", composition=filled)
    result = run_baseline(repo)
    assert "composition" not in result.stderr, result.stderr
