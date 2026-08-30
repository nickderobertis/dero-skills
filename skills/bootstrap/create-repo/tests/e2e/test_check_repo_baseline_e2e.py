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

from test_check_repo_baseline import (
    FULL_JUSTFILE,
    NO_ORCHESTRATOR_JUSTFILE,
    SCRIPT,
    _buildout_repo,
    make_repo,
)

# The gate delegating with Nx's comma-separated target list (`--targets=a,b`)
# rather than the spaced `-t a b` of FULL_JUSTFILE. Both spellings reach the
# checker from a real justfile, so both have to resolve to the same targets.
COMMA_TARGETS_JUSTFILE = FULL_JUSTFILE.replace(
    "bunx nx affected -t lint typecheck test build --base=origin/main",
    "bunx nx affected --targets=lint,typecheck,test,build --base=origin/main",
)
assert "--targets=lint,typecheck,test,build" in COMMA_TARGETS_JUSTFILE

# The same spelling with `test` dropped from the list: the suite is out of the
# gate, which the checker has to keep catching through the comma form too.
COMMA_TARGETS_WITHOUT_TEST_JUSTFILE = COMMA_TARGETS_JUSTFILE.replace(
    "--targets=lint,typecheck,test,build", "--targets=lint,typecheck,build"
)


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
    # The stub echoes its argv (surfaced in the finding detail): the committed
    # (ongoing) config is merged in AHEAD of the temp buildout config, so inline
    # ignore directives naming ongoing rules resolve at llmlint's preflight and
    # the repo's own settings win over the temp config's defaults.
    assert f"-c {repo / 'llmlint.yml'}" in result.stderr
    assert result.stderr.index(str(repo / "llmlint.yml")) < result.stderr.index(
        "buildout-"
    )
    # The buildout per-judge ceiling rides the CLI flag — the only place it beats
    # the committed config's own `oneharness.timeout` under first-config-wins.
    assert "--timeout 900" in result.stderr


def _run_script(repo: Path):
    """Run the deterministic checks the way an author does: the real script, no flags."""
    return subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), str(repo)],
        capture_output=True,
        text=True,
    )


def test_e2e_repo_without_a_project_graph_warns_but_still_exits_zero(tmp_path):
    # The gap the mandate cares about, seen exactly as an author sees it: the
    # advisory lands on stdout, names the standard and the reference that teaches
    # it, and the command still succeeds — an already-bootstrapped repo is guided,
    # not broken.
    repo = tmp_path / "no-graph"
    repo.mkdir()
    make_repo(repo, project_graph=False, justfile=NO_ORCHESTRATOR_JUSTFILE)
    result = _run_script(repo)
    assert result.returncode == 0, result.stderr
    assert "FAIL" not in result.stderr
    warning = [line for line in result.stdout.splitlines() if line.startswith("WARN")]
    assert len(warning) == 1, result.stdout
    assert "project graph" in warning[0]
    assert "monorepo orchestrator" in warning[0]
    assert "modularize" in warning[0]
    assert "references/project-graph.md" in warning[0]
    # Advisory findings still carry a concrete next action.
    assert "fix:" in result.stdout
    assert "1 advisory note(s)" in result.stdout


def test_e2e_repo_with_a_project_graph_reports_nothing_to_fix(tmp_path):
    # The same command over a repo that has the graph and delegates to it: no
    # advisory at all, and the quiet single-line success the checker promises.
    repo = tmp_path / "graph"
    repo.mkdir()
    make_repo(repo)
    result = _run_script(repo)
    assert result.returncode == 0, result.stderr
    assert "project graph" not in result.stdout
    assert [line for line in result.stdout.splitlines() if line.strip()] == [
        f"OK    baseline invariants satisfied: {repo.resolve()}"
    ]


def test_e2e_comma_separated_targets_count_as_running_the_suite(tmp_path):
    # A gate that hands `test` to the orchestrator in Nx's comma-separated
    # spelling runs the suite just as the spaced one does. Parsed as a single
    # token, `test` would be invisible and the checker would wrongly fail the
    # repo, so this is the command-line proof that the list is split on commas.
    repo = tmp_path / "comma"
    repo.mkdir()
    make_repo(repo, justfile=COMMA_TARGETS_JUSTFILE)
    result = _run_script(repo)
    assert result.returncode == 0, result.stderr
    assert "does not run `test`" not in result.stderr
    assert [line for line in result.stdout.splitlines() if line.strip()] == [
        f"OK    baseline invariants satisfied: {repo.resolve()}"
    ]


def test_e2e_comma_separated_targets_without_test_still_fail_the_gate(tmp_path):
    # The other half of that proof: splitting on commas must not turn into
    # accepting any list that merely contains the substring. Drop `test` from the
    # same comma-separated fan-out and the author sees the error and exit 1.
    repo = tmp_path / "comma-no-test"
    repo.mkdir()
    make_repo(repo, justfile=COMMA_TARGETS_WITHOUT_TEST_JUSTFILE)
    result = _run_script(repo)
    assert result.returncode == 1
    assert "does not run `test`" in result.stderr
    assert "make `check` depend on `test`" in result.stderr
