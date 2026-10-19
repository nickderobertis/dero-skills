"""End-to-end: the GitHub Actions budget figures a repository's CI and CD report.

Each case is a repository holding its copy of `actions-budget-figures.sh` and a
`budgets.yaml` naming it, measured by the real `onebudgetspec check` with the real
jq and the real onevcs release from npm. GitHub is the one boundary doubled
(`actions_figures_replay.py`): onevcs asks a replay of GitHub's own recordings —
the scrubbed responses onevcs's journeys replay, copied with a sidecar naming the
commit and path they came from, plus four read-only recordings of public
repositories made for these journeys, each saying why. A scenario under
`fixtures/actions-figures/scenarios/` names what one replay serves, and any entry
serving a recording for another request, or leaving runs out of a listing, says
why.

The expected figures are fixed numbers, and the v0.72.0 ones are also worked out
here from the raw recordings with jq alone (`derive`), applying the selection
onevcs states and the counting rules the script states — so a figure that moved
fails whichever of the two moved it. The read counts are held to the replay's own
record of what it served, never to the `cost` the answers report.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from actions_figures_replay import (
    FIXTURES,
    RECORDINGS,
    Replay,
    Repository,
    install_onevcs,
    recording,
    require,
)

ONEPIPELINE = "nickderobertis/onepipeline"
RELEASE = ["--release", "v0.72.0", "--repo", ONEPIPELINE]
RELEASE_COMMIT = "d8d674f1f0b46fff580540ab70137ebc91e6b15b"
# When v0.72.0's last CD job ended: a fixed --now, so release-cycle-time measures
# the release as it shipped rather than the moment the test runs.
RELEASE_END = "2026-10-15T07:20:03Z"
HEADS = {
    914: "62631f7c3936c095c6973f5108e9265360466e84",
    916: "c5574ad1e2747b59be7bdf843f86cd6e7a4e4b97",
    919: "4e3dee6c0842603388d96db3d4e8a6f64aac99d2",
}
# v0.72.0's two Published smoke runs: workflow_run runs GitHub records at the
# release commit, which no push or release selection takes.
PUBLISHED_SMOKE = (38657825455, 38693951540)
# A CI run recorded while three of its jobs were in progress, each on a runner.
IN_PROGRESS_RUN = "39212146687"
IN_PROGRESS_RUNNER = "GitHub Actions 1000251012"  # its "Mac PR proofs" job


def jobs_request(run: int | str, repo: str = ONEPIPELINE) -> str:
    return f"repos/{repo}/actions/runs/{run}/jobs?per_page=100&filter=latest"


def head_request(number: int) -> str:
    return (
        f"repos/{ONEPIPELINE}/actions/runs?per_page=100&head_sha={HEADS[number]}"
        "&event=pull_request"
    )


RELEASE_CHANGES_REQUESTS = [
    f"repos/{ONEPIPELINE}/releases?per_page=100",
    f"repos/{ONEPIPELINE}/compare/v0.71.1...v0.72.0?per_page=100",
    f"repos/{ONEPIPELINE}/pulls?state=closed&sort=updated&direction=desc&per_page=100",
]
COMMIT_RUNS_REQUEST = (
    f"repos/{ONEPIPELINE}/actions/runs?per_page=100&head_sha={RELEASE_COMMIT}"
)
CD_RUNS = (38649523160, 38649523280, 38649523429, 38649523612, 38649654071)
PR_RUNS = {
    914: (38534306223, 38534306114, 38534306078, 38534306473, 38534306075),
    916: (38632124776, 38632124537, 38632124755, 38632125278, 38632124786),
    919: (38553682939, 38553682932, 38553683001, 38553683522, 38553682956),
}
PR_REQUESTS = {
    number: [head_request(number), *map(jobs_request, runs)]
    for number, runs in PR_RUNS.items()
}
RELEASE_REQUESTS = [
    *RELEASE_CHANGES_REQUESTS,
    COMMIT_RUNS_REQUEST,
    *map(jobs_request, CD_RUNS),
]


@pytest.fixture(scope="session")
def onevcs_bin(tmp_path_factory) -> Path:
    return install_onevcs(tmp_path_factory.mktemp("onevcs"))


@pytest.fixture
def repository(tmp_path: Path, onevcs_bin: Path):
    """A repository whose figures read the replay of `scenario`."""
    require("bash", "jq", "onebudgetspec")

    def make(scenario: str) -> Repository:
        replay = Replay(tmp_path / "replay", scenario)
        return Repository(tmp_path / "repo", replay, onevcs_bin)

    return make


def run_direct(repo: Repository, *args: str, **env: str):
    """The script run outside `check`, its result file kept: what it wrote, if any."""
    result = repo.root / "result.json"
    result.write_text("", encoding="utf-8")
    done = subprocess.run(
        ["bash", "scripts/actions-budget-figures.sh", *args],
        cwd=repo.root,
        capture_output=True,
        text=True,
        env=repo.env(ONEBUDGETSPEC_RESULT=str(result), **env),
        timeout=300,
    )
    return done, result.read_text(encoding="utf-8")


def assert_within(result: dict, value: float) -> None:
    assert (result["verdict"], result["actual"]) == ("within", value), result
    assert result["exit"] == 0, result


def assert_error(result: dict, reason: str) -> None:
    """The budget's `error` (exit 3), naming `reason`, with no figure recorded."""
    assert result["exit"] == 3, result
    assert result["verdict"] == "error", result
    assert (result["actual"], result["detail"]) == (None, None), result
    assert reason in result["error"], result["error"]


def assert_refused_writing_nothing(
    repo: Repository, args: list[str], reason: str, **env
):
    done, written = run_direct(repo, *args, **env)
    assert done.returncode == 1, done
    assert reason in done.stderr, done.stderr
    assert written == "", "a refused figure writes nothing"


# --- the figures jq works out from the raw recordings -------------------------

DERIVE = r"""
def ts: fromdate;
# Counted: completed, not skipped, and taken by a runner; seconds never below 0.
def counted: select(.status == "completed" and .conclusion != "skipped"
                    and (.runner_name // "") != "")
  | . + {secs: ([(.completed_at | ts) - (.started_at | ts), 0] | max)};
. as $r
| [$r.compare.commits[].sha as $sha | $r.pulls[]
   | select(.merge_commit_sha == $sha and .merged_at != null)] as $prs
| [$prs[] | . as $pr
   # each workflow's latest pull_request run at the head created by the merge
   | ([$r.heads[$pr.head.sha].workflow_runs[]
       | select(.event == "pull_request" and (.created_at | ts) <= ($pr.merged_at | ts))]
      | group_by(.workflow_id) | map(max_by(.created_at | ts))) as $runs
   | [$runs[] | $r.jobs[.id | tostring].jobs[] | counted] as $jobs
   | {number: $pr.number, jobs: ($jobs | length), longest: ([$jobs[].secs] | max),
      checks: (([$jobs[].completed_at | ts] | max) - ([$runs[].created_at | ts] | min)),
      tail: ($now - ($pr.merged_at | ts))}] as $per
| {pr_job_count: ([$per[].jobs] | max), pr_longest_job: ([$per[].longest] | max),
   release_cycle_time: ([$per[] | .checks + .tail] | max),
   per_pull_request: $per,
   cd_job_count: ([$r.release.workflow_runs[] | select(.event == "push" or .event == "release")
                   | $r.jobs[.id | tostring].jobs[] | counted] | length)}
"""


def derive(now: str) -> dict:
    """v0.72.0's figures from the raw GitHub recordings, with jq and no onevcs."""
    raw: dict = {"heads": {}, "jobs": {}}
    sidecar = json.loads(
        (FIXTURES / "scenarios" / "onepipeline-v0.72.0.json").read_text(
            encoding="utf-8"
        )
    )
    for entry in sidecar["serve"]:
        envelope = recording(entry["recording"])
        url, body = envelope["request"]["url"], envelope["body"]
        if "/compare/" in url:
            raw["compare"] = body
        elif "/pulls?" in url:
            raw["pulls"] = body
        elif "/jobs?" in url:
            raw["jobs"][url.split("/runs/")[1].split("/")[0]] = body
        elif f"head_sha={RELEASE_COMMIT}" in url:
            raw["release"] = body
        elif "head_sha=" in url:
            raw["heads"][url.split("head_sha=")[1].split("&")[0]] = body
    done = subprocess.run(
        ["jq", "--argjson", "now", str(_epoch(now)), DERIVE],
        input=json.dumps(raw),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


def _epoch(timestamp: str) -> int:
    done = subprocess.run(
        ["jq", "-n", "--arg", "t", timestamp, "$t | fromdate"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(done.stdout)


# --- v0.72.0: --stage cd ---------------------------------------------------------


def test_cd_job_count_takes_the_push_and_release_runs_at_the_release_commit(repository):
    repo = repository("onepipeline-v0.72.0")
    result = repo.figure("job-count", "--stage", "cd", *RELEASE, unit="jobs")
    assert_within(result, 50)
    assert derive(RELEASE_END)["cd_job_count"] == 50
    assert result["detail"].splitlines() == [
        "50 jobs",
        "per workflow:",
        "  CI: 15",
        "  Engine currency: 1",
        "  Release: 32",
        "  Release-plz: 2",
        "  Visual docs: 0",
        "per runner OS:",
        "  Linux: 29",
        "  Windows: 8",
        "  macOS: 13",
    ]
    # No payload named a workflow_run run, so neither Published smoke run is read.
    assert repo.replay.served() == RELEASE_REQUESTS


def test_jobs_cancelled_before_a_runner_took_them_are_not_counted(repository):
    # The push Visual docs run at the release commit was cancelled by the next
    # merge: eight jobs completed and not skipped, none ever given a runner (one
    # with an empty name, which onevcs reports as no runner).
    jobs = [
        job
        for job in recording(
            "onepipeline-release-v0.72.0/repos_nickderobertis_onepipeline_actions_runs_"
            "38649523612_jobs__per_page_100_filter_latest__ffa686cc0e.json"
        )["body"]["jobs"]
        if job["status"] == "completed" and job["conclusion"] != "skipped"
    ]
    assert len(jobs) == 8
    assert [job["runner_name"] for job in jobs].count(None) == 7
    assert all(not job["runner_name"] for job in jobs)

    repo = repository("onepipeline-v0.72.0")
    result = repo.figure("job-count", "--stage", "cd", *RELEASE, unit="jobs")
    assert "  Visual docs: 0" in result["detail"].splitlines()
    longest = repo.figure("longest-job", "--stage", "cd", *RELEASE, unit="seconds")
    assert_within(longest, 7886)
    assert "Visual docs /" not in longest["detail"]


def test_a_workflow_run_payload_adds_the_run_it_names_and_no_other(
    repository, tmp_path
):
    repo = repository("onepipeline-v0.72.0")
    payload = tmp_path / "event.json"
    named, other = PUBLISHED_SMOKE
    payload.write_text(json.dumps({"workflow_run": {"id": named}}), encoding="utf-8")
    result = repo.figure(
        "job-count",
        "--stage",
        "cd",
        *RELEASE,
        unit="jobs",
        GITHUB_EVENT_PATH=str(payload),
    )
    # 50, and the 9 counted jobs of the Published smoke run the payload names.
    assert_within(result, 59)
    assert "  Published smoke: 9" in result["detail"].splitlines()
    assert repo.replay.served() == [*RELEASE_REQUESTS, jobs_request(named)]
    assert jobs_request(other) not in repo.replay.served()


def test_the_job_running_the_script_is_counted_to_now_and_no_other_running_job(
    repository,
):
    repo = repository("onepipeline-v0.72.0")
    # Its Mac PR proofs job started at 06:49:46; the two other in-progress jobs of
    # the run started at 06:49:27, so either would be the longest if it counted.
    now = "2026-10-19T09:49:46Z"
    env = {"GITHUB_RUN_ID": IN_PROGRESS_RUN, "RUNNER_NAME": IN_PROGRESS_RUNNER}
    args = ["--stage", "cd", *RELEASE, "--now", now]

    longest = repo.figure("longest-job", *args, unit="seconds", **env)
    assert_within(longest, 10800)
    lines = longest["detail"].splitlines()
    assert lines[0] == "CI / Mac PR proofs: 10800s"
    # The other two would have run 10819s to --now.
    assert not any(line.endswith(": 10819s") for line in lines), lines
    assert "  macOS: CI / Mac PR proofs: 10800s" in lines

    # 50, the run's 8 completed jobs that a runner took, and the running one.
    count = repo.figure("job-count", *args, unit="jobs", **env)
    assert_within(count, 59)
    assert jobs_request(IN_PROGRESS_RUN) in repo.replay.served()


# --- v0.72.0: --stage pr and release-cycle-time --------------------------------


def test_pr_figures_take_each_pull_request_and_report_the_largest(repository):
    derived = derive(RELEASE_END)
    repo = repository("onepipeline-v0.72.0")

    count = repo.figure("job-count", "--stage", "pr", *RELEASE, unit="jobs")
    assert_within(count, 26)
    assert derived["pr_job_count"] == 26
    detail = count["detail"].splitlines()
    assert [line for line in detail if line.startswith("#")] == [
        "#914: 25 jobs",
        "#916: 25 jobs",
        "#919: 26 jobs",
    ]
    # #914's and #916's Release sweep runs list only skipped jobs: they add 0.
    at_914 = detail[: detail.index("#916: 25 jobs")]
    assert "    Release sweep: 0" in at_914

    longest = repo.figure("longest-job", "--stage", "pr", *RELEASE, unit="seconds")
    assert_within(longest, 7371)
    assert derived["pr_longest_job"] == 7371
    blocks = longest["detail"].split("\n#")
    assert [block.splitlines()[0] for block in blocks] == ["#914:", "916:", "919:"]
    assert blocks[1].splitlines()[1] == "  CI / cross (windows-latest): 7371s"
    assert "    Windows: CI / cross (windows-latest): 7371s" in blocks[1].splitlines()
    assert "Release sweep / " not in blocks[0]


def test_release_cycle_time_adds_each_pull_requests_checks_to_its_wait_for_release(
    repository,
):
    derived = derive(RELEASE_END)
    repo = repository("onepipeline-v0.72.0")
    result = repo.figure(
        "release-cycle-time", *RELEASE, "--now", RELEASE_END, unit="seconds"
    )
    assert_within(result, 83074)
    assert derived["release_cycle_time"] == 83074
    assert result["detail"].splitlines() == [
        "#914: checks 9771s + merge to release 73303s",
        "#916: checks 8381s + merge to release 31144s",
        "#919: checks 42029s + merge to release 31026s",
    ]
    assert [
        (p["number"], p["checks"], p["tail"]) for p in derived["per_pull_request"]
    ] == [
        (914, 9771, 73303),
        (916, 8381, 31144),
        (919, 42029, 31026),
    ]


# --- reads: saved answers and what each figure costs ---------------------------

ALL_FIGURES = [
    {
        "id": "cd-longest-job",
        "args": ["longest-job", "--stage", "cd", *RELEASE],
        "unit": "seconds",
    },
    {
        "id": "cd-job-count",
        "args": ["job-count", "--stage", "cd", *RELEASE],
        "unit": "jobs",
    },
    {
        "id": "ci-longest-job",
        "args": ["longest-job", "--stage", "pr", *RELEASE],
        "unit": "seconds",
    },
    {
        "id": "ci-job-count",
        "args": ["job-count", "--stage", "pr", *RELEASE],
        "unit": "jobs",
    },
    {
        "id": "release-cycle-time",
        "args": ["release-cycle-time", *RELEASE, "--now", RELEASE_END],
        "unit": "seconds",
    },
    {
        "id": "release-read-requests",
        "args": ["release-read-requests", *RELEASE],
        "unit": "requests",
    },
    {
        "id": "pr-read-requests",
        "args": ["pr-read-requests", *RELEASE],
        "unit": "requests",
    },
]


def test_a_release_tail_asks_onevcs_each_question_once(repository, tmp_path):
    repo = repository("onepipeline-v0.72.0")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    code, results, _ = repo.check(ALL_FIGURES, RUNNER_TEMP=str(runner_temp))
    assert code == 0, results
    assert {r["id"]: r["actual"] for r in results} == {
        "cd-longest-job": 7886,
        "cd-job-count": 50,
        "ci-longest-job": 7371,
        "ci-job-count": 26,
        "release-cycle-time": 83074,
        "release-read-requests": 9,
        "pr-read-requests": 6,
    }
    served = repo.replay.served()
    assert len(served) == len(set(served)) == 27, served
    assert sorted(served) == sorted(
        RELEASE_REQUESTS + [r for requests in PR_REQUESTS.values() for r in requests]
    )
    saved = (
        runner_temp
        / "actions-budget-figures"
        / "nickderobertis__onepipeline"
        / "v0.72.0"
    )
    assert len(list(saved.glob("*.json"))) == 1 + 1 + len(CD_RUNS) + len(HEADS)

    # The read figures report what each read cost when it was made: every request
    # behind them, under the query that made it.
    by_id = {r["id"]: r for r in results}
    release_detail = by_id["release-read-requests"]["detail"].splitlines()
    assert release_detail[0] == "release-changes --tag v0.72.0: 3 requests"
    assert [
        line.strip() for line in release_detail if line.startswith("  ")
    ] == RELEASE_REQUESTS
    pr_detail = by_id["pr-read-requests"]["detail"].splitlines()
    assert [line for line in pr_detail if line.startswith("#")] == [
        "#914: 6 requests",
        "#916: 6 requests",
        "#919: 6 requests",
    ]
    assert [line.strip() for line in pr_detail if line.startswith("  ")] == [
        r for number in (914, 916, 919) for r in PR_REQUESTS[number]
    ]


def test_off_a_runner_every_figure_asks_for_itself(repository):
    repo = repository("onepipeline-v0.72.0")
    code, results, _ = repo.check(ALL_FIGURES[:2])
    assert code == 0, results
    # Two --stage cd figures with nowhere to save: each made the release's reads.
    assert repo.replay.served() == RELEASE_REQUESTS + RELEASE_REQUESTS


def test_a_failed_read_saves_nothing_so_a_later_figure_reads_it_again(
    repository, tmp_path
):
    repo = repository("refused-pull-request-runs")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    args = ["job-count", "--stage", "pr", *RELEASE]
    refused = repo.figure(*args, unit="jobs", RUNNER_TEMP=str(runner_temp))
    assert_error(refused, "onevcs host change-checks")
    assert "Bad credentials" in refused["error"]
    saved = (
        runner_temp
        / "actions-budget-figures"
        / "nickderobertis__onepipeline"
        / "v0.72.0"
    )
    names = sorted(path.name for path in saved.iterdir())
    assert names == [
        "change-checks-914-" + HEADS[914] + "-2026-10-14T10-58-20Z.json",
        "release-changes.json",
    ], names

    repo.replay.use("onepipeline-v0.72.0")
    before = len(repo.replay.served())
    measured = repo.figure(*args, unit="jobs", RUNNER_TEMP=str(runner_temp))
    assert_within(measured, 26)
    assert repo.replay.served()[before:] == PR_REQUESTS[916] + PR_REQUESTS[919]


def test_tags_a_squashed_spelling_would_merge_keep_their_own_saved_answers(
    repository, tmp_path
):
    # `release/v0.72.0` is the whole v0.72.0 release; `release_v0.72.0` is the
    # new-shape one, whose figures differ. Both are measured under one RUNNER_TEMP,
    # as two release tails of one runner would be.
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    env = {"RUNNER_TEMP": str(runner_temp)}
    figures = {
        tag: [
            {
                "id": "cd-job-count",
                "args": ["job-count", "--stage", "cd", *release],
                "unit": "jobs",
            },
            {
                "id": "release-read-requests",
                "args": ["release-read-requests", *release],
                "unit": "requests",
            },
        ]
        for tag, release in (
            ("slash", ["--release", "release/v0.72.0", "--repo", ONEPIPELINE]),
            ("underscore", ["--release", "release_v0.72.0", "--repo", ONEPIPELINE]),
        )
    }
    compare = f"repos/{ONEPIPELINE}/compare/release{{0}}v0.71.1...release{{0}}v0.72.0?per_page=100"

    def measured(tag: str) -> dict:
        code, results, _ = repo.check(figures[tag], **env)
        assert code == 0, results
        return {r["id"]: r["actual"] for r in results}

    repo = repository("slash-tag-release")
    assert measured("slash") == {"cd-job-count": 50, "release-read-requests": 9}
    slash_reads = repo.replay.served()
    assert len(slash_reads) == len(set(slash_reads)) == 9
    assert compare.format("/") in slash_reads

    # The second release asks for its own release, runs and jobs.
    repo.replay.use("underscore-tag-release")
    assert measured("underscore") == {
        "cd-job-count": 2 + 32,
        "release-read-requests": 6,
    }
    underscore_reads = repo.replay.served()[len(slash_reads) :]
    assert len(underscore_reads) == len(set(underscore_reads)) == 6
    assert compare.format("_") in underscore_reads

    saved = runner_temp / "actions-budget-figures" / "nickderobertis__onepipeline"
    assert sorted(path.name for path in saved.iterdir()) == [
        "release%2Fv0.72.0",
        "release_v0.72.0",
    ]

    # Measured again, each reads only its own saved answers: nothing is asked, and
    # the slash release keeps its own figures though the replay now serves the
    # other release's runs.
    before = len(repo.replay.served())
    assert measured("slash") == {"cd-job-count": 50, "release-read-requests": 9}
    assert measured("underscore") == {"cd-job-count": 34, "release-read-requests": 6}
    assert len(repo.replay.served()) == before


def test_a_release_in_the_new_ci_and_cd_shape_costs_six_reads_and_two_per_pull_request(
    repository, tmp_path
):
    repo = repository("new-shape-release")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    code, results, _ = repo.check(ALL_FIGURES, RUNNER_TEMP=str(runner_temp))
    assert code == 0, results
    by_id = {r["id"]: r for r in results}
    assert by_id["release-read-requests"]["actual"] == 6
    assert by_id["pr-read-requests"]["actual"] == 2
    release_reads = [
        *RELEASE_CHANGES_REQUESTS,
        COMMIT_RUNS_REQUEST,
        jobs_request(38649523429),
        jobs_request(38649654071),
    ]
    detail = by_id["release-read-requests"]["detail"].splitlines()
    assert [line.strip() for line in detail if line.startswith("  ")] == release_reads
    pr_reads = {
        914: [head_request(914), jobs_request(38534306223)],
        916: [head_request(916), jobs_request(38632124776)],
        919: [head_request(919), jobs_request(38553682939)],
    }
    detail = by_id["pr-read-requests"]["detail"].splitlines()
    assert [line for line in detail if line.startswith("#")] == [
        "#914: 2 requests",
        "#916: 2 requests",
        "#919: 2 requests",
    ]
    assert [line.strip() for line in detail if line.startswith("  ")] == [
        r for number in (914, 916, 919) for r in pr_reads[number]
    ]
    served = repo.replay.served()
    assert len(served) == len(set(served)) == 6 + 3 * 2
    # Release-plz and Release at the release commit; one CI run per pull request.
    assert by_id["cd-job-count"]["actual"] == 2 + 32
    assert by_id["ci-job-count"]["actual"] == 18


# --- what is missing is an error, never a figure -------------------------------


def test_a_release_commit_with_no_push_or_release_run_is_missing(repository):
    repo = repository("no-push-or-release-run")
    reason = f"missing: no push or release run at v0.72.0's commit {RELEASE_COMMIT}"
    for figure, unit in (("job-count", "jobs"), ("longest-job", "seconds")):
        args = [figure, "--stage", "cd", *RELEASE]
        assert_error(repo.figure(*args, unit=unit), reason)
        assert_refused_writing_nothing(repo, args, reason)
    assert_error(
        repo.figure("release-read-requests", *RELEASE, unit="requests"), reason
    )


def test_a_commit_that_maps_to_no_merged_pull_request_is_missing(repository):
    repo = repository("unmapped-commit")
    release = ["--release", "v0.13.0", "--repo", "nickderobertis/skilltest"]
    reason = (
        "missing: commit b7d6cf9e7803b4ea3ec1f5f18ce67ba546fffab4 of v0.13.0 maps to no "
        "merged pull request"
    )
    for args, unit in (
        (["job-count", "--stage", "pr", *release], "jobs"),
        (["longest-job", "--stage", "pr", *release], "seconds"),
        (["release-cycle-time", *release], "seconds"),
        (["pr-read-requests", *release], "requests"),
    ):
        assert_error(repo.figure(*args, unit=unit), reason)
    assert_refused_writing_nothing(repo, ["release-cycle-time", *release], reason)


def test_a_pull_request_with_no_pull_request_run_is_missing(repository):
    repo = repository("no-pull-request-run")
    reason = f"missing: #916 has no pull_request run at its head {HEADS[916]}"
    for args, unit in (
        (["job-count", "--stage", "pr", *RELEASE], "jobs"),
        (["release-cycle-time", *RELEASE, "--now", RELEASE_END], "seconds"),
    ):
        assert_error(repo.figure(*args, unit=unit), reason)
        assert_refused_writing_nothing(repo, args, reason)


def test_a_selected_run_whose_job_list_is_empty_is_missing(repository):
    repo = repository("empty-job-listings")
    reason = "missing: run 38632124776 (CI, pull_request) at #916's head lists no job"
    for args, unit in (
        (["job-count", "--stage", "pr", *RELEASE], "jobs"),
        (["release-cycle-time", *RELEASE, "--now", RELEASE_END], "seconds"),
    ):
        assert_error(repo.figure(*args, unit=unit), reason)
        assert_refused_writing_nothing(repo, args, reason)
    reason = "missing: run 38649523280 (CI, push) lists no job"
    args = ["job-count", "--stage", "cd", *RELEASE]
    assert_error(repo.figure(*args, unit="jobs"), reason)
    assert_refused_writing_nothing(repo, args, reason)


def test_a_selection_whose_jobs_were_all_skipped_counts_0_and_has_no_longest_job(
    repository,
):
    repo = repository("all-skipped")
    count = repo.figure("job-count", "--stage", "pr", *RELEASE, unit="jobs")
    assert_within(count, 0)
    assert "#916: 0 jobs" in count["detail"].splitlines()

    args = ["longest-job", "--stage", "pr", *RELEASE]
    reason = "missing: no counted job"
    assert_error(repo.figure(*args, unit="seconds"), reason)
    assert_refused_writing_nothing(repo, args, reason)


# --- failures and invocation ----------------------------------------------------


def test_an_onevcs_failure_is_an_error_naming_its_reason(repository):
    repo = repository("unmapped-commit")
    result = repo.figure(
        "job-count",
        "--stage",
        "pr",
        "--release",
        "v9.9.9",
        "--repo",
        "nickderobertis/skilltest",
        unit="jobs",
    )
    assert_error(result, "onevcs host release-changes")
    assert (
        "v9.9.9 was not found in repos/nickderobertis/skilltest/releases"
        in result["error"]
    )


def test_no_release_given_or_in_the_environment_is_an_error_naming_release(repository):
    repo = repository("onepipeline-v0.72.0")
    result = repo.figure(
        "job-count", "--stage", "cd", "--repo", ONEPIPELINE, unit="jobs"
    )
    assert_error(result, "pass --release TAG")
    assert repo.replay.served() == []


def test_the_release_and_repository_come_from_the_environment(repository, tmp_path):
    repo = repository("onepipeline-v0.72.0")
    payload = tmp_path / "release.json"
    payload.write_text(
        json.dumps({"release": {"tag_name": "v0.72.0"}}), encoding="utf-8"
    )
    from_payload = repo.figure(
        "job-count",
        "--stage",
        "cd",
        unit="jobs",
        GITHUB_REPOSITORY=ONEPIPELINE,
        GITHUB_EVENT_PATH=str(payload),
    )
    assert_within(from_payload, 50)
    from_tag = repo.figure(
        "job-count",
        "--stage",
        "cd",
        unit="jobs",
        GITHUB_REPOSITORY=ONEPIPELINE,
        GITHUB_REF_TYPE="tag",
        GITHUB_REF_NAME="v0.72.0",
    )
    assert_within(from_tag, 50)


def test_outside_a_check_it_exits_2_saying_to_run_it_under_check(repository):
    repo = repository("onepipeline-v0.72.0")
    done = subprocess.run(
        [
            "bash",
            "scripts/actions-budget-figures.sh",
            "job-count",
            "--stage",
            "cd",
            *RELEASE,
        ],
        cwd=repo.root,
        capture_output=True,
        text=True,
        env=repo.env(),
        timeout=60,
    )
    assert done.returncode == 2, done
    assert "ONEBUDGETSPEC_RESULT is unset" in done.stderr
    assert "under 'onebudgetspec check'" in done.stderr
    assert repo.replay.served() == []


# --- the committed recordings ---------------------------------------------------


def test_every_recording_says_where_it_came_from():
    for path in sorted(RECORDINGS.rglob("*.json")):
        if path.name.endswith(".source.json"):
            continue
        source = json.loads(path.with_name(path.name + ".source.json").read_text())
        if "copied_from" in source:
            copied = source["copied_from"]
            assert copied["repository"] == "github.com/nickderobertis/onevcs", path
            assert copied["commit"] == "56af7aa21876083fd1b3c54258164ae632fbf480", path
            assert copied["path"].endswith(path.relative_to(RECORDINGS).as_posix()), (
                path
            )
        else:
            recorded = source["recorded"]
            assert (
                recorded["request"]
                == recording(str(path.relative_to(RECORDINGS)))["request"]["url"]
            )
            assert recorded["why"], path


def test_every_committed_fixture_passes_the_public_boundary(onevcs_bin, tmp_path):
    """No fixture names a private organisation or repository."""
    for path in sorted(FIXTURES.rglob("*")):
        if not path.is_file():
            continue
        check = {
            "destination": "public",
            "text": [path.read_text(encoding="utf-8")],
            "paths": [path.relative_to(FIXTURES.parents[4]).as_posix()],
        }
        done = subprocess.run(
            [
                str(onevcs_bin / "onevcs"),
                "boundary",
                "check",
                "--destination",
                "public",
                "--input",
                "-",
            ],
            input=json.dumps(check),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert done.returncode == 0, f"{path}: {done.stdout}{done.stderr}"
    assert shutil.which("jq")


def test_a_workflow_run_payload_that_is_not_json_is_an_error(repository, tmp_path):
    repo = repository("onepipeline-v0.72.0")
    payload = tmp_path / "event.json"
    payload.write_text("not json", encoding="utf-8")
    args = ["job-count", "--stage", "cd", *RELEASE]
    reason = f"the event payload {payload} is not JSON"
    assert_error(
        repo.figure(*args, unit="jobs", GITHUB_EVENT_PATH=str(payload)), reason
    )
    assert_refused_writing_nothing(repo, args, reason, GITHUB_EVENT_PATH=str(payload))


def test_a_run_id_that_is_not_a_number_is_an_error(repository):
    repo = repository("onepipeline-v0.72.0")
    args = ["job-count", "--stage", "cd", *RELEASE]
    reason = "a run id must be a number, got: latest"
    assert_error(repo.figure(*args, unit="jobs", GITHUB_RUN_ID="latest"), reason)
    assert jobs_request("latest") not in repo.replay.served()


def test_release_cycle_time_with_no_counted_job_is_missing(repository):
    repo = repository("all-skipped")
    args = ["release-cycle-time", *RELEASE, "--now", RELEASE_END]
    reason = "missing: #914: no counted job ends its checks"
    assert_error(repo.figure(*args, unit="seconds"), reason)
    assert_refused_writing_nothing(repo, args, reason)
