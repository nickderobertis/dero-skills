"""The opt-in switch for this project's slow, harness-driven skill eval.

The eval drives a real ~20-30 minute repo bootstrap through a coding harness, so
it must never fire by accident. Two things keep it from doing so, and they are
different mechanisms for different callers:

* **The project graph** is what keeps it out of the gate. This directory is its
  own Nx project (`bootstrap-create-repo-skilltest`) declaring a `skilltest`
  target, and no gate recipe fans out over that name — so `just check` never
  collects these tests at all. That is the split `references/languages/python.md`
  prescribes over a marker the default run deselects.
* **This conftest** is the belt to that braces, for a developer who types a bare
  `pytest` at the repo root and sweeps the whole tree. Cases marked
  ``@pytest.mark.skilltest_e2e`` are skipped unless ``SKILLTEST_E2E`` is set,
  which is what the `skilltest` target (and `just skilltest`) does.

The guard is an environment variable rather than a ``--skilltest-e2e`` flag
because ``pytest_addoption`` is only honoured in an *initial* conftest: moving
the eval into its own project moved this file out of the rootdir, where a
command-line option could no longer be registered reliably.
"""

import os

import pytest

_TRUTHY = {"1", "true", "yes", "on"}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "skilltest_e2e: opt-in, slow skilltest skill eval — needs SKILLTEST_E2E=1",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if os.environ.get("SKILLTEST_E2E", "").strip().lower() in _TRUTHY:
        return
    skip = pytest.mark.skip(
        reason="opt-in eval: set SKILLTEST_E2E=1 (or run `just skilltest`)"
    )
    for item in items:
        if "skilltest_e2e" in item.keywords:
            item.add_marker(skip)
