"""Tests for the GitHub governance setup script.

Loads the PEP 723 script as a module so its functions can be exercised
directly. A fake ``gh`` runner is injected everywhere, so the suite needs no
real ``gh`` binary, no network, and no GitHub auth — it asserts on the calls
the script *would* make and the payloads it builds.

The module is registered in sys.modules before exec so the ``@dataclass`` in it
can resolve ``__module__`` under all Python versions.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_github_governance.py"

spec = importlib.util.spec_from_file_location("setup_github_governance", SCRIPT)
assert spec is not None and spec.loader is not None
gov = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gov
spec.loader.exec_module(gov)


# --- fake gh runner --------------------------------------------------------


def route(args) -> str:
    """Classify a ``gh`` arg list the way the fake keys its canned responses."""
    args = list(args)
    if args[:2] == ["repo", "view"]:
        if "nameWithOwner" in args:
            return "repo"
        if "defaultBranchRef" in args:
            return "branch"
        return "repo-view"
    if args and args[0] == "api":
        return "api:" + args[args.index("--method") + 1]
    return " ".join(args)


class FakeGh:
    """Records every call; returns canned Results keyed by route.

    Defaults resolve the current repo to ``owner/name`` and its default branch
    to ``main``, and let mutating ``api`` calls succeed. Override per route to
    simulate failures or different repos.
    """

    def __init__(self, responses=None):
        self.calls: list[tuple[list[str], str | None]] = []
        self.responses = dict(responses or {})

    def __call__(self, args, input=None):
        args = list(args)
        self.calls.append((args, input))
        key = route(args)
        if key in self.responses:
            return self.responses[key]
        defaults = {
            "repo": gov.Result(0, "owner/name\n"),
            "branch": gov.Result(0, "main\n"),
        }
        return defaults.get(key, gov.Result(0, "", ""))

    def api_calls(self) -> list[tuple[list[str], str | None]]:
        return [(a, i) for a, i in self.calls if a and a[0] == "api"]

    def body_for(self, method: str) -> dict:
        for args, stdin in self.calls:
            if args and args[0] == "api" and route(args) == f"api:{method}":
                assert stdin is not None
                return json.loads(stdin)
        raise AssertionError(f"no {method} api call recorded")


# --- payloads --------------------------------------------------------------


def test_repo_settings_is_squash_only_with_auto_merge_and_pr_title():
    s = gov.repo_settings_payload()
    assert s["allow_squash_merge"] is True
    assert s["allow_merge_commit"] is False
    assert s["allow_rebase_merge"] is False
    assert s["allow_auto_merge"] is True
    assert s["delete_branch_on_merge"] is True
    assert s["squash_merge_commit_title"] == "PR_TITLE"
    assert s["squash_merge_commit_message"] == "PR_BODY"


def test_protection_requires_given_checks_strictly():
    p = gov.protection_payload(
        ["check", "commitlint"], approvals=0, enforce_admins=False
    )
    assert p["required_status_checks"]["strict"] is True
    assert p["required_status_checks"]["contexts"] == ["check", "commitlint"]


def test_protection_admins_override_by_default_and_bind_when_requested():
    assert gov.protection_payload(["check"], 0, False)["enforce_admins"] is False
    assert gov.protection_payload(["check"], 0, True)["enforce_admins"] is True


def test_protection_reflects_approval_count():
    p = gov.protection_payload(["check"], approvals=2, enforce_admins=False)
    assert p["required_pull_request_reviews"]["required_approving_review_count"] == 2


def test_protection_sets_standard_protections():
    p = gov.protection_payload(["check"], 0, False)
    assert p["required_linear_history"] is True
    assert p["required_conversation_resolution"] is True
    assert p["allow_force_pushes"] is False
    assert p["allow_deletions"] is False
    assert p["restrictions"] is None


# --- normalize_contexts ----------------------------------------------------


def test_normalize_contexts_strips_and_dedupes_preserving_order():
    assert gov.normalize_contexts(["check ", "commitlint", "check", ""]) == [
        "check",
        "commitlint",
    ]


def test_normalize_contexts_rejects_empty():
    import pytest

    with pytest.raises(gov.GhError):
        gov.normalize_contexts(["", "   "])


# --- plan ------------------------------------------------------------------


def test_plan_is_patch_settings_then_put_protection():
    calls = gov.plan("owner/name", "main", ["check"], 0, False)
    assert [c.method for c in calls] == ["PATCH", "PUT"]
    assert calls[0].path == "repos/owner/name"
    assert calls[1].path == "repos/owner/name/branches/main/protection"
    # The body round-trips through the gh stdin the call would send.
    assert json.loads(calls[0].stdin) == gov.repo_settings_payload()
    assert calls[0].args == [
        "api",
        "--method",
        "PATCH",
        "repos/owner/name",
        "--input",
        "-",
    ]


# --- resolve ---------------------------------------------------------------


def test_resolve_repo_uses_given_value_without_calling_gh():
    fake = FakeGh()
    assert gov.resolve_repo(fake, "acme/widget") == "acme/widget"
    assert fake.calls == []


def test_resolve_repo_queries_gh_when_absent():
    fake = FakeGh()
    assert gov.resolve_repo(fake, None) == "owner/name"
    assert route(fake.calls[0][0]) == "repo"


def test_resolve_branch_uses_given_value_without_calling_gh():
    fake = FakeGh()
    assert gov.resolve_branch(fake, "owner/name", "release") == "release"
    assert fake.calls == []


def test_resolve_branch_queries_default_for_repo():
    fake = FakeGh()
    assert gov.resolve_branch(fake, "owner/name", None) == "main"
    args = fake.calls[0][0]
    assert route(args) == "branch"
    assert "owner/name" in args


def test_resolve_repo_failure_raises_gherror_with_fix():
    import pytest

    fake = FakeGh({"repo": gov.Result(1, "", "not a git repo")})
    with pytest.raises(gov.GhError) as exc:
        gov.resolve_repo(fake, None)
    assert exc.value.fix


# --- execute ---------------------------------------------------------------


def test_execute_runs_each_call_with_its_json_body():
    fake = FakeGh()
    calls = gov.plan("owner/name", "main", ["check", "commitlint"], 0, False)
    gov.execute(fake, calls)
    assert [route(a) for a, _ in fake.api_calls()] == ["api:PATCH", "api:PUT"]
    assert fake.body_for("PUT")["required_status_checks"]["contexts"] == [
        "check",
        "commitlint",
    ]


def test_execute_raises_on_first_failure_naming_the_call():
    import pytest

    fake = FakeGh({"api:PUT": gov.Result(1, "", "403 Forbidden")})
    calls = gov.plan("owner/name", "main", ["check"], 0, False)
    with pytest.raises(gov.GhError) as exc:
        gov.execute(fake, calls)
    assert "branch protection" in exc.value.message
    assert exc.value.fix


# --- main ------------------------------------------------------------------


def test_main_applies_and_is_quiet_on_success(capsys):
    fake = FakeGh()
    assert gov.main(["check", "commitlint"], run=fake) == 0
    out = capsys.readouterr()
    assert out.err == ""
    lines = [ln for ln in out.out.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "owner/name@main" in lines[0]
    assert "auto-merge" in lines[0]
    assert "admins can override" in lines[0]
    # Resolved repo + branch (2 reads) and applied both mutations (2 writes).
    assert [route(a) for a, _ in fake.api_calls()] == ["api:PATCH", "api:PUT"]


def test_main_with_explicit_repo_and_branch_skips_resolution():
    fake = FakeGh()
    assert gov.main(["check", "--repo", "acme/w", "--branch", "main"], run=fake) == 0
    # No `repo view` calls — only the two mutations.
    assert all(a[0] == "api" for a, _ in fake.calls)
    assert len(fake.calls) == 2


def test_main_dry_run_offline_makes_no_gh_calls(capsys):
    fake = FakeGh()
    code = gov.main(
        ["check", "commitlint", "--repo", "acme/w", "--branch", "main", "--dry-run"],
        run=fake,
    )
    assert code == 0
    assert fake.calls == []
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "PATCH repos/acme/w" in out
    assert "PUT repos/acme/w/branches/main/protection" in out


def test_main_dry_run_resolves_but_never_mutates():
    fake = FakeGh()
    assert gov.main(["check", "--dry-run"], run=fake) == 0
    # It may read (resolve repo/branch) but must not issue any api mutation.
    assert fake.api_calls() == []


def test_main_failure_returns_one_with_actionable_fix(capsys):
    fake = FakeGh({"api:PUT": gov.Result(1, "", "403 Forbidden")})
    assert gov.main(["check", "--repo", "acme/w", "--branch", "main"], run=fake) == 1
    err = capsys.readouterr().err
    assert "ERROR" in err
    assert "fix:" in err


def test_main_enforce_admins_flag_binds_admins():
    fake = FakeGh()
    code = gov.main(
        ["check", "--repo", "acme/w", "--branch", "main", "--enforce-admins"],
        run=fake,
    )
    assert code == 0
    assert fake.body_for("PUT")["enforce_admins"] is True


def test_main_approvals_flag_sets_review_count():
    fake = FakeGh()
    gov.main(
        ["check", "--repo", "acme/w", "--branch", "main", "--approvals", "2"],
        run=fake,
    )
    reviews = fake.body_for("PUT")["required_pull_request_reviews"]
    assert reviews["required_approving_review_count"] == 2


def test_main_requires_at_least_one_check():
    import pytest

    fake = FakeGh()
    with pytest.raises(SystemExit):
        gov.main([], run=fake)


def test_main_missing_gh_binary_errors(monkeypatch, capsys):
    # No fake runner: main must preflight the real `gh` and fail clearly.
    monkeypatch.setattr(gov.shutil, "which", lambda _: None)
    assert gov.main(["check"]) == 1
    assert "gh CLI not found" in capsys.readouterr().err


def test_main_offline_dry_run_works_without_gh(monkeypatch, capsys):
    # --repo and --branch given + --dry-run needs no `gh`, even if it is absent.
    monkeypatch.setattr(gov.shutil, "which", lambda _: None)
    code = gov.main(["check", "--repo", "acme/w", "--branch", "main", "--dry-run"])
    assert code == 0
    assert "DRY-RUN" in capsys.readouterr().out
