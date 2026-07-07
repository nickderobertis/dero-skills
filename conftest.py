"""Global pytest config: the opt-in switch for the slow skilltest skill evals.

Skill evals like ``skills/bootstrap/create-repo/tests/test_create_repo_skilltest.py``
drive a real ~20-30 minute repo bootstrap through a harness, so they must never
fire by accident in the normal gate. Mark such a test ``@pytest.mark.skilltest_e2e``
and it is skipped unless you opt in with ``--skilltest-e2e`` (or ``SKILLTEST_E2E=1``,
which ``just skilltest`` sets). The option is registered here at the rootdir
because a deep conftest cannot reliably add command-line options.
"""

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--skilltest-e2e",
        action="store_true",
        default=False,
        help="run the opt-in skilltest skill evals (slow: a full repo bootstrap per case)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "skilltest_e2e: opt-in, slow skilltest skill eval — needs --skilltest-e2e / SKILLTEST_E2E",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    env_opt_in = os.environ.get("SKILLTEST_E2E", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if config.getoption("--skilltest-e2e") or env_opt_in:
        return
    skip = pytest.mark.skip(
        reason="opt-in eval: pass --skilltest-e2e or set SKILLTEST_E2E=1 (or run `just skilltest`)"
    )
    for item in items:
        if "skilltest_e2e" in item.keywords:
            item.add_marker(skip)
