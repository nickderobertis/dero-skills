"""Publication and readiness through the cache sidecar CLI."""

import json
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from test_nx_remote_cache import SCRIPT


@pytest.fixture
def sidecar():
    class Sidecar(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(401)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Sidecar)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def start_ready(tmp_path, sidecar, delay=0):
    # Delay only child startup; all filesystem publication calls remain real.
    preload = tmp_path / "startup.cjs"
    preload.write_text(
        "if (process.argv.includes('_proxy')) "
        f"Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, {delay});"
    )
    return subprocess.Popen(
        ["node", str(SCRIPT), "ready", "--timeout-seconds", "1"],
        cwd=tmp_path,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path / "temp"),
            "GITHUB_JOB": "test-linux",
            "NODE_OPTIONS": f"--require={preload}",
            "NX_SELF_HOSTED_REMOTE_CACHE_SERVER": sidecar,
            "NX_SELF_HOSTED_REMOTE_CACHE_ACCESS_TOKEN": "test",
            "GITHUB_ENV": str(tmp_path / "env"),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def stop_proxy(proxy_file):
    try:
        proxy = json.loads(proxy_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return
    subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "const [url, secret] = process.argv.slice(1); "
            "await fetch(url + '/stop', {headers: {Authorization: secret}});",
            proxy["url"],
            proxy["secret"],
        ],
        check=True,
        capture_output=True,
        timeout=5,
    )


def test_proxy_publication_is_atomic_to_polling_reader(tmp_path, sidecar):
    child = start_ready(tmp_path, sidecar, delay=250)
    proxy_file = tmp_path / "temp/nx-cache-recording/test-linux/proxy.json"
    absent = 0
    try:
        deadline = time.monotonic() + 5
        # The publication file is the contract under test: inspect it concurrently
        # with the real writer, then verify ready's public environment output.
        while time.monotonic() < deadline:
            try:
                published = json.loads(proxy_file.read_text())
            except FileNotFoundError:
                absent += 1
                time.sleep(0.0001)
                continue
            assert published["url"].startswith("http://127.0.0.1:")
            assert published["secret"]
            break
        else:
            pytest.fail("proxy never published its address")
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, (stdout, stderr)
        assert stderr == ""
        assert absent > 0
        assert published["url"] in (tmp_path / "env").read_text()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        stop_proxy(proxy_file)


@pytest.mark.parametrize("recover", [True, False])
def test_ready_waits_for_parseable_proxy_publication(tmp_path, sidecar, recover):
    # Hold the real child before it starts so an external interrupted publisher
    # can supply absent, partial and complete files to the public ready command.
    child = start_ready(tmp_path, sidecar, delay=8000)
    state = tmp_path / "temp/nx-cache-recording/test-linux"
    proxy_file = state / "proxy.json"
    try:
        deadline = time.monotonic() + 3
        while not (state / "session.json").exists():
            assert time.monotonic() < deadline, "ready never started"
            time.sleep(0.01)
        time.sleep(0.2)
        assert not proxy_file.exists()
        proxy_file.write_text('{"url":')
        time.sleep(0.2)
        assert child.poll() is None, "ready did not wait for a complete file"
        if recover:
            proxy_file.write_text(json.dumps({"url": sidecar, "secret": "test"}))
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, (stdout, stderr)
        output = tmp_path / "env"
        if recover:
            assert stderr == ""
            assert output.read_text().splitlines() == [
                f"NX_SELF_HOSTED_REMOTE_CACHE_SERVER={sidecar}",
                "NX_SELF_HOSTED_REMOTE_CACHE_ACCESS_TOKEN=test",
            ]
        else:
            assert (
                "Nx recording proxy unavailable; tasks will run without remote cache"
                in stderr
            )
            assert not output.exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        # On recovery ready has detached a delayed child. Wait for its real
        # publication before stopping it through the authenticated endpoint.
        if recover:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if proxy_file.exists() and '"test"' not in proxy_file.read_text():
                    break
                time.sleep(0.05)
            stop_proxy(proxy_file)
