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


def slow_publication(tmp_path, mode):
    """Delay real disk writes in the child to make publication races observable."""
    preload = tmp_path / "slow-publication.cjs"
    preload.write_text(
        """
if (process.argv.includes('_proxy')) setTimeout(() => process.exit(0), 8000).unref();
const fs = require('node:fs');
const path = require('node:path');
const { syncBuiltinESMExports } = require('node:module');
const write = fs.writeFileSync;
const rename = fs.renameSync;
const pause = () => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 250);
fs.writeFileSync = (file, body, ...args) => {
  if (!path.basename(file).startsWith('proxy.json')) return write(file, body, ...args);
  const target = process.env.PUBLICATION_MODE === 'atomic'
    ? file : path.join(path.dirname(file), 'proxy.json');
  pause(); // ready first sees an absent file.
  write(target, body.slice(0, 1), ...args);
  pause(); // Readers must observe this incomplete write if it is published directly.
  if (process.env.PUBLICATION_MODE !== 'invalid') write(target, body, ...args);
};
fs.renameSync = (from, to) => {
  if (process.env.PUBLICATION_MODE === 'atomic') return rename(from, to);
};
syncBuiltinESMExports();
"""
    )
    return {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path / "temp"),
        "GITHUB_JOB": "test-linux",
        "NODE_OPTIONS": f"--require={preload}",
        "PUBLICATION_MODE": mode,
        "NX_RECORDING_SECRET": "test-secret",
    }


def test_proxy_publication_is_atomic_to_polling_reader(tmp_path):
    import time

    env = slow_publication(tmp_path, "atomic")
    proxy_file = tmp_path / "temp/nx-cache-recording/test-linux/proxy.json"
    child = subprocess.Popen(["node", str(SCRIPT), "_proxy"], env=env)
    absent = 0
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                published = json.loads(proxy_file.read_text())
            except FileNotFoundError:
                absent += 1
                time.sleep(0.001)
                continue
            assert published["url"].startswith("http://127.0.0.1:")
            assert published["secret"] == "test-secret"
            break
        else:
            pytest.fail("proxy never published its address")
        assert absent > 0
        assert list(proxy_file.parent.glob("proxy.json*")) == [proxy_file]
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.parametrize("mode", ["recovery", "invalid"])
def test_ready_waits_for_parseable_proxy_publication(tmp_path, mode):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    import time
    from urllib.request import Request, urlopen

    class Sidecar(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(401)
            self.end_headers()

        def log_message(self, *_args):
            pass

    sidecar = ThreadingHTTPServer(("127.0.0.1", 0), Sidecar)
    thread = Thread(target=sidecar.serve_forever)
    thread.start()
    output = tmp_path / "env"
    env = slow_publication(tmp_path, mode)
    started = time.monotonic()
    try:
        done = cli(
            tmp_path,
            "ready",
            "--timeout-seconds",
            "1",
            **{
                **env,
                "NX_SELF_HOSTED_REMOTE_CACHE_SERVER": f"http://127.0.0.1:{sidecar.server_port}",
                "NX_SELF_HOSTED_REMOTE_CACHE_ACCESS_TOKEN": "test",
                "GITHUB_ENV": str(output),
            },
        )
        assert done.returncode == 0, done.stderr
        if mode == "invalid":
            assert (
                "Nx recording proxy unavailable; tasks will run without remote cache"
                in done.stderr
            )
            assert not output.exists()
            assert 5 <= time.monotonic() - started < 8
        else:
            assert done.stderr == ""
            assert time.monotonic() - started < 5
            entries = dict(
                line.split("=", 1) for line in output.read_text().splitlines()
            )
            assert entries["NX_SELF_HOSTED_REMOTE_CACHE_ACCESS_TOKEN"] == "test"
    finally:
        proxy_file = tmp_path / "temp/nx-cache-recording/test-linux/proxy.json"
        try:
            proxy = json.loads(proxy_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        else:
            with urlopen(
                Request(
                    proxy["url"] + "/stop", headers={"Authorization": proxy["secret"]}
                ),
                timeout=5,
            ):
                pass
        sidecar.shutdown()
        sidecar.server_close()
        thread.join(timeout=5)
