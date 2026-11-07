"""Shared cache journeys run real Nx and the pinned op-nx action bundle.

# llmlint: ignore[e2e_not_mocked] The required journey runs real Nx and the pinned sidecar; only GitHub HTTP is doubled to reproduce scope and fault histories.
Only GitHub's HTTP services are doubled. The cache service preserves the scope
rule measured by spike-opnx-audit: a PR reads its merge ref and its base, and a
write belongs only to the current ref.
"""

from __future__ import annotations

import copy
import contextlib
import io
import tarfile
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TypedDict
from urllib.parse import parse_qs, urlparse

import pytest
from ci_cd_shape import compose

SKILL = Path(__file__).resolve().parents[2]
PIN = subprocess.check_output(
    [
        "node",
        "--input-type=module",
        "-e",
        f"import {{ OP_NX_COMMIT }} from '{(SKILL / 'assets/ci/nx-remote-cache.mjs').as_uri()}'; console.log(OP_NX_COMMIT)",
    ],
    text=True,
).strip()


class CompareCommit(TypedDict):
    sha: str | None


class CompareResponse(TypedDict):
    status: str
    merge_base_commit: CompareCommit


def command(args, root, env=None, timeout=120):
    return subprocess.run(
        args,
        cwd=root,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        timeout=timeout,
    )


@pytest.fixture(scope="session")
def cache_tools(tmp_path_factory):
    root = tmp_path_factory.mktemp("nx-cache-tools")
    (root / "package.json").write_text('{"private":true}')
    installed = command(["bun", "add", "--exact", "nx@23.1.3"], root)
    assert installed.returncode == 0, installed.stderr
    action = root / "op-nx"
    # llmlint: ignore[async_typed_clients_at_boundaries] This synchronous pytest fixture fetches the real pinned action bundle with git, whose checkout semantics are the interface under test; no application API client is involved.
    cloned = command(
        [
            "git",
            "clone",
            "--quiet",
            "https://github.com/op-nx/github-cache.git",
            str(action),
        ],
        root,
    )
    assert cloned.returncode == 0, cloned.stderr
    checked = command(["git", "checkout", "--quiet", PIN], action)
    assert checked.returncode == 0, checked.stderr
    assert command(["git", "rev-parse", "HEAD"], action).stdout.strip() == PIN
    return root / "node_modules", action


# llmlint: ignore[e2e_not_mocked] This GitHub HTTP boundary double preserves the measured scope rule and injects required faults; the actual cache sidecar and Nx client run unchanged.
class GithubCache:
    def __init__(self):
        self.entries = {}
        self.uploads = {}
        self.calls = []
        self.mode = "live"
        self.metadata = {}
        self.metadata_reads = []
        self.bases = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, data, status=200):
                body = (
                    json.dumps(data).encode()
                    if isinstance(data, (dict, list))
                    else data
                )
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.send_header(
                    "Content-Type",
                    "application/json"
                    if isinstance(data, (dict, list))
                    else "application/octet-stream",
                )
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = json.loads(
                    self.rfile.read(int(self.headers.get("Content-Length", 0)))
                )
                scope = self.headers.get("Authorization", "").removeprefix("Bearer ")
                method = self.path.rsplit("/", 1)[-1]
                owner.calls.append((method, scope, body))
                key = body.get("key"), body.get("version")
                if owner.mode == "fault":
                    self.reply({"code": "internal", "msg": "store unavailable"}, 500)
                elif method == "GetCacheEntryDownloadURL":
                    found = next(
                        (
                            owner.entries.get((s, *key))
                            for s in [scope, owner.bases.get(scope, "refs/heads/main")]
                            if (s, *key) in owner.entries
                        ),
                        None,
                    )
                    if found is None:
                        self.reply(
                            {"ok": False, "signedDownloadUrl": "", "matchedKey": ""}
                        )
                    else:
                        token = str(len(owner.uploads))
                        owner.uploads[token] = found
                        self.reply(
                            {
                                "ok": True,
                                "signedDownloadUrl": owner.url + "download/" + token,
                                "matchedKey": key[0],
                            }
                        )
                elif method == "CreateCacheEntry":
                    if owner.mode == "throttled":
                        self.reply(
                            {"code": "resource_exhausted", "msg": "rate limited"}, 429
                        )
                    else:
                        token = str(len(owner.uploads))
                        owner.uploads[token] = {
                            "scope": scope,
                            "key": key,
                            "blocks": {},
                        }
                        self.reply(
                            {
                                "ok": True,
                                "signedUploadUrl": owner.url + "upload/" + token,
                            }
                        )
                elif method == "FinalizeCacheEntryUpload":
                    upload = next(
                        v
                        for v in owner.uploads.values()
                        if isinstance(v, dict)
                        and v["scope"] == scope
                        and v["key"] == key
                    )
                    owner.entries[(scope, *key)] = upload["body"]
                    self.reply({"ok": True, "entryId": "1"})
                else:
                    self.reply({"error": method}, 404)

            def do_PUT(self):
                parsed = urlparse(self.path)
                token = parsed.path.rsplit("/", 1)[-1]
                upload = owner.uploads[token]
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                query = parse_qs(parsed.query)
                if query.get("comp") == ["block"]:
                    upload["blocks"][query["blockid"][0]] = body
                elif query.get("comp") == ["blocklist"]:
                    import xml.etree.ElementTree as ET

                    upload["body"] = b"".join(
                        upload["blocks"][node.text] for node in ET.fromstring(body)
                    )
                else:
                    upload["body"] = body
                self.reply(b"", 201)

            def do_GET(self):
                if self.path.startswith("/repos/"):
                    owner.metadata_reads.append(self.path)
                    value = owner.metadata.get(
                        self.path, owner.metadata.get(self.path.split("?")[0])
                    )
                    if value is None:
                        self.reply({"message": "No fixture route: " + self.path}, 404)
                    else:
                        query = parse_qs(urlparse(self.path).query)
                        page = int(query.get("page", ["1"])[0])
                        size = int(query.get("per_page", ["100"])[0])
                        if self.path not in owner.metadata and "page" in query:
                            start = (page - 1) * size
                            if isinstance(value, list):
                                value = value[start : start + size]
                            elif isinstance(value, dict):
                                value = {
                                    key: item[start : start + size]
                                    if isinstance(item, list)
                                    else item
                                    for key, item in value.items()
                                }
                        self.reply(value)
                    return
                token = self.path.split("?")[0].rsplit("/", 1)[-1]
                body = owner.uploads[token]
                self.reply(body)

            def do_DELETE(self):
                owner.calls.append(("DELETE", None, None))
                self.reply(b"", 405)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.fixture
def github_cache():
    cache = GithubCache()
    try:
        yield cache
    finally:
        cache.close()


class Workspace:
    def __init__(self, root, tools, files=None):
        self.root = root = compose(root, language="typescript", files=files)
        package = root / "package.json"
        manifest = json.loads(package.read_text())
        manifest["version"] = "1.0.0"
        package.write_text(json.dumps(manifest))
        installed = command(["bun", "install"], root)
        assert installed.returncode == 0, installed.stderr
        (root / "project.json").write_text(
            json.dumps(
                {
                    "name": "journey",
                    "targets": {
                        "test": {
                            "command": "node -e \"console.log('real task executed'); if(process.env.CACHE_JOURNEY_FAIL) process.exit(1)\"",
                            "cache": True,
                            "inputs": ["testInputs"],
                            "outputs": [],
                        }
                    },
                }
            )
        )
        done = command(["git", "init", "-q"], root)
        assert done.returncode == 0
        ignored = root / ".gitignore"
        ignored.write_text(ignored.read_text() + "\nnode_modules/\n.nx/\n")
        added = command(["git", "add", "-A"], root)
        assert added.returncode == 0, added.stderr
        self.action = tools[1]

    def cli(self, *args, env):
        return command(["node", "scripts/nx-remote-cache.mjs", *args], self.root, env)

    @contextlib.contextmanager
    def sidecar(self, cache, name, scope, base_scope="refs/heads/main", **extra):
        temp = self.root.parent / name
        temp.mkdir()
        event = temp / "event.json"
        cache.bases[scope] = base_scope
        pr_number = int(scope.split("/")[2]) if scope.startswith("refs/pull/") else 1
        event.write_text(
            json.dumps(
                {
                    "pull_request": {
                        "number": pr_number,
                        "head": {
                            "sha": extra.get("NX_HEAD", "1" * 40),
                            "repo": {"full_name": "example/project"},
                        },
                        "base": {
                            "sha": extra.get("NX_BASE", "2" * 40),
                            "repo": {"full_name": "example/project"},
                        },
                        "user": {"login": "maintainer"},
                    }
                }
            )
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "example/project",
            "GITHUB_REF": scope,
            "GITHUB_SHA": "3" * 40,
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_JOB": "test-linux",
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKSPACE": str(self.root),
            "GITHUB_TOKEN": "test-token",
            "GH_TOKEN": "test-token",
            "GITHUB_API_URL": cache.url.rstrip("/"),
            "ACTIONS_RUNTIME_TOKEN": scope,
            "ACTIONS_CACHE_SERVICE_V2": "true",
            "ACTIONS_RESULTS_URL": cache.url,
            "RUNNER_TEMP": str(temp),
            "GITHUB_ENV": str(temp / "env"),
            "GITHUB_OUTPUT": str(temp / "outputs"),
            "GITHUB_STEP_SUMMARY": str(temp / "summary"),
            "INPUT_PORT": str(port),
            "NX_SELF_HOSTED_REMOTE_CACHE_SERVER": f"http://127.0.0.1:{port}",
            "NX_SELF_HOSTED_REMOTE_CACHE_ACCESS_TOKEN": "test-access",
            "NX_DAEMON": "false",
            "NX_ISOLATE_PLUGINS": "false",
            "NX_BASE": "2" * 40,
            "NX_HEAD": "1" * 40,
            **extra,
        }
        if env["GITHUB_EVENT_NAME"] == "push":
            event.write_text(
                json.dumps(
                    {
                        "ref": scope,
                        "repository": {
                            "full_name": env["GITHUB_REPOSITORY"],
                            "default_branch": "main",
                        },
                    }
                )
            )
        log = (temp / "sidecar.log").open("w")
        process = subprocess.Popen(
            ["node", str(self.action / "start-cache-server/index.js")],
            cwd=self.root,
            env={**os.environ, **env},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        self.sidecar_process = process
        try:
            ready = self.cli("ready", "--timeout-seconds", "10", env=env)
            assert ready.returncode == 0, ready.stderr
            assert (temp / "env").exists(), (
                ready.stderr,
                (temp / "sidecar.log").read_text(),
            )
            env.update(
                dict(
                    line.split("=", 1)
                    for line in (temp / "env").read_text().splitlines()
                )
            )
            yield env, temp
        finally:
            self.cli(
                "report", "--job", "test-linux", "--out", str(temp / "report"), env=env
            )
            process.terminate()
            process.wait(timeout=10)
            log.close()

    def nx(self, env):
        return command(
            [
                "node",
                "node_modules/nx/dist/bin/nx.js",
                "run",
                "journey:test",
                "--output-style=static",
            ],
            self.root,
            env,
        )


@pytest.mark.parametrize("isolate", ["false", "true"])
def test_real_nx_stores_and_replays_with_exact_body_report(
    tmp_path, cache_tools, github_cache, isolate
):
    workspace = Workspace(tmp_path / "workspace", cache_tools)
    with workspace.sidecar(
        github_cache, "first", "refs/pull/1/merge", NX_ISOLATE_PLUGINS=isolate
    ) as (env, temp):
        run = workspace.nx(env)
        assert run.returncode == 0, (run.stdout, run.stderr)
    manifest = json.loads((temp / "report/manifest.json").read_text())
    assert len(manifest["entries"]) == 1, (manifest, (temp / "sidecar.log").read_text())
    [entry] = manifest["entries"]
    assert entry["source"] == "stored"
    assert manifest["stored_bytes"] == entry["bytes"]
    # The sidecar wraps its Nx body in the Actions cache archive. Compare the
    # accepted member bytes with the body the recording proxy accepted.
    accepted = []
    for archive in github_cache.entries.values():
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as packed:
            accepted.extend(
                packed.extractfile(member).read()
                for member in packed.getmembers()
                if member.isfile()
            )
    assert (temp / "report" / f"{entry['hash']}.tar").read_bytes() in accepted
    executions = json.loads((temp / "report/executions.json").read_text())
    assert executions["tasks"][0]["result"] == "executed"
    shutil.rmtree(workspace.root / ".nx")
    with workspace.sidecar(
        github_cache, "again", "refs/pull/1/merge", NX_ISOLATE_PLUGINS=isolate
    ) as (env, second):
        run = workspace.nx(env)
        assert run.returncode == 0, (run.stdout, run.stderr)
    replay = json.loads((second / "report/manifest.json").read_text())
    assert replay["stored_bytes"] == 0
    assert replay["entries"] == [{**entry, "source": "replayed"}]
    assert (
        json.loads((second / "report/executions.json").read_text())["tasks"][0][
            "result"
        ]
        == "remote_hit"
    )
    assert not any(call[0] == "DELETE" for call in github_cache.calls)


def utc_now(delta=0):
    return (
        (datetime.now(timezone.utc) + timedelta(seconds=delta))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def archive_report(path):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in path.iterdir():
            archive.write(file, file.name)
    return output.getvalue()


def source_metadata(
    cache,
    report,
    run_id=1,
    attempt=1,
    pr_number=1,
    head="1" * 40,
    base="2" * 40,
    merge="4" * 40,
):
    repository = {"id": 1, "full_name": "example/project"}
    pr = {
        "number": pr_number,
        "head": {"sha": head, "ref": f"change-{pr_number}", "repo": repository},
        "base": {"sha": base, "ref": "main", "repo": repository},
        "user": {"login": "maintainer"},
        "merged_at": utc_now(0.001),
        "merge_commit_sha": merge,
    }
    execution = json.loads((report / "executions.json").read_text())
    conclusion = (
        "failure"
        if any(task["result"] == "failed" for task in execution["tasks"])
        else "success"
    )
    recorded = json.loads(
        (SKILL / "tests/fixtures/nx-remote-cache/run-41674998643.json").read_text()
    )
    run_pr = copy.deepcopy(recorded["pull_requests"][0])
    run_pr["number"] = pr_number
    for side in ("head", "base"):
        run_pr[side].update(sha=pr[side]["sha"], ref=pr[side]["ref"])
    for side in ("head", "base"):
        run_pr[side]["repo"] = {
            **recorded["pull_requests"][0][side]["repo"],
            "id": 1,
            "name": "project",
            "url": "https://api.github.com/repos/example/project",
        }
    run = {
        "id": run_id,
        "run_attempt": attempt,
        "head_sha": head,
        "head_branch": f"change-{pr_number}",
        "event": "pull_request",
        "head_repository": repository,
        "pull_requests": [run_pr],
        "status": "completed",
        "conclusion": conclusion,
        "run_started_at": min(
            task["started_at"]
            for task in json.loads((report / "executions.json").read_text())["tasks"]
        ),
        "updated_at": utc_now(),
    }
    # A merge follows the final CI completion, even when the host pauses while
    # constructing metadata. Sampling it before reading the reports can race.
    pr["merged_at"] = utc_now(0.001)
    artifact = {
        "id": run_id * 100 + attempt,
        "name": "nx-cache-entries-test-linux",
        "expired": False,
        "created_at": utc_now(-0.001),
        "workflow_run": {"id": run_id, "head_sha": head, "head_repository_id": 1},
    }
    cache.metadata[f"/repos/example/project/commits/{head}/pulls"] = [pr]
    cache.metadata[f"/repos/example/project/compare/{base}...{head}"] = {
        "status": "ahead"
    }
    cache.metadata[f"/repos/example/project/compare/{base}...main"] = {
        "status": "identical"
    }
    cache.metadata[f"/repos/example/project/commits/{merge}/pulls"] = [pr]
    cache.metadata[f"/repos/example/project/pulls/{pr_number}"] = pr
    listing = cache.metadata.setdefault(
        "/repos/example/project/actions/workflows/ci.yml/runs", {"workflow_runs": []}
    )
    listing["workflow_runs"] = [
        r for r in listing["workflow_runs"] if r["id"] != run_id
    ] + [run]
    cache.metadata[
        f"/repos/example/project/actions/runs/{run_id}/attempts/{attempt}"
    ] = run
    cache.metadata[
        f"/repos/example/project/actions/runs/{run_id}/attempts/{attempt}/jobs"
    ] = {
        "jobs": [
            {
                "name": "test-linux",
                "conclusion": conclusion,
                "labels": ["ubuntu-latest"],
            }
        ]
    }
    artifact_listing = cache.metadata.setdefault(
        f"/repos/example/project/actions/runs/{run_id}/artifacts", {"artifacts": []}
    )
    artifact_listing["artifacts"].append(artifact)
    cache.metadata[f"/repos/example/project/actions/artifacts/{artifact['id']}/zip"] = (
        archive_report(report)
    )
    return pr, run, artifact


@pytest.mark.parametrize("source_replayed", [False, True])
def test_promote_latest_run_makes_results_readable_to_another_pr(
    tmp_path, cache_tools, github_cache, source_replayed
):
    workspace = Workspace(tmp_path / "workspace", cache_tools)
    with workspace.sidecar(github_cache, "prove", "refs/pull/1/merge") as (env, first):
        run = workspace.nx(env)
        assert run.returncode == 0, (run.stdout, run.stderr)
    report = first / "report"
    if source_replayed:
        shutil.rmtree(workspace.root / ".nx")
        with workspace.sidecar(
            github_cache, "retry", "refs/pull/1/merge", GITHUB_RUN_ID="2"
        ) as (env, latest):
            run = workspace.nx(env)
            assert run.returncode == 0, (run.stdout, run.stderr)
        report = latest / "report"
        manifest = json.loads((report / "manifest.json").read_text())
        assert manifest["stored_bytes"] == 0
        assert all(entry["source"] == "replayed" for entry in manifest["entries"])
    source_metadata(github_cache, report, run_id=2 if source_replayed else 1)
    # A distinct PR cannot read another PR's merge-ref scope before promotion.
    shutil.rmtree(workspace.root / ".nx")
    with workspace.sidecar(
        github_cache, "before-promote", "refs/pull/2/merge", GITHUB_RUN_ID="3"
    ) as (env, before):
        run = workspace.nx(env)
        assert run.returncode == 0, (run.stdout, run.stderr)
    assert (
        json.loads((before / "report/executions.json").read_text())["tasks"][0][
            "result"
        ]
        == "executed"
    )
    with workspace.sidecar(
        github_cache,
        "promote",
        "refs/heads/main",
        GITHUB_EVENT_NAME="push",
        GITHUB_SHA="4" * 40,
    ) as (env, promoted):
        done = workspace.cli(
            "promote", "--sha", "4" * 40, "--workflow", "ci.yml", env=env
        )
        assert done.returncode == 0, done.stderr
        assert (promoted / "nx-cache-promotion/receipt.json").exists(), (
            done.stdout,
            done.stderr,
        )
        receipt = json.loads((promoted / "nx-cache-promotion/receipt.json").read_text())
        assert len(receipt["entries"]) == 1, (done.stdout, done.stderr, receipt)
        assert receipt["entries"][0]["status"] == "promoted"
        assert receipt["scope"] == "refs/heads/main"
    shutil.rmtree(workspace.root / ".nx")
    with workspace.sidecar(
        github_cache, "after-promote", "refs/pull/3/merge", GITHUB_RUN_ID="4"
    ) as (env, after):
        run = workspace.nx(env)
        assert run.returncode == 0, (run.stdout, run.stderr)
    assert (
        json.loads((after / "report/executions.json").read_text())["tasks"][0]["result"]
        == "remote_hit"
    )
    assert not any(call[0] == "DELETE" for call in github_cache.calls)


def hash_workspace_files():
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["crates/cargo"]\n[workspace.package]\nversion = "1.0.0"\n[workspace.dependencies]\nthird = "2.0.0"\n',
        "Cargo.lock": 'version = 4\n[[package]]\nname = "cargo-project"\nversion = "1.0.0"\n',
        "crates/cargo/Cargo.toml": '[package]\nname = "cargo-project"\nversion.workspace = true\n',
        "crates/cargo/src/lib.rs": "pub fn value() -> u8 { 1 }\n",
        "packages/npm/package.json": '{"name":"npm-project","version":"1.0.0","private":true,"dependencies":{"third":"2.0.0"}}',
        "packages/npm/src/index.js": "export const value = 1;\n",
        "sdks/python/pyproject.toml": '[project]\nname = "python-project"\nversion = "1.0.0"\ndependencies = ["third==2.0.0"]\n',
        "sdks/python/src/version.py": '__version__ = "1.0.0"\n',
        "uv.lock": 'version = 1\n[[package]]\nname = "python-project"\nversion = "1.0.0"\nsource = { editable = "sdks/python" }\n',
        ".github/nx-version-files": "sdks/python/src/version.py\n",
        ".tool-versions": "nodejs 26.8.2\n",
        "CHANGELOG.md": "Release notes\n",
    }
    files["config/schema.json"] = '{"type":"object"}'
    files["nx.json"] = json.dumps(
        {
            "namedInputs": {
                "default": ["{projectRoot}/**/*", "sharedGlobals"],
                "sharedGlobals": [
                    "{workspaceRoot}/Cargo.toml",
                    "{workspaceRoot}/package.json",
                    "{workspaceRoot}/config/schema.json",
                ],
            }
        }
    )
    for root, name in [
        ("crates/cargo", "cargo-project"),
        ("packages/npm", "npm-project"),
        ("sdks/python", "python-project"),
    ]:
        files[f"{root}/project.json"] = json.dumps(
            {
                "name": name,
                "targets": {
                    "test": {
                        "command": "node -e \"console.log('hash journey executed')\"",
                        "cache": True,
                        "outputs": [],
                    }
                },
            }
        )
    return files


def local_run_environment(workspace, name, run_id):
    temp = workspace.root.parent / name
    temp.mkdir()
    event = temp / "event.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 1,
                    "head": {"sha": "1" * 40},
                    "base": {"sha": "2" * 40},
                }
            }
        )
    )
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "example/project",
        "GITHUB_REF": "refs/pull/1/merge",
        "GITHUB_SHA": "3" * 40,
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_JOB": "test-linux",
        "GITHUB_RUN_ID": str(run_id),
        "GITHUB_RUN_ATTEMPT": "1",
        "RUNNER_TEMP": str(temp),
        "NX_BASE": "2" * 40,
        "NX_HEAD": "1" * 40,
        "NX_DAEMON": "false",
        "NX_ISOLATE_PLUGINS": "false",
        "NX_SELF_HOSTED_REMOTE_CACHE_SERVER": "",
        "NX_SELF_HOSTED_REMOTE_CACHE_ACCESS_TOKEN": "",
    }, temp


def test_real_nx_hashes_ignore_only_release_edits_per_ecosystem(tmp_path, cache_tools):
    workspace = Workspace(
        tmp_path / "workspace", cache_tools, files=hash_workspace_files()
    )

    def hashes(name, number):
        shutil.rmtree(workspace.root / ".nx", ignore_errors=True)
        env, temp = local_run_environment(workspace, name, number)
        done = command(
            [
                "node",
                "node_modules/nx/dist/bin/nx.js",
                "run-many",
                "-t",
                "test",
                "--projects=cargo-project,npm-project,python-project",
                "--output-style=static",
            ],
            workspace.root,
            env,
        )
        assert done.returncode == 0, (done.stdout, done.stderr)
        reported = workspace.cli(
            "report", "--job", "test-linux", "--out", str(temp / "report"), env=env
        )
        assert reported.returncode == 0, reported.stderr
        assert (temp / "report/executions.json").exists(), reported.stderr
        doc = json.loads((temp / "report/executions.json").read_text())
        assert len(doc["tasks"]) == 3
        assert len(doc["store_attempts"]) == 3
        return {task["task"]: task["hash"] for task in doc["tasks"]}

    baseline = hashes("baseline", 1)
    for relative in hash_workspace_files():
        path = workspace.root / relative
        if path.is_file():
            path.write_text(path.read_text().replace("1.0.0", "1.0.1"))
    assert hashes("release", 2) == baseline
    for number, (path, task) in enumerate(
        [
            ("Cargo.toml", "cargo-project:test"),
            ("packages/npm/package.json", "npm-project:test"),
            ("sdks/python/pyproject.toml", "python-project:test"),
        ],
        3,
    ):
        file = workspace.root / path
        original = file.read_text()
        file.write_text(original.replace("2.0.0", "2.0.1"))
        changed = hashes("dependency-" + str(number), number)
        assert changed[task] != baseline[task]
        assert all(
            changed[other] == baseline[other] for other in baseline if other != task
        )
        file.write_text(original)
    schema = workspace.root / "config/schema.json"
    schema.write_text('{"type":"array"}')
    schema_changed = hashes("declared-schema", 6)
    assert all(schema_changed[task] != baseline[task] for task in baseline)
    schema.write_text('{"type":"object"}')
    source = workspace.root / "packages/npm/src/index.js"
    source.write_text("export const value = 2;\n")
    changed = hashes("source", 6)
    assert changed["npm-project:test"] != baseline["npm-project:test"]
    assert changed["cargo-project:test"] == baseline["cargo-project:test"]
    toolchain = workspace.root / ".tool-versions"
    toolchain.write_text("nodejs 26.9.0\n")
    all_changed = hashes("toolchain", 7)
    assert all(all_changed[task] != baseline[task] for task in baseline)
    toolchain.write_text("nodejs 26.8.2\n")
    pins = workspace.root / ".github/ci-tool-versions"
    pins.write_text(pins.read_text().replace("bun=1.4.4", "bun=1.4.5"))
    pins_changed = hashes("tool-pins", 8)
    assert all(pins_changed[task] != baseline[task] for task in baseline)


@pytest.fixture(scope="module")
def telemetry_history(tmp_path_factory, cache_tools, request):
    root = tmp_path_factory.mktemp("cache-telemetry-history")
    cache = GithubCache()
    workspace = Workspace(root / "workspace", cache_tools)

    def git(*args):
        done = command(
            [
                "git",
                "-c",
                "user.name=journey",
                "-c",
                "user.email=journey@example.com",
                *args,
            ],
            workspace.root,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    source = workspace.root / "src/library.js"
    source.parent.mkdir()
    source.write_text("export const value = 1;\n")
    (workspace.root / ".github/release-published-paths").write_text(
        "src/**\npackage.json\n"
    )
    git("add", "-A")
    git("commit", "-qm", "chore: base")
    base = git("rev-parse", "HEAD")
    source.write_text("export const value = 2;\n")
    git("add", "-A")
    git("commit", "-qm", "fix: published library")
    merged = git("rev-parse", "HEAD")
    try:
        cancelled_history = getattr(request, "param", None) == "cancelled"
        for attempt in (1, 2, 3) if cancelled_history else (1, 2):
            shutil.rmtree(workspace.root / ".nx", ignore_errors=True)
            with workspace.sidecar(
                cache,
                f"source-{attempt}",
                "refs/pull/1/merge",
                NX_BASE=base,
                NX_HEAD=merged,
                GITHUB_RUN_ATTEMPT=str(attempt),
            ) as (env, temp):
                done = workspace.nx(env)
                assert done.returncode == 0, (done.stdout, done.stderr)
                if cancelled_history and attempt < 3:
                    project_path = workspace.root / "project.json"
                    project_text = project_path.read_text()
                    project = json.loads(project_text)
                    sentinel = temp / "task-started"
                    project["targets"]["unfinished"] = {
                        "command": "node -e \"require('fs').writeFileSync(process.env.CANCEL_SENTINEL, 'started'); setTimeout(() => {}, 3000)\"",
                        "cache": False,
                    }
                    project_path.write_text(json.dumps(project))
                    process = subprocess.Popen(
                        [
                            "node",
                            "node_modules/nx/dist/bin/nx.js",
                            "run",
                            "journey:unfinished",
                            "--output-style=static",
                        ],
                        cwd=workspace.root,
                        env={**os.environ, **env, "CANCEL_SENTINEL": str(sentinel)},
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    try:
                        deadline = time.monotonic() + 30
                        while (
                            not sentinel.exists()
                            and process.poll() is None
                            and time.monotonic() < deadline
                        ):
                            time.sleep(0.05)
                        assert sentinel.exists(), (
                            "Nx never started the interrupted task"
                        )
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.wait(timeout=10)
                        project_path.write_text(project_text)

            pr, run, artifact = source_metadata(
                cache,
                temp / "report",
                attempt=attempt,
                head=merged,
                base=base,
                merge=merged,
            )
            if cancelled_history and attempt < 3:
                run["conclusion"] = "cancelled"
                cache.metadata[
                    f"/repos/example/project/actions/runs/1/attempts/{attempt}/jobs"
                ]["jobs"][0]["conclusion"] = "cancelled"
        with workspace.sidecar(
            cache,
            "promote-history",
            "refs/heads/main",
            GITHUB_EVENT_NAME="push",
            GITHUB_SHA=merged,
        ) as (env, promoted):
            done = workspace.cli(
                "promote", "--sha", merged, "--workflow", "ci.yml", env=env
            )
            assert done.returncode == 0
            receipt_path = promoted / "nx-cache-promotion/receipt.json"
            assert receipt_path.exists(), (done.stdout, done.stderr)
            receipt = json.loads(receipt_path.read_text())
            assert receipt["entries"], (done.stdout, receipt)
        package = workspace.root / "package.json"
        data = json.loads(package.read_text())
        data["version"] = "1.0.1"
        package.write_text(json.dumps(data))
        git("add", "-A")
        git("commit", "-qm", "chore: release")
        release_sha = git("rev-parse", "HEAD")
        shutil.rmtree(workspace.root / ".nx")
        release_pr = None
        reports = []
        for attempt in (1, 2):
            # Attempt two retains its local cache, which must stay outside the
            # rate. It happens after the release PR merged, within the interval.
            with workspace.sidecar(
                cache,
                f"release-{attempt}",
                "refs/pull/2/merge",
                NX_BASE=merged,
                NX_HEAD=release_sha,
                GITHUB_RUN_ID="2",
                GITHUB_RUN_ATTEMPT=str(attempt),
            ) as (env, temp):
                done = workspace.nx(env)
                assert done.returncode == 0, (done.stdout, done.stderr)
            current_pr, release_run, _ = source_metadata(
                cache,
                temp / "report",
                run_id=2,
                attempt=attempt,
                pr_number=2,
                head=release_sha,
                base=merged,
                merge=release_sha,
            )
            if release_pr is None:
                release_pr = current_pr
            else:
                current_pr["merged_at"] = release_pr["merged_at"]
            reports.append(temp / "report")
        published = utc_now(1)
        cache.metadata["/repos/example/project/releases"] = [
            {"tag_name": "v1.0.1", "draft": False, "published_at": published},
            {"tag_name": "v1.0.0", "draft": False, "published_at": utc_now(-3600)},
        ]
        cache.metadata["/repos/example/project/commits/v1.0.1"] = {"sha": release_sha}
        cache.metadata["/repos/example/project/commits/" + merged] = {"sha": merged}
        cache.metadata["/repos/example/project/compare/v1.0.0...v1.0.1"] = {
            "commits": [{"sha": merged}, {"sha": release_sha}]
        }
        cache.metadata[f"/repos/example/project/compare/{merged}...{merged}"] = {
            "status": "identical"
        }
        promotion_run = {
            "id": 100,
            "run_attempt": 1,
            "head_sha": merged,
            "head_branch": "main",
            "event": "push",
            "head_repository": {"id": 1, "full_name": "example/project"},
            "updated_at": published,
        }
        cache.metadata[
            "/repos/example/project/actions/workflows/release-plz.yml/runs"
        ] = {"workflow_runs": [promotion_run]}
        cache.metadata["/repos/example/project/actions/runs/100/artifacts"] = {
            "artifacts": [
                {
                    "id": 10000,
                    "name": "nx-cache-promotion-" + merged,
                    "expired": False,
                    "workflow_run": {
                        "id": 100,
                        "head_sha": merged,
                        "head_repository_id": 1,
                    },
                }
            ]
        }
        cache.metadata["/repos/example/project/actions/artifacts/10000/zip"] = (
            archive_report(promoted / "nx-cache-promotion")
        )
        yield workspace, cache, root, reports
    finally:
        cache.close()


def measure_cache_use(history, tmp_path):
    workspace, cache, _, _ = history
    budgets = workspace.root / "cache-use-journey.budgets.yaml"
    budgets.write_text(
        "schema_version: 1\nbudgets:\n  - id: replay\n    description: Remote replay from production task records\n    measure: reported\n    command: [bash, scripts/actions-budget-figures.sh, cache-use, --release, v1.0.1, --repo, example/project]\n    unit: percent\n    direction: min\n    threshold: 100\n"
    )
    env = {
        "GITHUB_API_URL": cache.url.rstrip("/"),
        "GH_TOKEN": "test-token",
        "RUNNER_TEMP": str(tmp_path / "read-cache"),
    }
    done = command(
        ["onebudgetspec", "check", "--json", str(budgets)], workspace.root, env
    )
    budgets.unlink()
    assert done.stdout, done.stderr
    return done, json.loads(done.stdout)["results"][0]


def test_cache_use_joins_real_writer_records_and_excludes_local_hits(
    telemetry_history, tmp_path
):
    done, result = measure_cache_use(telemetry_history, tmp_path)
    assert done.returncode == 0, (result, done.stderr)
    assert result["actual"] == 100
    assert "aggregate: 2/2 remote hits (100%)" in result["detail"]
    assert "release PR: 1/1" in result["detail"]
    assert "same-PR retry/push: 1/1" in result["detail"]
    assert "skipped local hits: 1" in result["detail"]


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "current-head",
        "behind-base",
        "head-behind-base",
        "not-in-pr",
        "fork",
        "wrong-base",
        "base-off-branch",
    ],
)
def test_cache_use_accepts_historical_push_only_with_run_evidence(
    telemetry_history, tmp_path, fault
):
    import copy

    _, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    try:
        # Recorded run 41674998643/1 of PR 602 has this earlier head while
        # GitHub's live PR association reports the later head below.
        head = "850b8ce7e25cf2fd64eeb6e79755b7fed754dc5d"
        later = "705d3530e2c17af7992293aa6bf17667887321da"
        # Add a distinct push carrying a proven replay, leaving the final push
        # and its promotion receipt intact. Losing this run changes 3/3 to 2/2.
        run_id = 41674998643
        run = copy.deepcopy(
            cache.metadata["/repos/example/project/actions/runs/1/attempts/2"]
        )
        if fault == "current-head":
            head = later = run["head_sha"]
        run.update(id=run_id, run_attempt=1, head_sha=head)
        cache.metadata[f"/repos/example/project/actions/runs/{run_id}/attempts/1"] = run
        cache.metadata["/repos/example/project/actions/workflows/ci.yml/runs"][
            "workflow_runs"
        ].append(run)
        cache.metadata[
            f"/repos/example/project/actions/runs/{run_id}/attempts/1/jobs"
        ] = copy.deepcopy(
            cache.metadata["/repos/example/project/actions/runs/1/attempts/2/jobs"]
        )
        association = copy.deepcopy(cache.metadata["/repos/example/project/pulls/1"])
        association["head"]["sha"] = later
        association["base"]["sha"] = "c" * 40  # live base has moved too
        run["pull_requests"][0]["head"]["sha"] = later
        if fault == "fork":
            run["pull_requests"][0]["head"]["repo"] = {
                "id": 99,
                "name": "project",
                "url": "https://api.github.com/repos/other/project",
            }
        membership = f"/repos/example/project/commits/{head}/pulls"
        cache.metadata[membership] = [] if fault == "not-in-pr" else [association]
        artifact = copy.deepcopy(
            cache.metadata["/repos/example/project/actions/runs/1/artifacts"][
                "artifacts"
            ][1]
        )
        artifact["id"] = 602
        artifact["workflow_run"].update(id=run_id, head_sha=head)
        cache.metadata[f"/repos/example/project/actions/runs/{run_id}/artifacts"] = {
            "artifacts": [artifact]
        }
        key = "/repos/example/project/actions/artifacts/602/zip"
        with zipfile.ZipFile(
            io.BytesIO(
                cache.metadata["/repos/example/project/actions/artifacts/102/zip"]
            )
        ) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        doc = json.loads(files["executions.json"])
        doc.update(run_id=run_id, attempt=1, head_sha=head)
        doc["selection"]["head_sha"] = head
        base = doc["selection"]["base_sha"]
        files["executions.json"] = json.dumps(doc).encode()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, body in files.items():
                archive.writestr(name, body)
        cache.metadata[key] = output.getvalue()
        head_compare = f"/repos/example/project/compare/{base}...{head}"
        branch_compare = f"/repos/example/project/compare/{base}...main"
        status = "ahead"
        merge_base = "d" * 40
        match fault:
            case "head-behind-base":
                # The event base contains the entire run head: GitHub's merge-base
                # is that head itself, rather than the branch point of a divergence.
                status = "behind"
                merge_base = head
            case "behind-base" | "base-off-branch":
                status = "diverged"
            case "wrong-base":
                status = "diverged"
                merge_base = None
        cache.metadata[head_compare] = CompareResponse(
            status=status,
            merge_base_commit=CompareCommit(sha=merge_base),
        )
        cache.metadata[branch_compare] = {
            "status": "diverged" if fault == "base-off-branch" else "ahead"
        }
        cache.metadata_reads.clear()
        done, result = measure_cache_use(telemetry_history, tmp_path)
        if fault not in (None, "current-head", "behind-base", "head-behind-base"):
            assert done.returncode != 0, result
            assert result["actual"] is None
            assert result["verdict"] == "error"
            expected = {
                "not-in-pr": "run head is not a commit of the source PR",
                "fork": "untrusted source PR repository",
                "wrong-base": "missing effective selection merge-base",
                "base-off-branch": "selection base is not on the source PR base branch",
            }
            assert expected[fault] in result["error"], result
        else:
            assert done.returncode == 0, (result, done.stderr)
            assert result["actual"] == 100
            assert "aggregate: 3/3 remote hits (100%)" in result["detail"]
            assert "same-PR retry/push: 2/2" in result["detail"]
            assert head_compare.removeprefix("/") in result["detail"]
            assert branch_compare.removeprefix("/") in result["detail"]
            assert cache.metadata_reads.count(head_compare) == 1
            assert cache.metadata_reads.count(branch_compare) == 1
            assert f"requests: {len(cache.metadata_reads)};" in result["detail"]
    finally:
        cache.metadata = original


@pytest.mark.parametrize(
    "fault",
    [
        "expired",
        "missing-job",
        "wrong-repository",
        "wrong-head",
        "wrong-attempt",
        "wrong-os",
        "missing-timing",
        "wrong-timing",
        "wrong-remote-read",
        "wrong-receipt-scope",
        "wrong-receipt-hash",
        "duplicate-conflict",
    ],
)
def test_cache_use_refuses_unavailable_or_hostile_evidence(
    telemetry_history, tmp_path, fault
):
    import copy

    workspace, cache, _, reports = telemetry_history
    original = copy.deepcopy(cache.metadata)
    try:
        if fault == "expired":
            cache.metadata["/repos/example/project/actions/runs/1/artifacts"][
                "artifacts"
            ][0]["expired"] = True
        elif fault == "missing-job":
            cache.metadata["/repos/example/project/actions/runs/2/attempts/1/jobs"][
                "jobs"
            ].append({"name": "test-windows", "conclusion": "failure"})
        elif fault.startswith("wrong-receipt"):
            key = "/repos/example/project/actions/artifacts/10000/zip"
            with zipfile.ZipFile(io.BytesIO(cache.metadata[key])) as source:
                receipt = json.loads(source.read("receipt.json"))
            if fault.endswith("scope"):
                receipt["scope"] = "refs/heads/hostile"
            else:
                receipt["attempts"][0]["hash"] = "deadbeef"
                receipt["entries"][0]["hash"] = "deadbeef"
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as target:
                target.writestr("receipt.json", json.dumps(receipt))
            cache.metadata[key] = output.getvalue()
        else:
            key = "/repos/example/project/actions/artifacts/201/zip"
            with zipfile.ZipFile(io.BytesIO(cache.metadata[key])) as source:
                files = {name: source.read(name) for name in source.namelist()}
            doc = json.loads(files["executions.json"])
            match fault:
                case "wrong-repository":
                    doc["repository"] = "hostile/fork"
                case "wrong-head":
                    doc["head_sha"] = "a" * 40
                case "wrong-attempt":
                    doc["attempt"] = 99
                case "wrong-os":
                    doc["os"] = "win32"
                case "missing-timing":
                    del doc["tasks"][0]["started_at"]
                case "wrong-remote-read":
                    doc["tasks"][0]["remote_read"] = "maybe"
                case "wrong-timing":
                    doc["tasks"][0]["started_at"] = "2099-01-01T00:00:00Z"
                    doc["tasks"][0]["finished_at"] = "2099-01-01T00:00:01Z"
                case _:
                    task = doc["tasks"][0]
                    task["finished_at"] = (
                        (
                            datetime.fromisoformat(
                                task["finished_at"].replace("Z", "+00:00")
                            )
                            - timedelta(milliseconds=1)
                        )
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z")
                    )
            files["executions.json"] = json.dumps(doc).encode()
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as target:
                for name, body in files.items():
                    target.writestr(name, body)
            if fault == "duplicate-conflict":
                artifact = copy.deepcopy(
                    cache.metadata["/repos/example/project/actions/runs/2/artifacts"][
                        "artifacts"
                    ][0]
                )
                artifact["id"] = 999
                cache.metadata["/repos/example/project/actions/runs/2/artifacts"][
                    "artifacts"
                ].append(artifact)
                cache.metadata["/repos/example/project/actions/artifacts/999/zip"] = (
                    output.getvalue()
                )
            else:
                cache.metadata[key] = output.getvalue()
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode != 0, (fault, result)
        assert result["actual"] is None
        assert result["verdict"] == "error"
        expected = {
            "expired": "expired artifact",
            "missing-job": "missing artifact",
            "wrong-repository": "identity mismatches",
            "wrong-head": "invalid selection",
            "wrong-attempt": "identity mismatches",
            "wrong-os": "OS mismatches",
            "missing-timing": "invalid execution task",
            "wrong-timing": "timing mismatches",
            "wrong-remote-read": "invalid execution task",
            "wrong-receipt-scope": "untrusted promotion",
            "wrong-receipt-hash": "no source expectation",
            "duplicate-conflict": "conflicting duplicate artifact",
        }
        assert expected[fault] in result["error"], result
    finally:
        cache.metadata = original


@pytest.mark.parametrize(
    "reason", ["no-pr", "fork", "run-fork", "dependabot", "gone", "put-fault"]
)
def test_promotion_faults_warn_and_leave_the_following_step_running(
    telemetry_history, tmp_path, reason
):
    import copy

    workspace, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    source_pr = cache.metadata["/repos/example/project/pulls/1"]
    sha = source_pr["merge_commit_sha"]
    try:
        match reason:
            case "no-pr":
                cache.metadata[f"/repos/example/project/commits/{sha}/pulls"] = []
            case "fork":
                source_pr["head"]["repo"]["full_name"] = "hostile/fork"
            case "run-fork":
                cache.metadata["/repos/example/project/actions/runs/1/attempts/2"][
                    "pull_requests"
                ][0]["head"]["repo"] = {
                    "id": 99,
                    "name": "project",
                    "url": "https://api.github.com/repos/hostile/project",
                }
            case "dependabot":
                source_pr["user"]["login"] = "dependabot[bot]"
            case "gone":
                cache.metadata["/repos/example/project/actions/runs/1/artifacts"][
                    "artifacts"
                ] = []
            case _:
                cache.mode = "fault"
        with workspace.sidecar(
            cache,
            "skip-" + reason,
            "refs/heads/main",
            GITHUB_EVENT_NAME="push",
            GITHUB_SHA=sha,
        ) as (env, temp):
            done = workspace.cli(
                "promote", "--sha", sha, "--workflow", "ci.yml", env=env
            )
            assert done.returncode == 0, done.stderr
            following = command(
                ["node", "-e", "console.log('release tool next step ran')"],
                workspace.root,
                env,
            )
            assert following.returncode == 0
            assert "release tool next step ran" in following.stdout
            if reason == "put-fault":
                receipt = json.loads(
                    (temp / "nx-cache-promotion/receipt.json").read_text()
                )
                assert receipt["entries"] == []
                assert all(
                    attempt["status"] == "failed" for attempt in receipt["attempts"]
                )
                assert "failed" in (temp / "summary").read_text()
            else:
                assert "skipped:" in done.stdout
                assert {
                    "no-pr": "no pull request",
                    "fork": "fork head",
                    "run-fork": "untrusted source PR repository",
                    "dependabot": "Dependabot",
                    "gone": "missing artifact",
                }[reason] in done.stdout
    finally:
        cache.mode = "live"
        cache.metadata = original


def test_manual_real_push_promotes_into_the_named_branch_scope(telemetry_history):
    workspace, cache, _, _ = telemetry_history
    source_pr = cache.metadata["/repos/example/project/pulls/1"]
    sha = source_pr["merge_commit_sha"]
    branch = "refs/heads/cache-promote/1"
    with workspace.sidecar(
        cache, "manual-push", branch, GITHUB_EVENT_NAME="push", GITHUB_SHA=sha
    ) as (env, temp):
        done = workspace.cli("promote", "--pr", "1", "--workflow", "ci.yml", env=env)
        assert done.returncode == 0, done.stderr
        receipt = json.loads((temp / "nx-cache-promotion/receipt.json").read_text())
        assert receipt["scope"] == branch
        assert receipt["entries"]
    shutil.rmtree(workspace.root / ".nx")
    with workspace.sidecar(
        cache,
        "branch-reader",
        "refs/pull/3/merge",
        base_scope=branch,
        GITHUB_RUN_ID="3",
    ) as (env, temp):
        done = workspace.nx(env)
        assert done.returncode == 0, (done.stdout, done.stderr)
    doc = json.loads((temp / "report/executions.json").read_text())
    assert doc["tasks"][0]["result"] == "remote_hit"


@pytest.mark.parametrize("identity", ["fork", "dependabot", "no-token"])
def test_untrusted_or_tokenless_jobs_run_tasks_without_writing(
    tmp_path, cache_tools, github_cache, identity
):
    workspace = Workspace(tmp_path / "workspace", cache_tools)
    extra = {}
    if identity == "no-token":
        extra.update({"GITHUB_TOKEN": "", "GH_TOKEN": ""})
    else:
        record = json.loads(
            (SKILL / "tests/fixtures/nx-cache-events" / f"{identity}.json").read_text()
        )
        event = tmp_path / "event.json"
        event.write_text(json.dumps(record["event"]))
        trust_output = tmp_path / "trust-output"
        trust = workspace.cli(
            "trust",
            env={
                "GITHUB_EVENT_PATH": str(event),
                "GITHUB_REPOSITORY": record["repository"],
                "GITHUB_OUTPUT": str(trust_output),
            },
        )
        assert trust.returncode == 0, trust.stderr
        assert "read_only=true" in trust_output.read_text()
        extra["CACHE_READ_ONLY"] = "1"
    with workspace.sidecar(github_cache, identity, "refs/pull/1/merge", **extra) as (
        env,
        temp,
    ):
        done = workspace.nx(env)
        assert done.returncode == 0, (done.stdout, done.stderr)
    manifest = json.loads((temp / "report/manifest.json").read_text())
    assert manifest["stored_bytes"] == 0
    assert not any(
        call[0] in {"CreateCacheEntry", "FinalizeCacheEntryUpload"}
        for call in github_cache.calls
    )


@pytest.mark.parametrize("store_mode", ["fault", "throttled", "disabled"])
def test_failed_store_attempts_qualify_reexecution_and_later_remote_hit(
    tmp_path, cache_tools, github_cache, store_mode
):
    workspace = Workspace(tmp_path / "workspace", cache_tools)

    def git(*args):
        done = command(
            [
                "git",
                "-c",
                "user.name=journey",
                "-c",
                "user.email=journey@example.com",
                *args,
            ],
            workspace.root,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    src = workspace.root / "src/module.js"
    src.parent.mkdir()
    src.write_text("export const value=1;\n")
    git("add", "-A")
    git("commit", "-qm", "chore: base")
    base = git("rev-parse", "HEAD")
    src.write_text("export const value=2;\n")
    git("add", "-A")
    git("commit", "-qm", "fix: module")
    sha = git("rev-parse", "HEAD")
    merged_at = None
    for attempt in (1, 2, 3):
        shutil.rmtree(workspace.root / ".nx", ignore_errors=True)
        if attempt == 1 and store_mode == "disabled":
            env, temp = local_run_environment(workspace, "disabled-first", 1)
            event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
            event["pull_request"]["head"]["sha"] = sha
            event["pull_request"]["base"]["sha"] = base
            Path(env["GITHUB_EVENT_PATH"]).write_text(json.dumps(event))
            env.update(NX_BASE=base, NX_HEAD=sha, GITHUB_ENV=str(temp / "env"))
            readiness = workspace.cli(
                "ready",
                "--timeout-seconds",
                "0.02",
                env={
                    **env,
                    "NX_SELF_HOSTED_REMOTE_CACHE_SERVER": "http://127.0.0.1:0",
                    "NX_SELF_HOSTED_REMOTE_CACHE_ACCESS_TOKEN": "test-token",
                },
            )
            assert readiness.returncode == 0
            assert "::warning::" in readiness.stdout + readiness.stderr
            assert not (temp / "env").exists()
            done = workspace.nx(env)
            assert done.returncode == 0, (done.stdout, done.stderr)
            reported = workspace.cli(
                "report", "--job", "test-linux", "--out", str(temp / "report"), env=env
            )
            assert reported.returncode == 0, reported.stderr
        else:
            github_cache.mode = store_mode if attempt == 1 else "live"
            with workspace.sidecar(
                github_cache,
                f"attempt-{attempt}",
                "refs/pull/1/merge",
                NX_BASE=base,
                NX_HEAD=sha,
                GITHUB_RUN_ATTEMPT=str(attempt),
            ) as (env, temp):
                done = workspace.nx(env)
                assert done.returncode == 0, (done.stdout, done.stderr)
        doc = json.loads((temp / "report/executions.json").read_text())
        pr, run, _ = source_metadata(
            github_cache,
            temp / "report",
            attempt=attempt,
            head=sha,
            base=base,
            merge=sha,
        )
        if attempt == 1:
            merged_at = pr["merged_at"]
            assert doc["tasks"][0]["result"] == "executed"
            assert len(doc["store_attempts"]) == 1
            failed = doc["store_attempts"][0]
            assert failed["status"] == "failed"
            # @actions/cache can swallow a service fault and the sidecar then
            # returns 409 after its existence probe finds no entry.
            assert failed["http_status"] == (None if store_mode == "disabled" else 409)
        elif attempt == 2:
            assert doc["tasks"][0]["result"] == "executed"
            assert doc["store_attempts"][0]["status"] == "stored"
        else:
            assert doc["tasks"][0]["result"] == "remote_hit"
        pr["merged_at"] = merged_at
    github_cache.mode = "live"
    github_cache.metadata["/repos/example/project/releases"] = [
        {"tag_name": "v1.0.1", "draft": False, "published_at": utc_now(1)},
        {"tag_name": "v1.0.0", "draft": False, "published_at": utc_now(-3600)},
    ]
    github_cache.metadata["/repos/example/project/commits/v1.0.1"] = {"sha": sha}
    github_cache.metadata["/repos/example/project/compare/v1.0.0...v1.0.1"] = {
        "commits": [{"sha": sha}]
    }
    github_cache.metadata[
        "/repos/example/project/actions/workflows/release-plz.yml/runs"
    ] = {"workflow_runs": []}
    done, result = measure_cache_use((workspace, github_cache, tmp_path, []), tmp_path)
    assert done.returncode == 1, (done.stderr, result)
    assert result["actual"] == 50
    assert "aggregate: 1/2 remote hits (50%)" in result["detail"]
    assert "failed, age " in result["detail"]
    assert "GET 404" in result["detail"]


def test_closed_run_pr_metadata_uses_commit_association_and_base_ancestry(
    telemetry_history, tmp_path
):
    import copy

    workspace, cache, _, reports = telemetry_history
    original = copy.deepcopy(cache.metadata)
    try:
        for key, run in list(cache.metadata.items()):
            if "/attempts/" not in key or key.endswith("/jobs"):
                continue
            run["actor"] = {"login": "maintainer"}
            run["pull_requests"] = []
        for number in (1, 2):
            pr = cache.metadata[f"/repos/example/project/pulls/{number}"]
            sha, base = pr["merge_commit_sha"], pr["base"]["sha"]
            cache.metadata[f"/repos/example/project/commits/{sha}"] = {
                "sha": sha,
                "parents": [{"sha": base}],
            }
            cache.metadata[f"/repos/example/project/compare/{base}...{base}"] = {
                "status": "identical"
            }
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode == 0, (result, done.stderr)
        assert result["actual"] == 100
        assert "aggregate: 2/2" in result["detail"]
    finally:
        cache.metadata = original


def test_cache_use_rejects_repeated_pagination(telemetry_history, tmp_path):
    import copy

    _, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    try:
        release = cache.metadata["/repos/example/project/releases"][0]
        for page in (1, 2):
            cache.metadata[
                f"/repos/example/project/releases?per_page=100&page={page}"
            ] = [release] * 100
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode != 0
        assert result["actual"] is None
        assert "repeated pagination page 2" in result["error"]
        assert "/releases" in result["error"]
    finally:
        cache.metadata = original


@pytest.mark.parametrize(
    "promotion", ["evicted", "never_promoted", "failed", "failed_task"]
)
def test_cache_use_counts_executing_a_proven_result_as_a_miss(
    telemetry_history, tmp_path, promotion
):
    import copy

    workspace, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    entries = dict(cache.entries)
    try:
        pr = cache.metadata["/repos/example/project/pulls/2"]
        cache.entries.clear()  # Evict only through the doubled HTTP service.
        if promotion == "failed":
            # Drive the real promote writer with a store outage. Its receipt
            # remains a qualification attempt even though no body is accepted.
            cache.mode = "fault"
            source_pr = cache.metadata["/repos/example/project/pulls/1"]
            with workspace.sidecar(
                cache,
                "promotion-miss-failed",
                "refs/heads/main",
                GITHUB_EVENT_NAME="push",
                GITHUB_SHA=source_pr["merge_commit_sha"],
            ) as (env, temp):
                promoted = workspace.cli(
                    "promote",
                    "--sha",
                    source_pr["merge_commit_sha"],
                    "--workflow",
                    "ci.yml",
                    env=env,
                )
                assert promoted.returncode == 0
            cache.metadata["/repos/example/project/actions/artifacts/10000/zip"] = (
                archive_report(temp / "nx-cache-promotion")
            )
        cache.mode = "live"
        cache.entries.clear()  # Evict at the HTTP service, then run real Nx.
        shutil.rmtree(workspace.root / ".nx", ignore_errors=True)
        with workspace.sidecar(
            cache,
            "release-miss-" + promotion,
            "refs/pull/2/merge",
            CACHE_JOURNEY_FAIL="1" if promotion == "failed_task" else "",
            NX_BASE=pr["base"]["sha"],
            NX_HEAD=pr["head"]["sha"],
            GITHUB_RUN_ID="2",
            GITHUB_RUN_ATTEMPT="3",
        ) as (env, temp):
            done = workspace.nx(env)
            assert done.returncode == (1 if promotion == "failed_task" else 0), (
                done.stdout,
                done.stderr,
            )
        execution = json.loads((temp / "report/executions.json").read_text())
        assert execution["tasks"][0]["result"] == (
            "failed" if promotion == "failed_task" else "executed"
        )
        if promotion == "failed_task":
            assert execution["store_attempts"] == []
        source_metadata(
            cache,
            temp / "report",
            run_id=2,
            attempt=3,
            pr_number=2,
            head=pr["head"]["sha"],
            base=pr["base"]["sha"],
            merge=pr["merge_commit_sha"],
        )
        cache.metadata["/repos/example/project/pulls/2"]["merged_at"] = pr["merged_at"]
        if promotion == "never_promoted":
            cache.metadata[
                "/repos/example/project/actions/workflows/release-plz.yml/runs"
            ] = {"workflow_runs": []}
        cache.metadata["/repos/example/project/releases"][0]["published_at"] = utc_now(
            1
        )
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode != 0, result
        assert result["actual"] == pytest.approx(200 / 3), result
        assert "aggregate: 2/3" in result["detail"]
        assert (
            ": " + ("evicted" if promotion == "failed_task" else promotion) + ", age "
            in result["detail"]
        )
        assert "GET 404" in result["detail"]
        assert not any(call[0] == "DELETE" for call in cache.calls)
    finally:
        cache.mode = "live"
        cache.metadata = original
        cache.entries = entries


def test_identical_duplicate_artifacts_do_not_duplicate_executions(
    telemetry_history, tmp_path
):
    import copy

    _, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    try:
        listing = cache.metadata["/repos/example/project/actions/runs/2/artifacts"][
            "artifacts"
        ]
        duplicate = copy.deepcopy(listing[0])
        duplicate["id"] = 999
        listing.append(duplicate)
        cache.metadata["/repos/example/project/actions/artifacts/999/zip"] = (
            cache.metadata[
                f"/repos/example/project/actions/artifacts/{listing[0]['id']}/zip"
            ]
        )
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode == 0, result
        assert result["actual"] == 100
        assert "aggregate: 2/2" in result["detail"]
    finally:
        cache.metadata = original


def test_first_run_is_missing_data_with_zero_eligible_executions(
    tmp_path, cache_tools, github_cache
):
    workspace = Workspace(tmp_path / "workspace", cache_tools)
    with workspace.sidecar(github_cache, "first-only", "refs/pull/1/merge") as (
        env,
        temp,
    ):
        done = workspace.nx(env)
        assert done.returncode == 0, (done.stdout, done.stderr)
    pr, _, _ = source_metadata(github_cache, temp / "report")
    github_cache.metadata["/repos/example/project/releases"] = [
        {"tag_name": "v1.0.1", "draft": False, "published_at": utc_now(1)},
        {"tag_name": "v1.0.0", "draft": False, "published_at": utc_now(-3600)},
    ]
    github_cache.metadata["/repos/example/project/commits/v1.0.1"] = {
        "sha": pr["merge_commit_sha"]
    }
    github_cache.metadata["/repos/example/project/compare/v1.0.0...v1.0.1"] = {
        "commits": [{"sha": pr["merge_commit_sha"]}]
    }
    github_cache.metadata[
        "/repos/example/project/actions/workflows/release-plz.yml/runs"
    ] = {"workflow_runs": []}
    done, result = measure_cache_use((workspace, github_cache, tmp_path, []), tmp_path)
    assert done.returncode != 0
    assert result["actual"] is None
    assert "zero eligible executions" in result["error"]


def test_changed_hash_is_fresh_then_later_push_retry_replays(
    telemetry_history, tmp_path
):
    import copy

    workspace, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    source = workspace.root / "src/library.js"
    original_source = source.read_text()
    try:
        source.write_text("export const value = 3;\n")
        for args in (
            ["add", "src/library.js"],
            [
                "-c",
                "user.name=journey",
                "-c",
                "user.email=journey@example.com",
                "commit",
                "-qm",
                "fix: later source push",
            ],
        ):
            committed = command(["git", *args], workspace.root)
            assert committed.returncode == 0, committed.stderr
        sha = command(["git", "rev-parse", "HEAD"], workspace.root).stdout.strip()
        base = cache.metadata["/repos/example/project/pulls/2"]["base"]["sha"]
        merged_at = None
        for attempt in (1, 2):
            shutil.rmtree(workspace.root / ".nx", ignore_errors=True)
            with workspace.sidecar(
                cache,
                f"later-push-{attempt}",
                "refs/pull/2/merge",
                NX_BASE=base,
                NX_HEAD=sha,
                GITHUB_RUN_ID="3",
                GITHUB_RUN_ATTEMPT=str(attempt),
            ) as (env, temp):
                done = workspace.nx(env)
                assert done.returncode == 0, (done.stdout, done.stderr)
            doc = json.loads((temp / "report/executions.json").read_text())
            assert doc["tasks"][0]["result"] == (
                "executed" if attempt == 1 else "remote_hit"
            )
            pr, _, _ = source_metadata(
                cache,
                temp / "report",
                run_id=3,
                attempt=attempt,
                pr_number=2,
                head=sha,
                base=base,
                merge=sha,
            )
            if merged_at is None:
                merged_at = pr["merged_at"]
            pr["merged_at"] = merged_at
            cache.metadata["/repos/example/project/commits/v1.0.1"] = {"sha": sha}
            cache.metadata["/repos/example/project/compare/v1.0.0...v1.0.1"] = {
                "commits": [{"sha": base}, {"sha": sha}]
            }
            cache.metadata["/repos/example/project/releases"][0]["published_at"] = (
                utc_now(1)
            )
            done, result = measure_cache_use(
                telemetry_history, tmp_path / f"attempt-{attempt}"
            )
            assert done.returncode == 0, (result, done.stderr)
            assert result["actual"] == 100
            assert f"aggregate: {attempt}/{attempt}" in result["detail"]
            assert f"release PR: {attempt - 1}/{attempt - 1}" in result["detail"]
    finally:
        cache.metadata = original
        source.write_text(original_source)
        command(["git", "add", "src/library.js"], workspace.root)


@pytest.mark.parametrize("fork,jobless", [(False, False), (True, False), (False, True)])
def test_recorded_run_counts_in_promote_and_cache_use(
    telemetry_history, tmp_path, fork, jobless
):
    """Drive CLI promotion and measurement through recorded GitHub HTTP shapes."""
    workspace, cache, _, _ = telemetry_history
    original = cache.metadata
    recorded = json.loads(
        (SKILL / "tests/fixtures/nx-remote-cache/run-41674998643.json").read_text()
    )
    run = copy.deepcopy(recorded)
    no_jobs_run = json.loads(
        (SKILL / "tests/fixtures/nx-remote-cache/run-41771574673.json").read_text()
    )
    no_jobs = json.loads(
        (SKILL / "tests/fixtures/nx-remote-cache/run-41771574673-jobs.json").read_text()
    )
    if jobless:
        # The completed-run HTTP response joins the recorded jobless run's PR.
        # Keep the rejected workflow's run and jobs responses unaltered.
        run["pull_requests"] = copy.deepcopy(no_jobs_run["pull_requests"])
        run["run_started_at"] = "2026-11-04T05:00:00Z"
        run["updated_at"] = "2026-11-04T05:01:00Z"
    repo = run["repository"]["full_name"]
    prefix = f"/repos/{repo}"
    pr = copy.deepcopy(run["pull_requests"][0])
    assert all(
        set(pr[side]["repo"]) == {"id", "name", "url"} for side in ("head", "base")
    )
    for side in ("head", "base"):
        pr[side]["repo"]["full_name"] = repo
    pr["head"]["sha"] = run["head_sha"]
    pr.update(
        user={"login": "maintainer"},
        merged_at=run["updated_at"],
        merge_commit_sha="a" * 40,
    )
    if fork:
        run["pull_requests"][0]["head"]["repo"] = {
            "id": 99,
            "name": "onepipeline-ui",
            "url": "https://api.github.com/repos/other/onepipeline-ui",
        }
    elif not jobless:
        assert run == recorded
    earlier = copy.deepcopy(run)
    earlier["id"] -= 1
    earlier["run_started_at"] = "2026-01-01T00:00:00Z"
    earlier["updated_at"] = "2026-01-01T00:01:00Z"
    if jobless:
        earlier["run_started_at"] = "2026-11-04T04:30:00Z"
        earlier["updated_at"] = "2026-11-04T04:31:00Z"
    # The cache body was produced by real Nx in telemetry_history. The two
    # artifacts model its writer and subsequent replay in the recorded run.
    with zipfile.ZipFile(
        io.BytesIO(cache.metadata["/repos/example/project/actions/artifacts/201/zip"])
    ) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        body = archive.read(f"{manifest['entries'][0]['hash']}.tar")
    hash_value = manifest["entries"][0]["hash"]
    metadata = {
        f"{prefix}/actions/workflows/ci.yml/runs": {"workflow_runs": [earlier, run]},
        f"{prefix}/commits/{pr['merge_commit_sha']}/pulls": [pr],
        f"{prefix}/commits/{run['head_sha']}/pulls": [pr],
        f"{prefix}/compare/{pr['base']['sha']}...{run['head_sha']}": {
            "status": "ahead"
        },
        f"{prefix}/compare/{pr['base']['sha']}...main": {"status": "ahead"},
        f"{prefix}/releases": [
            {
                "tag_name": "recording",
                "draft": False,
                "published_at": run["updated_at"],
            }
        ],
        f"{prefix}/commits/recording": {"sha": pr["merge_commit_sha"]},
        f"{prefix}/commits": [{"sha": pr["merge_commit_sha"]}],
        f"{prefix}/actions/workflows/release-plz.yml/runs": {"workflow_runs": []},
    }
    for source, replay in ((earlier, False), (run, True)):
        run_id = source["id"]
        metadata[f"{prefix}/actions/runs/{run_id}/attempts/1"] = source
        metadata[f"{prefix}/actions/runs/{run_id}/attempts/1/jobs"] = {
            "jobs": [
                {
                    "name": "test-linux",
                    "conclusion": "success",
                    "labels": ["ubuntu-latest"],
                }
            ],
        }
        artifact = {
            "id": run_id,
            "name": "nx-cache-entries-test-linux",
            "expired": False,
            "created_at": source["updated_at"],
            "workflow_run": {
                "id": run_id,
                "head_sha": source["head_sha"],
                "head_repository_id": source["head_repository"]["id"],
            },
        }
        metadata[f"{prefix}/actions/runs/{run_id}/artifacts"] = {
            "artifacts": [artifact]
        }
        task = {
            "task": "library:build",
            "hash": hash_value,
            "cacheable": True,
            "result": "remote_hit" if replay else "executed",
            "remote_status": 200 if replay else 404,
            "started_at": source["run_started_at"],
            "finished_at": source["updated_at"],
        }
        doc = {
            "schema": 1,
            "repository": repo,
            "ref": f"refs/pull/{pr['number']}/merge",
            "pr": pr["number"],
            "head_sha": source["head_sha"],
            "run_id": run_id,
            "attempt": 1,
            "job": "test-linux",
            "os": "linux",
            "selection": {
                "mode": "affected",
                "base_sha": pr["base"]["sha"],
                "head_sha": source["head_sha"],
            },
            "tasks": [task],
            "store_attempts": []
            if replay
            else [
                {
                    "task": task["task"],
                    "hash": hash_value,
                    "os": "linux",
                    "attempted_at": source["updated_at"],
                    "status": "stored",
                    "http_status": 200,
                }
            ],
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("executions.json", json.dumps(doc))
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema": 1,
                        "job": "test-linux",
                        "stored_bytes": 0 if replay else len(body),
                        "entries": [
                            {
                                "hash": hash_value,
                                "bytes": len(body),
                                "source": "replayed" if replay else "stored",
                            }
                        ],
                    }
                ),
            )
            archive.writestr(f"{hash_value}.tar", body)
        metadata[f"{prefix}/actions/artifacts/{run_id}/zip"] = output.getvalue()
    if jobless:
        metadata[f"{prefix}/actions/workflows/ci.yml/runs"]["workflow_runs"].append(
            no_jobs_run
        )
        run_id = no_jobs_run["id"]
        metadata[f"{prefix}/actions/runs/{run_id}/attempts/1"] = no_jobs_run
        metadata[f"{prefix}/actions/runs/{run_id}/attempts/1/jobs"] = no_jobs
        metadata[f"{prefix}/actions/runs/{run_id}/artifacts"] = {
            "total_count": 0,
            "artifacts": [],
        }
    cache.metadata = metadata
    try:
        with workspace.sidecar(
            cache,
            "recorded-fork" if fork else "recorded-jobless" if jobless else "recorded",
            "refs/heads/main",
            GITHUB_REPOSITORY=repo,
            GITHUB_EVENT_NAME="push",
            GITHUB_SHA=pr["merge_commit_sha"],
            ONEBUDGETSPEC_RESULT=str(tmp_path / "result.json"),
        ) as (env, temp):
            promoted = workspace.cli(
                "promote",
                "--repo",
                repo,
                "--sha",
                pr["merge_commit_sha"],
                "--workflow",
                "ci.yml",
                env=env,
            )
            measured = workspace.cli(
                "cache-use",
                "--repo",
                repo,
                "--release",
                "recording",
                env=env,
            )
            receipt = temp / "nx-cache-promotion/receipt.json"
            assert promoted.returncode == 0, promoted.stderr
            if fork:
                assert not receipt.exists()
                assert "untrusted source PR repository" in promoted.stderr
                assert measured.returncode != 0
                assert "untrusted source PR repository" in measured.stderr
            else:
                evidence = json.loads(receipt.read_text())
                assert evidence["source_run_id"] == recorded["id"]
                assert evidence["entries"]
                assert measured.returncode == 0, measured.stderr
                result = json.loads((tmp_path / "result.json").read_text())
                assert result["value"] == 100
                assert "aggregate: 1/1 remote hits (100%)" in result["detail"]
                if jobless:
                    assert (
                        f"run {no_jobs_run['id']}/1 started no job "
                        f"(conclusion: {no_jobs_run['conclusion']})"
                    ) in result["detail"]
    finally:
        cache.metadata = original


@pytest.mark.parametrize("artifact_kind", ["missing", "unavailable"])
@pytest.mark.parametrize("scope", ["job", "run"])
@pytest.mark.parametrize("conclusion", ["cancelled", "failure", "skipped", "success"])
def test_cache_use_missing_artifact_requires_cancellation(
    telemetry_history, tmp_path, scope, conclusion, artifact_kind
):
    _, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    prefix = "/repos/example/project/actions/runs/1/attempts/1"
    try:
        jobs = cache.metadata[f"{prefix}/jobs"]["jobs"]
        jobs.append({"name": "lint", "conclusion": conclusion})
        if artifact_kind == "unavailable":
            artifacts = cache.metadata[
                "/repos/example/project/actions/runs/1/artifacts"
            ]["artifacts"]
            artifact = copy.deepcopy(artifacts[0])
            artifact.update(id=999, name="nx-cache-entries-lint")
            artifacts.append(artifact)
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr(
                    "unavailable.json",
                    json.dumps(
                        {"schema": 1, "reason": "Nx run telemetry is incomplete"}
                    ),
                )
            cache.metadata["/repos/example/project/actions/artifacts/999/zip"] = (
                output.getvalue()
            )
        if scope == "run":
            cache.metadata[prefix]["conclusion"] = conclusion
            if conclusion == "cancelled":
                jobs[-1]["conclusion"] = None
        done, result = measure_cache_use(telemetry_history, tmp_path)
        if conclusion == "cancelled":
            assert done.returncode == 0, (result, done.stderr)
            assert "aggregate: 2/2 remote hits (100%)" in result["detail"]
            reason = (
                "no artifact" if artifact_kind == "missing" else "telemetry unavailable"
            )
            assert f"cancelled run 1/1 job lint: {reason}" in result["detail"]
        else:
            assert done.returncode != 0
            assert result["actual"] is None
            assert "missing" in result["error"]
    finally:
        cache.metadata = original


@pytest.mark.parametrize("telemetry_history", ["cancelled"], indirect=True)
def test_cache_use_cancelled_invocation_preserves_completed_records(
    telemetry_history, tmp_path
):
    _, cache, _, _ = telemetry_history
    for attempt in (1, 2):
        key = f"/repos/example/project/actions/artifacts/{100 + attempt}/zip"
        with zipfile.ZipFile(io.BytesIO(cache.metadata[key])) as archive:
            doc = json.loads(archive.read("executions.json"))
        assert doc["schema"] == 2
        assert len(doc["cancelled_invocations"]) == 1
        invocation = doc["cancelled_invocations"][0]
        assert invocation["id"]
        assert invocation["argv"][-3:] == [
            "run",
            "journey:unfinished",
            "--output-style=static",
        ]
        assert len(doc["tasks"]) == 1
        if attempt == 1:
            assert doc["store_attempts"][0]["status"] == "stored"
    done, result = measure_cache_use(telemetry_history, tmp_path)
    assert done.returncode == 0, (result, done.stderr)
    assert "aggregate: 3/3 remote hits (100%)" in result["detail"]
    assert "same-PR retry/push: 2/2" in result["detail"]
    for attempt in (1, 2):
        assert (
            f"cancelled run 1/{attempt} job test-linux invocation " in result["detail"]
        )
    assert '"run","journey:unfinished","--output-style=static"]' in result["detail"]
    assert (
        "unfinished tasks are not individually known; excluded from numerator and denominator"
        in result["detail"]
    )


@pytest.mark.parametrize("scope", ["job", "run"])
def test_cache_use_cancelled_job_without_any_artifact(
    telemetry_history, tmp_path, scope
):
    _, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    prefix = "/repos/example/project/actions/runs/1/attempts/1"
    try:
        cache.metadata["/repos/example/project/actions/runs/1/artifacts"][
            "artifacts"
        ] = [
            a
            for a in cache.metadata["/repos/example/project/actions/runs/1/artifacts"][
                "artifacts"
            ]
            if a["id"] != 101
        ]
        if scope == "job":
            cache.metadata[f"{prefix}/jobs"]["jobs"][0]["conclusion"] = "cancelled"
        else:
            cache.metadata[prefix]["conclusion"] = "cancelled"
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode == 0, (result, done.stderr)
        assert "aggregate: 1/1 remote hits (100%)" in result["detail"]
        assert "cancelled run 1/1 job test-linux: no artifact" in result["detail"]
    finally:
        cache.metadata = original


@pytest.mark.parametrize("cancelled", [False, True])
def test_cache_use_partial_report_requires_github_cancellation(
    telemetry_history, tmp_path, cancelled
):
    _, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    try:
        key = "/repos/example/project/actions/artifacts/101/zip"
        with zipfile.ZipFile(io.BytesIO(cache.metadata[key])) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        doc = json.loads(files["executions.json"])
        doc.update(
            schema=2,
            cancelled_invocations=[
                {"id": "interrupted", "argv": ["node", "nx", "affected", "-t", "test"]}
            ],
        )
        files["executions.json"] = json.dumps(doc).encode()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, body in files.items():
                archive.writestr(name, body)
        cache.metadata[key] = output.getvalue()
        if cancelled:
            cache.metadata["/repos/example/project/actions/runs/1/attempts/1"][
                "conclusion"
            ] = "cancelled"
        done, result = measure_cache_use(telemetry_history, tmp_path)
        if cancelled:
            assert done.returncode == 0, (result, done.stderr)
            assert "aggregate: 2/2 remote hits (100%)" in result["detail"]
            assert 'argv ["node","nx","affected","-t","test"]' in result["detail"]
        else:
            assert done.returncode != 0
            assert result["actual"] is None
            assert "telemetry is incomplete" in result["error"]
    finally:
        cache.metadata = original


def test_each_remote_read_outcome_reaches_task_telemetry(
    tmp_path, cache_tools, github_cache
):
    workspace = Workspace(tmp_path / "workspace", cache_tools)

    def read(name, *args, sidecar_gone=False):
        with workspace.sidecar(github_cache, name, "refs/pull/1/merge") as (env, temp):
            if sidecar_gone:
                workspace.sidecar_process.terminate()
                workspace.sidecar_process.wait(timeout=10)
            run = command(
                [
                    "node",
                    "node_modules/nx/dist/bin/nx.js",
                    "run",
                    "journey:test",
                    "--output-style=static",
                    *args,
                ],
                workspace.root,
                env,
            )
            assert run.returncode == 0, (run.stdout, run.stderr)
        doc = json.loads((temp / "report/executions.json").read_text())
        assert doc["schema"] == 2
        assert list(doc).count("base_sha") == 1
        [task] = doc["tasks"]
        return task

    # An absent result: the store answers, and holds nothing.
    absent = read("absent")
    assert (absent["remote_read"], absent["result"]) == ("miss", "executed")
    assert absent["remote_status"] == 404
    # The stored result, read back with the local cache gone.
    shutil.rmtree(workspace.root / ".nx")
    stored = read("stored")
    assert (stored["remote_read"], stored["result"]) == ("hit", "remote_hit")
    assert stored["remote_status"] == 200
    # A result the local cache serves makes no remote read.
    local = read("local")
    assert (local["remote_read"], local["result"]) == ("not_attempted", "local_hit")
    assert local["remote_status"] is None
    # Nor does a run told to skip the cache, with the result absent locally too.
    shutil.rmtree(workspace.root / ".nx")
    skipped = read("skipped", "--skip-nx-cache")
    assert (skipped["remote_read"], skipped["result"]) == ("not_attempted", "executed")
    assert skipped["remote_status"] is None
    # The read erroring at the proxy's boundary: the sidecar is gone and nothing answers.
    shutil.rmtree(workspace.root / ".nx")
    erroring = read("erroring", sidecar_gone=True)
    assert (erroring["remote_read"], erroring["result"]) == ("failed", "executed")
    assert erroring["remote_status"] is None
    # The pinned sidecar's stated limit: it answers a store fault as a 404 of its own, so
    # GitHub answering 500 reads as a miss, which can only understate replays.
    shutil.rmtree(workspace.root / ".nx")
    github_cache.mode = "fault"
    degraded = read("degraded")
    assert (degraded["remote_read"], degraded["result"]) == ("miss", "executed")
    assert degraded["remote_status"] == 404


@pytest.fixture(scope="module")
def moved_base_history(tmp_path_factory, cache_tools):
    """A carried pull request tested on a real merge ref, then merged on a base that moved."""
    root = tmp_path_factory.mktemp("moved-base-history")
    cache = GithubCache()
    workspace = Workspace(root / "workspace", cache_tools)

    def git(*args):
        done = command(
            [
                "git",
                "-c",
                "user.name=journey",
                "-c",
                "user.email=journey@example.com",
                *args,
            ],
            workspace.root,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    source = workspace.root / "src/library.js"
    source.parent.mkdir()
    source.write_text("export const value = 1;\n")
    (workspace.root / ".github/release-published-paths").write_text(
        "src/**\npackage.json\n"
    )
    git("add", "-A")
    git("commit", "-qm", "chore: base")
    tested_base = git("rev-parse", "HEAD")
    git("checkout", "-qb", "change-1")
    source.write_text("export const value = 2;\n")
    git("add", "-A")
    git("commit", "-qm", "fix: published library")
    head = git("rev-parse", "HEAD")
    # refs/pull/1/merge: the commit a pull_request run checks out, its first parent the base.
    git("checkout", "-q", "--detach", tested_base)
    git("merge", "-q", "--no-ff", "-m", "Merge change-1 into main", head)
    merge_ref = git("rev-parse", "HEAD")
    assert git("rev-parse", "HEAD^1") == tested_base
    try:
        executions = []
        for attempt in (1, 2):
            shutil.rmtree(workspace.root / ".nx", ignore_errors=True)
            with workspace.sidecar(
                cache,
                f"carried-{attempt}",
                "refs/pull/1/merge",
                GITHUB_SHA=merge_ref,
                NX_BASE=tested_base,
                NX_HEAD=head,
                GITHUB_RUN_ATTEMPT=str(attempt),
            ) as (env, temp):
                done = workspace.nx(env)
                assert done.returncode == 0, (done.stdout, done.stderr)
            executions.append(json.loads((temp / "report/executions.json").read_text()))
            pr, _, _ = source_metadata(
                cache,
                temp / "report",
                attempt=attempt,
                head=head,
                base=tested_base,
                merge="0" * 40,
            )
        # main moved under the pull request, and its squash landed on the newer base.
        git("checkout", "-q", "-B", "main", tested_base)
        (workspace.root / "src/other.js").write_text("export const other = 1;\n")
        git("add", "-A")
        git("commit", "-qm", "fix: another change")
        landed_base = git("rev-parse", "HEAD")
        source.write_text("export const value = 2;\n")
        git("add", "-A")
        git("commit", "-qm", "fix: published library (#1)")
        merged = git("rev-parse", "HEAD")
        assert landed_base != tested_base
        pr["merge_commit_sha"] = merged
        cache.metadata[f"/repos/example/project/commits/{merged}/pulls"] = [pr]
        cache.metadata[f"/repos/example/project/commits/{merged}"] = {
            "sha": merged,
            "parents": [{"sha": landed_base}],
        }
        with workspace.sidecar(
            cache,
            "promote",
            "refs/heads/main",
            GITHUB_EVENT_NAME="push",
            GITHUB_SHA=merged,
        ) as (env, promoted):
            done = workspace.cli(
                "promote", "--sha", merged, "--workflow", "ci.yml", env=env
            )
            assert done.returncode == 0, done.stderr
            assert (promoted / "nx-cache-promotion/receipt.json").exists(), done.stdout
        package = workspace.root / "package.json"
        data = json.loads(package.read_text())
        data["version"] = "1.0.1"
        package.write_text(json.dumps(data))
        git("add", "-A")
        git("commit", "-qm", "chore: release")
        release_sha = git("rev-parse", "HEAD")
        # The release pull request's tree holds what landed, so it runs at a hash no run proved.
        shutil.rmtree(workspace.root / ".nx")
        with workspace.sidecar(
            cache,
            "release",
            "refs/pull/2/merge",
            NX_BASE=merged,
            NX_HEAD=release_sha,
            GITHUB_RUN_ID="2",
        ) as (env, temp):
            done = workspace.nx(env)
            assert done.returncode == 0, (done.stdout, done.stderr)
        source_metadata(
            cache,
            temp / "report",
            run_id=2,
            pr_number=2,
            head=release_sha,
            base=merged,
            merge=release_sha,
        )
        published = utc_now(1)
        cache.metadata["/repos/example/project/releases"] = [
            {"tag_name": "v1.0.1", "draft": False, "published_at": published},
            {"tag_name": "v1.0.0", "draft": False, "published_at": utc_now(-3600)},
        ]
        cache.metadata["/repos/example/project/commits/v1.0.1"] = {"sha": release_sha}
        cache.metadata["/repos/example/project/compare/v1.0.0...v1.0.1"] = {
            "commits": [{"sha": merged}, {"sha": release_sha}]
        }
        cache.metadata[
            "/repos/example/project/actions/workflows/release-plz.yml/runs"
        ] = {
            "workflow_runs": [
                {
                    "id": 100,
                    "run_attempt": 1,
                    "head_sha": merged,
                    "head_branch": "main",
                    "event": "push",
                    "head_repository": {"id": 1, "full_name": "example/project"},
                    "updated_at": published,
                }
            ]
        }
        cache.metadata["/repos/example/project/actions/runs/100/artifacts"] = {
            "artifacts": [
                {
                    "id": 10000,
                    "name": "nx-cache-promotion-" + merged,
                    "expired": False,
                    "workflow_run": {
                        "id": 100,
                        "head_sha": merged,
                        "head_repository_id": 1,
                    },
                }
            ]
        }
        cache.metadata["/repos/example/project/actions/artifacts/10000/zip"] = (
            archive_report(promoted / "nx-cache-promotion")
        )
        yield workspace, cache, root, executions, (tested_base, landed_base, merged)
    finally:
        cache.close()


def measure_moved(history, tmp_path):
    workspace, cache, *_ = history
    return measure_cache_use((workspace, cache, None, None), tmp_path)


def rewrite_execution(cache, artifact, change):
    key = f"/repos/example/project/actions/artifacts/{artifact}/zip"
    with zipfile.ZipFile(io.BytesIO(cache.metadata[key])) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    doc = json.loads(files["executions.json"])
    change(doc)
    files["executions.json"] = json.dumps(doc).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    cache.metadata[key] = output.getvalue()


def as_schema_one(doc):
    doc["schema"] = 1
    del doc["base_sha"]
    for task in doc["tasks"]:
        del task["remote_read"]


def test_a_merge_ref_run_records_the_base_it_tested(moved_base_history):
    *_, executions, (tested_base, _, _) = moved_base_history
    assert [doc["base_sha"] for doc in executions] == [tested_base, tested_base]


def test_a_moved_base_counts_a_release_execution_as_a_base_moved_miss(
    moved_base_history, tmp_path
):
    # The retry replayed; the release pull request re-ran what the run proved on another base.
    *_, (tested_base, landed_base, _) = moved_base_history
    done, result = measure_moved(moved_base_history, tmp_path)
    assert result["actual"] == 50, result
    assert "aggregate: 1/2 remote hits (50%)" in result["detail"]
    assert "release PR: 0/1" in result["detail"]
    assert ": base_moved, #1 tested " in result["detail"]
    assert f"on base {tested_base}, merged on {landed_base}" in result["detail"]
    assert done.returncode != 0  # 50 is below the budget's minimum, and no error.
    assert result["verdict"] != "error"


def test_a_changed_hash_on_an_unmoved_base_stays_excluded(moved_base_history, tmp_path):
    _, cache, _, _, (tested_base, _, merged) = moved_base_history
    original = copy.deepcopy(cache.metadata)
    try:
        cache.metadata[f"/repos/example/project/commits/{merged}"]["parents"] = [
            {"sha": tested_base}
        ]
        done, result = measure_moved(moved_base_history, tmp_path)
        assert done.returncode == 0, (result, done.stderr)
        assert "aggregate: 1/1 remote hits (100%)" in result["detail"]
        assert "base_moved" not in result["detail"]
        assert "release PR: 0/0" in result["detail"]
    finally:
        cache.metadata = original


def test_schema_one_evidence_reads_with_its_base_unknown_and_counted(
    moved_base_history, tmp_path
):
    _, cache, *_ = moved_base_history
    original = copy.deepcopy(cache.metadata)
    try:
        # Both of the carried run's attempts, as an earlier script wrote them.
        rewrite_execution(cache, 101, as_schema_one)
        rewrite_execution(cache, 102, as_schema_one)
        done, result = measure_moved(moved_base_history, tmp_path)
        assert done.returncode == 0, (result, done.stderr)
        assert "aggregate: 1/1 remote hits (100%)" in result["detail"]
        assert "base_moved" not in result["detail"]
        assert "carried runs with an unknown base, excluded: 1" in result["detail"]
    finally:
        cache.metadata = original


def test_an_earlier_incomplete_schema_two_document_still_needs_cancellation(
    moved_base_history, tmp_path
):
    # Before base_sha, schema 2 meant incomplete alone, with or without a named invocation.
    _, cache, *_ = moved_base_history
    original = copy.deepcopy(cache.metadata)

    def earlier_schema_two(doc):
        del doc["base_sha"]
        for task in doc["tasks"]:
            del task["remote_read"]

    try:
        rewrite_execution(cache, 102, earlier_schema_two)
        done, result = measure_moved(moved_base_history, tmp_path)
        assert done.returncode != 0
        assert result["actual"] is None
        assert "telemetry is incomplete" in result["error"]
    finally:
        cache.metadata = original


def test_a_base_sha_that_is_not_a_commit_is_refused(moved_base_history, tmp_path):
    _, cache, *_ = moved_base_history
    original = copy.deepcopy(cache.metadata)
    try:
        rewrite_execution(cache, 102, lambda doc: doc.update(base_sha="not-a-sha"))
        done, result = measure_moved(moved_base_history, tmp_path)
        assert done.returncode != 0
        assert result["actual"] is None
        assert "invalid base_sha" in result["error"]
    finally:
        cache.metadata = original


def test_a_whole_workflow_rerun_is_reported_unavailable_with_a_figure_from_the_rest(
    telemetry_history, tmp_path
):
    # GitHub lists only the last attempt's artifacts once every job has run again.
    _, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    listing = "/repos/example/project/actions/runs/1/artifacts"
    try:
        cache.metadata[listing]["artifacts"] = [
            a for a in cache.metadata[listing]["artifacts"] if a["id"] != 101
        ]
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode == 0, (result, done.stderr)
        assert (
            "unavailable: run 1/1 job test-linux: a whole-workflow re-run removed"
            in result["detail"]
        )
        assert "unavailable artifacts: 1;" in result["detail"]
        assert "aggregate: 1/1 remote hits (100%)" in result["detail"]
    finally:
        cache.metadata = original


def test_a_rerun_removing_the_attempt_a_receipt_names_still_measures(
    telemetry_history, tmp_path
):
    # The release pull request's replay is what is left, and the receipt says what was written.
    _, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    listing = "/repos/example/project/actions/runs/1/artifacts"
    key = "/repos/example/project/actions/artifacts/10000/zip"
    try:
        with zipfile.ZipFile(io.BytesIO(cache.metadata[key])) as source:
            receipt = json.loads(source.read("receipt.json"))
        receipt["source_attempt"] = 1
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as target:
            target.writestr("receipt.json", json.dumps(receipt))
        cache.metadata[key] = output.getvalue()
        cache.metadata[listing]["artifacts"] = [
            a for a in cache.metadata[listing]["artifacts"] if a["id"] != 101
        ]
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode == 0, (result, done.stderr)
        assert "unavailable artifacts: 1;" in result["detail"]
    finally:
        cache.metadata = original


def carry_pull_request(workspace, cache, number, run_created_at):
    """A real merge commit changing a published path, carried by the release."""

    def git(*args):
        done = command(["git", *args], workspace.root)
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    first = git("rev-list", "--max-parents=0", "HEAD").splitlines()[-1]
    tree = git("rev-parse", "HEAD~1^{tree}")
    merge = git(
        "-c",
        "user.name=journey",
        "-c",
        "user.email=journey@example.com",
        "commit-tree",
        tree,
        "-p",
        first,
        "-m",
        f"fix: an old change (#{number})",
    )
    repository = {"id": 1, "full_name": "example/project"}
    head = "b" * 40
    pr = {
        "number": number,
        "head": {"sha": head, "ref": f"change-{number}", "repo": repository},
        "base": {"sha": first, "ref": "main", "repo": repository},
        "user": {"login": "maintainer"},
        "merged_at": run_created_at or "2000-01-01T00:00:00Z",
        "merge_commit_sha": merge,
    }
    cache.metadata[f"/repos/example/project/commits/{merge}/pulls"] = [pr]
    compare = cache.metadata["/repos/example/project/compare/v1.0.0...v1.0.1"]
    compare["commits"].insert(0, {"sha": merge})
    if run_created_at is None:
        return pr
    run = {
        "id": number,
        "run_attempt": 1,
        "head_sha": head,
        "head_branch": f"change-{number}",
        "event": "pull_request",
        "head_repository": repository,
        "pull_requests": [
            {
                "number": number,
                "head": {"sha": head, "ref": f"change-{number}"},
                "base": {"sha": first, "ref": "main"},
            }
        ],
        "status": "completed",
        "conclusion": "success",
        "created_at": run_created_at,
        "run_started_at": run_created_at,
        "updated_at": run_created_at,
    }
    listing = cache.metadata["/repos/example/project/actions/workflows/ci.yml/runs"]
    listing["workflow_runs"].append(run)
    prefix = f"/repos/example/project/actions/runs/{number}"
    cache.metadata[f"{prefix}/attempts/1"] = run
    cache.metadata[f"{prefix}/attempts/1/jobs"] = {
        "jobs": [
            {
                "name": "test-linux",
                "conclusion": "success",
                "labels": ["ubuntu-latest"],
            }
        ]
    }
    cache.metadata[f"{prefix}/artifacts"] = {"artifacts": []}
    return pr


def test_a_pull_request_predating_adoption_is_reported_with_a_figure_from_the_rest(
    telemetry_history, tmp_path
):
    workspace, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    try:
        carry_pull_request(workspace, cache, 3, "2000-01-01T00:00:00Z")
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode == 0, (result, done.stderr)
        assert "aggregate: 2/2 remote hits (100%)" in result["detail"]
        assert "unavailable: run 3/1 predates" in result["detail"]
        assert "unavailable artifacts: 1;" in result["detail"]
    finally:
        cache.metadata = original


def test_a_pull_request_merged_before_adoption_with_no_run_left_is_reported(
    telemetry_history, tmp_path
):
    workspace, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    try:
        carry_pull_request(workspace, cache, 3, None)
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode == 0, (result, done.stderr)
        assert "unavailable: #3 merged before" in result["detail"]
    finally:
        cache.metadata = original


def test_a_run_after_adoption_with_no_artifact_still_errors(
    telemetry_history, tmp_path
):
    # Telemetry was on and the artifact is gone: that is what the figure must not pass over.
    workspace, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    try:
        # The release commit's time: after adoption, and before the release's publication.
        release_commit = command(
            ["git", "log", "-1", "--format=%cI", "HEAD"], workspace.root
        ).stdout.strip()
        when = datetime.fromisoformat(release_commit).astimezone(timezone.utc)
        carry_pull_request(workspace, cache, 3, when.isoformat().replace("+00:00", "Z"))
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode != 0
        assert result["actual"] is None
        assert "missing artifact" in result["error"]
    finally:
        cache.metadata = original


def test_a_superseded_attempts_invalid_artifact_still_errors(
    telemetry_history, tmp_path
):
    _, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    try:
        cache.metadata["/repos/example/project/actions/artifacts/101/zip"] = (
            b"not a zip archive, though long enough to read an end record from"
        )
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode != 0
        assert result["actual"] is None
        assert "not a ZIP" in result["error"]
    finally:
        cache.metadata = original


def test_nothing_measurable_still_reports_missing(telemetry_history, tmp_path):
    # Every run predates adoption and uploaded nothing: an empty figure is no pass.
    _, cache, _, _ = telemetry_history
    original = copy.deepcopy(cache.metadata)
    prefix = "/repos/example/project/actions"
    try:
        for run_id, attempts in ((1, (1, 2)), (2, (1, 2))):
            cache.metadata[f"{prefix}/runs/{run_id}/artifacts"] = {"artifacts": []}
            for attempt in attempts:
                run = cache.metadata[f"{prefix}/runs/{run_id}/attempts/{attempt}"]
                run["created_at"] = "2000-01-01T00:00:00Z"
        cache.metadata[f"{prefix}/runs/100/artifacts"] = {"artifacts": []}
        done, result = measure_cache_use(telemetry_history, tmp_path)
        assert done.returncode != 0
        assert result["actual"] is None
        assert "zero eligible executions" in result["error"]
    finally:
        cache.metadata = original
