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
import re
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
# The root budgets a pull request cannot measure, which leave its table for one line.
GENERATED_OMITTED = {
    f"{NAME}-{suffix}"
    for suffix in (
        "cd-job-time",
        "release-cycle-time",
        "cd-job-count",
        "release-read-requests",
        "pr-read-requests",
        "nx-cache-use",
        "gate-time",
    )
}
OMITTED_LINE = (
    f"{len(GENERATED_OMITTED)} budgets are left out because a pull request cannot "
    "measure them (label not-measured-on-pr)."
)
CI_CHECK_TIME = f"{NAME}-ci-check-time"
CI_JOB_COUNT = f"{NAME}-ci-job-count"
GENERATED_MEASURED = {CI_CHECK_TIME, CI_JOB_COUNT}
# The job's token reaches this one, unlike onevcs's figures: it has no figure here only
# because this host double serves no cache route, and on GitHub the report shows its reading.
CACHE_HEADROOM = f"{NAME}-cache-headroom"
NO_CACHE_ROUTE = "actions/cache/storage-limit answered HTTP 401"
# Off a pull request's own run those two read a release, and this job has no onevcs.
FIGURES_NEED_ONEVCS = "onevcs is not on PATH"
# Why a budget whose argv cannot run here has no row figure, once saved results are read.
NO_SAVED_RESULT = "no saved result was found on this run"
UNAVAILABLE = ("demo-needs-cargo", "demo-needs-telemetry")
# Measured in test-linux only: its telemetry never reaches the budgets job's checkout.
SAVED_BUDGET = "demo-saved-figure"
SAVED_FIGURE = 37
# This run's jobs as a recorded listing shapes them: the longest counted job, and the
# job running the report, which has no time yet.
LONGEST_JOB_SECONDS = 600
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
SAVED_CATALOG = f"""\
schema_version: 1
budgets:
  - id: {SAVED_BUDGET}
    description: Requests the demo test records. It protects the upstream the test reaches.
    measure: reported
    command: [node, measure.mjs, dist/telemetry/saved.json]
    unit: requests
    direction: max
    threshold: 100
"""
SAVED_MEASURE = """\
import { readFileSync, writeFileSync } from 'node:fs';
writeFileSync(process.env.ONEBUDGETSPEC_RESULT, readFileSync(process.argv[2]));
"""
SAVED_PROJECT = json.dumps(
    {
        "name": "saved",
        "targets": {
            "budgets": {
                "command": "onebudgetspec check packages/saved/budgets.yaml "
                "--save-results target/budget-results/saved.json",
                "outputs": ["{workspaceRoot}/target/budget-results/saved.json"],
            }
        },
    }
)
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
    "packages/saved/budgets.yaml": SAVED_CATALOG,
    "packages/saved/measure.mjs": SAVED_MEASURE,
    "packages/saved/project.json": SAVED_PROJECT,
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
def runner_path(tmp_path_factory, gh_session_bus_trap) -> str:
    """What a hosted runner has for a budget command: no uv, cargo, bun or onevcs."""
    directory = tmp_path_factory.mktemp("runner-bin")
    node = shutil.which("node")
    assert node is not None, "node not on PATH"
    (directory / "node").symlink_to(node)
    return os.pathsep.join([str(directory), gh_session_bus_trap, "/usr/bin", "/bin"])


class Journey:
    """A composed repository and one host double, driven through the generated job."""

    def __init__(
        self,
        released: Released,
        runner_path: str,
        root: Path,
        kind: str,
        catalog: dict[str, str] = CATALOG_FILES,
    ):
        self.token = "generated-job-token"
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
            files={**catalog, **self.composition.files},
            shape=self.composition.shape,
        )
        self.workflow = yaml.safe_load(
            (self.path / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        )
        self.base_sha = self.repository.commit("composed")
        self._serve_this_runs_jobs()

    def _serve_this_runs_jobs(self) -> None:
        """GitHub's job listing, which the harness the action ships has no need of."""
        answer_otherwise = self.host.read

        def read(handler, path: str):
            listed = re.fullmatch(
                r"/repos/owner/repo/actions/runs/(\d+)/jobs", path.split("?")[0]
            )
            if listed is None:
                return answer_otherwise(handler, path)
            jobs = self.run_jobs(int(listed[1]))
            body = json.dumps({"total_count": len(jobs), "jobs": jobs}).encode()
            return self.host.reply(handler, 200, body)

        self.host.read = read

    def run_jobs(self, run_id: int) -> list[dict]:
        """This run's jobs: each the workflow names, and the budgets job still running."""

        def job(name: str, seconds: int | None) -> dict:
            return {
                "id": len(name),
                "run_id": run_id,
                "workflow_name": self.workflow["name"],
                "name": name,
                "status": "completed" if seconds is not None else "in_progress",
                "conclusion": "success" if seconds is not None else None,
                "started_at": "2026-11-07T07:00:00Z",
                "completed_at": (
                    f"2026-11-07T07:{seconds // 60:02}:{seconds % 60:02}Z"
                    if seconds is not None
                    else None
                ),
                "labels": ["ubuntu-latest"],
                "runner_name": f"GitHub Actions {len(name)}",
            }

        seconds = {"test-linux": LONGEST_JOB_SECONDS, "budgets": None}
        return [job(name, seconds.get(name, 120)) for name in self.workflow["jobs"]]

    def test_linux(self, run_id: int) -> None:
        """The job before `budgets`: each generated target, then its real upload's inputs."""
        self.repository.write(
            "packages/saved/dist/telemetry/saved.json",
            json.dumps({"value": SAVED_FIGURE}),
        )
        for project in (".github/reporting", "packages/saved"):
            target = json.loads((self.path / project / "project.json").read_text())
            done = subprocess.run(
                target["targets"]["budgets"]["command"].removeprefix("bunx "),
                shell=True,
                cwd=self.path,
                capture_output=True,
                text=True,
            )
            assert done.returncode == 0, done.stdout + done.stderr
        [upload] = [
            step
            for step in self.workflow["jobs"]["test-linux"]["steps"]
            if step.get("uses", "").startswith("actions/upload-artifact@")
            and step["with"]["name"].startswith("budget-check-results-")
        ]
        assert upload["if"] == "always()"
        saved = self.path / upload["with"]["path"]
        members = {file.name: file.read_text() for file in saved.iterdir()}
        assert set(members) == {"report-job.json", "saved.json"}
        self.host.attach(run_id, upload["with"]["name"], self.harness.zipped(members))
        # Another job's checkout: neither the results nor their telemetry reach `budgets`.
        shutil.rmtree(saved)
        shutil.rmtree(self.path / "packages/saved/dist")

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
        return {"token": self.token, **given}

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
                # The harness's environment inherits nothing, and a fork's run holds no token.
                "DBUS_SESSION_BUS_ADDRESS": "disabled:",
                # What a runner sets for every step, which the figures script reads.
                "GITHUB_EVENT_NAME": event_name,
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

    def pull_request(
        self, bundle_size: int | None = None, test_linux: bool = False, **event
    ):
        self.repository.git("checkout", "--quiet", "-B", "change", self.base_sha)
        if bundle_size is not None:
            self.repository.write("packages/demo/bundle-size", f"{bundle_size}\n")
        head = self.repository.commit("change")
        self.host.head = head
        if test_linux:
            self.test_linux(self.next_run + 1)
        return self.run(
            "pull_request",
            head,
            event=self.harness.event_pull_request(1, head, self.base_sha, **event),
            base_ref="main",
            register=False,
        )

    def executed(self) -> list[str]:
        return sorted(self.counts.read_text().split()) if self.counts.exists() else []


# Where each part of the core's comment layout opens; each closes at its next `</details>`.
SECTIONS = {
    "attention": "<details open><summary><b>Needs attention",
    "within": "<details><summary><b>Within budget",
    "details": "<details><summary>Details</summary>",
}
NOT_MEASURED = "Not measured on this run"
WHY_MISSING = "Why some figures are missing"


def section(markdown: str, name: str) -> str:
    """One collapsible part of a report, or "" when the report has none."""
    start = markdown.find(SECTIONS[name])
    return "" if start < 0 else markdown[start : markdown.index("</details>", start)]


def listed(text: str, heading: str) -> dict[str, str]:
    """The `- names: text` bullets under one bold heading of Details, by budget."""
    if f"**{heading}" not in text:
        return {}
    block = text.split(f"**{heading}", 1)[1].split("\n\n", 2)[1]
    items = {}
    for line in block.splitlines():
        names, said = line.removeprefix("- ").split(": ", 1)
        items.update(dict.fromkeys(names.split(", "), said))
    return items


def placements(markdown: str, budget: str) -> list[tuple[str, str]]:
    """Every place a report shows `budget`: a table row, or a not-measured bullet."""
    found = [
        (name, line)
        for name in ("attention", "within")
        for line in section(markdown, name).splitlines()
        if line.startswith(f"| **{budget}** |")
    ]
    unmeasured = listed(section(markdown, "details"), NOT_MEASURED)
    if budget in unmeasured:
        found.append(("not-measured", unmeasured[budget]))
    return found


def placement(markdown: str, budget: str) -> tuple[str, str]:
    found = placements(markdown, budget)
    assert len(found) == 1, (budget, found)
    return found[0]


def why_missing(markdown: str) -> dict[str, str]:
    return listed(section(markdown, "details"), WHY_MISSING)


def assert_readable_layout(markdown: str, job) -> None:
    """The summary line, then what needs attention open, the rest folded beneath."""
    rows = job.rows()
    unmeasured = {budget for budget, row in rows.items() if "verdict" not in row}
    attention = {
        budget
        for budget, row in rows.items()
        if row.get("verdict") == "over" or row.get("flag")
    }
    within = set(rows) - unmeasured - attention
    assert attention == {REPORT_ONLY_OVER}, rows
    summary = markdown.split("\n\n")[1]
    assert summary.startswith(
        f"**⚠️ {len(attention)} need attention** · {len(within)} within budget · "
        f"{len(unmeasured)} not measured · head {job.sha[:7]} · [run {job.run_id}]("
    ), summary
    assert markdown.index(SECTIONS["attention"]) < markdown.index(SECTIONS["within"])
    assert markdown.index(SECTIONS["within"]) < markdown.index(SECTIONS["details"])
    assert section(markdown, "attention").startswith(
        f"{SECTIONS['attention']} ({len(attention)})</b></summary>\n\n"
        "| Budget | Now · vs base | Reason |\n"
    )
    assert section(markdown, "within").startswith(
        f"{SECTIONS['within']} ({len(within)})</b></summary>\n\n"
        "| Budget | Now · vs base |\n"
    )
    details = section(markdown, "details")
    assert f"\n**{NOT_MEASURED} ({len(unmeasured)})**\n" in details
    for budget in attention:
        assert placement(markdown, budget)[0] == "attention", budget
    for budget in within:
        assert placement(markdown, budget)[0] == "within", budget
    for budget in unmeasured:
        assert placement(markdown, budget)[0] == "not-measured", budget
    assert "Over limit (report-only)" in placement(markdown, REPORT_ONLY_OVER)[1]
    assert markdown.count(OMITTED_LINE) == 1
    assert f"\n- {OMITTED_LINE}\n" in details


def assert_omitted_and_counted(job) -> None:
    """The budgets a pull request cannot measure: no row, one line, and no measurement."""
    markdown = job.markdown()
    report = {row["id"] for row in job.saved("comparison-report.json")["rows"]}
    measured = {r["id"] for r in job.saved("budget-run.json")["results"]["results"]}
    for budget in GENERATED_OMITTED:
        assert budget not in report and budget not in measured, budget
        assert placements(markdown, budget) == [], budget
    [line] = [line for line in markdown.splitlines() if "left out" in line]
    assert OMITTED_LINE in line
    assert f"\n- {OMITTED_LINE}\n" in section(markdown, "details")


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

    budgets = sorted(
        {
            *RUNNABLE,
            *UNAVAILABLE,
            SAVED_BUDGET,
            REPORT_BUDGET,
            *GENERATED_MEASURED,
            CACHE_HEADROOM,
        }
    )
    report = job.saved("comparison-report.json")
    assert sorted(row["id"] for row in report["rows"]) == budgets
    for budget in budgets:
        placement(job.markdown(), budget)

    assert journey.executed() == sorted(RUNNABLE)
    budget_run = job.saved("budget-run.json")
    verdicts = {r["id"]: r for r in budget_run["results"]["results"]}
    for budget, unit in RUNNABLE.items():
        wanted = "over" if budget == REPORT_ONLY_OVER else "within"
        assert verdicts[budget]["verdict"] == wanted, budget
        assert verdicts[budget]["unit"] == unit
    assert verdicts["demo-coverage-percent"]["actual"] == 91

    unavailable = {u["id"]: u["reason"] for u in budget_run["unavailable"]}
    assert NO_CACHE_ROUTE in unavailable.pop(CACHE_HEADROOM)
    assert set(unavailable) == {*UNAVAILABLE, SAVED_BUDGET}
    for budget in unavailable:
        assert unavailable[budget] == NO_SAVED_RESULT, budget
    assert budget_run["enforcement_exclude_labels"] == ["release-tail", "host-variable"]

    assert len(journey.host.comments) == 1
    body = journey.host.comments[0]["body"]
    assert "report-only" in body
    for budget in UNAVAILABLE:
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
    # This push maps to no pull request, so it has no figure for the two CI-time budgets.
    assert {r["id"] for r in history["runs"][0]["results"]["results"]} == {
        r["id"]
        for r in json.loads(pull_files["budget-run.json"].decode())["results"][
            "results"
        ]
    } - GENERATED_MEASURED


def test_under_the_actions_token_a_pull_request_keeps_one_comment_and_main_its_history(
    journey,
):
    """The token the generated job passes: one account GitHub spells two ways."""
    journey.host.actions_bot()
    first_base = journey.base_push()
    second_base = journey.base_push()
    assert first_base.exit_code == second_base.exit_code == 0, second_base.stdout
    history = json.loads(
        journey.host.artifact_files(second_base.run_id)["rolling-history.json"].decode()
    )
    assert [run["head_sha"] for run in history["runs"]] == [
        first_base.sha,
        second_base.sha,
    ]

    first = journey.pull_request()
    second = journey.pull_request(bundle_size=600)
    assert first.exit_code == second.exit_code == 0, second.stdout
    assert len(journey.host.comments) == 1
    # The comment is posted before the job's own budget has its verdict, so it is this
    # push's row, rather than the final report, that shows the comment moved.
    row = placement(journey.host.comments[0]["body"], "demo-bundle-bytes")
    assert row == placement(second.markdown(), "demo-bundle-bytes")
    assert row != placement(first.markdown(), "demo-bundle-bytes")


def test_a_freshly_bootstrapped_repository_saves_history_from_its_own_budget(
    released, runner_path, tmp_path
):
    """Nothing else measures in its job, and the own budget joins only the final upload."""
    journey = Journey(released, runner_path, tmp_path, "rust-cli", catalog={})
    try:
        first = journey.base_push()
        second = journey.base_push()
        assert first.exit_code == second.exit_code == 0, second.stdout
        saved = journey.host.artifact_files(second.run_id)["rolling-history.json"]
        runs = json.loads(saved.decode())["runs"]
        assert [run["head_sha"] for run in runs] == [first.sha, second.sha]
        for run in runs:
            assert [r["id"] for r in run["results"]["results"]] == [REPORT_BUDGET]
    finally:
        journey.close()


def test_history_is_unavailable_below_five_saved_commits_then_compared(journey):
    budget = "demo-bundle-bytes"
    for _ in range(4):
        assert journey.base_push().exit_code == 0
    few = journey.pull_request()
    assert few.rows()[budget]["baseline_n"] == 4
    assert few.rows()[budget]["flag"] is False
    assert placement(few.markdown(), budget) == (
        "within",
        f"| **{budget}** | 500 bytes<br>baseline 4/5 |",
    )
    assert few.exit_code == 0, few.stdout

    assert journey.base_push().exit_code == 0
    enough = journey.pull_request()
    assert enough.exit_code == 0, enough.stdout
    assert enough.rows()[budget]["baseline_n"] == 5
    assert placement(enough.markdown(), budget) == (
        "within",
        f"| **{budget}** | 500 bytes<br>+0.0% (+0.0 SD) |",
    )


def test_an_enforced_budget_over_its_line_fails_and_a_report_only_one_does_not(journey):
    report_only = journey.pull_request()
    assert report_only.exit_code == 0, report_only.stdout
    assert report_only.rows()[REPORT_ONLY_OVER]["verdict"] == "over"
    place, row = placement(report_only.markdown(), REPORT_ONLY_OVER)
    assert place == "attention"
    assert row.endswith("| Over limit (report-only) |"), row

    enforced = journey.pull_request(bundle_size=5000)
    assert enforced.exit_code == 1, enforced.stdout
    assert enforced.rows()["demo-bundle-bytes"]["verdict"] == "over"
    place, row = placement(enforced.markdown(), "demo-bundle-bytes")
    assert place == "attention"
    assert row.endswith("| Over limit |"), row
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


def test_a_new_repositorys_first_report_starts_with_numbers(every_composition):
    """What a saved target measured, this run's own jobs, and one line for the rest."""
    journey = every_composition
    composed = yaml.safe_load((journey.path / "budgets.yaml").read_text())["budgets"]
    assert {budget["id"] for budget in composed} == {
        *GENERATED_OMITTED,
        *GENERATED_MEASURED,
        CACHE_HEADROOM,
    }

    journey.host.clear_requests()
    job = journey.pull_request(test_linux=True)
    assert job.exit_code == 0, job.stdout
    assert job.outcomes["saved-results"] == "success", job.stdout

    rows = job.rows()
    assert rows[SAVED_BUDGET]["current"] == SAVED_FIGURE
    assert rows[SAVED_BUDGET]["verdict"] == "within"
    assert placement(job.markdown(), SAVED_BUDGET) == (
        "within",
        f"| **{SAVED_BUDGET}** | {SAVED_FIGURE:.1f} requests<br>baseline 0/5 |",
    )
    assert SAVED_BUDGET not in journey.executed()

    assert rows[CI_CHECK_TIME]["current"] == LONGEST_JOB_SECONDS
    assert rows[CI_JOB_COUNT]["current"] == len(journey.workflow["jobs"])
    for budget in GENERATED_MEASURED:
        assert rows[budget]["verdict"] == "within", rows[budget]
        assert placement(job.markdown(), budget)[0] == "within"
    read = f"/runs/{job.run_id}/jobs"
    assert journey.host.count(read) == journey.host.count(r"/runs/\d+/jobs") > 0
    assert journey.host.count("/releases/tags|/compare") == 0

    assert_omitted_and_counted(job)
    assert_omitted_and_counted_in_comment(journey)
    [comment] = journey.host.comments
    assert_readable_layout(comment["body"], job)


def assert_omitted_and_counted_in_comment(journey) -> None:
    body = journey.host.comments[0]["body"]
    assert OMITTED_LINE in body
    for budget in GENERATED_OMITTED:
        assert placements(body, budget) == [], budget
    placement(body, CI_CHECK_TIME)


def test_a_missing_saved_results_artifact_leaves_its_budget_unavailable_and_fails_nothing(
    journey,
):
    """No artifact: the row says so, and a test job that died is its own red check."""
    job = journey.pull_request()
    assert job.exit_code == 0, job.stdout
    row = job.rows()[SAVED_BUDGET]
    assert "verdict" not in row, row
    assert row["unavailable"] == NO_SAVED_RESULT
    assert placement(job.markdown(), SAVED_BUDGET) == (
        "not-measured",
        "no result this run · baseline 0/5",
    )
    assert why_missing(job.markdown())[SAVED_BUDGET] == NO_SAVED_RESULT


def test_a_base_push_takes_the_merged_pull_requests_figures(journey):
    """A pull request's value and its baseline are one measurement."""
    merged = journey.pull_request(test_linux=True)
    assert merged.exit_code == 0, merged.stdout
    journey.host.add_run(
        merged.run_id, merged.sha, event="pull_request", branch="change"
    )
    journey.repository.git("checkout", "--quiet", "main")
    merge = journey.repository.commit("squash")
    journey.host.merge_pull_request(merge, merged.sha)
    journey.base_sha = merge
    journey.host.clear_requests()

    base = journey.run("push", merge)
    assert base.exit_code == 0, base.stdout
    assert journey.executed() == [], "a figure the merged run holds runs no command"
    assert journey.host.count(r"/runs/\d+/jobs") == 0
    history = json.loads(
        journey.host.artifact_files(base.run_id)["rolling-history.json"].decode()
    )
    taken = {r["id"]: r["actual"] for r in history["runs"][0]["results"]["results"]}
    assert taken[SAVED_BUDGET] == SAVED_FIGURE
    assert taken[CI_CHECK_TIME] == LONGEST_JOB_SECONDS
    assert taken[CI_JOB_COUNT] == len(journey.workflow["jobs"])
    assert not set(taken) & GENERATED_OMITTED


def test_a_base_push_with_no_pull_request_keeps_its_rows_unavailable_and_reads_no_run(
    journey,
):
    """The figures read a release off a pull request, which this job cannot do."""
    journey.host.clear_requests()
    base = journey.base_push()
    assert base.exit_code == 0, base.stdout
    assert journey.host.count(r"/runs/\d+/jobs|/releases/tags|/compare") == 0
    for budget in GENERATED_MEASURED:
        place, row = placement(base.markdown(), budget)
        assert place == "not-measured", row
        assert FIGURES_NEED_ONEVCS in why_missing(base.markdown())[budget]
    assert_omitted_and_counted(base)


def test_a_fork_gets_a_summary_and_artifact_without_a_comment_or_credential(journey):
    base = journey.base_push()
    journey.host.clear_requests()
    job = journey.pull_request(head_repository="someone/fork")
    assert job.exit_code == 0, job.stdout
    assert journey.host.comments == []
    assert journey.host.count("commits/main") == 0, "a budget command reached the host"
    assert journey.host.count(r"/runs/\d+/jobs") == 0, "the figures hold no credential"
    for budget in GENERATED_MEASURED:
        assert placement(job.markdown(), budget)[0] == "not-measured"
    assert "someone/fork" in job.summary
    assert journey.host.artifact_files(job.run_id) is not None
    assert base.exit_code == 0


def test_a_budget_command_holding_no_token_runs_the_real_gh_without_a_session_bus(
    journey,
):
    # Asking for a bus would fail this test at teardown through the conftest's trap, on
    # the PATH `runner_path` hands the job: the one run here where `gh` would ask.
    journey.token = ""
    job = journey.base_push()
    assert "demo-host-reads" in journey.executed(), job.stdout


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
            "uv",
            "run",
            "--script",
            str(COMPOSER),
            "--ci-cd",
            str(journey.path),
            "--shape",
            chosen.shape,
            "--language",
            chosen.language,
            "--name",
            NAME,
            "--platforms",
            chosen.platforms,
        ],
        capture_output=True,
        text=True,
    )
    assert again.returncode == 0, again.stderr
    after = {p: p.read_bytes() for p in before}
    assert after == before


def test_the_plan_for_each_shape_composes_the_budget_reference(tmp_path):
    for chosen in COMPOSITIONS.values():
        done = subprocess.run(
            [
                "uv",
                "run",
                "--script",
                str(COMPOSER),
                "--shape",
                chosen.shape,
                "--language",
                chosen.language,
            ],
            capture_output=True,
            text=True,
        )
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

    metric = json.loads((vendored / "report-metric.source.json").read_text())
    writer = (released.tree / metric["source"]).read_bytes()
    assert hashlib.sha256(writer).hexdigest() == metric["source_sha256"]
    assert f'name: "{metric["metric"]}"'.encode() in writer
