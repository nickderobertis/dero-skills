"""Publication and readiness through the cache sidecar CLI."""

import json
import os
import subprocess

import pytest

from test_nx_remote_cache import SCRIPT, cli


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
        else:
            assert done.stderr == ""
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
