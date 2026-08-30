"""End-to-end tests for the root command surface that drives the graph.

The recipes are the interface everybody — contributor, hook, CI — actually uses,
and each one resolves a *tier* and a *merge base* before Nx ever sees them. Those
two decisions are the whole of the staged gate, and they are made in `just`, so
they are tested through `just`:

* `just --dry-run <recipe>` is a real user-facing flag — it is how you ask a
  justfile what a recipe will run — and because the tier is resolved in `just`
  rather than in a shell `if`, its output IS the one orchestrator command that
  would execute. Nothing is parsed out of the justfile text.
* The paths that must fail before anything runs — a mistyped tier, an `NX_BASE`
  or `--diff-base` that is not a git ref — are driven as ordinary `just` runs and
  asserted on the real exit code and message.

This module lives beside the graph tests because it covers the same seam from the
other side: those assert what Nx selects, these assert what the surface asks Nx
for.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("just") is None,
    reason="the command surface needs `just`; run `just session-setup`",
)


def run_just(*args: str, env: dict[str, str] | None = None):
    # NX_BASE is *input* to the surface under test, so it comes from the test and
    # never from the ambient environment: CI exports it (nx-set-shas), and a test
    # asserting the fallback would otherwise read CI's SHA and fail there while
    # passing locally. Tests that mean to supply one pass it in `env`.
    ambient = {k: v for k, v in os.environ.items() if k != "NX_BASE"}
    return subprocess.run(
        ["just", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={**ambient, **(env or {})},
    )


def dry_run(*args: str, env: dict[str, str] | None = None) -> str:
    """What a recipe would run, as `just` itself reports it."""
    result = run_just("--dry-run", *args, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


GATE_TARGETS = "-t format-check lint validate smoke test"


def test_the_default_tier_is_affected_and_keyed_off_a_merge_base() -> None:
    assert f"nx affected --base=origin/main {GATE_TARGETS}" in dry_run("check")


def test_the_broader_tier_is_a_full_sweep_over_the_same_targets() -> None:
    # Same recipe, same targets — the tier is a flag on one command, never a
    # second gate, so local and CI cannot drift.
    assert f"nx run-many {GATE_TARGETS}" in dry_run("check", "all")


def test_an_unknown_tier_aborts_instead_of_buying_the_weaker_one() -> None:
    result = run_just("check", "affcted")
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "unknown tier" in combined, combined
    # `just` refuses while resolving the recipe, so the orchestrator never runs —
    # no Nx banner in the output. (The echoed recipe line does mention `nx`.)
    assert "NX " not in combined, combined


def test_ci_can_hand_the_merge_base_in_through_the_environment() -> None:
    # `nx-set-shas` exports NX_BASE; the recipe must key off it rather than Nx's
    # non-deterministic implicit default.
    assert "nx affected --base=abc1234 " in dry_run("check", env={"NX_BASE": "abc1234"})


@pytest.mark.parametrize("hostile", ["x;rm -rf /", "$(whoami)", 'a"b'])
def test_a_merge_base_that_is_not_a_git_ref_is_refused_at_the_boundary(
    hostile: str,
) -> None:
    result = run_just("--dry-run", "check", env={"NX_BASE": hostile})
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "NX_BASE must be a plain git ref or SHA" in combined, combined


def test_the_diff_base_argument_is_validated_the_same_way() -> None:
    ok = dry_run("lint-llm-diff", "v1.2.3")
    assert '--diff-base "v1.2.3"' in ok, ok

    result = run_just("--dry-run", "lint-llm-diff", "main;id")
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "--diff-base must be a plain git ref or SHA" in combined, combined


@pytest.mark.parametrize(
    ("recipe", "expected"),
    [
        ("test", "nx affected --base=origin/main -t test"),
        ("lint", "nx affected --base=origin/main -t lint"),
        ("format-check", "nx affected --base=origin/main -t format-check"),
        # Formatting is not a "what changed" question, so it sweeps the graph.
        ("format", "nx run-many -t format"),
        ("validate", "nx affected --base=origin/main -t validate smoke"),
        ("baseline", "nx run repo-baseline:test"),
        ("check-versions", "nx run authoring-tools:validate"),
    ],
)
def test_every_recipe_delegates_to_the_orchestrator(recipe: str, expected: str) -> None:
    # None of them may hand-roll a loop over projects; the orchestrator is what
    # decides which projects a target runs in.
    assert expected in dry_run(recipe)


def test_upgrade_re_runs_the_gate_as_the_broader_tier() -> None:
    # An upgrade can reach any project, so the affected set would understate it.
    body = dry_run("upgrade")
    assert "uv lock --upgrade" in body, body
    assert "just check all" in body, body


def test_bootstrap_installs_each_ecosystem_once() -> None:
    # One resolve per ecosystem, never one per project — and the two uv-installed
    # binaries the gate's own targets shell out to.
    body = dry_run("bootstrap")
    for step in ("bun install", "uv sync", "shellcheck-py", "setup-llmlint.sh"):
        assert step in body, (step, body)
