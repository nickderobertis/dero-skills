"""End-to-end tests for the create-repo baseline checker.

Drives the real PEP 723 script as a subprocess (``uv run --script``) so the
``--buildout`` path composes the config and actually shells out to ``llmlint``.
Only the genuinely-external harness is stubbed (a fake ``llmlint`` on PATH); the
checker itself, its config composition, and the subprocess boundary are real.

The repo-builder fixtures (``make_repo``/``_buildout_repo``) are shared with the
in-process unit layer in ``../test_check_repo_baseline.py`` and imported from it
(``tests/`` is placed on ``sys.path`` by ``../conftest.py``) so the builders stay
defined in exactly one place.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from test_check_repo_baseline import SCRIPT, _buildout_repo


def _write_stub_llmlint(dir_path: Path, exit_code: int) -> None:
    """Drop a fake `llmlint` on PATH — the genuinely-external harness we may stub."""
    stub = dir_path / "llmlint"
    stub.write_text(
        f'#!/usr/bin/env bash\necho "stub llmlint $*" >&2\nexit {exit_code}\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_script_with_stub(repo: Path, exit_code: int, tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub_llmlint(bin_dir, exit_code)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), str(repo), "--buildout"],
        capture_output=True,
        text=True,
        env=env,
    )


def test_e2e_buildout_invokes_real_llmlint_binary_and_passes(tmp_path):
    # Drive the real script end to end: it composes the config and actually shells
    # out to `llmlint` (a stub on PATH, exit 0). Only the external harness is stubbed.
    repo = _buildout_repo(tmp_path / "repo")
    result = _run_script_with_stub(repo, 0, tmp_path)
    assert result.returncode == 0, result.stderr


def test_e2e_buildout_propagates_llmlint_violations(tmp_path):
    # Stub llmlint fails (exit 1); the checker surfaces it as a failing invariant.
    repo = _buildout_repo(tmp_path / "repo")
    result = _run_script_with_stub(repo, 1, tmp_path)
    assert result.returncode == 1
    assert "structural issue" in result.stderr
