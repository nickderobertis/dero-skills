# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Apply the create-repo merge model and branch protection to a GitHub repo.

Usage:
    uv run --script scripts/setup_github_governance.py CHECK [CHECK ...] \
        [--repo OWNER/NAME] [--branch NAME] [--approvals N] \
        [--fork-pr-approval POLICY] [--enforce-admins] [--dry-run] [--verify]

The positional ``CHECK`` arguments are the status-check contexts that must be
green before a PR can merge. List *every* gating check by name — including the
full-e2e gate job (the one that runs ``just check``); a check that is not
required is only advisory, and a red one can still be merged past. One of these
MUST be the llmlint job (the LLM-judge tier is a mandated blocking PR check),
else auto-merge lands past a red llmlint run — pass --allow-missing-llmlint only
for a repo that genuinely has no llmlint tier.

What it sets (the model the create-repo skill prescribes — see references/ci.md):
  * Merge model: squash-merge only (merge commits and rebase-merging disabled),
    auto-merge enabled, head branches deleted on merge, and the squash subject
    taken from the PR title / body from the PR description.
  * Branch protection on the default branch: the required checks above, linear
    history, conversation resolution, no force-pushes, no branch deletion.
    Admins can override by default (``enforce_admins: false``); pass
    --enforce-admins to bind them too. Checks are non-strict by default — a PR
    need not be rebased onto the latest default branch before merging, which
    avoids the re-update-and-re-run-CI churn every time the base moves. Pass
    --strict to require branches be up to date (catches semantic conflicts
    between independently-green PRs, at the cost of that friction).
  * Fork-PR workflow approval: requires a maintainer to approve before a fork's
    CI runs (default: all external contributors). Credential-gated checks (the
    live/llmlint tiers) fail fast without their secret, so fork PRs must not
    auto-run them unreviewed. Tune the class of contributor with
    --fork-pr-approval.

This sets the *full desired state* idempotently: re-running applies the same
config, and the branch-protection PUT replaces any existing protection on the
branch. Use --dry-run to print exactly what it would do without touching the
repo (pass --repo and --branch to preview fully offline).

Use --verify to *read back* the live state and report where it diverges from the
desired one — branch protection (are all gating checks required? force-push and
deletion blocked?), the merge model (squash-only, auto-merge, delete-on-merge),
and the fork-PR approval policy. This is the piece the filesystem baseline checker
structurally cannot see (repo-side settings, not files), so a repo can pass every
file-level gate while its gating checks are not actually required to merge.
--verify writes nothing and exits non-zero on any divergence, so it belongs in a
creation-verification step or a periodic drift check.

Talks to GitHub through the authenticated ``gh`` CLI, so it carries no
dependencies and inherits your existing credentials — it needs admin rights on
the target repo. Output is itself agent context, so it is minimal: a single OK
line on success; on failure, the failing call's error and a concrete next
action. Self-contained via PEP 723 so it runs in any consuming repo with
``uv run --script``.
"""

from __future__ import annotations

# llmlint: ignore-file[async_typed_clients_at_boundaries] `gh` is the mandated GitHub
# boundary (see SKILL.md compatibility) and it inherits the operator's auth; a synchronous
# subprocess is the correct, intentional client here — there is no async typed client to prefer.

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# The llmlint job's status-check context. The create-repo CI names the job so
# its context is a bare ``llmlint``; match case-insensitively (and any suffix a
# repo appends, e.g. ``llmlint (changed files)``) so the guard holds whatever the
# repo calls the job. The LLM-judge tier is a mandated blocking PR check, so its
# context must be among the required checks — otherwise auto-merge lands past a
# red llmlint run (the failure mode this guard exists to prevent).
LLMLINT_CONTEXT_RE = re.compile(r"llmlint", re.IGNORECASE)

# Allowed GitHub values that make the squash commit subject/body follow the PR,
# so the PR title is what lands (and what a Conventional-Commits / release
# pipeline reads).
SQUASH_TITLE = "PR_TITLE"
SQUASH_MESSAGE = "PR_BODY"

# Fork-PR workflow approval policy (GitHub's "Require approval for fork pull
# request workflows"). The create-repo model turns this ON so a maintainer
# approves before a fork's CI runs: credential-gated checks (the live/llmlint
# tiers) fail fast without their secret rather than no-oping to a misleading
# green, so fork PRs must not auto-run them unreviewed. The enum has no "off"
# value — the setting always requires approval from *some* class of contributor.
FORK_PR_APPROVAL_POLICIES = (
    "first_time_contributors_new_to_github",
    "first_time_contributors",
    "all_external_contributors",
)
DEFAULT_FORK_PR_APPROVAL = "all_external_contributors"


@dataclass
class Result:
    """The outcome of one ``gh`` invocation."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


# A runner takes the ``gh`` arguments (without the leading "gh") and optional
# stdin, and returns a Result. Injected in tests so no real ``gh`` is needed.
Runner = Callable[..., Result]


@dataclass
class GhCall:
    """One mutating ``gh api`` request: an HTTP method, path, and JSON body."""

    method: str  # "PATCH" | "PUT"
    path: str  # e.g. "repos/owner/name" or ".../branches/main/protection"
    body: dict  # JSON-serializable request body
    summary: str  # human label, shown in dry-run output and errors

    @property
    def args(self) -> list[str]:
        return ["api", "--method", self.method, self.path, "--input", "-"]

    @property
    def stdin(self) -> str:
        return json.dumps(self.body)


class GhError(Exception):
    """A ``gh`` call failed. Carries a suggested next action for the operator."""

    def __init__(self, message: str, fix: str) -> None:
        super().__init__(message)
        self.message = message
        self.fix = fix


def _subprocess_run(args: Sequence[str], *, input: str | None = None) -> Result:
    proc = subprocess.run(
        ["gh", *args],
        input=input,
        capture_output=True,
        text=True,
        check=False,
    )
    return Result(proc.returncode, proc.stdout, proc.stderr)


def normalize_contexts(contexts: Sequence[str]) -> list[str]:
    """Strip blanks and de-duplicate (preserving order); require at least one.

    A protection rule with no required checks gates nothing, which silently
    defeats the point — so an empty list is an error, not an accepted no-op.
    """
    seen: list[str] = []
    for ctx in contexts:
        name = ctx.strip()
        if name and name not in seen:
            seen.append(name)
    if not seen:
        raise GhError(
            "no required status checks given",
            "pass at least one check context, e.g. `... check commitlint llmlint`",
        )
    return seen


def require_llmlint(contexts: Sequence[str], *, allow_missing: bool) -> None:
    """Fail unless an llmlint check is among the required contexts.

    The create-repo skill mandates the llmlint LLM-judge tier as a blocking PR
    check: it runs outside ``just check`` (non-deterministic, credentialed), so
    the only thing making a red llmlint run block a merge is its presence in the
    required status checks. Leave it out and auto-merge lands past it — the exact
    hole this guard closes. ``allow_missing`` (the --allow-missing-llmlint flag)
    waives it for the rare repo with no llmlint tier.
    """
    if allow_missing or any(LLMLINT_CONTEXT_RE.search(c) for c in contexts):
        return
    raise GhError(
        "no llmlint check among the required status checks",
        "add the llmlint job's context (the create-repo CI names it `llmlint`) so "
        "a red llmlint run blocks merge — the LLM-judge tier is a mandated blocking "
        "PR check; pass --allow-missing-llmlint only if this repo has no llmlint tier",
    )


def repo_settings_payload() -> dict:
    """The merge-model PATCH body for ``repos/{owner}/{repo}`` (constant)."""
    return {
        "allow_squash_merge": True,
        "allow_merge_commit": False,
        "allow_rebase_merge": False,
        "allow_auto_merge": True,
        "delete_branch_on_merge": True,
        "squash_merge_commit_title": SQUASH_TITLE,
        "squash_merge_commit_message": SQUASH_MESSAGE,
    }


def fork_pr_approval_payload(policy: str) -> dict:
    """The body for the fork-PR-approval PUT (``{"approval_policy": ...}``)."""
    return {"approval_policy": policy}


def protection_payload(
    contexts: Sequence[str],
    approvals: int,
    enforce_admins: bool,
    strict: bool = False,
) -> dict:
    """The branch-protection PUT body for the default branch.

    ``strict`` maps to GitHub's "require branches be up to date before merging".
    It defaults to off: forcing every PR to re-sync onto the latest default
    branch and re-run CI before it can land is real friction, and worth it only
    when independently-green PRs routinely conflict semantically.
    """
    return {
        "required_status_checks": {"strict": strict, "contexts": list(contexts)},
        "enforce_admins": enforce_admins,
        "required_pull_request_reviews": {"required_approving_review_count": approvals},
        "required_linear_history": True,
        "required_conversation_resolution": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "restrictions": None,
    }


def plan(
    repo: str,
    branch: str,
    contexts: Sequence[str],
    approvals: int,
    enforce_admins: bool,
    strict: bool = False,
    fork_pr_approval: str = DEFAULT_FORK_PR_APPROVAL,
) -> list[GhCall]:
    """Build the ordered list of mutating calls.

    Merge model, then branch protection, then the fork-PR workflow approval
    policy (so a maintainer gates fork CI before credential-requiring checks run).
    """
    return [
        GhCall(
            "PATCH",
            f"repos/{repo}",
            repo_settings_payload(),
            "merge model (squash-only, auto-merge, delete-on-merge)",
        ),
        GhCall(
            "PUT",
            f"repos/{repo}/branches/{branch}/protection",
            protection_payload(contexts, approvals, enforce_admins, strict),
            f"branch protection on {branch}",
        ),
        GhCall(
            "PUT",
            f"repos/{repo}/actions/permissions/fork-pr-contributor-approval",
            fork_pr_approval_payload(fork_pr_approval),
            f"fork PR workflow approval ({fork_pr_approval})",
        ),
    ]


def _capture(run: Runner, args: Sequence[str], fix: str) -> str:
    """Run a read-only ``gh`` command and return its stdout, or raise GhError."""
    res = run(list(args))
    if res.returncode != 0:
        raise GhError(f"`gh {' '.join(args)}` failed: {res.stderr.strip()}", fix)
    return res.stdout.strip()


def resolve_repo(run: Runner, repo: str | None) -> str:
    """Return ``owner/name`` — the given value, or the current repo via ``gh``."""
    if repo:
        return repo
    return _capture(
        run,
        ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        "run inside a GitHub repo, or pass --repo OWNER/NAME",
    )


def resolve_branch(run: Runner, repo: str, branch: str | None) -> str:
    """Return the branch to protect — the given value, or the repo default."""
    if branch:
        return branch
    return _capture(
        run,
        [
            "repo",
            "view",
            repo,
            "--json",
            "defaultBranchRef",
            "--jq",
            ".defaultBranchRef.name",
        ],
        "pass --branch NAME (could not read the repo's default branch)",
    )


def execute(run: Runner, calls: Sequence[GhCall]) -> None:
    """Run each call in order; raise GhError at the first failure."""
    for call in calls:
        res = run(call.args, input=call.stdin)
        if res.returncode != 0:
            raise GhError(
                f"failed to set {call.summary}: {res.stderr.strip()}",
                "check you have admin rights on the repo and are authenticated "
                "(`gh auth status`); see `gh api` output above",
            )


def _flag(value: object) -> bool:
    """Read a GitHub protection flag that GET returns as ``{"enabled": bool}``.

    The branch-protection GET wraps each boolean in an ``{"enabled": ...}`` object,
    unlike the PUT body (a bare bool). Accept either shape so verify reads the same
    settings this script writes.
    """
    if isinstance(value, dict):
        return bool(value.get("enabled"))
    return bool(value)


def _protection_contexts(required_status_checks: dict) -> set[str]:
    """Return the required check contexts from a protection GET response.

    GitHub exposes them as the legacy ``contexts`` list and, newer, as ``checks``
    (each ``{"context": ..., "app_id": ...}``). Union both so a check required
    under either representation counts. Each item's shape is validated before use —
    this reads a third-party API response, so it never assumes the element types.
    """
    raw_contexts = required_status_checks.get("contexts")
    contexts = (
        {c for c in raw_contexts if isinstance(c, str)}
        if isinstance(raw_contexts, list)
        else set()
    )
    raw_checks = required_status_checks.get("checks")
    if isinstance(raw_checks, list):
        for check in raw_checks:
            if isinstance(check, dict) and isinstance(check.get("context"), str):
                contexts.add(check["context"])
    return contexts


@dataclass(frozen=True)
class LiveMergeModel:
    """The repo-level merge settings, parsed from the ``repos/{repo}`` GET."""

    settings: dict

    @classmethod
    def from_api(cls, data: dict) -> LiveMergeModel:
        # Validate the shape at the boundary before indexing — this is a
        # third-party API response, not trusted internal state.
        data = data if isinstance(data, dict) else {}
        return cls({key: data.get(key) for key in repo_settings_payload()})


@dataclass(frozen=True)
class LiveProtection:
    """Branch protection, normalized from the protection GET into plain fields.

    The GET wraps booleans as ``{"enabled": ...}`` and lists required checks two
    ways; ``from_api`` flattens both so the comparison logic works in typed fields.
    """

    required_contexts: frozenset[str]
    strict: bool
    enforce_admins: bool
    approvals: int
    linear_history: bool
    conversation_resolution: bool
    force_pushes: bool
    deletions: bool

    @classmethod
    def from_api(cls, data: dict) -> LiveProtection:
        data = data if isinstance(data, dict) else {}
        checks = data.get("required_status_checks")
        checks = checks if isinstance(checks, dict) else {}
        reviews = data.get("required_pull_request_reviews")
        reviews = reviews if isinstance(reviews, dict) else {}
        return cls(
            required_contexts=frozenset(_protection_contexts(checks)),
            strict=bool(checks.get("strict")),
            enforce_admins=_flag(data.get("enforce_admins")),
            approvals=reviews.get("required_approving_review_count", 0),
            linear_history=_flag(data.get("required_linear_history")),
            conversation_resolution=_flag(data.get("required_conversation_resolution")),
            force_pushes=_flag(data.get("allow_force_pushes")),
            deletions=_flag(data.get("allow_deletions")),
        )


@dataclass(frozen=True)
class LiveForkApproval:
    """The fork-PR workflow approval policy, from its Actions-permissions GET."""

    policy: str | None

    @classmethod
    def from_api(cls, data: dict) -> LiveForkApproval:
        data = data if isinstance(data, dict) else {}
        return cls(policy=data.get("approval_policy"))


def verify_repo_settings(model: LiveMergeModel) -> list[str]:
    """Problems where the live merge model diverges from the desired one."""
    problems: list[str] = []
    for key, expected in repo_settings_payload().items():
        actual = model.settings.get(key)
        if actual != expected:
            problems.append(f"merge model: {key} is {actual!r}, want {expected!r}")
    return problems


def verify_protection(
    live: LiveProtection,
    contexts: Sequence[str],
    approvals: int,
    enforce_admins: bool,
    strict: bool,
) -> list[str]:
    """Problems where live branch protection diverges from the desired state."""
    problems: list[str] = []
    missing = [c for c in contexts if c not in live.required_contexts]
    if missing:
        problems.append(
            f"branch protection: required checks missing {missing} "
            f"(has {sorted(live.required_contexts)})"
        )
    if live.strict != strict:
        problems.append(f"branch protection: strict is {live.strict}, want {strict}")
    if live.enforce_admins != enforce_admins:
        problems.append(
            f"branch protection: enforce_admins is {live.enforce_admins}, "
            f"want {enforce_admins}"
        )
    if live.approvals != approvals:
        problems.append(
            f"branch protection: required approvals is {live.approvals}, "
            f"want {approvals}"
        )
    for label, actual, want in (
        ("required_linear_history", live.linear_history, True),
        ("required_conversation_resolution", live.conversation_resolution, True),
        ("allow_force_pushes", live.force_pushes, False),
        ("allow_deletions", live.deletions, False),
    ):
        if actual != want:
            problems.append(f"branch protection: {label} is {actual}, want {want}")
    return problems


def verify_fork_approval(live: LiveForkApproval, policy: str) -> list[str]:
    """Problem where the live fork-PR approval policy diverges from the desired one."""
    if live.policy != policy:
        return [f"fork-PR approval policy is {live.policy!r}, want {policy!r}"]
    return []


def verify(
    run: Runner,
    repo: str,
    branch: str,
    contexts: Sequence[str],
    approvals: int,
    enforce_admins: bool,
    strict: bool,
    fork_pr_approval: str,
) -> list[str]:
    """Read the live governance state and return the list of divergences.

    Reads (never writes) the three settings this script configures — merge model,
    branch protection, fork-PR approval — parses each GitHub response into a typed
    model at the boundary, and diffs it against the desired state. An empty list
    means the repo matches; the caller reports and sets the exit code.
    """
    problems: list[str] = []

    settings = _get_json(
        run,
        f"repos/{repo}",
        "check you can read the repo (`gh auth status`) and --repo is correct",
    )
    problems += verify_repo_settings(LiveMergeModel.from_api(settings))

    protection = _get_json(
        run,
        f"repos/{repo}/branches/{branch}/protection",
        "check admin access and that --branch is correct (`gh auth status`); a "
        "genuine 404 is reported as unprotected, other errors are surfaced here",
        allow_missing=True,
    )
    if protection is None:
        problems.append(
            f"branch protection: {branch} is not protected (no protection rule)"
        )
    else:
        problems += verify_protection(
            LiveProtection.from_api(protection),
            contexts,
            approvals,
            enforce_admins,
            strict,
        )

    fork = _get_json(
        run,
        f"repos/{repo}/actions/permissions/fork-pr-contributor-approval",
        "check admin access to Actions settings",
    )
    problems += verify_fork_approval(LiveForkApproval.from_api(fork), fork_pr_approval)

    return problems


def _is_not_found(stderr: str) -> bool:
    """True when a ``gh api`` failure is a genuine 404 (the resource is absent).

    Distinguished from auth/network/other API errors so ``allow_missing`` returns
    None only for a real absence — anything else is surfaced with its actual error.
    """
    low = stderr.lower()
    return "404" in low or "not found" in low


def _get_json(
    run: Runner, path: str, fix: str, *, allow_missing: bool = False
) -> dict | None:
    """GET a ``gh api`` path and parse its JSON body.

    Raises GhError on any failure, carrying the exact ``gh`` error and the
    suggested fix — except that a genuine 404 with ``allow_missing`` returns None
    (used for branch protection, which 404s when the branch is simply unprotected:
    a divergence to report, not a hard error). Auth, network, and other API errors
    are never silently swallowed as "missing".
    """
    res = run(["api", path])
    if res.returncode != 0:
        stderr = res.stderr.strip()
        if allow_missing and _is_not_found(stderr):
            return None
        raise GhError(f"`gh api {path}` failed: {stderr}", fix)
    try:
        return json.loads(res.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise GhError(f"`gh api {path}` returned invalid JSON: {exc}", fix) from exc


def _render_dry_run(repo: str, branch: str, calls: Sequence[GhCall]) -> str:
    lines = [f"DRY-RUN would configure {repo}@{branch}:"]
    for call in calls:
        lines.append(f"  {call.method} {call.path}  # {call.summary}")
        for body_line in json.dumps(call.body, indent=2).splitlines():
            lines.append(f"    {body_line}")
    return "\n".join(lines)


def _success_line(
    repo: str,
    branch: str,
    contexts: Sequence[str],
    enforce_admins: bool,
    strict: bool,
    fork_pr_approval: str,
) -> str:
    admins = "admins enforced" if enforce_admins else "admins can override"
    strictness = "strict" if strict else "non-strict"
    checks = ", ".join(contexts)
    return (
        f"OK    {repo}@{branch}: squash-only + auto-merge + delete-on-merge; "
        f"{len(contexts)} required check(s) [{checks}], {strictness}; {admins}; "
        f"fork-PR approval: {fork_pr_approval}"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "checks",
        nargs="+",
        metavar="CHECK",
        help="required status-check context(s); list every gating check",
    )
    parser.add_argument("--repo", help="OWNER/NAME (default: the current repo)")
    parser.add_argument("--branch", help="branch to protect (default: repo default)")
    parser.add_argument(
        "--approvals",
        type=int,
        default=0,
        help="required approving reviews (default: 0; raise for a team)",
    )
    parser.add_argument(
        "--enforce-admins",
        action="store_true",
        help="also bind admins to the protection (default: admins can override)",
    )
    parser.add_argument(
        "--allow-missing-llmlint",
        action="store_true",
        help="permit governance without an llmlint required check (default: the "
        "llmlint LLM-judge tier is a mandated blocking PR check, so its context "
        "must be among the required checks or auto-merge lands past a red run)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="require branches be up to date before merging (default: off, to "
        "avoid the re-sync/re-run-CI churn each time the base branch moves)",
    )
    parser.add_argument(
        "--fork-pr-approval",
        choices=FORK_PR_APPROVAL_POLICIES,
        default=DEFAULT_FORK_PR_APPROVAL,
        help="who must be approved by a maintainer before their fork PR runs CI "
        f"(default: {DEFAULT_FORK_PR_APPROVAL}); keeps credential-gated checks "
        "from auto-running unreviewed on forks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would change without modifying the repo",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="read-only: report where the live governance state diverges from the "
        "desired one (branch protection, merge model, fork-PR approval) instead of "
        "applying it; exits non-zero on any divergence",
    )
    return parser.parse_args(argv)


def main(argv: list[str], run: Runner | None = None) -> int:
    args = parse_args(argv)

    # With a real ``gh``, ensure it is installed before doing anything. A fully
    # offline dry-run (--repo and --branch both given) needs no ``gh`` at all.
    if run is None:
        offline_dry_run = args.dry_run and args.repo and args.branch and not args.verify
        needs_gh = not offline_dry_run
        if needs_gh and shutil.which("gh") is None:
            print(
                "ERROR gh CLI not found\n"
                "      fix: install GitHub CLI (https://cli.github.com) and run "
                "`gh auth login`",
                file=sys.stderr,
            )
            return 1
        run = _subprocess_run

    try:
        contexts = normalize_contexts(args.checks)
        require_llmlint(contexts, allow_missing=args.allow_missing_llmlint)
        repo = resolve_repo(run, args.repo)
        branch = resolve_branch(run, repo, args.branch)
        if args.verify:
            problems = verify(
                run,
                repo,
                branch,
                contexts,
                args.approvals,
                args.enforce_admins,
                args.strict,
                args.fork_pr_approval,
            )
            if problems:
                for problem in problems:
                    print(f"DRIFT {problem}", file=sys.stderr)
                print(
                    f"FAIL  {repo}@{branch}: {len(problems)} governance "
                    "divergence(s); re-run without --verify to apply",
                    file=sys.stderr,
                )
                return 1
            print(f"OK    {repo}@{branch}: governance matches the desired state")
            return 0
        calls = plan(
            repo,
            branch,
            contexts,
            args.approvals,
            args.enforce_admins,
            args.strict,
            args.fork_pr_approval,
        )
        if args.dry_run:
            print(_render_dry_run(repo, branch, calls))
            return 0
        execute(run, calls)
    except GhError as exc:
        print(f"ERROR {exc.message}\n      fix: {exc.fix}", file=sys.stderr)
        return 1

    print(
        _success_line(
            repo,
            branch,
            contexts,
            args.enforce_admins,
            args.strict,
            args.fork_pr_approval,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
