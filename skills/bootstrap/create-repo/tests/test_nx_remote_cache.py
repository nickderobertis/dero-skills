"""Trust and local fallback through the same CLI a composed job runs."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "assets/ci/nx-remote-cache.mjs"
EVENTS = Path(__file__).parent / "fixtures/nx-cache-events"


def cli(tmp_path, *args, **extra):
    return subprocess.run(
        ["node", str(SCRIPT), *args],
        cwd=tmp_path,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path / "temp"),
            "GITHUB_JOB": "test-linux",
            **extra,
        },
        text=True,
        capture_output=True,
        timeout=15,
    )


@pytest.mark.parametrize(
    ("name", "read_only", "promotable"),
    [
        ("fork", True, False),
        ("dependabot", True, False),
        ("same-repository", False, True),
        ("push", False, False),
    ],
)
def test_trust_reads_recorded_public_pr_resources(
    tmp_path, name, read_only, promotable
):
    record = json.loads((EVENTS / f"{name}.json").read_text())
    event = tmp_path / "event.json"
    event.write_text(json.dumps(record["event"]))
    output = tmp_path / "outputs"
    done = cli(
        tmp_path,
        "trust",
        GITHUB_EVENT_PATH=str(event),
        GITHUB_REPOSITORY=record["repository"],
        GITHUB_OUTPUT=str(output),
    )
    assert done.returncode == 0, done.stderr
    assert output.read_text().splitlines() == [
        f"read_only={str(read_only).lower()}",
        f"promotable={str(promotable).lower()}",
    ]


def test_dead_sidecar_leaves_environment_untouched(tmp_path):
    output = tmp_path / "env"
    done = cli(
        tmp_path,
        "ready",
        "--timeout-seconds",
        "0.02",
        NX_SELF_HOSTED_REMOTE_CACHE_SERVER="http://127.0.0.1:1",
        NX_SELF_HOSTED_REMOTE_CACHE_ACCESS_TOKEN="test",
        GITHUB_ENV=str(output),
    )
    assert done.returncode == 0
    assert "::warning::" in done.stderr
    assert not output.exists()


def test_report_missing_telemetry_is_nonblocking_and_explicit(tmp_path):
    report = tmp_path / "report"
    done = cli(tmp_path, "report", "--job", "test-linux", "--out", str(report))
    assert done.returncode == 0
    assert "telemetry is unavailable" in done.stderr
    assert not (report / "executions.json").exists()
    assert (
        "unavailable" in json.loads((report / "unavailable.json").read_text())["reason"]
    )


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["ready", "--timeout-seconds", "-1"],
        ["report", "--job", "linux"],
        ["promote", "--workflow", "ci.yml"],
        ["promote", "--sha", "abc", "--workflow", "ci.yml"],
        ["cache-use", "--workflow", "ci.yml"],
    ],
)
def test_usage_errors_exit_two(tmp_path, args):
    done = cli(tmp_path, *args)
    assert done.returncode == 2, done


def test_report_unwritable_destination_still_exits_zero(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    done = cli(tmp_path, "report", "--job", "test-linux", "--out", str(blocked))
    assert done.returncode == 0
    assert "Cannot write unavailable cache evidence" in done.stderr
    assert blocked.read_text() == "not a directory"


@pytest.mark.parametrize("command", ["cache-use", "promote"])
def test_invalid_repository_is_a_usage_error(tmp_path, command):
    args = ["--repo", "not-owner-slash-name"]
    if command == "promote":
        args += ["--pr", "1", "--workflow", "ci.yml"]
    done = cli(tmp_path, command, *args)
    assert done.returncode == 2
    assert "--repo must be OWNER/NAME" in done.stderr
