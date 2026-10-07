"""The baseline checker's command line over a repository built from fleet shapes.

Each shape the in-process suite (``../test_check_repo_baseline_fleet.py``) pins
on its own is assembled here into one git work tree and audited the way an
author does: the real PEP 723 script as a ``uv run --script`` subprocess, its
output and exit code read as they are printed. The repository is clean, then
broken one way at a time, so the command is seen both passing a shape it used to
misreport and still failing the genuine gap beside it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from test_check_repo_baseline import SCRIPT, make_repo, write_package
from test_check_repo_baseline_fleet import (
    CHAIN,
    CI_DIR_HOOK_SETTINGS,
    DOGFOOD_WORKFLOW,
    FLEET_GATES,
    LITERAL_TEMPLATES_AGENTS,
    NOTIGNORED_ACTION_MANIFEST,
    POETRY_MANIFEST,
    PROVISIONS_JUST,
    RELEASE_WORKFLOW_MENTIONING_THE_ACTION,
    git,
    untyped_manifest,
    write_manifest,
)


def run_checker(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), str(repo)],
        capture_output=True,
        text=True,
    )


def fleet_repo(root: Path, *, in_git: bool = True) -> Path:
    """One repository carrying every fleet shape, each written as the fleet has it."""
    root.mkdir()
    justfile = FLEET_GATES["shell-expansion-subcommand"].replace(
        "lint-llm-diff:\n    @echo lint-llm-diff\n",
        "@lint-llm-diff *args:\n    llmlint --diff --diff-base origin/main {{args}}\n",
    )
    repo = make_repo(
        root,
        justfile=justfile,
        composition=LITERAL_TEMPLATES_AGENTS,
        settings=CI_DIR_HOOK_SETTINGS,
        notignored=DOGFOOD_WORKFLOW,
        oneharness=False,
    )
    (repo / "scripts" / "setup-llmlint.sh").unlink()
    ci = repo / "scripts" / "ci"
    ci.mkdir()
    (ci / "session-setup.sh").write_text(PROVISIONS_JUST, encoding="utf-8")
    (ci / "setup-llmlint.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    for name, text in CHAIN.items():
        (repo / name).write_text(text, encoding="utf-8")
    (repo / ".github" / "workflows" / "release.yml").write_text(
        RELEASE_WORKFLOW_MENTIONING_THE_ACTION, encoding="utf-8"
    )
    (repo / "action.yml").write_text(NOTIGNORED_ACTION_MANIFEST, encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "e2e.rs").write_text(
        "/// from mock-server/mockserver-monorepo, whose draft-04 meta-schema `$ref` is\n"
        "#[test]\nfn generates() {}\n",
        encoding="utf-8",
    )
    write_manifest(
        repo,
        "sdks/fern",
        POETRY_MANIFEST.format(classifier='"Typing :: Typed",'),
        marker=True,
        package="src/fern",
    )
    write_manifest(
        repo,
        "assets/scaffolding",
        untyped_manifest('name = "@@CROZIER_SDK_NAME@@"'),
        marker=False,
        package="sdk",
    )
    write_package(
        repo, marker=False, classifiers=(), project_dir="tests/fixtures/zoonk/expected"
    )
    write_package(
        repo, marker=False, classifiers=(), project_dir="runs/plan-1/checkout"
    )
    (repo / ".gitignore").write_text("runs/\n", encoding="utf-8")
    if in_git:
        git(repo, "init", "-q")
    return repo


def test_e2e_a_repo_built_from_fleet_shapes_passes_without_a_note(tmp_path):
    repo = fleet_repo(tmp_path / "fleet")
    result = run_checker(repo)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert [line for line in result.stdout.splitlines() if line.strip()] == [
        f"OK    baseline invariants satisfied: {repo.resolve()}"
    ]


def test_e2e_tracking_the_ignored_manifest_makes_it_the_repos_to_type(tmp_path):
    repo = fleet_repo(tmp_path / "fleet")
    git(repo, "add", "-f", "runs/plan-1/checkout/pyproject.toml")
    result = run_checker(repo)
    assert result.returncode == 1
    errors = [line for line in result.stderr.splitlines() if line.startswith("ERROR")]
    assert errors == [
        "ERROR runs/plan-1/checkout/pyproject.toml (hatchling.build) publishes an "
        "untyped distribution: missing a `py.typed` marker and the "
        "`Typing :: Typed` classifier"
    ], result.stderr


def test_e2e_outside_git_the_ignore_file_leaves_nothing_out(tmp_path):
    repo = fleet_repo(tmp_path / "fleet", in_git=False)
    result = run_checker(repo)
    assert result.returncode == 1
    assert "runs/plan-1/checkout/pyproject.toml (hatchling.build)" in result.stderr


def test_e2e_a_broken_extends_chain_is_reported_not_raised(tmp_path):
    repo = fleet_repo(tmp_path / "fleet")
    (repo / "oneharness.identities.toml").unlink()
    result = run_checker(repo)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert (
        "ERROR oneharness.toml cannot be resolved: oneharness.dispatch.toml extends "
        "'oneharness.identities.toml', which does not exist"
    ) in result.stderr


def test_e2e_a_hook_pointing_at_nothing_still_fails(tmp_path):
    repo = fleet_repo(tmp_path / "fleet")
    (repo / "scripts" / "ci" / "session-setup.sh").unlink()
    result = run_checker(repo)
    assert result.returncode == 1
    assert (
        "ERROR SessionStart hook runs session-setup.sh but the script is missing"
        in result.stderr
    )
