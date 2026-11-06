"""The additive required-check mode, driven through the real CLI.

``uv run --script`` runs the real script; the one thing doubled is GitHub, by a
stateful ``gh`` on PATH that keeps the protection and rulesets in a JSON file and
applies each write the way the required-status-checks subresource does: a POST
appends its contexts and leaves everything else alone. Assertions read that state
and the recorded calls, so they see what reached the host rather than what the
script says it did.
"""

from __future__ import annotations

import copy
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_github_governance.py"
REPO = "acme/w"
PROTECTION = f"repos/{REPO}/branches/main/protection"
RULES = f"repos/{REPO}/rules/branches/main"
SUBRESOURCE = f"{PROTECTION}/required_status_checks/contexts"

# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] This double stands at the gh executable boundary the mode is tested through, and the shapes it answers were read from GitHub's real protection and rules endpoints; GitHub publishes no schema this offline tier could reconcile with.
FAKE_GH = r"""
import json, os, sys

directory = os.environ["FAKE_GH_DIR"]
state_file = os.path.join(directory, "state.json")
state = json.load(open(state_file))
args = sys.argv[1:]
body = sys.stdin.read() if "--input" in args else None
method = args[args.index("--method") + 1] if "--method" in args else "GET"
path = next(a for a in args[1:] if a.startswith("repos/"))
with open(os.path.join(directory, "calls.jsonl"), "a") as log:
    log.write(json.dumps({"method": method, "path": path, "body": body}) + "\n")
if path in state.get("fail", {}):
    sys.exit(state["fail"][path])
if path.endswith("/rules/branches/main"):
    print(json.dumps(state["rules"]))
elif path.endswith("/required_status_checks/contexts") and method == "POST":
    checks = state["protection"]["required_status_checks"]
    for name in json.loads(body)["contexts"]:
        checks["contexts"].append(name)
        checks["checks"].append({"context": name, "app_id": 15368})
    if state.get("after_write"):
        state["protection"] = state["after_write"]
    json.dump(state, open(state_file, "w"))
    print(json.dumps(checks["contexts"]))
elif path.endswith("/protection") and method == "GET":
    if state["protection"] is None:
        sys.exit("gh: Branch not protected (HTTP 404)")
    print(json.dumps(state["protection"]))
else:
    sys.exit(f"fake gh: nothing answers {method} {path}")
"""


def existing_protection() -> dict:
    """Live protection with nondefault settings a full-profile PUT would reset."""
    return {
        "url": f"https://api.github.com/{PROTECTION}",
        "required_status_checks": {
            "url": f"https://api.github.com/{PROTECTION}/required_status_checks",
            "strict": True,
            "contexts": ["lint", "cross (windows-latest)", "legacy"],
            "contexts_url": f"https://api.github.com/{SUBRESOURCE}",
            "checks": [
                {"context": "lint", "app_id": 15368},
                {"context": "cross (windows-latest)", "app_id": 999},
                {"context": "legacy", "app_id": None},
            ],
        },
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": True,
            "required_approving_review_count": 2,
        },
        "enforce_admins": {"enabled": True},
        "required_linear_history": {"enabled": False},
        "required_conversation_resolution": {"enabled": False},
        "allow_force_pushes": {"enabled": True},
        "allow_deletions": {"enabled": False},
        "restrictions": {"users": [{"login": "release-bot"}], "teams": [], "apps": []},
    }


RULE = {
    "type": "required_status_checks",
    "ruleset_source_type": "Organization",
    "ruleset_source": "acme",
    "ruleset_id": 77,
    "parameters": {"required_status_checks": [{"context": "org-check"}]},
}
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


class Host:
    def __init__(self, root: Path, **state) -> None:
        self.root = root
        bin_dir = root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!/usr/bin/env -S uv run --script\n{FAKE_GH}")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_DIR": str(root),
        }
        (root / "state.json").write_text(
            json.dumps({"protection": existing_protection(), "rules": [], **state})
        )
        self.before = copy.deepcopy(self.state["protection"])

    @property
    def state(self) -> dict:
        return json.loads((self.root / "state.json").read_text())

    @property
    def calls(self) -> list[dict]:
        log = self.root / "calls.jsonl"
        lines = log.read_text().splitlines() if log.exists() else []
        return [json.loads(line) for line in lines]

    @property
    def writes(self) -> list[dict]:
        return [call for call in self.calls if call["method"] != "GET"]

    def run(self, *flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["uv", "run", "--script", str(SCRIPT), *flags],
            capture_output=True,
            text=True,
            env=self.env,
        )

    def add(self, *checks: str, **kw) -> subprocess.CompletedProcess[str]:
        flags = [flag for check in checks for flag in ("--add-required-check", check)]
        return self.run(
            *flags, "--repo", REPO, "--branch", "main", *kw.get("extra", ())
        )


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def test_a_missing_check_is_added_and_everything_else_is_preserved(host: Host):
    done = host.add("budgets")
    assert done.returncode == 0, done.stderr

    assert [(c["method"], c["path"]) for c in host.writes] == [("POST", SUBRESOURCE)]
    assert json.loads(host.writes[0]["body"]) == {"contexts": ["budgets"]}

    after = host.state["protection"]
    checks = after["required_status_checks"]
    assert checks["contexts"] == ["lint", "cross (windows-latest)", "legacy", "budgets"]
    assert checks["checks"][:3] == [
        {"context": "lint", "app_id": 15368},
        {"context": "cross (windows-latest)", "app_id": 999},
        {"context": "legacy", "app_id": None},
    ]
    assert checks["strict"] is True
    for key, value in host.before.items():
        if key != "required_status_checks":
            assert after[key] == value, key
    assert "added budgets" in done.stdout
    assert "after [budgets (app 15368)" in done.stdout


def test_a_repeated_call_writes_nothing(host: Host):
    assert host.add("budgets").returncode == 0
    state = host.state
    again = host.add("budgets")
    assert again.returncode == 0, again.stderr
    assert "already required" in again.stdout
    assert len(host.writes) == 1
    assert host.state == state


def test_only_the_missing_ones_of_several_are_sent(host: Host):
    done = host.add("lint", "budgets", "budgets", "other")
    assert done.returncode == 0, done.stderr
    assert json.loads(host.writes[0]["body"]) == {"contexts": ["budgets", "other"]}


def test_dry_run_reads_and_prints_the_one_call_without_writing(host: Host):
    done = host.add("budgets", extra=["--dry-run"])
    assert done.returncode == 0, done.stderr
    assert f"POST {SUBRESOURCE}" in done.stdout
    assert '{"contexts": ["budgets"]}' in done.stdout
    assert host.writes == []
    assert host.state["protection"] == host.before


def test_dry_run_of_a_check_already_required_plans_nothing(host: Host):
    done = host.add("lint", extra=["--dry-run"])
    assert done.returncode == 0, done.stderr
    assert "already required" in done.stdout
    assert "POST" not in done.stdout


def test_verify_only_reads_and_fails_on_a_missing_check(host: Host):
    done = host.add("budgets", extra=["--verify"])
    assert done.returncode == 1
    assert "missing ['budgets']" in done.stderr
    assert host.writes == []


def test_verify_passes_on_a_required_check_and_names_the_set(host: Host):
    done = host.add("lint", extra=["--verify"])
    assert done.returncode == 0, done.stderr
    assert "legacy" in done.stdout
    assert host.writes == []


@pytest.mark.parametrize("flag", [[], ["--dry-run"], ["--verify"]])
def test_a_failed_read_is_refused_before_any_write(tmp_path: Path, flag):
    host = Host(tmp_path, fail={PROTECTION: "gh: Server Error (HTTP 500)"})
    done = host.add("budgets", extra=flag)
    assert done.returncode == 1
    assert "HTTP 500" in done.stderr
    assert "nothing was written" in done.stderr
    assert host.writes == []


def test_unreadable_rulesets_are_refused_before_any_write(tmp_path: Path):
    host = Host(tmp_path, fail={RULES: "gh: Must have admin rights (HTTP 403)"})
    done = host.add("budgets")
    assert done.returncode == 1
    assert "HTTP 403" in done.stderr
    assert host.writes == []


@pytest.mark.parametrize("flag", [[], ["--dry-run"], ["--verify"]])
def test_a_rulesets_only_branch_is_refused_by_name(tmp_path: Path, flag):
    host = Host(tmp_path, protection=None, rules=[RULE, {**RULE, "type": "deletion"}])
    done = host.add("budgets", extra=flag)
    assert done.returncode == 1
    assert "rulesets-only" in done.stderr
    assert "ruleset 77 (Organization acme): required_status_checks, deletion" in (
        done.stderr
    )
    assert host.writes == []


def test_classic_with_an_inherited_ruleset_is_refused_as_ambiguous(tmp_path: Path):
    host = Host(tmp_path, rules=[RULE])
    done = host.add("budgets")
    assert done.returncode == 1
    assert "classic plus rulesets" in done.stderr
    assert host.writes == []


def test_an_unprotected_branch_is_refused(tmp_path: Path):
    host = Host(tmp_path, protection=None)
    done = host.add("budgets")
    assert done.returncode == 1
    assert "no classic branch protection" in done.stderr
    assert host.writes == []


def test_protection_with_no_required_checks_is_refused(tmp_path: Path):
    protection = existing_protection()
    del protection["required_status_checks"]
    host = Host(tmp_path, protection=protection)
    done = host.add("budgets")
    assert done.returncode == 1
    assert "requires no status checks" in done.stderr
    assert host.writes == []


def _after_budgets_added(change) -> dict:
    """The protection a write would leave, then `change`d the way a host might."""
    protection = existing_protection()
    required = protection["required_status_checks"]
    required["contexts"].append("budgets")
    required["checks"].append({"context": "budgets", "app_id": 15368})
    change(protection)
    return protection


def _drop_legacy(protection: dict) -> None:
    required = protection["required_status_checks"]
    required["contexts"].remove("legacy")
    required["checks"] = [c for c in required["checks"] if c["context"] != "legacy"]


def _rebind_lint(protection: dict) -> None:
    protection["required_status_checks"]["checks"][0]["app_id"] = None


def _remove_budgets(protection: dict) -> None:
    required = protection["required_status_checks"]
    required["contexts"].remove("budgets")
    required["checks"].pop()


def _add_stranger(protection: dict) -> None:
    protection["required_status_checks"]["contexts"].append("stranger")


def _reset_admins(protection: dict) -> None:
    protection["enforce_admins"] = {"enabled": False}


def _loosen_strict(protection: dict) -> None:
    protection["required_status_checks"]["strict"] = False


def _disable_checks(protection: dict) -> None:
    del protection["required_status_checks"]


@pytest.mark.parametrize(
    ("change", "finding"),
    [
        (_drop_legacy, "dropped legacy"),
        (_rebind_lint, "lint is bound to None, was 15368"),
        (_remove_budgets, "did not add budgets"),
        (_add_stranger, "unexpected new context stranger"),
        (_reset_admins, "protection setting enforce_admins changed"),
        (_loosen_strict, "protection setting required_status_checks changed"),
        (_disable_checks, "required status checks are no longer enabled"),
    ],
)
def test_anything_the_read_back_shows_moved_fails_the_operation(
    tmp_path: Path, change, finding
):
    host = Host(tmp_path, after_write=_after_budgets_added(change))
    done = host.add("budgets")
    assert done.returncode == 1
    assert finding in done.stderr
    assert "legacy" in done.stderr.split("fix:")[1], "the operator gets the prior set"


def test_a_rules_answer_that_is_no_list_is_refused_before_any_write(tmp_path: Path):
    host = Host(tmp_path, rules={"message": "unexpected"})
    done = host.add("budgets")
    assert done.returncode == 1
    assert "did not answer a list of rules" in done.stderr
    assert host.writes == []


def test_a_refused_write_fails_with_the_hosts_error(tmp_path: Path):
    host = Host(tmp_path, fail={SUBRESOURCE: "gh: Resource not accessible (HTTP 403)"})
    done = host.add("budgets")
    assert done.returncode == 1
    assert "HTTP 403" in done.stderr


def test_positional_checks_and_the_additive_mode_are_exclusive(host: Host):
    done = host.run("lint", "--add-required-check", "budgets", "--repo", REPO)
    assert done.returncode == 2
    assert "pass one or the other" in done.stderr
    assert host.calls == []


@pytest.mark.parametrize(
    ("flag", "name"),
    [
        (["--strict"], "--strict"),
        (["--enforce-admins"], "--enforce-admins"),
        (["--approvals", "1"], "--approvals"),
        (["--fork-pr-approval", "first_time_contributors"], "--fork-pr-approval"),
        (["--visibility", "private"], "--visibility"),
        (["--repo-dir", "/tmp"], "--repo-dir"),
        (["--allow-missing-llmlint"], "--allow-missing-llmlint"),
    ],
)
def test_a_profile_flag_is_refused_rather_than_ignored(host: Host, flag, name):
    done = host.add("budgets", extra=flag)
    assert done.returncode == 2
    assert name in done.stderr
    assert host.calls == []


def test_a_blank_check_is_refused(host: Host):
    done = host.add("  ")
    assert done.returncode == 1
    assert "no required status checks given" in done.stderr
    assert host.calls == []


def test_help_and_the_docstring_document_the_contract(host: Host):
    done = host.run("--help")
    assert done.returncode == 0
    assert "--add-required-check CHECK" in done.stdout
    assert "additive mode (repeatable)" in " ".join(done.stdout.split())
    source = SCRIPT.read_text(encoding="utf-8")
    assert "--add-required-check CHECK \\\n" in source


def test_the_full_profile_still_replaces_with_its_own_put(tmp_path: Path):
    """Positional mode keeps its calls: the additive mode changed nothing about it."""
    done = subprocess.run(
        [
            "uv",
            "run",
            "--script",
            str(SCRIPT),
            "lint",
            "llmlint",
            "--repo",
            REPO,
            "--branch",
            "main",
            "--dry-run",
            "--repo-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert f"PUT {PROTECTION}" in done.stdout
    assert "required_status_checks/contexts" not in done.stdout
