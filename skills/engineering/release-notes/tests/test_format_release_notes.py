"""Tests for the release-notes formatter.

The formatter is a Node.js script, so these tests drive it as a subprocess and
skip automatically when node is not installed (the skill declares Node as an
optional runtime dependency).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "format_release_notes.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _run(stdin: str) -> str:
    result = subprocess.run(
        ["node", str(SCRIPT)],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_groups_by_type():
    out = _run(
        "feat(api): add pagination\n"
        "fix: handle null token\n"
        "chore: bump deps\n"
    )
    assert "## Features" in out
    assert "- add pagination" in out
    assert "## Fixes" in out
    assert "- handle null token" in out
    assert "## Other" in out
    assert "- bump deps" in out


def test_detects_breaking_change():
    out = _run("refactor!: drop the deprecated v1 client\n")
    assert "## Breaking changes" in out
    assert "drop the deprecated v1 client" in out


def test_empty_input():
    out = _run("\n\n")
    assert "_No notable changes._" in out
