# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Apply the create-repo merge model and branch protection to a GitHub repo.

Usage:
    uv run --script scripts/setup_github_governance.py [CHECK ...] \
        [--repo-dir DIR] [--repo OWNER/NAME] [--branch NAME] [--approvals N] \
        [--fork-pr-approval POLICY] [--visibility VISIBILITY] [--enforce-admins] \
        [--dry-run] [--verify]
    uv run --script scripts/setup_github_governance.py --add-required-check CHECK \
        [--add-required-check CHECK ...] [--repo OWNER/NAME] [--branch NAME] \
        [--dry-run] [--verify]

The positional ``CHECK`` arguments are the status-check contexts that must be
green before a PR can merge. With none given, they are the ``required_checks``
of the repository's ``.github/governance.json`` (under ``--repo-dir``, default
``.``), which ``compose_repo_plan.py --ci-cd`` writes: ``lint``, one ``test-<platform>`` job
per supported platform and ``budgets``, the CI shape references/ci.md states. Given explicitly, list *every* gating check by name; a check that is not
required is only advisory, and a red one can still be merged past. The llmlint
tier (a mandated blocking PR check) must be among them — a context named
llmlint, or a ``lint`` context whose job in ``.github/workflows/ci.yml`` runs
``just lint-llm-diff`` — else auto-merge lands past a red llmlint run; pass
--allow-missing-llmlint only for a repo that genuinely has no llmlint tier.

What it sets (the model the create-repo skill prescribes — see references/ci.md):
  * Merge model: squash-merge only (merge commits and rebase-merging disabled),
    auto-merge enabled, head branches deleted on merge, and the squash subject
    taken from the PR title / body from the PR description.
  * Branch protection on the default branch: the required checks above, linear
    history, conversation resolution, no force-pushes, no branch deletion.
    Admins can override by default (``enforce_admins: false``); pass
    --enforce-admins to bind them too. Checks are non-strict by default — a PR
    need not be rebased onto the latest default branch before merging, which
    avoids the re-update-and-re-run-CI churn every time the base moves, at the
    cost of not catching a semantic conflict between two independently green
    PRs (CI does not run on push to main to catch it later either). Up-to-date
    branches are an explicit opt-in only: --strict, or ``"strict": true`` in
    ``.github/governance.json``.
  * Fork-PR workflow approval: requires a maintainer to approve before a fork's
    CI runs (default: all external contributors). Credential-gated checks (the
    live/llmlint tiers) fail fast without their secret, so fork PRs must not
    auto-run them unreviewed. Tune the class of contributor with
    --fork-pr-approval. GitHub offers this setting on public repositories only,
    so on a private or internal one it is not applicable: the script reads the
    repository's visibility once, neither applies nor verifies the setting there,
    and reports it as ``fork-PR approval: not applicable (private)`` (or
    ``(internal)``). Every other setting is still applied and verified.

This sets the *full desired state* idempotently: re-running applies the same
config, and the branch-protection PUT replaces any existing protection on the
branch. Use --dry-run to print exactly what it would do without touching the
repo (pass --repo and --branch to preview fully offline). An offline preview
cannot read the visibility, so it plans for a public repository unless
--visibility private|internal says otherwise; whenever the script does talk to
GitHub it reads the live visibility, and refuses a --visibility that disagrees.

Use --verify to *read back* the live state and report where it diverges from the
desired one — branch protection (are all gating checks required? force-push and
deletion blocked?), the merge model (squash-only, auto-merge, delete-on-merge),
and, on a public repository, the fork-PR approval policy. This is the piece the filesystem baseline checker
structurally cannot see (repo-side settings, not files), so a repo can pass every
file-level gate while its gating checks are not actually required to merge.
--verify writes nothing and exits non-zero on any divergence, so it belongs in a
creation-verification step or a periodic drift check.

``--add-required-check CHECK`` (repeatable, exclusive with the positional ``CHECK``
arguments and every other profile flag) is the additive mode: it sets no profile
and reads no governance record. It reads the live classic protection, adds only the
named contexts that are missing through the required-status-checks subresource, so
nothing else can be reset, then reads the protection back and fails if a prior
context, an app binding or any other protection setting moved. A repeated call
writes nothing. --dry-run prints the one call it would make and --verify reports
whether each context is already required; both read and neither writes. Classic
protection is the only model it writes: a branch with no classic required checks,
or any ruleset applying to it, or a read that fails, is refused by name before any
write.

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
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple, NewType

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


# Repository visibilities, as the ``repos/{repo}`` GET spells them. GitHub offers
# fork-PR workflow approval on public repositories only — a private or internal
# repository refuses the PUT ("Fork PR approval is not allowed for private
# repositories") — so it is applied and verified only where it exists.
# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] Issue #117 explicitly names these three supported visibilities; an upstream GitHub schema drift gate is outside this task, per the manager ruling.
class Visibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    INTERNAL = "internal"


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


VISIBILITIES = tuple(Visibility)
DEFAULT_VISIBILITY = Visibility.PUBLIC


class Verification(NamedTuple):
    problems: list[str]
    visibility: Visibility


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


# The governance record ``compose_repo_plan.py --ci-cd`` writes, and the CI workflow
# whose `lint` job carries the llmlint tier in the composed shape.
GOVERNANCE_FILE = ".github/governance.json"
CI_WORKFLOW = ".github/workflows/ci.yml"
LLMLINT_DIFF_RE = re.compile(r"\bjust\s+lint-llm-diff\b")


class Governance(NamedTuple):
    """A repository's recorded governance: its required checks and strictness."""

    required_checks: list[str]
    strict: bool


def read_governance(repo_dir: Path) -> Governance | None:
    """``.github/governance.json`` under ``repo_dir``; None when there is none."""
    path = repo_dir / GOVERNANCE_FILE
    if not path.is_file():
        return None
    fix = (
        f"make {GOVERNANCE_FILE} an object with `required_checks` (a list of check "
        "names) and an optional boolean `strict`, as compose_repo_plan.py --ci-cd "
        "writes it"
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GhError(f"{path} is not valid JSON ({exc.msg})", fix) from exc
    checks = data.get("required_checks") if isinstance(data, dict) else None
    strict = data.get("strict", False) if isinstance(data, dict) else None
    if (
        not isinstance(checks, list)
        or not all(isinstance(c, str) for c in checks)
        or not isinstance(strict, bool)
    ):
        raise GhError(f"{path} is not a governance record", fix)
    return Governance(checks, strict)


def lint_job_runs_llmlint(repo_dir: Path) -> bool:
    """Whether the composed CI's `lint` job runs the diff-scoped llmlint tier."""
    path = repo_dir / CI_WORKFLOW
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index("  lint:")
    except ValueError:
        return False
    body = []
    for line in lines[start + 1 :]:
        if (
            line.startswith("  ")
            and not line.startswith("   ")
            and line.rstrip().endswith(":")
        ):
            break
        body.append(line.split("#", 1)[0])
    return bool(LLMLINT_DIFF_RE.search("\n".join(body)))


def require_llmlint(
    contexts: Sequence[str], *, allow_missing: bool, repo_dir: Path | None = None
) -> None:
    """Fail unless an llmlint check is among the required contexts.

    The create-repo skill mandates the llmlint LLM-judge tier as a blocking PR
    check: it runs outside ``just check`` (non-deterministic, credentialed), so
    the only thing making a red llmlint run block a merge is its presence in the
    required status checks. Leave it out and auto-merge lands past it — the exact
    hole this guard closes. ``allow_missing`` (the --allow-missing-llmlint flag)
    waives it for the rare repo with no llmlint tier. In the composed CI shape
    the tier is a step of the `lint` job, so a required `lint` whose job in
    ``repo_dir``'s ci.yml runs ``just lint-llm-diff`` satisfies it too.
    """
    if allow_missing or any(LLMLINT_CONTEXT_RE.search(c) for c in contexts):
        return
    if repo_dir is not None and "lint" in contexts and lint_job_runs_llmlint(repo_dir):
        return
    raise GhError(
        "no llmlint check among the required status checks",
        "require the context of the job that runs llmlint (`lint` in the composed "
        "CI shape, whose job runs `just lint-llm-diff`) so a red llmlint run blocks merge — the LLM-judge tier is a mandated blocking "
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
    visibility: Visibility = DEFAULT_VISIBILITY,
) -> list[GhCall]:
    """Build the ordered list of mutating calls.

    Merge model, then branch protection, then — on a public repository, the only
    kind that has the setting — the fork-PR workflow approval policy (so a
    maintainer gates fork CI before credential-requiring checks run).
    """
    calls = [
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
    ]
    if fork_approval_applies(visibility):
        calls.append(
            GhCall(
                "PUT",
                f"repos/{repo}/actions/permissions/fork-pr-contributor-approval",
                fork_pr_approval_payload(fork_pr_approval),
                f"fork PR workflow approval ({fork_pr_approval})",
            )
        )
    return calls


def fork_approval_applies(visibility: Visibility) -> bool:
    """Whether the fork-PR approval setting exists for this visibility."""
    return visibility == Visibility.PUBLIC


def fork_approval_label(visibility: Visibility, policy: str) -> str:
    """How the fork-PR approval outcome reads in the success and dry-run lines."""
    if fork_approval_applies(visibility):
        return policy
    return f"not applicable ({visibility})"


def visibility_from_api(data: object) -> Visibility:
    """Read the visibility from a ``repos/{repo}`` GET response.

    ``visibility`` is the field that tells internal from private; the older
    ``private`` boolean is accepted as a fallback. A response carrying neither is
    an error rather than a guess — guessing public would send a PUT GitHub then
    refuses, guessing private would skip a setting that exists.
    """
    match data:
        case {"visibility": str(visibility)} if visibility.lower() in VISIBILITIES:
            return Visibility(visibility.lower())
        case {"private": bool(private)}:
            return Visibility.PRIVATE if private else Visibility.PUBLIC
    raise GhError(
        "could not read the repository's visibility from `gh api repos/{repo}`",
        "check `gh api repos/OWNER/NAME --jq .visibility` prints public, private "
        "or internal",
    )


def read_visibility(run: Runner, repo: str) -> Visibility:
    """Read the repository's visibility with one ``repos/{repo}`` GET."""
    return visibility_from_api(
        _get_json(
            run,
            f"repos/{repo}",
            "check you can read the repo (`gh auth status`) and --repo is correct",
        )
    )


def check_visibility_flag(given: Visibility | None, live: Visibility) -> None:
    """Refuse a --visibility that disagrees with the live repository."""
    if given is not None and given != live:
        raise GhError(
            f"--visibility {given} but the repository is {live}",
            f"drop --visibility (it is read from GitHub) or pass --visibility {live}",
        )


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
) -> Verification:
    """Read the live governance state; return its divergences and the visibility.

    Reads (never writes) the three settings this script configures — merge model,
    branch protection, fork-PR approval — parses each GitHub response into a typed
    model at the boundary, and diffs it against the desired state. The visibility
    comes from the same settings read; on a private or internal repository the
    fork-PR approval read is skipped, since the setting does not exist there. An
    empty list means the repo matches; the caller reports and sets the exit code.
    """
    problems: list[str] = []

    settings = _get_json(
        run,
        f"repos/{repo}",
        "check you can read the repo (`gh auth status`) and --repo is correct",
    )
    visibility = visibility_from_api(settings)
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

    if fork_approval_applies(visibility):
        fork = _get_json(
            run,
            f"repos/{repo}/actions/permissions/fork-pr-contributor-approval",
            "check admin access to Actions settings",
        )
        problems += verify_fork_approval(
            LiveForkApproval.from_api(fork), fork_pr_approval
        )

    return Verification(problems, visibility)


def _is_not_found(stderr: str) -> bool:
    """True when a ``gh api`` failure is a genuine 404 (the resource is absent).

    Distinguished from auth/network/other API errors so ``allow_missing`` returns
    None only for a real absence — anything else is surfaced with its actual error.
    """
    low = stderr.lower()
    return "404" in low or "not found" in low


def _get_json(
    run: Runner, path: str, fix: str, *, allow_missing: bool = False
) -> dict | list | None:
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


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] `gh` is this script's GitHub boundary and no gate can reach GitHub offline; the two answers are read only for this mode's comparison, and one that drifts from the shape below is refused rather than read, so drift fails closed.
# A required check's name, and the GitHub App that must report it.
CheckContext = NewType("CheckContext", str)
AppId = NewType("AppId", int)
# Each required context to its app binding; None allows any source.
Bindings = dict[CheckContext, AppId | None]


class ClassicChecks(NamedTuple):
    """Classic protection as the additive mode compares it around its one write."""

    bindings: Bindings
    # The whole protection GET but those contexts, which the write must not move.
    policy: dict


def _is_check(check: object) -> bool:
    """One entry of `checks`: a named context, bound to an app id or to any source."""
    if not isinstance(check, dict) or not isinstance(check.get("context"), str):
        return False
    app = check.get("app_id")
    return app is None or (isinstance(app, int) and not isinstance(app, bool))


def _check_bindings(required_status_checks: dict, fix: str) -> Bindings:
    """Each required context to its app binding, from both shapes GitHub lists.

    Comparison needs the whole set, so an answer missing either list, or holding an
    entry that cannot be read, is refused rather than filtered down to what can.
    """
    contexts = required_status_checks.get("contexts")
    checks = required_status_checks.get("checks")
    if (
        not isinstance(contexts, list)
        or not all(isinstance(context, str) for context in contexts)
        or not isinstance(checks, list)
        or not all(_is_check(check) for check in checks)
    ):
        raise GhError(
            "GitHub answered required_status_checks without a readable `contexts` "
            f"and `checks` list: {json.dumps(required_status_checks)}",
            fix,
        )
    bindings: Bindings = dict.fromkeys(CheckContext(name) for name in contexts)
    for check in checks:
        app = check.get("app_id")
        bindings[CheckContext(check["context"])] = None if app is None else AppId(app)
    return bindings


def _classic_checks(protection: dict | list | None, fix: str) -> ClassicChecks | None:
    """The protection GET split for comparison; None when it requires no checks."""
    if not isinstance(protection, dict):
        raise GhError(
            f"GitHub answered branch protection that is no object: {json.dumps(protection)}",
            fix,
        )
    required = protection.get("required_status_checks")
    if required is None:
        return None
    if not isinstance(required, dict):
        raise GhError(
            f"GitHub answered required_status_checks that is no object: {json.dumps(required)}",
            fix,
        )
    required_rest = {
        key: value
        for key, value in required.items()
        if key not in ("contexts", "checks")
    }
    return ClassicChecks(
        _check_bindings(required, fix),
        {**protection, "required_status_checks": required_rest},
    )


def _required_checks_path(repo: str, branch: str) -> str:
    return f"repos/{repo}/branches/{branch}/protection/required_status_checks/contexts"


def _ruleset_names(rules: list) -> list[str]:
    """Each ruleset applying to a branch, named with the rule types it applies."""
    types: dict[str, list[str]] = {}
    for rule in rules:
        rule = rule if isinstance(rule, dict) else {}
        name = (
            f"ruleset {rule.get('ruleset_id', '?')} "
            f"({rule.get('ruleset_source_type', 'unknown source')} "
            f"{rule.get('ruleset_source', '?')})"
        )
        types.setdefault(name, []).append(str(rule.get("type", "unknown rule")))
    return [f"{name}: {', '.join(kinds)}" for name, kinds in types.items()]


def read_classic_checks(run: Runner, repo: str, branch: str) -> ClassicChecks:
    """The branch's classic required checks, or a refusal of any other model.

    The rules endpoint answers for every ruleset that applies, inherited ones
    included, so a model this mode does not write is named before anything is sent.
    Its first page is enough: one applicable rule refuses.
    """
    fix = "check you can read the repo's protection (`gh auth status`, admin rights) and --repo/--branch; nothing was written"
    rules = _get_json(run, f"repos/{repo}/rules/branches/{branch}", fix)
    if not isinstance(rules, list):
        raise GhError(
            f"`gh api repos/{repo}/rules/branches/{branch}` did not answer a list of rules",
            fix,
        )
    protection = _get_json(
        run, f"repos/{repo}/branches/{branch}/protection", fix, allow_missing=True
    )
    if rules:
        model = "rulesets-only" if protection is None else "classic plus rulesets"
        raise GhError(
            f"{repo}@{branch} is governed by {model} ({'; '.join(_ruleset_names(rules))}), "
            "which --add-required-check does not write",
            "add the check through whoever owns that ruleset, or remove the ruleset; "
            "nothing was written",
        )
    if protection is None:
        raise GhError(
            f"{repo}@{branch} has no classic branch protection and no ruleset, or this "
            "token cannot read its protection",
            "apply the full profile first (this script with positional checks); "
            "nothing was written",
        )
    classic = _classic_checks(protection, fix)
    if classic is None:
        raise GhError(
            f"{repo}@{branch} has classic protection that requires no status checks, "
            "so there is no set to add to",
            "turn required status checks on deliberately, since that also decides "
            "strictness; nothing was written",
        )
    return classic


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def _show_checks(bindings: Bindings) -> str:
    """A required set as output: each context, with its app binding when it has one."""
    shown = [
        name if app is None else f"{name} (app {app})"
        for name, app in sorted(bindings.items())
    ]
    return f"[{', '.join(shown)}]"


def readback_problems(
    before: ClassicChecks, after: ClassicChecks | None, added: Sequence[CheckContext]
) -> list[str]:
    """Everything the read-back shows moved, beyond the contexts added."""
    if after is None:
        return ["required status checks are no longer enabled"]
    problems = []
    for name, app in before.bindings.items():
        if name not in after.bindings:
            problems.append(f"dropped {name}")
        elif after.bindings[name] != app:
            problems.append(f"{name} is bound to {after.bindings[name]}, was {app}")
    problems += [f"did not add {name}" for name in added if name not in after.bindings]
    problems += [
        f"unexpected new context {name}"
        for name in after.bindings
        if name not in before.bindings and name not in added
    ]
    for key in sorted(before.policy.keys() | after.policy.keys()):
        if before.policy.get(key) != after.policy.get(key):
            problems.append(f"protection setting {key} changed")
    return problems


def add_required_checks(run: Runner, args: argparse.Namespace) -> int:
    """The additive mode: add only the missing contexts, then prove nothing else moved."""
    wanted = [
        CheckContext(name) for name in normalize_contexts(args.add_required_check)
    ]
    repo = resolve_repo(run, args.repo)
    branch = resolve_branch(run, repo, args.branch)
    target = f"{repo}@{branch}"
    before = read_classic_checks(run, repo, branch)
    missing = [name for name in wanted if name not in before.bindings]
    if args.verify:
        if missing:
            print(
                f"DRIFT branch protection: required checks missing {missing} "
                f"(has {_show_checks(before.bindings)})",
                file=sys.stderr,
            )
            print(
                f"FAIL  {target}: {len(missing)} check(s) not required; re-run "
                "without --verify to add them",
                file=sys.stderr,
            )
            return 1
        print(
            f"OK    {target}: {', '.join(wanted)} required; required checks "
            f"{_show_checks(before.bindings)}"
        )
        return 0
    if not missing:
        prefix = "DRY-RUN" if args.dry_run else "OK   "
        print(
            f"{prefix} {target}: {', '.join(wanted)} already required, nothing to "
            f"write; required checks {_show_checks(before.bindings)}"
        )
        return 0
    call = GhCall(
        "POST",
        _required_checks_path(repo, branch),
        {"contexts": missing},
        f"required checks {', '.join(missing)} on {branch}",
    )
    if args.dry_run:
        print(
            f"DRY-RUN would add {', '.join(missing)} to {target}, whose required "
            f"checks are {_show_checks(before.bindings)}:\n"
            f"  {call.method} {call.path}\n    {call.stdin}"
        )
        return 0
    execute(run, [call])
    fix = (
        f"compare {target}'s protection with the set before this write, "
        f"{_show_checks(before.bindings)}, and restore what moved by hand; this "
        "script made only the one POST"
    )
    protection = _get_json(run, f"repos/{repo}/branches/{branch}/protection", fix)
    after = _classic_checks(protection, fix)
    problems = readback_problems(before, after, missing)
    if problems:
        raise GhError(
            f"{target} read back wrong after adding {', '.join(missing)}: "
            f"{'; '.join(problems)}",
            fix,
        )
    print(
        f"OK    {target}: added {', '.join(missing)}; required checks before "
        f"{_show_checks(before.bindings)}, after {_show_checks(after.bindings)}; "
        "every other protection setting unchanged"
    )
    return 0


def _render_dry_run(
    repo: str,
    branch: str,
    calls: Sequence[GhCall],
    visibility: Visibility,
    contexts: Sequence[str] = (),
    strict: bool = False,
) -> str:
    strictness = "strict" if strict else "non-strict"
    lines = [
        f"DRY-RUN would configure {repo}@{branch}:",
        f"  required checks: {', '.join(contexts)} ({strictness})",
    ]
    for call in calls:
        lines.append(f"  {call.method} {call.path}  # {call.summary}")
        for body_line in json.dumps(call.body, indent=2).splitlines():
            lines.append(f"    {body_line}")
    if not fork_approval_applies(visibility):
        lines.append(
            f"  fork-PR approval: {fork_approval_label(visibility, '')} — not set"
        )
    return "\n".join(lines)


def _success_line(
    repo: str,
    branch: str,
    contexts: Sequence[str],
    enforce_admins: bool,
    strict: bool,
    fork_pr_approval: str,
    visibility: Visibility = DEFAULT_VISIBILITY,
) -> str:
    admins = "admins enforced" if enforce_admins else "admins can override"
    strictness = "strict" if strict else "non-strict"
    checks = ", ".join(contexts)
    return (
        f"OK    {repo}@{branch}: squash-only + auto-merge + delete-on-merge; "
        f"{len(contexts)} required check(s) [{checks}], {strictness}; {admins}; "
        f"fork-PR approval: {fork_approval_label(visibility, fork_pr_approval)}"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "checks",
        nargs="*",
        metavar="CHECK",
        help="required status-check context(s); list every gating check (default: "
        f"the required_checks of --repo-dir's {GOVERNANCE_FILE})",
    )
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path("."),
        help=f"the checkout whose {GOVERNANCE_FILE} and {CI_WORKFLOW} are read "
        "(default: .)",
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
        help="require branches be up to date before merging — an explicit opt-in "
        f"(default: off, or {GOVERNANCE_FILE}'s `strict`, to avoid the "
        "re-sync/re-run-CI churn each time the base branch moves)",
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
        "--visibility",
        type=Visibility,
        choices=VISIBILITIES,
        help="the repository's visibility, for an offline --dry-run that cannot "
        f"read it (default there: {DEFAULT_VISIBILITY}); otherwise it is read from "
        "GitHub and a disagreeing value is refused. Fork-PR approval exists only "
        "on public repositories, so it is skipped on private and internal ones",
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
        "desired one (branch protection, merge model, and fork-PR approval on a "
        "public repository) instead of "
        "applying it; exits non-zero on any divergence",
    )
    parser.add_argument(
        "--add-required-check",
        action="append",
        default=[],
        metavar="CHECK",
        help="additive mode (repeatable): add this context to the live required "
        "checks, preserving every other context, app binding and setting; replaces "
        "the positional CHECKs and takes only --repo, --branch, --dry-run and "
        "--verify. Refuses, before any write, a branch not governed by classic "
        "protection alone",
    )
    args = parser.parse_args(argv)
    if args.add_required_check:
        if args.checks:
            parser.error(
                "--add-required-check adds to the live set and positional CHECKs "
                "replace it; pass one or the other"
            )
        profile_flags = {
            "--repo-dir": args.repo_dir != Path("."),
            "--approvals": args.approvals != 0,
            "--enforce-admins": args.enforce_admins,
            "--allow-missing-llmlint": args.allow_missing_llmlint,
            "--strict": args.strict,
            "--fork-pr-approval": args.fork_pr_approval != DEFAULT_FORK_PR_APPROVAL,
            "--visibility": args.visibility is not None,
        }
        given = [flag for flag, set_ in profile_flags.items() if set_]
        if given:
            parser.error(
                f"--add-required-check preserves the live settings, so {', '.join(given)} "
                "would be ignored; drop them, or use the full profile"
            )
    return args


def main(argv: list[str], run: Runner | None = None) -> int:
    args = parse_args(argv)

    # With a real ``gh``, ensure it is installed before doing anything. A fully
    # offline dry-run (--repo and --branch both given) needs no ``gh`` at all.
    offline_dry_run = bool(
        args.dry_run
        and args.repo
        and args.branch
        and not args.verify
        and not args.add_required_check
    )
    if run is None:
        if not offline_dry_run and shutil.which("gh") is None:
            print(
                "ERROR gh CLI not found\n"
                "      fix: install GitHub CLI (https://cli.github.com) and run "
                "`gh auth login`",
                file=sys.stderr,
            )
            return 1
        run = _subprocess_run

    try:
        if args.add_required_check:
            return add_required_checks(run, args)
        recorded = read_governance(args.repo_dir)
        checks = args.checks or (recorded.required_checks if recorded else [])
        if not checks:
            raise GhError(
                f"no required status checks given and no {GOVERNANCE_FILE} under "
                f"{args.repo_dir}",
                "pass the check contexts, or run from (or --repo-dir) a checkout "
                "compose_repo_plan.py --ci-cd wrote its governance record into",
            )
        contexts = normalize_contexts(checks)
        args.strict = args.strict or bool(recorded and recorded.strict)
        require_llmlint(
            contexts,
            allow_missing=args.allow_missing_llmlint,
            repo_dir=args.repo_dir,
        )
        repo = resolve_repo(run, args.repo)
        branch = resolve_branch(run, repo, args.branch)
        if args.verify:
            problems, visibility = verify(
                run,
                repo,
                branch,
                contexts,
                args.approvals,
                args.enforce_admins,
                args.strict,
                args.fork_pr_approval,
            )
            check_visibility_flag(args.visibility, visibility)
            if problems:
                for problem in problems:
                    print(f"DRIFT {problem}", file=sys.stderr)
                print(
                    f"FAIL  {repo}@{branch}: {len(problems)} governance "
                    "divergence(s); re-run without --verify to apply",
                    file=sys.stderr,
                )
                return 1
            print(
                f"OK    {repo}@{branch}: governance matches the desired state; "
                "fork-PR approval: "
                f"{fork_approval_label(visibility, args.fork_pr_approval)}"
            )
            return 0
        # Read the visibility once, before planning: it decides whether the
        # fork-PR approval call exists. An offline dry run cannot read it.
        if offline_dry_run:
            visibility = args.visibility or DEFAULT_VISIBILITY
        else:
            visibility = read_visibility(run, repo)
            check_visibility_flag(args.visibility, visibility)
        calls = plan(
            repo,
            branch,
            contexts,
            args.approvals,
            args.enforce_admins,
            args.strict,
            args.fork_pr_approval,
            visibility,
        )
        if args.dry_run:
            print(
                _render_dry_run(repo, branch, calls, visibility, contexts, args.strict)
            )
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
            visibility,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
