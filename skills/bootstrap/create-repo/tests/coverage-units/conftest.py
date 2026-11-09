"""The composed workspace every coverage-unit journey shares and restores after itself."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from coverage_fixture import Fixture  # noqa: E402


@pytest.fixture(scope="module")
def workspace(tmp_path_factory) -> Fixture:
    fixture = Fixture(tmp_path_factory.mktemp("coverage-units") / "repo")
    fixture.coverage()
    return fixture
