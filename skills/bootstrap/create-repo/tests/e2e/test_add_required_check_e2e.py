"""The additive required-check mode, driven through the real CLI and the real ``gh``.

``uv run --script`` runs the real script, which runs the real ``gh``. ``gh`` reaches a real
local HTTP server through ``HTTP_PROXY``, using its own development host, ``github.localhost``.
That server keeps the protection and rulesets and applies each write the way the
required-status-checks subresource does. It answers only the operations GitHub's description
declares, and each body it takes or gives is checked against that description, except those
a test names as malformed. Assertions read its state and recorded calls, so they see what
reached the host rather than what the script says it did.
"""

from __future__ import annotations

import copy
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

import pytest

from github_rest_contract import (
    DELETION_RULE,
    OPERATIONS,
    PROTECTION,
    REPO,
    RULE,
    RULES,
    SNAPSHOT,
    SOURCE_COMMIT,
    SUBRESOURCE,
    existing_protection,
    load_snapshot,
    operation_for,
    problems,
    rendered,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_github_governance.py"
# Moving this means the snapshot was re-derived; the external tier re-derives it from GitHub.
SNAPSHOT_SHA256 = "e6b2703d7594f6a5f7aa8bd64cb24558f805f37c03f1cd606b52b81e00b125b6"
CONTRACT = load_snapshot()
SCRUBBED = {
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "NO_PROXY",
    "no_proxy",
    "FORCE_COLOR",
    "GH_REPO",
}


class Refusal(NamedTuple):
    status: int
    message: str


class Call(NamedTuple):
    method: str
    path: str
    body: str | None


@dataclass
class HostState:
    """What the double holds and how it answers; None protection is an unprotected branch."""

    protection: dict | list | None = field(default_factory=existing_protection)
    rules: list | dict = field(default_factory=list)
    fail: dict[str, Refusal] = field(default_factory=dict)
    # What a write leaves behind, for a host that moved more than it was asked to.
    after_write: dict | None = None
    fail_after_write: dict[str, Refusal] = field(default_factory=dict)


def request_body(handler: http.server.BaseHTTPRequestHandler) -> str | None:
    """The body as sent: `gh api --input` streams it, so it arrives chunked."""
    if handler.headers.get("Transfer-Encoding", "").lower() != "chunked":
        length = int(handler.headers.get("Content-Length", 0))
        return handler.rfile.read(length).decode() if length else None
    chunks = []
    while size := int(handler.rfile.readline().strip(), 16):
        chunks.append(handler.rfile.read(size))
        handler.rfile.readline()
    handler.rfile.readline()
    return b"".join(chunks).decode()


class Host:
    """One local GitHub double, closed and audited by `opened_hosts`."""

    opened: list[Host] = []

    def __init__(
        self, root: Path, state: HostState | None = None, *, malformed: bool = False
    ) -> None:
        gh = shutil.which("gh")
        assert gh, "these journeys drive the real GitHub CLI; install gh, then re-run"
        self.state = state or HostState()
        self.before = copy.deepcopy(self.state.protection)
        self.calls: list[Call] = []
        # Where a body left GitHub's description; `malformed` says a test meant it to.
        self.problems: list[str] = []
        self.malformed = malformed
        self.lock = threading.Lock()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def answer(self) -> None:
                owner.handle(self)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = answer

            def log_message(self, format, *args) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.env = {
            **{k: v for k, v in os.environ.items() if k not in SCRUBBED},
            "GH_HOST": "github.localhost",
            "GH_TOKEN": "journey-token",
            "GH_CONFIG_DIR": str(root / "gh-config"),
            "GH_PROMPT_DISABLED": "1",
            "HTTP_PROXY": url,
            "http_proxy": url,
            # Nx forces colour on, and argparse would then split its help with escapes.
            "NO_COLOR": "1",
        }
        Host.opened.append(self)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def handle(self, handler: http.server.BaseHTTPRequestHandler) -> None:
        body = request_body(handler)
        path = handler.path.split("://", 1)[-1].split("/", 1)[1].split("?", 1)[0]
        method = handler.command
        with self.lock:
            self.calls.append(Call(method, path, body))
            if not handler.headers.get("User-Agent", "").startswith("GitHub CLI"):
                self.problems.append(f"{method} {path} did not come through gh")
            if handler.headers.get("Authorization", "") in ("", "Bearer "):
                self.problems.append(f"{method} {path} carried no credential")
            status, answer = self.respond(method, path, body)
        payload = json.dumps(answer).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def respond(self, method: str, path: str, body: str | None) -> tuple[int, object]:
        operation = operation_for(CONTRACT, method, path)
        if operation is None:
            self.problems.append(f"{method} {path} is no declared operation")
            return 404, {"message": "Not Found"}
        if path in self.state.fail:
            refusal = self.state.fail[path]
            return refusal.status, {"message": refusal.message}
        if method == "POST":
            sent = json.loads(body or "null")
            self.audit(sent, operation["requestBody"], f"{method} {path} request")
            answer = self.add_contexts(sent["contexts"])
        elif path == RULES:
            answer = self.state.rules
        elif self.state.protection is None:
            return 404, {"message": "Branch not protected"}
        else:
            answer = self.state.protection
        self.audit(answer, operation["responses"]["200"], f"{method} {path} answer")
        return 200, answer

    def add_contexts(self, contexts: list[str]) -> list[str]:
        checks = self.state.protection["required_status_checks"]
        for name in contexts:
            checks["contexts"].append(name)
            checks["checks"].append({"context": name, "app_id": 15368})
        answer = list(checks["contexts"])
        if self.state.after_write is not None:
            self.state.protection = self.state.after_write
        self.state.fail = {**self.state.fail, **self.state.fail_after_write}
        return answer

    def audit(self, value: object, schema: dict, what: str) -> None:
        found = problems(value, schema, CONTRACT["schemas"])
        self.problems += [f"{what}: {problem}" for problem in found]

    @property
    def writes(self) -> list[dict]:
        return [call for call in self.calls if call.method != "GET"]

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


@pytest.fixture(autouse=True)
def opened_hosts() -> Iterator[None]:
    """Close each double, and hold every body it saw to GitHub's description."""
    Host.opened = []
    yield
    for host in Host.opened:
        host.close()
    for host in Host.opened:
        if host.malformed:
            assert host.problems, "a body meant as malformed conforms: drop the case"
        else:
            assert host.problems == []


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def test_the_snapshot_is_the_derived_contract_for_each_call_the_mode_makes():
    assert hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest() == SNAPSHOT_SHA256
    assert SNAPSHOT.read_text(encoding="utf-8") == rendered(CONTRACT)
    assert CONTRACT["source"]["commit"] == SOURCE_COMMIT
    declared = {
        (method, path)
        for path, methods in CONTRACT["paths"].items()
        for method in methods
    }
    assert declared == set(OPERATIONS)


def test_the_mode_requires_exactly_what_githubs_description_requires():
    status_checks = CONTRACT["schemas"]["protected-branch-required-status-check"]
    assert set(status_checks["required"]) == {"contexts", "checks"}
    check = status_checks["properties"]["checks"]["items"]
    assert set(check["required"]) == {"context", "app_id"}
    assert check["properties"]["app_id"]["nullable"] is True


def test_the_description_check_itself_sees_a_body_leave_the_schema():
    schema = {"$ref": "#/components/schemas/protected-branch-required-status-check"}
    schemas = CONTRACT["schemas"]
    good = existing_protection()["required_status_checks"]
    assert problems(good, schema, schemas) == []
    assert "$.contexts is missing" in problems({"checks": []}, schema, schemas)
    assert problems({"contexts": [1], "checks": []}, schema, schemas) == [
        "$.contexts[0] is not string"
    ]


def test_a_missing_check_is_added_and_everything_else_is_preserved(host: Host):
    done = host.add("budgets")
    assert done.returncode == 0, done.stderr

    assert [(c.method, c.path) for c in host.writes] == [("POST", SUBRESOURCE)]
    assert json.loads(host.writes[0].body) == {"contexts": ["budgets"]}

    after = host.state.protection
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
    state = copy.deepcopy(host.state)
    again = host.add("budgets")
    assert again.returncode == 0, again.stderr
    assert "already required" in again.stdout
    assert len(host.writes) == 1
    assert host.state == state


def test_only_the_missing_ones_of_several_are_sent(host: Host):
    done = host.add("lint", "budgets", "budgets", "other")
    assert done.returncode == 0, done.stderr
    assert json.loads(host.writes[0].body) == {"contexts": ["budgets", "other"]}


def test_dry_run_reads_and_prints_the_one_call_without_writing(host: Host):
    done = host.add("budgets", extra=["--dry-run"])
    assert done.returncode == 0, done.stderr
    assert f"POST {SUBRESOURCE}" in done.stdout
    assert '{"contexts": ["budgets"]}' in done.stdout
    assert host.writes == []
    assert host.state.protection == host.before


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
    host = Host(tmp_path, HostState(fail={PROTECTION: Refusal(500, "Server Error")}))
    done = host.add("budgets", extra=flag)
    assert done.returncode == 1
    assert "HTTP 500" in done.stderr
    assert "nothing was written" in done.stderr
    assert host.writes == []


def test_unreadable_rulesets_are_refused_before_any_write(tmp_path: Path):
    host = Host(
        tmp_path, HostState(fail={RULES: Refusal(403, "Must have admin rights")})
    )
    done = host.add("budgets")
    assert done.returncode == 1
    assert "HTTP 403" in done.stderr
    assert host.writes == []


@pytest.mark.parametrize("flag", [[], ["--dry-run"], ["--verify"]])
def test_a_rulesets_only_branch_is_refused_by_name(tmp_path: Path, flag):
    host = Host(tmp_path, HostState(protection=None, rules=[RULE, DELETION_RULE]))
    done = host.add("budgets", extra=flag)
    assert done.returncode == 1
    assert "rulesets-only" in done.stderr
    assert "ruleset 77 (Organization acme): required_status_checks, deletion" in (
        done.stderr
    )
    assert host.writes == []


def test_classic_with_an_inherited_ruleset_is_refused_as_ambiguous(tmp_path: Path):
    host = Host(tmp_path, HostState(rules=[RULE]))
    done = host.add("budgets")
    assert done.returncode == 1
    assert "classic plus rulesets" in done.stderr
    assert host.writes == []


def test_an_unprotected_branch_is_refused(tmp_path: Path):
    host = Host(tmp_path, HostState(protection=None))
    done = host.add("budgets")
    assert done.returncode == 1
    assert "no classic branch protection" in done.stderr
    assert host.writes == []


def test_protection_with_no_required_checks_is_refused(tmp_path: Path):
    protection = existing_protection()
    del protection["required_status_checks"]
    host = Host(tmp_path, HostState(protection=protection))
    done = host.add("budgets")
    assert done.returncode == 1
    assert "requires no status checks" in done.stderr
    assert host.writes == []


MALFORMED_CHECKS = {
    "no object": ["lint"],
    "no contexts": {"strict": True, "checks": [{"context": "lint", "app_id": 1}]},
    "no checks": {"strict": True, "contexts": ["lint"]},
    "a context that is no name": {"contexts": ["lint", 7], "checks": []},
    "a check that is no object": {"contexts": ["lint"], "checks": ["lint"]},
    "a check without a context": {"contexts": ["lint"], "checks": [{"app_id": 1}]},
    "a check without an app id": {
        "contexts": ["lint"],
        "checks": [{"context": "lint"}],
    },
    "an app id that is no id": {
        "contexts": ["lint"],
        "checks": [{"context": "lint", "app_id": "15368"}],
    },
    "an app id that is a flag": {
        "contexts": ["lint"],
        "checks": [{"context": "lint", "app_id": True}],
    },
}


@pytest.mark.parametrize("flag", [None, "--dry-run", "--verify"])
@pytest.mark.parametrize("malformed", sorted(MALFORMED_CHECKS))
def test_required_checks_it_cannot_read_are_refused_before_any_write(
    tmp_path: Path, malformed, flag
):
    protection = existing_protection()
    protection["required_status_checks"] = MALFORMED_CHECKS[malformed]
    host = Host(tmp_path, HostState(protection=protection), malformed=True)
    done = host.add("budgets", extra=[flag] if flag else [])
    assert done.returncode == 1
    assert "required_status_checks" in done.stderr
    assert "nothing was written" in done.stderr
    assert "DRY-RUN" not in done.stdout
    assert host.writes == []


def test_a_protection_answer_that_is_no_object_is_refused_before_any_write(
    tmp_path: Path,
):
    host = Host(tmp_path, HostState(protection=["lint"]), malformed=True)
    done = host.add("budgets")
    assert done.returncode == 1
    assert "no object" in done.stderr
    assert host.writes == []


def test_a_read_back_it_cannot_read_fails_and_says_what_to_compare(tmp_path: Path):
    def unreadable(protection: dict) -> None:
        del protection["required_status_checks"]["checks"]

    host = Host(
        tmp_path,
        HostState(after_write=_after_budgets_added(unreadable)),
        malformed=True,
    )
    done = host.add("budgets")
    assert done.returncode == 1
    assert "required_status_checks" in done.stderr
    assert "made only the one POST" in done.stderr
    assert "legacy" in done.stderr.split("fix:")[1]
    assert len(host.writes) == 1


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
    protection["enforce_admins"]["enabled"] = False


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
    host = Host(tmp_path, HostState(after_write=_after_budgets_added(change)))
    done = host.add("budgets")
    assert done.returncode == 1
    assert finding in done.stderr
    assert "legacy" in done.stderr.split("fix:")[1], "the operator gets the prior set"


def test_a_failed_read_back_fails_and_says_what_to_compare(tmp_path: Path):
    host = Host(
        tmp_path, HostState(fail_after_write={PROTECTION: Refusal(502, "Server Error")})
    )
    done = host.add("budgets")
    assert done.returncode == 1
    assert "HTTP 502" in done.stderr
    assert "made only the one POST" in done.stderr
    assert "legacy" in done.stderr.split("fix:")[1]
    assert len(host.writes) == 1


def test_a_rules_answer_that_is_no_list_is_refused_before_any_write(tmp_path: Path):
    host = Host(tmp_path, HostState(rules={"message": "unexpected"}), malformed=True)
    done = host.add("budgets")
    assert done.returncode == 1
    assert "did not answer a list of rules" in done.stderr
    assert host.writes == []


def test_a_refused_write_fails_with_the_hosts_error(tmp_path: Path):
    host = Host(
        tmp_path, HostState(fail={SUBRESOURCE: Refusal(403, "Resource not accessible")})
    )
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
