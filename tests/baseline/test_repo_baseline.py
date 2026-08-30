"""The create-repo baseline checker, dogfooded against this repository.

This repo hosts the `create-repo` skill, so it has to be a passing example of
what that skill teaches — `references/shapes/skills-repo.md` makes dogfooding a
rule, not a nicety. This tier drives the checker the way `just baseline` and a
consuming repo do: the real `check_repo_baseline.py` as a subprocess against the
real repository root, reading its real exit code and output. Nothing is stubbed.

It is a project of its own because its inputs are the repository's *root*
configuration — the justfile, the workflows, `AGENTS.md`, the project
definitions — which belong to no single project. `nx.json`'s `repoBaseline`
named input is what puts those files in this project's graph, so a cached pass
cannot outlive an edit to any of them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = (
    REPO_ROOT
    / "skills"
    / "bootstrap"
    / "create-repo"
    / "scripts"
    / "check_repo_baseline.py"
)


@pytest.fixture(scope="module")
def audit() -> subprocess.CompletedProcess[str]:
    """Run the checker against this repo, exactly as `just baseline` does."""
    return subprocess.run(
        ["uv", "run", "--script", str(CHECKER), "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_this_repo_passes_its_own_baseline(
    audit: subprocess.CompletedProcess[str],
) -> None:
    assert audit.returncode == 0, audit.stdout + audit.stderr


def test_this_repo_raises_no_advisory_either(
    audit: subprocess.CompletedProcess[str],
) -> None:
    """A WARN is advisory for a repo bootstrapped before a rule existed. This
    repo is where those rules are written, so an advisory against it means the
    canonical example has fallen behind its own guidance — fix the repo (or, if
    the advice itself is wrong, the checker), never this assertion."""
    warnings = [
        line
        for line in (audit.stdout + audit.stderr).splitlines()
        if line.startswith("WARN")
    ]
    assert warnings == [], "\n".join(warnings)
