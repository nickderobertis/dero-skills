"""End-to-end eval for the `create-repo` skill via ``skilltest-pytest``.

This drives the skill through a real harness so it bootstraps a real repository
on a **real local filesystem**, then checks the result **deterministically**:

- the skill's own baseline checker (`check_repo_baseline.py`) must pass against
  the produced directory — the load-bearing assertion;
- the expected files exist (Rust crate, AGENTS.md, the `CLAUDE.md` symlink, CI);
- the hello-world CLI actually **builds and prints a greeting** (`cargo run`);
- a deterministic **mock-call** eval in the case asserts no destructive command
  ran (no LLM judge, so no judge flakiness).

The GitHub remote is mocked, so the run never creates
`nickderobertis/create-repo-e2e-rust-cli` or pushes: the case's `gh`/`git push`
mocks intercept the model's direct calls, and a fake `gh` on `PATH` neutralizes
any `gh` a skill script spawns internally (the mock hook only sees the model's
own tool calls, not nested subprocesses).

Why the `run_skill` code form and not an auto-collected `*.skilltest.yaml`: this
eval needs a provider (`oneharness` on `PATH`, or a custom `SKILLTEST_PROVIDER`)
plus a harness token — the same reason `llmlint` is kept out of the uv-only
`just check` gate. Auto-collected cases have no skip hook and would error under
`just check`; here we `skipif` so a clean clone stays green and the eval runs
for real only when a provider is available.

The eval runs the harness in bypass mode inside an ephemeral, throwaway
workspace (a pytest ``tmp_path``): it must be able to create files, and a
sandboxed container (CI, Claude Code on the web) is the intended home. It is
never part of `just check`.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from skilltest_pytest import describe_failures, run_skill

SKILL_DIR = Path(__file__).resolve().parents[1]
BASELINE_CHECKER = SKILL_DIR / "scripts" / "check_repo_baseline.py"
CASE = Path(__file__).resolve().parent / "cases" / "create_repo_rust_cli.yaml"
# Raises the harness run timeout past oneharness's 120s default (a full bootstrap
# needs far longer); see the file's comment.
CONFIG = Path(__file__).resolve().parent / "skilltest.yaml"


def _provider_available() -> bool:
    """True when skilltest can reach a provider without extra setup.

    A custom ``SKILLTEST_PROVIDER`` is taken at face value; otherwise the default
    ``oneharness`` provider must be on ``PATH``. When neither holds the eval
    skips rather than fails, keeping the uv-only gate green on a clean clone.
    """
    if os.environ.get("SKILLTEST_PROVIDER"):
        return True
    return shutil.which("oneharness") is not None


def _write_fake_gh(bin_dir: Path) -> None:
    """A no-op `gh` that never touches the network — the belt to the case mocks'
    braces, covering any `gh` a skill script spawns as a nested subprocess (which
    the harness mock hook, seeing only the model's own tool calls, would miss)."""
    gh = bin_dir / "gh"
    gh.write_text(
        '#!/usr/bin/env bash\necho "mock gh: $* (no remote created)"\nexit 0\n',
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _find_repo_root(workspace: Path) -> Path:
    """The skill is told to bootstrap in place, so the repo root is the workspace.
    Fall back to a single nested directory that carries an ``AGENTS.md`` in case
    the model created a subdirectory anyway."""
    if (workspace / "AGENTS.md").exists():
        return workspace
    nested = [p.parent for p in workspace.glob("*/AGENTS.md")]
    if len(nested) == 1:
        return nested[0]
    return workspace


def _tree(root: Path, limit: int = 60) -> str:
    """A compact listing of what the skill produced — attached to failures so a
    failing run is diagnosable from the report alone."""
    paths = sorted(
        p for p in root.rglob("*") if "/target/" not in f"/{p.relative_to(root)}/"
    )
    lines = [str(p.relative_to(root)) for p in paths[:limit]]
    if len(paths) > limit:
        lines.append(f"... (+{len(paths) - limit} more)")
    return "\n".join(lines) or "(empty)"


def _run_baseline_checker(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--script", str(BASELINE_CHECKER), str(repo)],
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(
    not _provider_available(),
    reason="no skilltest provider available (set SKILLTEST_PROVIDER or put oneharness on PATH)",
)
def test_create_repo_bootstraps_a_baseline_passing_rust_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    # Fake `gh` first on PATH so no run — direct or nested — creates a real remote.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_gh(bin_dir)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    # The harness must be able to write files; give it bypass in this throwaway
    # sandbox (IS_SANDBOX lets Claude Code bypass under the container's root user).
    monkeypatch.setenv("ONEHARNESS_MODE", "bypass")
    monkeypatch.setenv("IS_SANDBOX", "1")

    # run_skill inherits this process's env and runs the harness in `cwd`.
    report = run_skill(
        CASE,
        platforms=["claude-code"],
        models=["claude-opus-4-8"],
        config=CONFIG,
        cwd=workspace,
    )

    # 1. The case's deterministic mock-call eval (no destructive command ran).
    assert report.passed, describe_failures(report)

    repo = _find_repo_root(workspace)
    tree = _tree(repo)

    # 2. The load-bearing assertion: the skill's own baseline checker passes.
    baseline = _run_baseline_checker(repo)
    assert baseline.returncode == 0, (
        f"baseline checker failed on the produced repo:\n"
        f"{baseline.stdout}\n{baseline.stderr}\n--- produced tree ---\n{tree}"
    )

    # 3. It is actually a Rust CLI, with the agent layer wired up.
    for expected in ("Cargo.toml", "src/main.rs", "AGENTS.md"):
        assert (repo / expected).exists(), f"missing {expected}\n--- tree ---\n{tree}"
    assert (repo / "CLAUDE.md").is_symlink(), f"CLAUDE.md is not a symlink\n{tree}"
    assert os.readlink(repo / "CLAUDE.md") == "AGENTS.md"

    # 4. The composition was recorded and names the actual stack.
    agents = (repo / "AGENTS.md").read_text(encoding="utf-8").lower()
    assert "rust" in agents, "AGENTS.md does not mention the Rust stack it composed for"

    # 5. The hello-world CLI genuinely builds and prints a greeting.
    if shutil.which("cargo"):
        build = subprocess.run(
            ["cargo", "build", "--quiet"], cwd=repo, capture_output=True, text=True
        )
        assert build.returncode == 0, f"cargo build failed:\n{build.stderr}"
        run = subprocess.run(
            ["cargo", "run", "--quiet"], cwd=repo, capture_output=True, text=True
        )
        assert run.returncode == 0, f"cargo run failed:\n{run.stderr}"
        assert "hello" in run.stdout.lower(), f"CLI did not greet: {run.stdout!r}"
