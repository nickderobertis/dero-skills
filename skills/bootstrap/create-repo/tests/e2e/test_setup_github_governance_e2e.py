"""End-to-end tests for the GitHub governance setup script.

Runs the real PEP 723 script the way the skill documents — as a subprocess via
``uv run --script`` — so argument parsing and the offline ``--dry-run`` path
resolve end to end. An offline ``--dry-run`` (with ``--repo``/``--branch``)
needs no ``gh`` binary, network, or auth.

The in-process unit layer (functions exercised directly with a fake ``gh``
runner) lives beside this file's sibling, ``../test_setup_github_governance.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_github_governance.py"


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the governance script the way the skill documents: `uv run
    --script`, so the PEP 723 script resolves and parses args end to end. An
    offline `--dry-run` (with --repo/--branch) needs no `gh`, network, or auth."""
    return subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def test_e2e_dry_run_plans_all_three_mutations_including_fork_approval():
    result = _run_script(
        "check", "commitlint", "--repo", "acme/w", "--branch", "main", "--dry-run"
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "PATCH repos/acme/w" in out
    assert "PUT repos/acme/w/branches/main/protection" in out
    assert "PUT repos/acme/w/actions/permissions/fork-pr-contributor-approval" in out
    # The default policy is rendered in the body the call would send.
    assert '"approval_policy": "all_external_contributors"' in out


def test_e2e_dry_run_honors_fork_pr_approval_flag():
    result = _run_script(
        "check",
        "--repo",
        "acme/w",
        "--branch",
        "main",
        "--fork-pr-approval",
        "first_time_contributors",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    assert '"approval_policy": "first_time_contributors"' in result.stdout


def test_e2e_rejects_unknown_fork_pr_approval_policy():
    result = _run_script(
        "check", "--repo", "a/b", "--branch", "main", "--fork-pr-approval", "bogus"
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr
