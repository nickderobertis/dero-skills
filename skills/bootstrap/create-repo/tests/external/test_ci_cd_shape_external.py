"""End-to-end: a composed repository's release tail and `just gate`, run for real.

The repository is composed by the real composer (`compose_repo_plan.py --ci-cd`).
Nothing below the GitHub boundary is stood in for:

* **The release tail.** `publish`'s install step runs as release.yml writes it,
  installing the onevcs and onebudgetspec releases the skill pins from npm; then
  its last step, `onebudgetspec check budgets.yaml --label release-tail`, runs as
  written over the composed budgets.yaml and figures script, with the real jq.
  `ONEVCS_GH` — onevcs's seam for replaying recorded responses — names a double
  serving GitHub's own answers for onetaskgraph's v0.13.0 release, copied from
  onetaskgraph's release-tail fixtures at its landing commit
  (`fixtures/release-tail/README.md`). The double records every request it
  serves, and the read assertions are held to that record.
* **The gate.** `bun install` installs the onebudgetspec CLI and SDK the composer
  pinned, and the composed `just gate` runs around a short real body, through
  scripts/gate-time.sh and scripts/gate-time-budget.mjs, then the wrapper's
  `--check` over seeded records.

Assertions are on exit statuses, output and record contents — never on how long
anything took.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from ci_cd_shape import NAME, compose

RELEASE_TAIL = Path(__file__).resolve().parent / "fixtures" / "release-tail"
RECORDINGS = RELEASE_TAIL / "onetaskgraph-v0.13.0"
REPOSITORY = "nickderobertis/onetaskgraph"
RELEASE = "v0.13.0"
RELEASE_RUN = 41252264998  # the Release run whose publish job runs the tail
TAIL_IDS = [
    f"{NAME}-{suffix}"
    for suffix in (
        "ci-check-time",
        "cd-job-time",
        "release-cycle-time",
        "ci-job-count",
        "cd-job-count",
        "release-read-requests",
        "pr-read-requests",
    )
]
# The program `ONEVCS_GH` names: it serves the response routed for the request it
# is asked, records the request, and fails the way `gh` does on an error status.
DOUBLE = r"""#!/bin/sh
dir="$(dirname "$0")/.."
for arg in "$@"; do last="$arg"; done
case "$last" in
  https://api.github.com/*) request="${last#https://api.github.com/}" ;;
  *) request="$last" ;;
esac
printf '%s\n' "$request" >> "$dir/served"
n=$(awk -F '\t' -v r="$request" '$1 == r { print $2; exit }' "$dir/routes.tsv")
if [ -z "$n" ]; then
  echo "replay: no recording for $request" >&2
  exit 1
fi
cat "$dir/responses/$n"
"""
RESULT_RE = re.compile(
    r"^budget (\S+): (?:actual (\S+) \S+, budget (\S+) \S+.* — (within|over)|.*error)",
    re.M,
)


def require(*tools: str) -> None:
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        pytest.fail(
            f"{', '.join(missing)} not on PATH — these journeys run the real toolchain"
        )


def step(workflow: str, name: str) -> dict[str, str]:
    """One step of a composed workflow: its `run` script and its `env` lines."""
    lines = workflow.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}")
    found: dict[str, str] = {"run": "", "env": ""}
    key = None
    for line in lines[start + 1 :]:
        if line.startswith("      - ") or (
            line.strip() and not line.startswith("        ")
        ):
            break
        if line.startswith("        run: "):
            value = line.removeprefix("        run: ")
            key, found["run"] = ("run", "") if value == "|" else (None, value)
        elif line.startswith("        env:"):
            key = "env"
        elif key and line.startswith("          "):
            found[key] += line.removeprefix("          ") + "\n"
        elif line.strip():
            key = None
    return found


class Replay:
    """onetaskgraph v0.13.0's recordings, each served for the path it answers."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "bin").mkdir(parents=True)
        (root / "responses").mkdir()
        (root / "bin" / "gh").write_text(DOUBLE, encoding="utf-8")
        (root / "bin" / "gh").chmod(0o755)
        routes = []
        for n, recording in enumerate(sorted(RECORDINGS.glob("*.http"))):
            sidecar = json.loads(
                Path(f"{recording}.source.json").read_text(encoding="utf-8")
            )
            # Bytes, not text: a recording's header lines end in CRLF.
            text = recording.read_bytes().decode("utf-8")
            if change := sidecar.get("served_in_progress"):
                head, _, body = text.partition("\r\n\r\n")
                answer = json.loads(body)
                [job] = [j for j in answer["jobs"] if j["name"] == change["job"]]
                job.update(status="in_progress", conclusion=None, completed_at=None)
                text = f"{head}\r\n\r\n{json.dumps(answer)}"
            (root / "responses" / str(n)).write_bytes(text.encode("utf-8"))
            routes.append(f"{sidecar['path']}\t{n}\n")
        (root / "routes.tsv").write_text("".join(routes), encoding="utf-8")

    @property
    def program(self) -> Path:
        return self.root / "bin" / "gh"

    def served(self) -> list[str]:
        path = self.root / "served"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def tail_runner(self) -> str:
        record = RECORDINGS / f"jobs-of-release-run-{RELEASE_RUN}.http"
        body = record.read_bytes().decode("utf-8").partition("\r\n\r\n")[2]
        [job] = [j for j in json.loads(body)["jobs"] if j["name"] == "publish"]
        return job["runner_name"]


@pytest.fixture(scope="module")
def released(tmp_path_factory):
    """A composed repository, and the tools publish's install step put on PATH."""
    require("bash", "jq", "npm", "node")
    root = tmp_path_factory.mktemp("release-tail")
    repo = compose(root / "repo")
    workflow = (repo / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    install = step(workflow, "Install onevcs and onebudgetspec for the release tail")
    (root / "runner-temp").mkdir()
    github_path = root / "github-path"
    github_path.write_text("", encoding="utf-8")
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", install["run"]],
        cwd=repo,
        env={
            **os.environ,
            "RUNNER_TEMP": str(root / "runner-temp"),
            "GITHUB_PATH": str(github_path),
        },
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert done.returncode == 0, (
        f"publish's install step failed: {done.stdout}{done.stderr}"
    )
    tool_path = [p for p in github_path.read_text(encoding="utf-8").splitlines() if p]
    path = os.pathsep.join([*tool_path, os.environ["PATH"]])
    for tool, version in (("onevcs", "0.57.6"), ("onebudgetspec", "0.1.3")):
        shown = subprocess.run(
            [tool, "--version"], env={"PATH": path}, capture_output=True, text=True
        )
        assert shown.stdout.split() == [tool, version], shown
    tail = step(workflow, "Report the release-tail budgets")
    return repo, path, tail


def run_tail(released, tmp_path: Path, thresholds: dict[str, float]):
    """publish's last step, as written, over the composed budgets with `thresholds`."""
    repo, path, tail = released
    checkout = tmp_path / "checkout"
    shutil.copytree(repo, checkout, symlinks=True)
    budgets = checkout / "budgets.yaml"
    text = budgets.read_text(encoding="utf-8")
    for budget, threshold in thresholds.items():
        text, n = re.subn(
            rf"(  - id: {re.escape(budget)}\n(?:    .*\n)*?    threshold: )\S+",
            rf"\g<1>{threshold}",
            text,
        )
        assert n == 1, budget
    budgets.write_text(text, encoding="utf-8")
    replay = Replay(tmp_path / "replay")
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"action": "published", "release": {"tag_name": RELEASE}})
    )
    (tmp_path / "home").mkdir()
    (tmp_path / "runner-temp").mkdir()
    assert tail["env"].strip() == "GH_TOKEN: ${{ github.token }}"
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", tail["run"]],
        cwd=checkout,
        env={
            "PATH": path,
            "HOME": str(tmp_path / "home"),
            "ONEVCS_HOME": str(tmp_path / "home" / "onevcs"),
            "ONEVCS_GH": str(replay.program),
            "GH_TOKEN": "replay-token",
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_RUN_ID": str(RELEASE_RUN),
            "RUNNER_NAME": replay.tail_runner(),
            "RUNNER_TEMP": str(tmp_path / "runner-temp"),
        },
        capture_output=True,
        text=True,
        timeout=900,
    )
    output = done.stdout + done.stderr
    results = {m[0]: m for m in RESULT_RE.findall(output)}
    return done.returncode, output, results, replay.served()


HIGH = dict.fromkeys(TAIL_IDS, 10**9)


def test_the_tail_reports_the_seven_budgets_reading_each_answer_once(
    released, tmp_path
):
    assert step(
        (released[0] / ".github/workflows/release.yml").read_text(),
        "Report the release-tail budgets",
    )["run"] == ("onebudgetspec check budgets.yaml --label release-tail")
    code, output, results, served = run_tail(released, tmp_path, HIGH)
    assert code == 0, output
    assert sorted(results) == sorted(TAIL_IDS), output
    assert all(verdict == "within" for *_, verdict in results.values()), output
    # The host-variable budget is not the tail's.
    assert f"{NAME}-gate-time" not in output
    # Each figure's breakdown, from the script's detail.
    assert "longest per runner OS:" in output
    assert re.search(r"^#3843: checks \d+s \+ merge to release \d+s$", output, re.M), (
        output
    )
    assert "per workflow:" in output and "per runner OS:" in output
    assert f"release-changes --tag {RELEASE}: 3 requests" in output
    assert (
        "left out as concluded skipped: Visual docs (run 41252214397, push)" in output
    )
    # Every read made once across the seven figures, as the double recorded it.
    assert served, output
    assert len(served) == len(set(served)), served
    pr_reads = [
        r
        for r in served
        if "event=pull_request" in r
        or any(f"/runs/{run}/jobs" in r for run in (41247587655, 41250222569))
    ]
    release_reads = [r for r in served if r not in pr_reads]
    # The read figures are the double's own count: the release's reads, and the
    # most any one pull request needed (its runs at head and one CI run's jobs).
    assert results[f"{NAME}-release-read-requests"][1] == str(len(release_reads)), (
        served
    )
    assert results[f"{NAME}-pr-read-requests"][1] == str(len(pr_reads) // 2), served
    assert sum("/releases?" in r for r in served) == 1
    assert not any("/runs/41252214397/jobs" in r for r in served)  # the skipped run
    # Counts, not durations: each pull request's CI jobs, and the CD jobs at the
    # release commit — which, with no --exclude-workflow in the composed shape's
    # commands, include onetaskgraph's cache warmer.
    assert results[f"{NAME}-ci-job-count"][1] == "4"
    assert results[f"{NAME}-cd-job-count"][1] == "9"


def test_a_figure_over_its_threshold_turns_the_tail_non_zero_naming_it(
    released, tmp_path
):
    reads = f"{NAME}-pr-read-requests"
    code, output, results, _served = run_tail(released, tmp_path, {**HIGH, reads: 1})
    assert code == 1, output
    budget, actual, threshold, verdict = results[reads]
    assert (verdict, threshold) == ("over", "1"), output
    assert float(actual) > 1
    assert [b for b, *_, v in results.values() if v == "over"] == [reads], output


# --- the gate -------------------------------------------------------------------


@pytest.fixture(scope="module")
def gated(tmp_path_factory):
    """A composed repository with its pinned packages installed and a short real body."""
    require("bun", "node", "just")
    repo = compose(tmp_path_factory.mktemp("gate") / "repo")
    installed = subprocess.run(
        ["bun", "install"], cwd=repo, capture_output=True, text=True, timeout=900
    )
    assert installed.returncode == 0, installed.stderr
    justfile = repo / "justfile"
    text = justfile.read_text(encoding="utf-8")
    assert "\ngate-body: check supply-chain\n" in text
    justfile.write_text(
        text.replace(
            "\ngate-body: check supply-chain\n",
            "\ngate-body:\n    @echo gate body ran\n",
        ),
        encoding="utf-8",
    )
    return repo


def just(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["just", *args], cwd=repo, capture_output=True, text=True, timeout=600
    )


def wrapper(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "scripts/gate-time.sh", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=600,
    )


def seed(repo: Path, started: str, ended: str, exit_status: int = 0) -> None:
    (repo / ".gate-time.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "invocation": "seeded",
                "started_at": started,
                "ended_at": ended,
                "exit": exit_status,
            }
        )
    )


def test_the_gate_writes_a_fresh_record_of_its_wall_clock(gated):
    seed(gated, "2020-01-01T00:00:00Z", "2020-01-01T00:00:01Z")
    done = just(gated, "gate")
    assert done.returncode == 0, done.stderr
    assert "gate body ran" in done.stdout
    record = json.loads((gated / ".gate-time.json").read_text())
    assert record["schema_version"] == 1 and record["exit"] == 0
    assert record["invocation"] != "seeded" and not record["started_at"].startswith(
        "2020"
    )
    stamp = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
    assert re.fullmatch(stamp, record["started_at"]) and re.fullmatch(
        stamp, record["ended_at"]
    )
    assert record["ended_at"] >= record["started_at"]
    assert f"budget {NAME}-gate-time:" in done.stdout + done.stderr
    assert "warning" not in done.stderr


def test_a_failed_body_fails_the_gate_and_checks_no_budget(gated, tmp_path):
    justfile = gated / "justfile"
    text = justfile.read_text(encoding="utf-8")
    justfile.write_text(
        text.replace("    @echo gate body ran\n", "    @exit 7\n"), encoding="utf-8"
    )
    try:
        done = just(gated, "gate")
    finally:
        justfile.write_text(text, encoding="utf-8")
    assert done.returncode != 0
    assert "the gate failed" in done.stderr
    assert json.loads((gated / ".gate-time.json").read_text())["exit"] != 0
    assert f"budget {NAME}-gate-time:" not in done.stdout + done.stderr


def test_an_over_record_warns_naming_the_budget_and_passes(gated):
    seed(gated, "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z")
    done = wrapper(gated, "--check")
    assert done.returncode == 0, done.stderr
    assert f"gate-time: warning: {NAME}-gate-time is over its budget" in done.stderr
    assert f"budget {NAME}-gate-time: actual 3600" in done.stdout + done.stderr


def test_a_record_within_passes_quietly(gated):
    seed(gated, "2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z")
    done = wrapper(gated, "--check")
    assert done.returncode == 0, done.stderr
    assert "warning" not in done.stderr
    assert re.search(
        rf"budget {NAME}-gate-time: actual 600 .*within", done.stdout + done.stderr
    )


def test_a_missing_record_fails(gated):
    (gated / ".gate-time.json").unlink(missing_ok=True)
    done = wrapper(gated, "--check")
    assert done.returncode == 3, done.stderr
    assert f"checking {NAME}-gate-time failed (onebudgetspec exit 3)" in done.stderr
    assert "no gate record at .gate-time.json" in done.stdout + done.stderr


def test_an_invalid_budgets_file_fails(gated):
    seed(gated, "2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z")
    budgets = gated / "budgets.yaml"
    text = budgets.read_text(encoding="utf-8")
    budgets.write_text(
        text.replace("schema_version: 1", "schema_version: 99"), encoding="utf-8"
    )
    try:
        done = wrapper(gated, "--check")
    finally:
        budgets.write_text(text, encoding="utf-8")
    assert done.returncode == 2, done.stderr
    assert f"checking {NAME}-gate-time failed (onebudgetspec exit 2)" in done.stderr
