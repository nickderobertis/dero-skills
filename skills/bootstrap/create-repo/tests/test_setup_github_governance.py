"""Tests for the GitHub governance setup script.

Loads the PEP 723 script as a module so its functions can be exercised
directly. A fake ``gh`` runner is injected everywhere, so the suite needs no
real ``gh`` binary, no network, and no GitHub auth — it asserts on the calls
the script *would* make and the payloads it builds.

The module is registered in sys.modules before exec so the ``@dataclass`` in it
can resolve ``__module__`` under all Python versions.

The end-to-end layer — running the script as a real ``uv run --script``
subprocess — lives in ``e2e/test_setup_github_governance_e2e.py``.
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


def _resource(path: str) -> str:
    """The governance resource a ``gh api`` path targets."""
    if path.endswith("/protection"):
        return "protection"
    if path.endswith("/fork-pr-contributor-approval"):
        return "fork-approval"
    return "repo-settings"


def route(args) -> str:
    """Classify a ``gh`` arg list the way the fake keys its canned responses.

    ``api`` calls are keyed by *purpose* (the resource path) and by whether they
    mutate (``--method``, keyed ``api:*``) or read (a plain GET, keyed ``get:*``),
    so the plan's two PUTs and --verify's three GETs never collide.
    """
    match list(args):
        case ["repo", "view", *rest]:
            if "nameWithOwner" in rest:
                return "repo"
            if "defaultBranchRef" in rest:
                return "branch"
            return "repo-view"
        case ["api", "--method", _, path, *_]:
            return f"api:{_resource(path)}"
        case ["api", path, *_]:
            return f"get:{_resource(path)}"
        case other:
            return " ".join(other)


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

    def body_for(self, key: str) -> dict:
        """Return the JSON body of the api call whose route is ``key``.

        ``key`` is a route from ``route()`` (e.g. ``api:protection``,
        ``api:fork-approval``, ``api:repo-settings``).
        """
        for args, stdin in self.calls:
            if args and args[0] == "api" and route(args) == key:
                assert stdin is not None
                return json.loads(stdin)
        raise AssertionError(f"no {key} api call recorded")


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


def test_protection_requires_given_checks_non_strictly_by_default():
    p = gov.protection_payload(
        ["check", "commitlint"], approvals=0, enforce_admins=False
    )
    # Non-strict by default: a PR need not be up to date with the base branch
    # before merging, so the base moving does not force a re-sync + re-run of CI.
    assert p["required_status_checks"]["strict"] is False
    assert p["required_status_checks"]["contexts"] == ["check", "commitlint"]


def test_protection_can_opt_into_strict_up_to_date_checks():
    p = gov.protection_payload(
        ["check"], approvals=0, enforce_admins=False, strict=True
    )
    assert p["required_status_checks"]["strict"] is True


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


# --- require_llmlint (the mandated blocking PR check) ----------------------


def test_require_llmlint_passes_when_context_present():
    # No raise: the canonical `llmlint` context satisfies the guard.
    gov.require_llmlint(["check", "commitlint", "llmlint"], allow_missing=False)


def test_require_llmlint_matches_case_and_suffix_insensitively():
    # A repo that spells the job differently (e.g. `llmlint (changed files)` or
    # `LLMLint`) still satisfies the guard — it matches the substring, any case.
    gov.require_llmlint(["check", "llmlint (changed files)"], allow_missing=False)
    gov.require_llmlint(["Check", "LLMLint"], allow_missing=False)


def test_require_llmlint_raises_when_absent():
    import pytest

    with pytest.raises(gov.GhError) as exc:
        gov.require_llmlint(["check", "commitlint"], allow_missing=False)
    # The error names a concrete fix (add the context) and the escape hatch.
    assert "llmlint" in exc.value.message
    assert "--allow-missing-llmlint" in exc.value.fix


def test_require_llmlint_waived_by_allow_missing():
    # The escape hatch for a repo with no llmlint tier: no raise despite absence.
    gov.require_llmlint(["check"], allow_missing=True)


def test_main_refuses_to_apply_without_llmlint_check(capsys):
    fake = FakeGh()
    code = gov.main(
        ["check", "commitlint", "--repo", "acme/w", "--branch", "main"], run=fake
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "llmlint" in err
    assert "fix:" in err
    # It bails at the guard, before issuing any mutation.
    assert fake.api_calls() == []


def test_main_allow_missing_llmlint_applies_without_it():
    fake = FakeGh()
    code = gov.main(
        [
            "check",
            "commitlint",
            "--repo",
            "acme/w",
            "--branch",
            "main",
            "--allow-missing-llmlint",
        ],
        run=fake,
    )
    assert code == 0
    # The three mutations still run; only the llmlint guard was waived.
    assert [route(a) for a, _ in fake.api_calls()] == [
        "api:repo-settings",
        "api:protection",
        "api:fork-approval",
    ]


def test_main_verify_also_requires_llmlint(capsys):
    # The guard runs in every mode: --verify refuses too, before reading live state.
    fake = _verify_fake()
    code = gov.main(
        ["check", "commitlint", "--repo", "owner/name", "--branch", "main", "--verify"],
        run=fake,
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "llmlint" in err
    # Bailed before any read-back GET.
    assert fake.api_calls() == []


# --- plan ------------------------------------------------------------------


def test_plan_is_patch_settings_then_protection_then_fork_approval():
    calls = gov.plan("owner/name", "main", ["check"], 0, False)
    assert [c.method for c in calls] == ["PATCH", "PUT", "PUT"]
    assert calls[0].path == "repos/owner/name"
    assert calls[1].path == "repos/owner/name/branches/main/protection"
    assert (
        calls[2].path
        == "repos/owner/name/actions/permissions/fork-pr-contributor-approval"
    )
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


def test_fork_pr_approval_payload_and_default():
    assert gov.fork_pr_approval_payload("first_time_contributors") == {
        "approval_policy": "first_time_contributors"
    }
    # The default is the most conservative: every external contributor is gated.
    assert gov.DEFAULT_FORK_PR_APPROVAL == "all_external_contributors"
    calls = gov.plan("owner/name", "main", ["check"], 0, False)
    assert json.loads(calls[2].stdin) == {
        "approval_policy": "all_external_contributors"
    }


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
    assert [route(a) for a, _ in fake.api_calls()] == [
        "api:repo-settings",
        "api:protection",
        "api:fork-approval",
    ]
    assert fake.body_for("api:protection")["required_status_checks"]["contexts"] == [
        "check",
        "commitlint",
    ]


def test_execute_raises_on_first_failure_naming_the_call():
    import pytest

    fake = FakeGh({"api:protection": gov.Result(1, "", "403 Forbidden")})
    calls = gov.plan("owner/name", "main", ["check"], 0, False)
    with pytest.raises(gov.GhError) as exc:
        gov.execute(fake, calls)
    assert "branch protection" in exc.value.message
    assert exc.value.fix


# --- main ------------------------------------------------------------------


def test_main_applies_and_is_quiet_on_success(capsys):
    fake = FakeGh()
    assert gov.main(["check", "commitlint", "llmlint"], run=fake) == 0
    out = capsys.readouterr()
    assert out.err == ""
    lines = [ln for ln in out.out.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "owner/name@main" in lines[0]
    assert "auto-merge" in lines[0]
    assert "admins can override" in lines[0]
    assert "fork-PR approval: all_external_contributors" in lines[0]
    # Resolved repo + branch (2 reads) and applied all three mutations.
    assert [route(a) for a, _ in fake.api_calls()] == [
        "api:repo-settings",
        "api:protection",
        "api:fork-approval",
    ]


def test_main_with_explicit_repo_and_branch_skips_resolution():
    fake = FakeGh()
    assert (
        gov.main(["check", "llmlint", "--repo", "acme/w", "--branch", "main"], run=fake)
        == 0
    )
    # No `repo view` calls — only the three mutations.
    assert all(a[0] == "api" for a, _ in fake.calls)
    assert len(fake.calls) == 3


def test_main_dry_run_offline_makes_no_gh_calls(capsys):
    fake = FakeGh()
    code = gov.main(
        [
            "check",
            "commitlint",
            "llmlint",
            "--repo",
            "acme/w",
            "--branch",
            "main",
            "--dry-run",
        ],
        run=fake,
    )
    assert code == 0
    assert fake.calls == []
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "PATCH repos/acme/w" in out
    assert "PUT repos/acme/w/branches/main/protection" in out
    assert "PUT repos/acme/w/actions/permissions/fork-pr-contributor-approval" in out
    assert "all_external_contributors" in out


def test_main_dry_run_resolves_but_never_mutates():
    fake = FakeGh()
    assert gov.main(["check", "llmlint", "--dry-run"], run=fake) == 0
    # It may read (resolve repo/branch) but must not issue any api mutation.
    assert fake.api_calls() == []


def test_main_failure_returns_one_with_actionable_fix(capsys):
    fake = FakeGh({"api:protection": gov.Result(1, "", "403 Forbidden")})
    assert (
        gov.main(["check", "llmlint", "--repo", "acme/w", "--branch", "main"], run=fake)
        == 1
    )
    err = capsys.readouterr().err
    assert "ERROR" in err
    assert "fix:" in err


def test_main_enforce_admins_flag_binds_admins():
    fake = FakeGh()
    code = gov.main(
        [
            "check",
            "llmlint",
            "--repo",
            "acme/w",
            "--branch",
            "main",
            "--enforce-admins",
        ],
        run=fake,
    )
    assert code == 0
    assert fake.body_for("api:protection")["enforce_admins"] is True


def test_main_defaults_to_non_strict_and_strict_flag_opts_in():
    fake = FakeGh()
    gov.main(["check", "llmlint", "--repo", "acme/w", "--branch", "main"], run=fake)
    assert fake.body_for("api:protection")["required_status_checks"]["strict"] is False

    fake = FakeGh()
    gov.main(
        ["check", "llmlint", "--repo", "acme/w", "--branch", "main", "--strict"],
        run=fake,
    )
    assert fake.body_for("api:protection")["required_status_checks"]["strict"] is True


def test_main_approvals_flag_sets_review_count():
    fake = FakeGh()
    gov.main(
        [
            "check",
            "llmlint",
            "--repo",
            "acme/w",
            "--branch",
            "main",
            "--approvals",
            "2",
        ],
        run=fake,
    )
    reviews = fake.body_for("api:protection")["required_pull_request_reviews"]
    assert reviews["required_approving_review_count"] == 2


def test_main_defaults_fork_pr_approval_to_all_external():
    fake = FakeGh()
    gov.main(["check", "llmlint", "--repo", "acme/w", "--branch", "main"], run=fake)
    assert fake.body_for("api:fork-approval") == {
        "approval_policy": "all_external_contributors"
    }


def test_main_fork_pr_approval_flag_overrides_policy():
    fake = FakeGh()
    gov.main(
        [
            "check",
            "llmlint",
            "--repo",
            "acme/w",
            "--branch",
            "main",
            "--fork-pr-approval",
            "first_time_contributors",
        ],
        run=fake,
    )
    assert fake.body_for("api:fork-approval") == {
        "approval_policy": "first_time_contributors"
    }


def test_main_rejects_unknown_fork_pr_approval_policy():
    import pytest

    fake = FakeGh()
    with pytest.raises(SystemExit):
        gov.main(
            [
                "check",
                "--repo",
                "a/b",
                "--branch",
                "main",
                "--fork-pr-approval",
                "bogus",
            ],
            run=fake,
        )


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
    code = gov.main(
        ["check", "llmlint", "--repo", "acme/w", "--branch", "main", "--dry-run"]
    )
    assert code == 0
    assert "DRY-RUN" in capsys.readouterr().out


# --- verify (read-back governance) -----------------------------------------


def _live_settings(**over) -> dict:
    d = dict(gov.repo_settings_payload())
    d.update(over)
    return d


def _live_protection(
    contexts=("check", "commitlint"),
    *,
    strict=False,
    enforce_admins=False,
    approvals=0,
    linear=True,
    conversation=True,
    force_pushes=False,
    deletions=False,
) -> dict:
    # Mirrors GitHub's protection GET shape: booleans wrapped as {"enabled": ...}.
    return {
        "required_status_checks": {"strict": strict, "contexts": list(contexts)},
        "enforce_admins": {"enabled": enforce_admins},
        "required_pull_request_reviews": {"required_approving_review_count": approvals},
        "required_linear_history": {"enabled": linear},
        "required_conversation_resolution": {"enabled": conversation},
        "allow_force_pushes": {"enabled": force_pushes},
        "allow_deletions": {"enabled": deletions},
    }


def _verify_fake(
    *, settings=None, protection=..., fork_policy="all_external_contributors"
) -> FakeGh:
    """A fake gh whose GETs return a (by default conformant) live governance state.

    Pass ``protection=None`` to simulate an unprotected branch (a 404), or a dict
    to inject drift; ``settings``/``fork_policy`` likewise override the merge model
    and fork policy.
    """
    responses = {
        "get:repo-settings": gov.Result(
            0, json.dumps(settings if settings is not None else _live_settings())
        ),
        "get:fork-approval": gov.Result(
            0, json.dumps({"approval_policy": fork_policy})
        ),
    }
    if protection is None:
        responses["get:protection"] = gov.Result(1, "", "Not Found")
    else:
        prot = protection if protection is not ... else _live_protection()
        responses["get:protection"] = gov.Result(0, json.dumps(prot))
    return FakeGh(responses)


def test_verify_conformant_state_reports_no_problems():
    fake = _verify_fake()
    problems = gov.verify(
        fake,
        "owner/name",
        "main",
        ["check", "commitlint"],
        0,
        False,
        False,
        "all_external_contributors",
    )
    assert problems == []
    # It only ever READS: no --method mutations issued.
    assert fake.api_calls() and all("--method" not in a for a, _ in fake.api_calls())


def test_verify_flags_missing_required_check():
    fake = _verify_fake(protection=_live_protection(contexts=("check",)))
    problems = gov.verify(
        fake,
        "owner/name",
        "main",
        ["check", "commitlint"],
        0,
        False,
        False,
        "all_external_contributors",
    )
    assert any("commitlint" in p and "missing" in p for p in problems)


def test_verify_flags_merge_model_drift():
    fake = _verify_fake(settings=_live_settings(allow_merge_commit=True))
    problems = gov.verify(
        fake,
        "owner/name",
        "main",
        ["check"],
        0,
        False,
        False,
        "all_external_contributors",
    )
    assert any("allow_merge_commit" in p for p in problems)


def test_verify_flags_allowed_force_pushes():
    fake = _verify_fake(protection=_live_protection(force_pushes=True))
    problems = gov.verify(
        fake,
        "owner/name",
        "main",
        ["check", "commitlint"],
        0,
        False,
        False,
        "all_external_contributors",
    )
    assert any("allow_force_pushes" in p for p in problems)


def test_verify_flags_unprotected_branch():
    # A genuine 404 (stderr says "Not Found") is the branch simply being unprotected.
    fake = _verify_fake(protection=None)
    problems = gov.verify(
        fake,
        "owner/name",
        "main",
        ["check"],
        0,
        False,
        False,
        "all_external_contributors",
    )
    assert any("not protected" in p for p in problems)


def test_verify_non_404_protection_error_raises_not_misclassified():
    # An auth/network error (not a 404) must surface as an error, never be
    # silently reported as "branch not protected".
    import pytest

    fake = _verify_fake()
    fake.responses["get:protection"] = gov.Result(1, "", "HTTP 403: Forbidden")
    with pytest.raises(gov.GhError) as exc:
        gov.verify(
            fake,
            "owner/name",
            "main",
            ["check"],
            0,
            False,
            False,
            "all_external_contributors",
        )
    assert "403" in exc.value.message


def test_verify_flags_fork_policy_drift():
    fake = _verify_fake(fork_policy="first_time_contributors")
    problems = gov.verify(
        fake,
        "owner/name",
        "main",
        ["check", "commitlint"],
        0,
        False,
        False,
        "all_external_contributors",
    )
    assert any("fork-PR approval" in p for p in problems)


def test_verify_reads_nested_enabled_and_checks_shape():
    # GitHub's newer protection GET lists checks under `checks`, not `contexts`;
    # verify must still see a check required under that representation.
    prot = _live_protection(contexts=())
    prot["required_status_checks"]["checks"] = [
        {"context": "check", "app_id": 1},
        {"context": "commitlint", "app_id": 1},
    ]
    fake = _verify_fake(protection=prot)
    problems = gov.verify(
        fake,
        "owner/name",
        "main",
        ["check", "commitlint"],
        0,
        False,
        False,
        "all_external_contributors",
    )
    assert problems == []


def test_main_verify_passes_on_conformant_state(capsys):
    fake = _verify_fake(
        protection=_live_protection(contexts=("check", "commitlint", "llmlint"))
    )
    code = gov.main(
        [
            "check",
            "commitlint",
            "llmlint",
            "--repo",
            "owner/name",
            "--branch",
            "main",
            "--verify",
        ],
        run=fake,
    )
    assert code == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert "governance matches" in out.out
    # Verify never mutates.
    assert fake.api_calls() and all("--method" not in a for a, _ in fake.api_calls())


def test_main_verify_fails_and_reports_drift(capsys):
    fake = _verify_fake(
        protection=_live_protection(
            contexts=("check", "commitlint", "llmlint"), force_pushes=True
        )
    )
    code = gov.main(
        [
            "check",
            "commitlint",
            "llmlint",
            "--repo",
            "owner/name",
            "--branch",
            "main",
            "--verify",
        ],
        run=fake,
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "DRIFT" in err
    assert "FAIL" in err
