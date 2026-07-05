"""Natural-language eval for the `create-repo` skill via ``skilltest-pytest``.

Runs the skill through a real harness (the ``skilltest`` binary bundled with
``skilltest-sdk``, driving the configured provider) and lets a judge score
whether the plan it produces follows the skill's load-bearing invariants. The
case lives in ``cases/create_repo.yaml``.

Why the code (``run_skill``) form instead of an auto-collected
``*.skilltest.yaml``: this eval needs a provider (``oneharness`` by default,
plus a harness token) that the uv-only ``just check`` gate deliberately does not
assume — the same reason ``llmlint`` is kept out of the gate. Auto-collected
cases have no skip hook and would error under ``just check`` with no provider.
Here we guard with ``skipif`` so a clean clone stays green and the eval runs for
real only when a provider is available (``oneharness`` on ``PATH`` or a custom
``SKILLTEST_PROVIDER``).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from skilltest_pytest import describe_failures, run_skill

CASE = Path(__file__).resolve().parent / "cases" / "create_repo.yaml"


def _provider_available() -> bool:
    """True when skilltest can reach a provider without extra setup.

    A custom ``SKILLTEST_PROVIDER`` command is taken at face value; otherwise the
    default ``oneharness`` provider must be on ``PATH``. When neither holds the
    eval skips rather than fails, keeping the uv-only gate green on a clean clone.
    """
    if os.environ.get("SKILLTEST_PROVIDER"):
        return True
    return shutil.which("oneharness") is not None


@pytest.mark.skipif(
    not _provider_available(),
    reason="no skilltest provider available (set SKILLTEST_PROVIDER or put oneharness on PATH)",
)
def test_create_repo_skill_bootstraps_a_python_cli() -> None:
    report = run_skill(
        CASE,
        platforms=["claude-code"],
        models=["claude-opus-4-8"],
    )
    assert report.passed, describe_failures(report)
