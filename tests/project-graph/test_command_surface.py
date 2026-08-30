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

import json
import os
import re
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


def test_the_gate_target_list_has_exactly_one_source() -> None:
    """The justfile owns the gate's targets; a second copy drifts in silence.

    `package.json` used to restate them in a `check` script nothing invoked, so
    adding a target to the gate left a stale list behind with no check to notice.
    Nothing outside the justfile may name an orchestrator target list.
    """
    scripts = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8")).get(
        "scripts", {}
    )
    restated = {
        name: body for name, body in scripts.items() if re.search(r"-t\s+[\w-]", body)
    }
    assert restated == {}, (
        "package.json restates the orchestrator's target list, which the justfile "
        f"owns — the two drift the moment the gate changes: {restated}"
    )


# The dry-run cases above read what a recipe *would* run. These execute it: real
# `just`, the real committed justfile, and every third-party launcher a recipe
# hands work off to replaced by a recording double. That is what makes `upgrade`
# — which otherwise relocks both ecosystems and runs the whole gate — checkable
# here, along with the multi-step recipes' ordering and their abort-on-failure
# behaviour, none of which a dry run observes.

STUB = """#!/bin/sh
# Recording double for a launcher a recipe delegates to (`uv`, `bun`, `bunx`) or
# for a script it shells out to. Logs the invocation, then succeeds — unless
# STUB_FAIL globs it, which is how the failure path is driven.
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$STUB_LOG"
case "$(basename "$0") $*" in
  ${STUB_FAIL:-__never_matches__}) echo "stub: $(basename "$0") failed" >&2; exit 3 ;;
esac
exit 0
"""


@pytest.fixture
def surface(tmp_path: Path) -> Path:
    """The repo's own justfile, with everything it delegates to doubled.

    The recipes are the layer under test, so `just` is real and the justfile is
    the committed one, byte for byte. It runs in a directory of its own so a
    recipe that would install, relock, or sweep the graph cannot touch this
    checkout.
    """
    shutil.copy(REPO_ROOT / "justfile", tmp_path / "justfile")
    for directory, names in (
        ("bin", ("uv", "bun", "bunx")),
        ("scripts", ("setup-llmlint.sh", "session-setup.sh")),
    ):
        (tmp_path / directory).mkdir()
        for name in names:
            double = tmp_path / directory / name
            double.write_text(STUB, encoding="utf-8")
            double.chmod(0o755)
    return tmp_path


def run_recipe(
    surface: Path, *args: str, fail_pattern: str | None = None
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run a recipe for real; return the result and what it actually invoked."""
    log = surface / "invocations.log"
    env = {k: v for k, v in os.environ.items() if k != "NX_BASE"}
    env["PATH"] = f"{surface / 'bin'}{os.pathsep}{env['PATH']}"
    env["STUB_LOG"] = str(log)
    if fail_pattern is not None:
        env["STUB_FAIL"] = fail_pattern
    result = subprocess.run(
        ["just", *args],
        cwd=surface,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    invoked = (
        [line.strip() for line in log.read_text(encoding="utf-8").splitlines()]
        if log.exists()
        else []
    )
    return result, [line for line in invoked if line]


GATE_SWEEP = f"bunx nx run-many {GATE_TARGETS}"


def test_bootstrap_runs_one_install_per_ecosystem(surface) -> None:
    result, invoked = run_recipe(surface, "bootstrap")
    assert result.returncode == 0, result.stdout + result.stderr
    assert invoked == [
        "bun install --frozen-lockfile",
        "uv sync --locked",
        "uv tool install --quiet shellcheck-py",
        "setup-llmlint.sh",
    ]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("check",), [f"bunx nx affected --base=origin/main {GATE_TARGETS}"]),
        (("check", "all"), [GATE_SWEEP]),
        (("test",), ["bunx nx affected --base=origin/main -t test"]),
        (("lint",), ["bunx nx affected --base=origin/main -t lint"]),
        (("format",), ["bunx nx run-many -t format"]),
    ],
)
def test_each_gate_recipe_runs_exactly_one_orchestrator_command(
    surface, args, expected
) -> None:
    result, invoked = run_recipe(surface, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert invoked == expected


def test_upgrade_relocks_both_ecosystems_then_sweeps_the_whole_graph(surface) -> None:
    # The one recipe no dry run can vouch for on its own: its last line recurses
    # into `just check all`, so what it finally asks the orchestrator for is only
    # visible by running it.
    result, invoked = run_recipe(surface, "upgrade")
    assert result.returncode == 0, result.stdout + result.stderr
    assert invoked == [
        "uv lock --upgrade",
        "uv sync",
        "bun update",
        GATE_SWEEP,
    ]


def test_upgrade_stops_at_a_failed_relock_instead_of_gating_a_stale_tree(
    surface,
) -> None:
    # If the relock fails, the sweep that follows would report on the OLD
    # dependencies — a pass that means nothing. The recipe must abort first.
    result, invoked = run_recipe(surface, "upgrade", fail_pattern="uv lock*")
    assert result.returncode != 0
    assert invoked == ["uv lock --upgrade"]
    assert GATE_SWEEP not in invoked


@pytest.mark.parametrize(
    ("recipe", "project_target", "args"),
    [
        # Both live outside the gate — `skilltest` drives a real ~20-30min harness
        # bootstrap, `lint-llm` a real model — so the graph, not the recipe, is
        # what keeps them out of it. The recipe's whole job is to name the right
        # project:target and hand the caller's narrowing arguments through
        # untouched: `just skilltest -x` and `just lint-llm <path>` are the
        # documented ways to run one case, or one file, instead of all of them.
        ("skilltest", "bootstrap-create-repo-skilltest:skilltest", ("-x",)),
        (
            "lint-llm",
            "llmlint-tier:lint-llm",
            ("scripts/setup-llmlint.sh", "justfile"),
        ),
    ],
)
def test_an_expensive_recipe_delegates_and_forwards_what_narrows_it(
    surface, recipe: str, project_target: str, args: tuple[str, ...]
) -> None:
    delegated = f"bunx nx run {project_target}"

    bare, invoked = run_recipe(surface, recipe)
    assert bare.returncode == 0, bare.stdout + bare.stderr
    assert invoked == [delegated]

    # The double appends to one log across both runs, so the caller's arguments
    # show up as the second entry — the same delegation, with them tacked on
    # verbatim and in order.
    narrowed, invoked = run_recipe(surface, recipe, *args)
    assert narrowed.returncode == 0, narrowed.stdout + narrowed.stderr
    assert invoked == [delegated, f"{delegated} {' '.join(args)}"]


@pytest.mark.parametrize(
    ("recipe", "project_target"),
    [
        ("skilltest", "bootstrap-create-repo-skilltest:skilltest"),
        ("lint-llm", "llmlint-tier:lint-llm"),
    ],
)
def test_an_expensive_recipe_reports_the_failure_of_what_it_delegated_to(
    surface, recipe: str, project_target: str
) -> None:
    # These two are the recipes a human runs by hand and reads the exit code of,
    # with no gate behind them to catch a swallowed status: a failed eval that
    # exits 0 reads as a passing one.
    result, invoked = run_recipe(surface, recipe, fail_pattern="bunx nx run*")
    assert result.returncode != 0
    assert invoked == [f"bunx nx run {project_target}"]
