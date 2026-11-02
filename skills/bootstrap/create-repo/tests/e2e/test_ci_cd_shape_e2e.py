"""End-to-end: the CI/CD shape a composed repository starts inside.

Each case composes a repository with the real composer
(`compose_repo_plan.py --ci-cd`, as a subprocess) for a Rust or a TypeScript
shape, cross-platform or Linux-only, then reads what it wrote: the workflows'
jobs and timeouts, the root budgets through the real `onebudgetspec list` and
`validate`, the figures script byte for byte against the skill's source. The
same compositions are then handed to the real baseline checker — unmodified
(passes) and with one part of the shape taken away (each reported) — and to the
real governance script's offline dry run.

The budget values are the ones the three repositories that proved the shape
registered in their root budgets.yaml at their landing commits:

* onepipeline     58ba93a881768108f0a66e1ffd193b65977995e4 (nick/949/onepipeline-ci-budgets)
* onetaskgraph    a8f736ba4f53e56255ccac8a7be47f71c3672249 (nick/3633/onetaskgraph-ci-budgets)
* onepipeline-ui  cc93c9ca1c1d10eca031eef245df084ccfa631ea (nick/343/onepipeline-ui-ci-budgets-clean)

All three carry the same unit, label and threshold for every budget but
`-ci-check-time`, where onepipeline's is 10800 and the other two's 1800, the
30-minute line the skill ships. Their CD commands also pass `--exclude-workflow`
naming each repository's own main-push cache warmer; the composed shape has no
such workflow, so its commands carry no exclusion and are otherwise identical.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from pathlib import Path

import pytest
from ci_cd_shape import (
    ASSETS,
    COMPOSITIONS,
    GOVERNANCE,
    NAME,
    baseline,
    compose,
    jobs,
    run,
    workflow,
)

FIGURES = ["bash", "scripts/actions-budget-figures.sh"]
# Each budget as the landing commits register it: command, unit, threshold, label.
LANDED = {
    "ci-check-time": (
        [*FIGURES, "longest-job", "--stage", "pr"],
        "seconds",
        1800,
        "release-tail",
    ),
    "cd-job-time": (
        [*FIGURES, "longest-job", "--stage", "cd"],
        "seconds",
        1800,
        "release-tail",
    ),
    "release-cycle-time": (
        [*FIGURES, "release-cycle-time"],
        "seconds",
        5400,
        "release-tail",
    ),
    "ci-job-count": (
        [*FIGURES, "job-count", "--stage", "pr"],
        "jobs",
        None,
        "release-tail",
    ),
    "cd-job-count": (
        [*FIGURES, "job-count", "--stage", "cd"],
        "jobs",
        None,
        "release-tail",
    ),
    "release-read-requests": (
        [*FIGURES, "release-read-requests"],
        "requests",
        6,
        "release-tail",
    ),
    "pr-read-requests": ([*FIGURES, "pr-read-requests"], "requests", 2, "release-tail"),
    "gate-time": (
        ["node", "scripts/gate-time-budget.mjs"],
        "seconds",
        1800,
        "host-variable",
    ),
}
# What each composition runs: its CI jobs and its CD jobs (release-plz.yml's one,
# then release.yml's), in the order the workflows declare them.
CI_JOBS = {
    "cross-platform": ["lint", "test-linux", "test-windows", "test-macos"],
    "linux": ["lint", "test-linux"],
}
CD_JOBS = {
    "cross-platform": [
        "release-plz",
        "build-linux",
        "build-linux-arm64",
        "build-macos",
        "build-windows",
        "publish",
    ],
    "linux": ["release-plz", "build-linux", "build-linux-arm64", "publish"],
}


def onebudgetspec() -> str:
    found = shutil.which("onebudgetspec")
    if found is None:
        pytest.fail("onebudgetspec not on PATH — run `just bootstrap`")
    return found


@pytest.fixture(params=sorted(COMPOSITIONS))
def composed(request, tmp_path):
    language, platforms, arch = COMPOSITIONS[request.param]
    repo = compose(tmp_path / "repo", language, platforms, arch)
    return repo, language, platforms, arch


def expected_ci_jobs(platforms: str, arch: str | None) -> list[str]:
    ci = CI_JOBS[platforms]
    return [ci[0], f"visual-docs-{arch}", *ci[1:]] if arch else ci


def test_ci_runs_lint_and_a_test_job_per_platform_each_held_to_30_minutes(composed):
    repo, _language, platforms, arch = composed
    ci = workflow(repo, "ci.yml")
    found = jobs(ci)
    assert list(found) == expected_ci_jobs(platforms, arch)
    for name, body in found.items():
        assert "    timeout-minutes: 30" in body.splitlines(), name
        assert "needs:" not in body, f"{name} waits on another job"
    # Pull requests only: no push to main, nothing re-run after the merge.
    on = ci[ci.index("\non:\n") : ci.index("\nconcurrency:")].splitlines()
    assert [line for line in on if line and not line.startswith("#")] == [
        "on:",
        "  pull_request:",
    ]


def test_lint_runs_everything_platform_independent_once(composed):
    repo, language, _platforms, _arch = composed
    lint = jobs(workflow(repo, "ci.yml"))["lint"]
    for step in (
        "amannn/action-semantic-pull-request@",
        "nickderobertis/notignored@",
        "run: just lint\n",
        "run: just supply-chain",
        "just lint-llm-validate --diff-base origin/main",
        "just lint-llm-diff origin/main",
    ):
        assert step in lint + "\n", step
    tests = [
        b for n, b in jobs(workflow(repo, "ci.yml")).items() if n.startswith("test-")
    ]
    for body in tests:
        assert "run: just ci-test" in body
        assert "lint-llm" not in body and "supply-chain" not in body
    msrv = "cargo-hack" in lint
    assert msrv == (language == "rust")


def test_lint_writes_contents_only_where_it_publishes_visual_docs(composed):
    repo, _language, _platforms, arch = composed
    lint = jobs(workflow(repo, "ci.yml"))["lint"]
    grants = lint[lint.index("permissions:\n") : lint.index("steps:")]
    contents = re.findall(r"^\s+contents: (\w+)", grants, re.MULTILINE)
    # Only the visual docs push to gh-pages; every other composition stays read-only.
    assert contents == (["write"] if arch else ["read"]), grants


def test_the_release_runs_release_plz_then_a_build_job_per_platform_and_publish(
    composed,
):
    repo, language, platforms, _arch = composed
    plz = jobs(workflow(repo, "release-plz.yml"))
    release = jobs(workflow(repo, "release.yml"))
    assert [*plz, *release] == CD_JOBS[platforms]
    for name, body in {**plz, **release}.items():
        assert "    timeout-minutes: 30" in body.splitlines(), name
    builds = [n for n in release if n.startswith("build-")]
    assert f"    needs: [{', '.join(builds)}]" in release["publish"].splitlines()
    arm = release["build-linux-arm64"]
    assert "runs-on: ubuntu-24.04-arm" in arm
    for phase in ("build", "publish", "verify"):
        assert f"just release build-linux-arm64 {phase} --tag" in arm
    if language == "rust":
        assert "target: aarch64-unknown-linux-gnu" in arm
        assert "release-plz release-pr" in plz["release-plz"]
        if platforms == "cross-platform":
            assert "target: aarch64-apple-darwin" in release["build-macos"]
    else:
        # release-plz versions Cargo workspaces only: the job keeps its id and runs
        # release-please.
        assert "googleapis/release-please-action@" in plz["release-plz"]
    assert (
        "gh pr merge" in plz["release-plz"] and "--auto --squash" in plz["release-plz"]
    )
    # No test re-run at release time, and no separate published-smoke workflow.
    assert "ci-test" not in workflow(repo, "release.yml")
    assert sorted(p.name for p in (repo / ".github" / "workflows").iterdir()) == [
        "ci.yml",
        "release-plz.yml",
        "release.yml",
    ]


def test_publish_ends_in_the_release_tail_after_installing_the_pinned_onevcs(composed):
    repo, *_ = composed
    publish = jobs(workflow(repo, "release.yml"))["publish"]
    steps = publish.split("\n      - ")
    install, tail = steps[-2], steps[-1]
    assert "onevcs-cli@0.57.6" in install and "@onebudgetspec/cli@0.1.3" in install
    assert tail.rstrip().endswith(
        "run: onebudgetspec check budgets.yaml --label release-tail"
    )
    assert "GH_TOKEN: ${{ github.token }}" in tail
    assert "continue-on-error" not in publish
    for scope in ("actions: read", "contents: read", "pull-requests: read"):
        assert scope in publish


def test_the_root_budgets_are_the_eight_the_three_repositories_register(composed):
    repo, _language, platforms, arch = composed
    tool = onebudgetspec()
    valid = run([tool, "validate", "budgets.yaml"], cwd=repo)
    assert valid.returncode == 0, valid.stderr
    assert "valid: 8 budget(s)" in valid.stdout
    listed = run([tool, "list", "--json", "budgets.yaml"], cwd=repo)
    budgets = {b["id"]: b for b in json.loads(listed.stdout)["budgets"]}
    assert sorted(budgets) == sorted(f"{NAME}-{suffix}" for suffix in LANDED)
    counts = {
        "ci-job-count": len(expected_ci_jobs(platforms, arch)),
        "cd-job-count": len(CD_JOBS[platforms]),
    }
    for suffix, (command, unit, threshold, label) in LANDED.items():
        budget = budgets[f"{NAME}-{suffix}"]
        assert budget["command"] == command, suffix
        assert budget["unit"] == unit, suffix
        # llmlint: ignore[tests_hold_no_nonfunctional_thresholds] The threshold is the composed repository's budgets.yaml content the task fixes to what the three repositories' landing commits register, not a performance bar this suite holds itself to: a wrong value here is a wrong file the composer wrote.
        assert budget["threshold"] == counts.get(suffix, threshold), suffix
        assert budget["labels"] == [label], suffix
        assert budget["measure"] == "reported" and budget["direction"] == "max"


def test_the_figures_script_is_a_verbatim_copy_of_the_skills(composed):
    repo, *_ = composed
    copy = repo / "scripts" / "actions-budget-figures.sh"
    source = ASSETS / "budgets" / "actions-budget-figures.sh"
    assert copy.read_bytes() == source.read_bytes()


def test_the_gate_records_its_wall_clock_and_checks_it_as_a_warning(composed):
    repo, *_ = composed
    justfile = (repo / "justfile").read_text(encoding="utf-8")
    assert "\ngate:\n    @bash scripts/gate-time.sh just gate-body\n" in justfile
    budgets_recipe = justfile[justfile.index("\nbudgets tier=") :]
    assert (
        "onebudgetspec check budgets.yaml --exclude-label release-tail "
        "--exclude-label host-variable" in budgets_recipe
    )
    wrapper = (repo / "scripts" / "gate-time.sh").read_text(encoding="utf-8")
    assert wrapper.startswith("#!/usr/bin/env bash\n")
    assert "check budgets.yaml --label host-variable" in wrapper
    assert f"{NAME}-gate-time is over its budget" in wrapper
    assert ".gate-time.json" in (repo / ".gitignore").read_text(encoding="utf-8")
    # CI owns the gate: nothing installs a pre-push hook.
    assert not (repo / ".husky").exists()
    assert "pre-push:" not in justfile
    assert "husky" not in (repo / "package.json").read_text(encoding="utf-8")


def test_every_placeholder_a_job_runs_fails_naming_what_to_implement(composed):
    # The recipes only the repository can fill — the supply chain and each release
    # phase — are run exactly as the composed workflows call them, with the real
    # `just`: each says what to implement for that job and phase and exits
    # non-zero, so an unfilled recipe fails its job rather than passing it.
    repo, _language, platforms, _arch = composed
    calls = {
        f"{name}: {line.strip()[len('run: ') :]}"
        for file in ("ci.yml", "release.yml")
        for name, body in jobs(workflow(repo, file)).items()
        for line in body.splitlines()
        if line.strip().startswith(("run: just supply-chain", "run: just release "))
    }
    release_phases = {
        build: ("build", "publish", "verify")
        for build in CD_JOBS[platforms]
        if build.startswith("build-")
    }
    release_phases["publish"] = ("available", "build", "publish", "verify")
    assert calls == {"lint: just supply-chain"} | {
        f'{job}: just release {job} {phase} --tag "$TAG"'
        for job, phases in release_phases.items()
        for phase in phases
    }
    env = {**os.environ, "TAG": "v1.2.3"}
    env.pop("NX_BASE", None)
    for call in sorted(calls):
        _job, command = call.split(": ", 1)
        argv = shlex.split(command.replace('"$TAG"', env["TAG"]))
        done = run(argv, cwd=repo, env=env)
        assert done.returncode != 0, (call, done.stdout)
        assert "TODO: implement" in done.stdout, (call, done.stdout)
        if argv[1] == "release":
            assert f"phase {argv[3]} of release job {argv[2]}" in done.stdout, call
        else:
            assert "supply-chain check the CI lint job runs" in done.stdout, call


def test_composing_again_changes_nothing_and_a_differing_file_is_refused(tmp_path):
    repo = compose(tmp_path / "repo")
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()}
    again = run(
        [
            "uv", "run", "--script", str(ASSETS.parent / "scripts" / "compose_repo_plan.py"),
            "--ci-cd", str(repo), "--language", "rust", "--name", NAME,
        ]
    )  # fmt: skip
    assert again.returncode == 0, again.stderr
    assert {p: p.read_bytes() for p in repo.rglob("*") if p.is_file()} == before

    ci = repo / ".github" / "workflows" / "ci.yml"
    ci.write_text("name: ci\n", encoding="utf-8")
    refused = run(
        [
            "uv", "run", "--script", str(ASSETS.parent / "scripts" / "compose_repo_plan.py"),
            "--ci-cd", str(repo), "--language", "rust", "--name", NAME,
            "--platforms", "linux",
        ]
    )  # fmt: skip
    assert refused.returncode == 2
    assert "ci.yml already exists and differs" in refused.stderr
    assert ci.read_text(encoding="utf-8") == "name: ci\n"


def test_a_language_without_a_known_toolchain_is_refused(tmp_path):
    refused = run(
        [
            "uv", "run", "--script", str(ASSETS.parent / "scripts" / "compose_repo_plan.py"),
            "--ci-cd", str(tmp_path), "--language", "python", "--name", NAME,
        ]
    )  # fmt: skip
    assert refused.returncode == 2
    assert "--ci-cd needs exactly one of --language rust / --language typescript" in (
        refused.stderr
    )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("platforms", ["cross-platform", "linux"])
def test_the_governance_dry_run_requires_exactly_lint_and_the_test_jobs(
    tmp_path, platforms
):
    repo = compose(tmp_path / "repo", platforms=platforms)
    done = run(
        [
            "uv", "run", "--script", str(GOVERNANCE),
            "--repo", "acme/demo", "--branch", "main", "--dry-run",
        ],
        cwd=repo,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    want = CI_JOBS[platforms]
    assert f"  required checks: {', '.join(want)} (non-strict)" in done.stdout
    protection = done.stdout[done.stdout.index('"required_status_checks"') :]
    assert re.search(
        r'"strict": false,\s+"contexts": \[\s+'
        + r",\s+".join(f'"{job}"' for job in want)
        + r"\s+\]",
        protection,
    ), protection

    opted = run(
        [
            "uv", "run", "--script", str(GOVERNANCE), "--repo", "acme/demo",
            "--branch", "main", "--dry-run", "--strict",
        ],
        cwd=repo,
    )  # fmt: skip
    assert f"  required checks: {', '.join(want)} (strict)" in opted.stdout


def test_governance_refuses_a_lint_job_that_runs_no_llmlint(tmp_path):
    repo = compose(tmp_path / "repo")
    ci = repo / ".github" / "workflows" / "ci.yml"
    text = ci.read_text(encoding="utf-8")
    ci.write_text(
        text.replace("just lint-llm-diff origin/main", "true"), encoding="utf-8"
    )
    done = run(
        [
            "uv", "run", "--script", str(GOVERNANCE),
            "--repo", "acme/demo", "--branch", "main", "--dry-run",
        ],
        cwd=repo,
    )  # fmt: skip
    assert done.returncode == 1
    assert "no llmlint check among the required status checks" in done.stderr


def test_the_baseline_checker_passes_the_unmodified_composition(composed):
    repo, *_ = composed
    done = baseline(repo)
    assert done.returncode == 0, done.stderr
    assert "OK    baseline invariants satisfied" in done.stdout


def edit(repo: Path, rel: str, old: str, new: str) -> None:
    path = repo / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{rel} has no {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def drop_job(repo: Path, name: str) -> None:
    ci = repo / ".github" / "workflows" / "ci.yml"
    text = ci.read_text(encoding="utf-8")
    body = jobs(text)[name]
    ci.write_text(text.replace(f"  {name}:\n{body}\n", ""), encoding="utf-8")


CI = ".github/workflows/ci.yml"
EXTRA_JOB = (
    "  coverage:\n    runs-on: ubuntu-latest\n    timeout-minutes: 30\n"
    "    steps:\n      - run: just coverage\n"
)
# Each way a composition can fall out of the shape, and the finding it must raise.
BREAKS = {
    "a CI job dropped": (
        lambda repo: drop_job(repo, "test-windows"),
        "ci.yml lacks the CI job(s) test-windows",
    ),
    "a job's timeout dropped": (
        lambda repo: edit(repo, CI, "    timeout-minutes: 30\n", ""),
        "job `lint` is not held to 30 minutes (timeout-minutes: unset)",
    ),
    "a job's timeout raised": (
        lambda repo: edit(
            repo,
            CI,
            "    runs-on: ubuntu-latest\n    timeout-minutes: 30\n    steps:",
            "    runs-on: ubuntu-latest\n    timeout-minutes: 45\n    steps:",
        ),
        "job `test-linux` is not held to 30 minutes (timeout-minutes: 45)",
    ),
    "a budget dropped": (
        lambda repo: edit(repo, "budgets.yaml", f"  - id: {NAME}-pr-read-requests", "  - id: gone"),
        "lacks the budget(s) <name>-pr-read-requests",
    ),
    "the figures script dropped while the budgets run it": (
        lambda repo: (repo / "scripts" / "actions-budget-figures.sh").unlink(),
        "runs scripts/actions-budget-figures.sh, but the repository holds no copy",
    ),
    "a pull-request job added": (
        lambda repo: edit(repo, CI, "\njobs:\n", "\njobs:\n" + EXTRA_JOB),
        "ci.yml runs job(s) coverage on pull requests",
    ),
    "a pull-request workflow added": (
        lambda repo: (repo / ".github" / "workflows" / "notignored.yml").write_text(
            "name: notignored\non:\n  pull_request:\njobs:\n  suppressions:\n"
            "    runs-on: ubuntu-latest\n    steps:\n      - run: true\n",
            encoding="utf-8",
        ),
        "notignored.yml runs job(s) suppressions on pull requests",
    ),
    "CI on push to main": (
        lambda repo: edit(repo, CI, "\non:\n  pull_request:\n", "\non:\n  pull_request:\n  push:\n    branches: [main]\n"),
        "ci.yml runs CI on push to main",
    ),
    "an extra required context": (
        lambda repo: edit(repo, ".github/governance.json", '"lint",', '"lint",\n    "llmlint",'),
        "governance.json requires llmlint, beyond `lint` and the test jobs",
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(BREAKS))
def test_the_baseline_checker_reports_a_composition_out_of_shape(tmp_path, name):
    repo = compose(tmp_path / "repo")
    breaking, finding = BREAKS[name]
    breaking(repo)
    done = baseline(repo)
    assert done.returncode == 1, done.stdout
    assert finding in done.stderr, done.stderr


def test_non_strict_protection_is_not_reported(tmp_path):
    # Up-to-date branches are an opt-in, so either strictness passes.
    repo = compose(tmp_path / "repo")
    governance = json.loads((repo / ".github" / "governance.json").read_text())
    assert governance["strict"] is False
    assert baseline(repo).returncode == 0
    governance["strict"] = True
    (repo / ".github" / "governance.json").write_text(json.dumps(governance))
    assert baseline(repo).returncode == 0


def test_a_repository_without_the_shape_is_not_held_to_it(tmp_path):
    # The governance record is what marks the shape; without it (and without a
    # release workflow's `publish`) the repository is on another release model.
    repo = compose(tmp_path / "repo")
    (repo / ".github" / "governance.json").unlink()
    (repo / ".github" / "workflows" / "release.yml").unlink()
    drop_job(repo, "test-linux")
    done = baseline(repo)
    assert "lacks the CI job" not in done.stderr
    assert done.returncode == 0, done.stderr
