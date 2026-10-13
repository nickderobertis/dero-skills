"""Exercise the actual prepare command, Husky and Git against a local remote."""

import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def run(repo, *args, env=None, check=True):
    return subprocess.run(
        args, cwd=repo, env=env, text=True, capture_output=True, check=check
    )


def test_prepare_and_push_journeys(tmp_path):
    repo = tmp_path / "clone"
    repo.mkdir()
    remote = tmp_path / "remote.git"
    run(tmp_path, "git", "init", "--bare", str(remote))
    run(repo, "git", "init", "-b", "main")
    run(repo, "git", "config", "user.name", "Hook test")
    run(repo, "git", "config", "user.email", "hooks@example.invalid")
    run(repo, "git", "remote", "add", "origin", str(remote))
    for name in ("package.json", "commitlint.config.js"):
        shutil.copy2(ROOT / name, repo / name)
    shutil.copytree(
        ROOT / ".husky", repo / ".husky", ignore=shutil.ignore_patterns("_")
    )
    (repo / "tools").mkdir()
    shutil.copy2(ROOT / "tools/setup-hooks.mjs", repo / "tools/setup-hooks.mjs")
    (repo / "node_modules").symlink_to(ROOT / "node_modules", target_is_directory=True)
    env = os.environ.copy()
    env.pop("HUSKY", None)
    trace = tmp_path / "push-trace.json"
    env["GIT_TRACE2_EVENT"] = str(trace)
    run(repo, "bun", "run", "prepare", env=env)
    assert run(repo, "git", "config", "core.hooksPath").stdout.strip() == ".husky/_"
    assert not (repo / ".husky/_/pre-push").exists()
    assert (repo / ".husky/_/commit-msg").is_file()
    dispatchers = {
        p.name: p.read_bytes() for p in (repo / ".husky/_").iterdir() if p.is_file()
    }
    # Commitlint still executes through the real Husky commit-msg dispatcher.
    run(repo, "git", "add", ".")
    rejected = run(repo, "git", "commit", "-m", "invalid subject", env=env, check=False)
    assert rejected.returncode != 0
    assert "subject-empty" in rejected.stdout + rejected.stderr
    run(repo, "git", "commit", "-m", "fix: initial fixture", env=env)
    artifacts = repo / "visuals"
    artifacts.mkdir()
    (artifacts / "baseline.png").write_bytes(b"unchanged baseline")
    (artifacts / "gallery.html").write_text("unchanged gallery")
    before = {p.name: p.read_bytes() for p in artifacts.iterdir()}

    def push():
        trace.write_text("")
        run(repo, "git", "push", "origin", "main", env=env)
        expected = run(repo, "git", "rev-parse", "HEAD").stdout.strip()
        actual = run(tmp_path, "git", "--git-dir", str(remote), "rev-parse", "main")
        assert actual.stdout.strip() == expected
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        children = [e for e in events if e.get("event") == "child_start"]
        assert children  # Trace observes the real receive-pack subprocess.
        assert all(e.get("child_class") != "hook" for e in children)
        assert all(
            not any(
                word in " ".join(e.get("argv", []))
                for word in ("just", "llmlint", "capture")
            )
            for e in children
        )
        assert {p.name: p.read_bytes() for p in artifacts.iterdir()} == before

    push()
    (repo / "llmlint.yml").write_text("# guarded path changed\n")
    run(repo, "git", "add", "llmlint.yml")
    run(repo, "git", "commit", "-m", "fix: change guarded path", env=env)
    push()
    push()  # Up-to-date push also executes no hook.
    # Reproduce an existing clone's old Husky-generated dispatcher, then rerun.
    run(repo, "node", "node_modules/husky/bin.js", env=env)
    assert (repo / ".husky/_/pre-push").exists()
    run(repo, "bun", "run", "prepare", env=env)
    assert not (repo / ".husky/_/pre-push").exists()
    assert {
        p.name: p.read_bytes() for p in (repo / ".husky/_").iterdir() if p.is_file()
    } == dispatchers
    push()


def test_prepare_respects_disabled_husky(tmp_path):
    run(tmp_path, "git", "init")
    run(tmp_path, "git", "config", "core.hooksPath", "shared-hooks")
    (tmp_path / "tools").mkdir()
    shutil.copy2(ROOT / "tools/setup-hooks.mjs", tmp_path / "tools/setup-hooks.mjs")
    shutil.copy2(ROOT / "package.json", tmp_path / "package.json")
    (tmp_path / "node_modules").symlink_to(
        ROOT / "node_modules", target_is_directory=True
    )
    env = {**os.environ, "HUSKY": "0"}
    run(tmp_path, "bun", "run", "prepare", env=env)
    assert (
        run(tmp_path, "git", "config", "core.hooksPath").stdout.strip()
        == "shared-hooks"
    )
    assert not (tmp_path / ".husky").exists()


def test_prepare_outside_git_reports_failure_and_recovers(tmp_path):
    (tmp_path / "tools").mkdir()
    shutil.copy2(ROOT / "tools/setup-hooks.mjs", tmp_path / "tools/setup-hooks.mjs")
    shutil.copy2(ROOT / "package.json", tmp_path / "package.json")
    (tmp_path / "node_modules").symlink_to(
        ROOT / "node_modules", target_is_directory=True
    )
    env = os.environ.copy()
    env.pop("HUSKY", None)
    failed = run(tmp_path, "bun", "run", "prepare", env=env, check=False)
    assert failed.returncode != 0
    assert "run hook setup from a Git checkout" in failed.stderr
    assert not (tmp_path / ".husky").exists()
    run(tmp_path, "git", "init")
    run(tmp_path, "bun", "run", "prepare", env=env)
    assert run(tmp_path, "git", "config", "core.hooksPath").stdout.strip() == ".husky/_"
    assert not (tmp_path / ".husky/_/pre-push").exists()
    assert (tmp_path / ".husky/_/commit-msg").is_file()
