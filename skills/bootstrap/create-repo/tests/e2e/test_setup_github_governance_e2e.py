"""End-to-end tests for the GitHub governance setup script.

Runs the real PEP 723 script the way the skill documents — as a subprocess via
``uv run --script`` — so argument parsing and the offline ``--dry-run`` path
resolve end to end. An offline ``--dry-run`` (with ``--repo``/``--branch``)
needs no ``gh`` binary, network, or auth. Apply and ``--verify`` runs put a
fake ``gh`` on PATH instead — the only boundary doubled — to prove the
visibility rule: fork-PR approval is applied and verified on a public
repository and skipped as not applicable on a private or internal one.

The in-process unit layer (functions exercised directly with a fake ``gh``
runner) lives beside this file's sibling, ``../test_setup_github_governance.py``.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import NamedTuple, NotRequired, TypedDict

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_github_governance.py"


def _run_script(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the governance script the way the skill documents: `uv run
    --script`, so the PEP 723 script resolves and parses args end to end. An
    offline `--dry-run` (with --repo/--branch) needs no `gh`, network, or auth."""
    return subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def test_e2e_dry_run_plans_all_three_mutations_including_fork_approval():
    result = _run_script(
        "check",
        "commitlint",
        "llmlint",
        "--repo",
        "acme/w",
        "--branch",
        "main",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "PATCH repos/acme/w" in out
    assert "PUT repos/acme/w/branches/main/protection" in out
    assert "PUT repos/acme/w/actions/permissions/fork-pr-contributor-approval" in out
    # The default policy is rendered in the body the call would send.
    assert '"approval_policy": "all_external_contributors"' in out


def test_e2e_dry_run_honors_fork_pr_approval_flag():
    result = _run_script(
        "check",
        "llmlint",
        "--repo",
        "acme/w",
        "--branch",
        "main",
        "--fork-pr-approval",
        "first_time_contributors",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    assert '"approval_policy": "first_time_contributors"' in result.stdout


def test_e2e_rejects_unknown_fork_pr_approval_policy():
    result = _run_script(
        "check", "--repo", "a/b", "--branch", "main", "--fork-pr-approval", "bogus"
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_e2e_refuses_governance_without_llmlint_required_check():
    # The mandated blocking PR check: without an `llmlint` context the script
    # refuses (even in offline --dry-run), naming the fix and the escape hatch.
    result = _run_script(
        "check", "commitlint", "--repo", "acme/w", "--branch", "main", "--dry-run"
    )
    assert result.returncode == 1
    assert "llmlint" in result.stderr
    assert "--allow-missing-llmlint" in result.stderr


# --- visibility: fork-PR approval exists on public repositories only ---------
#
# Apply and --verify talk to GitHub, so these runs put a fake `gh` first on PATH:
# the one boundary doubled. It answers the reads from a JSON state file, records
# every request (args + the JSON body piped to `--input -`), and refuses the
# fork-PR approval endpoint on a private or internal repository exactly as GitHub
# does, so a script that still called it would fail here as it did in the field.

CHECKS = ("check", "commitlint", "llmlint")
FORK_PATH = "repos/acme/w/actions/permissions/fork-pr-contributor-approval"
FORK_REFUSAL = "Fork PR approval is not allowed for private repositories (HTTP 422)"

FAKE_GH = """\
import json, os, sys

state_dir = os.environ["FAKE_GH_DIR"]
with open(os.path.join(state_dir, "state.json")) as fh:
    state = json.load(fh)
args = sys.argv[1:]
body = sys.stdin.read() if "--input" in args else None
with open(os.path.join(state_dir, "calls.jsonl"), "a") as fh:
    fh.write(json.dumps({"args": args, "body": body}) + "\\n")

if args[:1] == ["repo"]:
    print("acme/w")
    sys.exit(0)
if args[:1] != ["api"]:
    sys.exit(f"fake gh: unexpected command {args}")
if "--jq" in args:
    print("main")
    sys.exit(0)
method = args[args.index("--method") + 1] if "--method" in args else "GET"
path = next(a for a in args[1:] if a.startswith("repos/"))
if path in state.get("fail", {}):
    sys.exit(state["fail"][path])
if path.endswith("/fork-pr-contributor-approval") and state["visibility"] != "public":
    sys.exit(FORK_REFUSAL)
if method != "GET":
    print("{}")
elif path.endswith("/protection"):
    print(json.dumps(state["protection"]))
elif path.endswith("/fork-pr-contributor-approval"):
    print(json.dumps({"approval_policy": state["fork_policy"]}))
else:
    print(json.dumps({**state["settings"], **state.get("visibility_response", {"visibility": state["visibility"]})}))
""".replace("FORK_REFUSAL", repr(FORK_REFUSAL))


class RepositoryState(TypedDict):
    visibility: str
    settings: dict[str, object]
    protection: dict[str, object]
    fork_policy: str
    fail: NotRequired[dict[str, str]]
    visibility_response: NotRequired[dict[str, object]]


class RecordedCall(TypedDict):
    args: list[str]
    body: str | None


class ApiRequest(NamedTuple):
    method: str
    path: str


def _conformant_state(visibility: str) -> RepositoryState:
    """GitHub's answers for a repo that already matches the desired governance."""
    return {
        "visibility": visibility,
        "settings": {
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
            "allow_auto_merge": True,
            "delete_branch_on_merge": True,
            "squash_merge_commit_title": "PR_TITLE",
            "squash_merge_commit_message": "PR_BODY",
        },
        # The protection GET's shape: booleans wrapped as {"enabled": ...}.
        "protection": {
            "required_status_checks": {"strict": False, "contexts": list(CHECKS)},
            "enforce_admins": {"enabled": False},
            "required_pull_request_reviews": {"required_approving_review_count": 0},
            "required_linear_history": {"enabled": True},
            "required_conversation_resolution": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        },
        "fork_policy": "all_external_contributors",
    }


def _run_with_fake_gh(
    tmp_path: Path, state: RepositoryState, *extra: str, online_dry_run: bool = False
):
    """Run the real script against the fake `gh`; return (result, requests)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    # llmlint: ignore[e2e_not_mocked] Issue #117 requires offline CLI journeys with only gh doubled at its executable boundary; the governance script, argument parsing and subprocess calls run for real.
    gh.write_text("#!/usr/bin/env -S uv run --script\n" + FAKE_GH, encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_GH_DIR": str(tmp_path),
    }
    result = subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), *CHECKS]
        + (
            [*extra]
            if online_dry_run
            else ["--repo", "acme/w", "--branch", "main", *extra]
        ),
        capture_output=True,
        text=True,
        env=env,
    )
    log = tmp_path / "calls.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, [json.loads(line) for line in lines]


def _requests(calls: list[RecordedCall]) -> list[ApiRequest]:
    """Each recorded `gh api` request as (method, path)."""
    out = []
    for call in calls:
        args = call["args"]
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        out.append(ApiRequest(method, next(a for a in args if a.startswith("repos/"))))
    return out


def test_e2e_apply_public_sets_fork_approval(tmp_path):
    result, calls = _run_with_fake_gh(tmp_path, _conformant_state("public"))
    assert result.returncode == 0, result.stderr
    assert _requests(calls) == [
        ("GET", "repos/acme/w"),
        ("PATCH", "repos/acme/w"),
        ("PUT", "repos/acme/w/branches/main/protection"),
        ("PUT", FORK_PATH),
    ]
    assert json.loads(calls[-1]["body"]) == {
        "approval_policy": "all_external_contributors"
    }
    assert result.stdout.startswith("OK ")
    assert "fork-PR approval: all_external_contributors" in result.stdout


@pytest.mark.parametrize("visibility", ["private", "internal"])
def test_e2e_apply_not_public_skips_fork_approval(tmp_path, visibility):
    result, calls = _run_with_fake_gh(tmp_path, _conformant_state(visibility))
    assert result.returncode == 0, result.stderr
    assert "ERROR" not in result.stderr
    assert _requests(calls) == [
        ("GET", "repos/acme/w"),
        ("PATCH", "repos/acme/w"),
        ("PUT", "repos/acme/w/branches/main/protection"),
    ]
    assert result.stdout.startswith("OK ")
    assert f"fork-PR approval: not applicable ({visibility})" in result.stdout


def test_e2e_verify_public_reads_fork_approval(tmp_path):
    result, calls = _run_with_fake_gh(tmp_path, _conformant_state("public"), "--verify")
    assert result.returncode == 0, result.stderr
    assert _requests(calls) == [
        ("GET", "repos/acme/w"),
        ("GET", "repos/acme/w/branches/main/protection"),
        ("GET", FORK_PATH),
    ]
    assert "governance matches" in result.stdout
    assert "fork-PR approval: all_external_contributors" in result.stdout


def test_e2e_verify_public_still_refuses_fork_policy_drift(tmp_path):
    state = _conformant_state("public")
    state["fork_policy"] = "first_time_contributors"
    result, _ = _run_with_fake_gh(tmp_path, state, "--verify")
    assert result.returncode == 1
    assert "DRIFT fork-PR approval policy" in result.stderr


@pytest.mark.parametrize("visibility", ["private", "internal"])
def test_e2e_verify_not_public_passes_without_fork_read(tmp_path, visibility):
    result, calls = _run_with_fake_gh(
        tmp_path, _conformant_state(visibility), "--verify"
    )
    assert result.returncode == 0, result.stderr
    assert _requests(calls) == [
        ("GET", "repos/acme/w"),
        ("GET", "repos/acme/w/branches/main/protection"),
    ]
    assert result.stdout.startswith("OK ")
    assert "governance matches" in result.stdout
    assert f"fork-PR approval: not applicable ({visibility})" in result.stdout


def test_e2e_verify_private_refuses_another_mismatch(tmp_path):
    state = _conformant_state("private")
    state["settings"]["allow_merge_commit"] = True
    result, calls = _run_with_fake_gh(tmp_path, state, "--verify")
    assert result.returncode == 1
    assert "DRIFT merge model: allow_merge_commit" in result.stderr
    assert "FAIL" in result.stderr
    assert ("GET", FORK_PATH) not in _requests(calls)


def test_e2e_verify_private_refuses_failed_read(tmp_path):
    state = _conformant_state("private")
    state["fail"] = {"repos/acme/w/branches/main/protection": "HTTP 403: Forbidden"}
    result, _ = _run_with_fake_gh(tmp_path, state, "--verify")
    assert result.returncode == 1
    assert "ERROR" in result.stderr and "403" in result.stderr
    assert "OK" not in result.stdout


@pytest.mark.parametrize("visibility", ["private", "internal"])
def test_e2e_offline_dry_run_visibility_omits_fork_approval(visibility):
    result = _run_script(
        *CHECKS,
        "--repo",
        "acme/w",
        "--branch",
        "main",
        "--dry-run",
        "--visibility",
        visibility,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "PATCH repos/acme/w" in out
    assert "PUT repos/acme/w/branches/main/protection" in out
    assert "fork-pr-contributor-approval" not in out
    assert f"fork-PR approval: not applicable ({visibility})" in out


def test_e2e_verify_refuses_visibility_flag_disagreeing_with_live(tmp_path):
    result, calls = _run_with_fake_gh(
        tmp_path,
        _conformant_state("private"),
        "--verify",
        "--visibility",
        "public",
    )
    assert result.returncode == 1
    assert "--visibility public but the repository is private" in result.stderr
    assert "OK" not in result.stdout
    assert _requests(calls) == [
        ("GET", "repos/acme/w"),
        ("GET", "repos/acme/w/branches/main/protection"),
    ]


@pytest.mark.parametrize(
    ("response", "visibility"),
    [
        ({"visibility": "INTERNAL"}, "internal"),
        ({"private": True}, "private"),
        ({"private": False}, "public"),
    ],
)
def test_e2e_apply_accepts_visibility_response_forms(tmp_path, response, visibility):
    state = _conformant_state(visibility)
    state["visibility_response"] = response
    result, calls = _run_with_fake_gh(tmp_path, state)
    assert result.returncode == 0, result.stderr
    assert (("PUT", FORK_PATH) in _requests(calls)) == (visibility == "public")
    assert "OK" in result.stdout
    label = (
        "all_external_contributors"
        if visibility == "public"
        else f"not applicable ({visibility})"
    )
    assert f"fork-PR approval: {label}" in result.stdout


@pytest.mark.parametrize("response", [{}, {"visibility": "unknown"}])
def test_e2e_apply_refuses_unreadable_visibility(tmp_path, response):
    state = _conformant_state("private")
    state["visibility_response"] = response
    result, calls = _run_with_fake_gh(tmp_path, state)
    assert result.returncode == 1
    assert "could not read the repository's visibility" in result.stderr
    assert "fix:" in result.stderr
    assert "OK" not in result.stdout
    assert _requests(calls) == [("GET", "repos/acme/w")]


def test_e2e_apply_refuses_failed_visibility_read(tmp_path):
    state = _conformant_state("private")
    state["fail"] = {"repos/acme/w": "HTTP 403: Forbidden"}
    result, calls = _run_with_fake_gh(tmp_path, state)
    assert result.returncode == 1
    assert "403" in result.stderr and "fix:" in result.stderr
    assert "OK" not in result.stdout
    assert _requests(calls) == [("GET", "repos/acme/w")]


def test_e2e_apply_refuses_visibility_flag_disagreeing_with_live(tmp_path):
    result, calls = _run_with_fake_gh(
        tmp_path, _conformant_state("private"), "--visibility", "public"
    )
    assert result.returncode == 1
    assert "--visibility public but the repository is private" in result.stderr
    assert "OK" not in result.stdout
    assert _requests(calls) == [("GET", "repos/acme/w")]


@pytest.mark.parametrize("visibility", ["public", "private", "internal"])
def test_e2e_online_dry_run_reads_visibility_without_mutating(tmp_path, visibility):
    result, calls = _run_with_fake_gh(
        tmp_path, _conformant_state(visibility), "--dry-run", online_dry_run=True
    )
    assert result.returncode == 0, result.stderr
    api_calls = [call for call in calls if call["args"][0] == "api"]
    assert _requests(api_calls) == [("GET", "repos/acme/w")]
    assert "DRY-RUN" in result.stdout
    assert "PATCH repos/acme/w" in result.stdout
    assert (FORK_PATH in result.stdout) == (visibility == "public")
    if visibility != "public":
        assert f"not applicable ({visibility})" in result.stdout


def test_e2e_online_dry_run_refuses_disagreeing_visibility(tmp_path):
    result, calls = _run_with_fake_gh(
        tmp_path,
        _conformant_state("private"),
        "--dry-run",
        "--visibility",
        "public",
        online_dry_run=True,
    )
    assert result.returncode == 1
    assert "--visibility public but the repository is private" in result.stderr
    assert "DRY-RUN" not in result.stdout
    api_calls = [call for call in calls if call["args"][0] == "api"]
    assert _requests(api_calls) == [("GET", "repos/acme/w")]
