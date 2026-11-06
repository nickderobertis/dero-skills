"""The generated budgets job, driven at GitHub's boundary with the released action.

The composer writes real Rust CLI, web and skills repositories. Each journey reads the
`budgets` job out of the generated ci.yml and runs what its `uses:` pins with its `with:`
inputs: action.yml's own steps, the released driver and plugin archive, the published core
wheel, the real `gh` and the real `actions/upload-artifact` code. Only GitHub's API and
artifact services, the release host and the package index are doubled, by the harness the
action ships at that same tag, so a pin bump brings the harness that matches it. Reaching the
release, PyPI and codeload is why this sits in the external tier; offline it fails rather
than skips.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

import pytest
import yaml

from ci_cd_shape import CHECKER, COMPOSER, FIXTURES, NAME, compose
from http_fetch import fetch_all

ACTION_REPOSITORY = "nickderobertis/onebudgetspec-github-action"
REPORT_BUDGET = f"{NAME}-budget-report-command-seconds"
RUNNER_TARGETS = {
    ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("Linux", "aarch64"): "aarch64-unknown-linux-gnu",
    ("Darwin", "arm64"): "aarch64-apple-darwin",
}
COUNT = 'echo "$ONEBUDGETSPEC_BUDGET_ID" >> "$JOURNEY_COUNTS"'

# A catalog with different units, measures and commands, most without the job's label.
RUNNABLE = {
    "demo-bundle-bytes": "bytes",
    "demo-coverage-percent": "percent",
    "demo-host-reads": "requests",
    "demo-walltime": "seconds",
    "demo-laptop-figure": "count",
}
# Over its line, and report-only because it carries one of the job's excluded labels.
REPORT_ONLY_OVER = "demo-laptop-figure"
# Every budget the composer writes at the root, and why its command cannot measure in the
# job: the failure's own words, which is what its row has to show a reader.
FIGURES_NEED_ONEVCS = "onevcs is not on PATH"
GENERATED_UNMEASURABLE = {
    f"{NAME}-ci-check-time": FIGURES_NEED_ONEVCS,
    f"{NAME}-cd-job-time": FIGURES_NEED_ONEVCS,
    f"{NAME}-release-cycle-time": FIGURES_NEED_ONEVCS,
    f"{NAME}-ci-job-count": FIGURES_NEED_ONEVCS,
    f"{NAME}-cd-job-count": FIGURES_NEED_ONEVCS,
    f"{NAME}-release-read-requests": FIGURES_NEED_ONEVCS,
    f"{NAME}-pr-read-requests": FIGURES_NEED_ONEVCS,
    f"{NAME}-nx-cache-use": "no release: pass --release TAG",
    f"{NAME}-gate-time": "Cannot find package '@onebudgetspec/sdk'",
}
UNAVAILABLE = {
    "demo-needs-cargo": "needs cargo",
    "demo-needs-telemetry": "dist/telemetry/journey.json",
}
CATALOG = f"""\
schema_version: 1
budgets:
  - id: demo-bundle-bytes
    description: The size of the demo bundle. It protects the download a reader waits on.
    measure: reported
    command: [bash, -c, '{COUNT}; echo "{{\\"value\\": $(cat bundle-size)}}" > "$ONEBUDGETSPEC_RESULT"']
    unit: bytes
    direction: max
    threshold: 1000
  - id: demo-coverage-percent
    description: The share of lines the tests reach. It protects against untested code landing.
    measure: reported
    command: [node, measure-coverage.mjs]
    unit: percent
    direction: min
    threshold: 80
  - id: demo-host-reads
    description: The GitHub reads one run spends. It protects the rate limit other jobs share.
    labels: [ci-report]
    measure: reported
    command: [bash, -c, '{COUNT}; gh api repos/owner/repo/commits/main > /dev/null; echo "{{\\"value\\": 1}}" > "$ONEBUDGETSPEC_RESULT"']
    unit: requests
    direction: max
    threshold: 5
  - id: demo-walltime
    description: How long the smallest command takes. It protects a developer's fastest feedback.
    measure: elapsed
    command: [bash, -c, '{COUNT}']
    unit: seconds
    direction: max
    threshold: 60
  - id: demo-laptop-figure
    description: Startup work on a developer's machine. It protects local turnaround.
    labels: [host-variable]
    measure: reported
    command: [bash, -c, '{COUNT}; echo "{{\\"value\\": 50}}" > "$ONEBUDGETSPEC_RESULT"']
    unit: count
    direction: max
    threshold: 10
  - id: demo-needs-cargo
    description: Allocations in the Rust measuring tool. It protects that tool's memory use.
    labels: [ci-report]
    measure: reported
    command: [cargo, run, --quiet, -p, measure]
    unit: count
    direction: max
    threshold: 10
  - id: demo-needs-telemetry
    description: Slow steps in the user journey. It protects the journey's responsiveness.
    measure: reported
    command: [node, measure-coverage.mjs, dist/telemetry/journey.json]
    unit: count
    direction: max
    threshold: 10
"""
MEASURE = """\
import { appendFileSync, writeFileSync } from 'node:fs';
appendFileSync(process.env.JOURNEY_COUNTS, `${process.env.ONEBUDGETSPEC_BUDGET_ID}\\n`);
writeFileSync(process.env.ONEBUDGETSPEC_RESULT, JSON.stringify({ value: 91, detail: 'lines' }));
"""
PROJECT = json.dumps(
    {
        "name": "demo",
        "targets": {
            "budgets": {
                "command": "bunx onebudgetspec check packages/demo/budgets.yaml"
            }
        },
    }
)
CATALOG_FILES = {
    "packages/demo/budgets.yaml": CATALOG,
    "packages/demo/measure-coverage.mjs": MEASURE,
    "packages/demo/bundle-size": "500\n",
    "packages/demo/project.json": PROJECT,
}


class Composition(NamedTuple):
    shape: str
    language: str
    platforms: str
    # The product that shape holds, which the composer reads for what it publishes.
    files: dict[str, str]


COMPOSITIONS = {
    "rust-cli": Composition(
        "cli",
        "rust",
        "cross-platform",
        {
            "Cargo.toml": '[workspace]\nmembers = ["crates/demo-cli"]\nresolver = "3"\n',
            "crates/demo-cli/Cargo.toml": (
                '[package]\nname = "demo-cli"\nversion = "0.1.0"\nedition = "2024"\n'
            ),
            "crates/demo-cli/src/main.rs": 'fn main() {\n    println!("demo");\n}\n',
            ".cargo/config.toml": (
                '[build]\ntarget-dir = "target"\n\n[profile.dev]\ndebug = 1\n'
            ),
        },
    ),
    "web": Composition(
        "web-app",
        "typescript",
        "linux",
        {"apps/web/app/page.tsx": "export default () => <main>demo</main>;\n"},
    ),
    "skills": Composition(
        "skills-repo",
        "typescript",
        "linux",
        {
            "skills/demo/SKILL.md": (
                "---\nname: demo\ndescription: Use when a journey needs a skill.\n"
                "compatibility: none\n---\n"
            ),
        },
    ),
}


def action_tag() -> str:
    text = COMPOSER.read_text(encoding="utf-8")
    return text.split('ONEBUDGETSPEC_ACTION_VERSION = "', 1)[1].split('"', 1)[0]


class Released(NamedTuple):
    tag: str
    tree: Path
    harness: ModuleType
    assets: dict[str, bytes]


@pytest.fixture(scope="session")
def released(tmp_path_factory) -> Released:
    """The action at the composer's pin: its tree, its harness and its real payload."""
    tag = action_tag()
    version = tag.removeprefix("v")
    directory = tmp_path_factory.mktemp("released-action")
    releases = f"https://github.com/{ACTION_REPOSITORY}/releases/download/{tag}"
    target = RUNNER_TARGETS[(platform.system(), platform.machine())]
    archive_name = f"onebudgetspec-github-action-{version}-{target}.tar.gz"
    names = (archive_name, "SHA256SUMS", "action.yml")
    source, *bodies = fetch_all(
        [
            f"https://codeload.github.com/{ACTION_REPOSITORY}/tar.gz/refs/tags/{tag}",
            *(f"{releases}/{name}" for name in names),
        ]
    )
    with tarfile.open(fileobj=io.BytesIO(source)) as archive:
        for member in archive.getmembers():
            member.name = member.name.split("/", 1)[-1]
            archive.extract(member, directory, filter="data")
    assets = dict(zip(names, bodies, strict=True))
    listed = f"{hashlib.sha256(assets[archive_name]).hexdigest()}  {archive_name}"
    assert listed in assets["SHA256SUMS"].decode().splitlines()
    saved = directory / "dist" / archive_name
    saved.parent.mkdir()
    saved.write_bytes(assets[archive_name])
    spec = importlib.util.spec_from_file_location(
        "released_action_harness", directory / "tests" / "action-harness" / "harness.py"
    )
    assert spec is not None and spec.loader is not None
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    # The harness builds this archive from source for the action's own tests; a consumer
    # gets the released one.
    harness._cached["archive"] = saved
    return Released(tag, directory, harness, assets)


@pytest.fixture(scope="session")
def runner_path(tmp_path_factory) -> str:
    """What a hosted runner has for a budget command: no uv, cargo, bun or onevcs."""
    directory = tmp_path_factory.mktemp("runner-bin")
    node = shutil.which("node")
    assert node is not None, "node not on PATH"
    (directory / "node").symlink_to(node)
    return f"{directory}{os.pathsep}/usr/bin{os.pathsep}/bin"


class Journey:
    """A composed repository and one host double, driven through the generated job."""

    def __init__(self, released: Released, runner_path: str, root: Path, kind: str):
        self.released = released
        self.harness = released.harness
        self.host = self.harness.Host()
        self.repository = self.harness.Repository()
        self.runner_path = runner_path
        self.counts = root / "counts"
        self.next_run = 1000
        self.jobs = []
        self.composition = COMPOSITIONS[kind]
        self.path = compose(
            self.repository.path,
            language=self.composition.language,
            platforms=self.composition.platforms,
            files={**CATALOG_FILES, **self.composition.files},
            shape=self.composition.shape,
        )
        self.workflow = yaml.safe_load(
            (self.path / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        )
        self.base_sha = self.repository.commit("composed")

    def close(self) -> None:
        for job in self.jobs:
            job.close()
        self.repository.close()
        self.host.close()

    def inputs(self) -> dict[str, str]:
        """The action reference and `with:` the generated job really names."""
        steps = self.workflow["jobs"]["budgets"]["steps"]
        uses = [s for s in steps if s.get("uses", "").startswith(ACTION_REPOSITORY)]
        assert [s["uses"] for s in uses] == [f"{ACTION_REPOSITORY}@{self.released.tag}"]
        given = dict(uses[0]["with"])
        assert given.pop("token") == "${{ github.token }}"
        return {"token": "generated-job-token", **given}

    def run(self, event_name: str, sha: str, **options):
        self.next_run += 1
        self.counts.unlink(missing_ok=True)
        job = self.harness.Job(
            self.host,
            self.repository,
            event_name,
            sha,
            inputs=self.inputs(),
            run_id=self.next_run,
            environment={
                "JOURNEY_COUNTS": str(self.counts),
                "PATH": self.runner_path,
                # What a runner sets for every step, which the figures script reads.
                "GITHUB_REPOSITORY": self.harness.REPOSITORY,
                "GITHUB_RUN_ID": str(self.next_run),
                "RUNNER_NAME": "hosted-runner",
            },
            **options,
        )
        self.jobs.append(job)
        return job.run()

    def base_push(self):
        self.repository.git("checkout", "--quiet", "main")
        sha = self.repository.commit("merged")
        self.base_sha = sha
        return self.run("push", sha)

    def pull_request(self, bundle_size: int | None = None, **event):
        self.repository.git("checkout", "--quiet", "-B", "change", self.base_sha)
        if bundle_size is not None:
            self.repository.write("packages/demo/bundle-size", f"{bundle_size}\n")
        head = self.repository.commit("change")
        self.host.head = head
        return self.run(
            "pull_request",
            head,
            event=self.harness.event_pull_request(1, head, self.base_sha, **event),
            base_ref="main",
            register=False,
        )

    def executed(self) -> list[str]:
        return sorted(self.counts.read_text().split()) if self.counts.exists() else []


def table_rows(markdown: str, budget: str) -> list[str]:
    return [
        line
        for line in markdown.splitlines()
        if line.startswith("|") and f"/ {budget} " in line
    ]


def one_table_row(markdown: str, budget: str) -> str:
    rows = table_rows(markdown, budget)
    assert len(rows) == 1, (budget, rows)
    return rows[0]


def row_verdict(row: str) -> str:
    """The cell after the budget's name, which holds its verdict or says it has none."""
    return row.split("|")[2].strip()


def assert_unavailable_with_reasons(job) -> None:
    """Each budget that cannot measure: one unavailable row, its reason, and no `error`."""
    budget_run = job.saved("budget-run.json")
    measured = budget_run["results"]["results"]
    assert not [r["id"] for r in measured if r["verdict"] == "error"]
    assert not {r["id"] for r in measured} & set(GENERATED_UNMEASURABLE)
    reasons = {u["id"]: u["reason"] for u in budget_run["unavailable"]}
    report = {row["id"]: row for row in job.saved("comparison-report.json")["rows"]}
    markdown = job.markdown()
    for budget, reason in GENERATED_UNMEASURABLE.items():
        assert reason in reasons[budget], reasons[budget]
        assert "verdict" not in report[budget], report[budget]
        assert reason in report[budget]["unavailable"], report[budget]
        row = one_table_row(markdown, budget)
        assert row_verdict(row) == "unavailable", row
        assert reason in row, row
        assert "report-only" in row, row


@pytest.fixture(params=sorted(COMPOSITIONS))
def every_composition(request, released, runner_path, tmp_path):
    journey = Journey(released, runner_path, tmp_path, request.param)
    yield journey
    journey.close()


@pytest.fixture
def journey(released, runner_path, tmp_path):
    journey = Journey(released, runner_path, tmp_path, "rust-cli")
    yield journey
    journey.close()


def test_the_generated_job_reports_every_budget_and_runs_each_command_once(
    every_composition,
):
    journey = every_composition
    triggers = journey.workflow[True]  # YAML 1.1 reads the bare `on` key as true
    assert set(triggers) == {"pull_request", "push"}
    assert triggers["pull_request"] is None, "no path, branch or draft filter"
    assert triggers["push"] == {"branches": ["main"]}
    budgets = journey.workflow["jobs"]["budgets"]
    assert budgets["if"] == "always()"
    assert set(budgets["needs"]) == set(journey.workflow["jobs"]) - {"budgets"}
    # Discovery must skip these: either would make `list` refuse the whole catalog.
    journey.repository.write("tests/fixtures/budgets.yaml", "not: a budgets file\n")
    journey.repository.write("conformance/case/budgets.yaml", "not: a budgets file\n")
    journey.base_sha = journey.repository.commit("fixture trees")
    job = journey.pull_request()
    assert job.exit_code == 0, job.stdout

    budgets = sorted({*RUNNABLE, *UNAVAILABLE, REPORT_BUDGET, *GENERATED_UNMEASURABLE})
    report = job.saved("comparison-report.json")
    assert sorted(row["id"] for row in report["rows"]) == budgets
    for budget in budgets:
        one_table_row(job.markdown(), budget)

    assert journey.executed() == sorted(RUNNABLE)
    budget_run = job.saved("budget-run.json")
    verdicts = {r["id"]: r for r in budget_run["results"]["results"]}
    for budget, unit in RUNNABLE.items():
        wanted = "over" if budget == REPORT_ONLY_OVER else "within"
        assert verdicts[budget]["verdict"] == wanted, budget
        assert verdicts[budget]["unit"] == unit
    assert verdicts["demo-coverage-percent"]["actual"] == 91

    unavailable = {u["id"]: u["reason"] for u in budget_run["unavailable"]}
    assert set(unavailable) == {*UNAVAILABLE, *GENERATED_UNMEASURABLE}
    for budget, reason in UNAVAILABLE.items():
        assert reason in unavailable[budget], unavailable[budget]
    assert budget_run["enforcement_exclude_labels"] == ["release-tail", "host-variable"]

    assert len(journey.host.comments) == 1
    body = journey.host.comments[0]["body"]
    assert "report-only" in body
    for budget, _ in UNAVAILABLE.items():
        assert budget in body

    # The job's own budget ran after the main upload and was saved with its verdict.
    assert job.outcomes["main-upload"] == "success", job.stdout
    assert job.outcomes["final-upload"] == "success", job.stdout
    saved = json.loads(
        journey.host.artifact_files(job.run_id)["budget-run.json"].decode()
    )
    own = {r["id"]: r for r in saved["results"]["results"]}[REPORT_BUDGET]
    assert own["verdict"] in ("within", "over"), "a real verdict, saved"
    assert own["unit"] == "seconds"
    assert journey.host.artifact_files(job.run_id, "budget-results-main") is not None


def test_a_base_push_saves_the_same_results_and_posts_nothing(journey):
    pull_request = journey.pull_request()
    journey.host.clear_requests()
    posted = len(journey.host.comments)
    base = journey.base_push()
    assert base.exit_code == 0, base.stdout
    assert len(journey.host.comments) == posted
    assert journey.executed() == sorted(RUNNABLE)

    base_files = journey.host.artifact_files(base.run_id)
    pull_files = journey.host.artifact_files(pull_request.run_id)
    assert set(base_files) == {*pull_files, "rolling-history.json"}
    history = json.loads(base_files["rolling-history.json"].decode())
    assert [run["head_sha"] for run in history["runs"]] == [base.sha]
    assert {r["id"] for r in history["runs"][0]["results"]["results"]} == {
        r["id"]
        for r in json.loads(pull_files["budget-run.json"].decode())["results"][
            "results"
        ]
    }


def test_history_is_unavailable_below_five_saved_commits_then_provisional(journey):
    budget = "demo-bundle-bytes"
    for _ in range(4):
        assert journey.base_push().exit_code == 0
    few = journey.pull_request()
    assert few.rows()[budget]["baseline_n"] == 4
    assert "minimum 5" in few.rows()[budget]["unavailable"]
    assert few.exit_code == 0, few.stdout

    assert journey.base_push().exit_code == 0
    enough = journey.pull_request()
    assert enough.exit_code == 0, enough.stdout
    assert enough.rows()[budget]["baseline_n"] == 5
    assert "provisional" in enough.markdown()


def test_an_enforced_budget_over_its_line_fails_and_a_report_only_one_does_not(journey):
    report_only = journey.pull_request()
    assert report_only.exit_code == 0, report_only.stdout
    assert report_only.rows()[REPORT_ONLY_OVER]["verdict"] == "over"
    row = next(
        line
        for line in report_only.markdown().splitlines()
        if f"/ {REPORT_ONLY_OVER} " in line
    )
    assert "report-only" in row

    enforced = journey.pull_request(bundle_size=5000)
    assert enforced.exit_code == 1, enforced.stdout
    assert enforced.rows()["demo-bundle-bytes"]["verdict"] == "over"
    assert journey.executed() == sorted(RUNNABLE)
    assert len(journey.host.comments) == 1


def test_only_an_adverse_move_is_flagged_and_a_flag_fails_nothing(journey):
    for _ in range(5):
        assert journey.base_push().exit_code == 0
    worse = journey.pull_request(bundle_size=900)
    assert worse.exit_code == 0, worse.stdout
    row = worse.rows()["demo-bundle-bytes"]
    assert row["verdict"] == "within"
    assert row["flag"] is True, row

    better = journey.pull_request(bundle_size=100)
    assert better.exit_code == 0, better.stdout
    row = better.rows()["demo-bundle-bytes"]
    assert row["current"] == 100
    assert row["flag"] is False, row


def test_budgets_for_a_release_or_a_laptop_are_unavailable_with_their_reason(
    every_composition,
):
    """They keep their ordinary commands, which cannot measure here and cost no read."""
    journey = every_composition
    composed = yaml.safe_load((journey.path / "budgets.yaml").read_text())["budgets"]
    assert {budget["id"] for budget in composed} == set(GENERATED_UNMEASURABLE)

    journey.host.clear_requests()
    job = journey.pull_request()
    assert job.exit_code == 0, job.stdout
    assert journey.host.count("/releases|/runs/\\d+/jobs|/compare") == 0
    assert_unavailable_with_reasons(job)

    body = journey.host.comments[0]["body"]
    for budget, reason in GENERATED_UNMEASURABLE.items():
        row = one_table_row(body, budget)
        assert row_verdict(row) == "unavailable", row
        assert reason in row, row

    base = journey.base_push()
    assert base.exit_code == 0, base.stdout
    assert_unavailable_with_reasons(base)
    history = json.loads(
        journey.host.artifact_files(base.run_id)["rolling-history.json"].decode()
    )
    measured = {r["id"] for r in history["runs"][0]["results"]["results"]}
    assert not measured & set(GENERATED_UNMEASURABLE)


def test_a_fork_gets_a_summary_and_artifact_without_a_comment_or_credential(journey):
    base = journey.base_push()
    journey.host.clear_requests()
    job = journey.pull_request(head_repository="someone/fork")
    assert job.exit_code == 0, job.stdout
    assert journey.host.comments == []
    assert journey.host.count("commits/main") == 0, "a budget command reached the host"
    assert "someone/fork" in job.summary
    assert journey.host.artifact_files(job.run_id) is not None
    assert base.exit_code == 0


def test_posting_and_history_failures_are_diagnostics_and_a_real_failure_survives(
    journey,
):
    journey.host.write_status = 403
    posted = journey.pull_request()
    assert posted.exit_code == 0, posted.stdout
    assert "not delivered" in posted.summary
    journey.host.write_status = None

    journey.host.refuse.append(
        {"path": r"actions/runs/\d+$", "status": 429, "times": 1}
    )
    unread = journey.pull_request()
    assert unread.exit_code == 0, unread.stdout
    assert "baseline unavailable" in unread.markdown()

    journey.host.write_status = 403
    failed = journey.pull_request(bundle_size=5000)
    assert failed.exit_code == 1, failed.stdout
    assert "not delivered" in failed.summary


def test_the_composition_passes_the_baseline_and_composes_again_unchanged(
    every_composition, tmp_path
):
    journey = every_composition
    checked = subprocess.run(
        ["uv", "run", "--script", str(CHECKER), str(journey.path)],
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr
    before = {
        p: p.read_bytes()
        for p in journey.path.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(journey.path).parts[:1]
    }
    chosen = journey.composition
    again = subprocess.run(
        [
            "uv", "run", "--script", str(COMPOSER), "--ci-cd", str(journey.path),
            "--shape", chosen.shape, "--language", chosen.language, "--name", NAME,
            "--platforms", chosen.platforms,
        ],
        capture_output=True,
        text=True,
    )  # fmt: skip
    assert again.returncode == 0, again.stderr
    after = {p: p.read_bytes() for p in before}
    assert after == before


def test_the_plan_for_each_shape_composes_the_budget_reference(tmp_path):
    for chosen in COMPOSITIONS.values():
        done = subprocess.run(
            [
                "uv", "run", "--script", str(COMPOSER),
                "--shape", chosen.shape, "--language", chosen.language,
            ],
            capture_output=True,
            text=True,
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
        assert "onebudgetspec-github-action" in done.stdout


def test_the_vendored_contract_is_the_release_itself(released, tmp_path):
    """The fast tier's fixtures are this release's bytes and this release's --help."""
    vendored = FIXTURES / "onebudgetspec-github-action" / released.tag
    assert (vendored / "action.yml").read_bytes() == released.assets["action.yml"]
    assert (vendored / "SHA256SUMS").read_bytes() == released.assets["SHA256SUMS"]
    assert (released.tree / "action.yml").read_bytes() == released.assets["action.yml"]

    archive_name = next(n for n in released.assets if n.endswith(".tar.gz"))
    with tarfile.open(fileobj=io.BytesIO(released.assets[archive_name])) as archive:
        archive.extractall(tmp_path, filter="data")
    driver = tmp_path / "onebudgetspec-github-action"
    version = subprocess.run(
        [str(driver), "--version"], capture_output=True, text=True, check=True
    )
    assert version.stdout.split()[-1] == released.tag.removeprefix("v")
    shown = subprocess.run(
        [str(driver), "telemetry", "--help"], capture_output=True, text=True, check=True
    )
    assert shown.stdout == (vendored / "telemetry-help.txt").read_text(encoding="utf-8")
    source = json.loads((vendored / "telemetry-help.source.json").read_text())
    listed = f"{source['archive_sha256']}  {source['archive']}"
    assert listed in released.assets["SHA256SUMS"].decode().splitlines()
    shown_digest = hashlib.sha256(shown.stdout.encode()).hexdigest()
    assert shown_digest == source["help_sha256"]
