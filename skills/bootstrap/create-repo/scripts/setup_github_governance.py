# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Apply the create-repo merge model and branch protection to a GitHub repo.

Usage:
    uv run --script scripts/setup_github_governance.py CHECK [CHECK ...] \
        [--repo OWNER/NAME] [--branch NAME] [--approvals N] \
        [--enforce-admins] [--dry-run]

The positional ``CHECK`` arguments are the status-check contexts that must be
green before a PR can merge. List *every* gating check by name — including the
full-e2e gate job (the one that runs ``just check``); a check that is not
required is only advisory, and a red one can still be merged past.

What it sets (the model the create-repo skill prescribes — see references/ci.md):
  * Merge model: squash-merge only (merge commits and rebase-merging disabled),
    auto-merge enabled, head branches deleted on merge, and the squash subject
    taken from the PR title / body from the PR description.
  * Branch protection on the default branch: the required checks above (strict =
    branch must be up to date), linear history, conversation resolution, no
    force-pushes, no branch deletion. Admins can override by default
    (``enforce_admins: false``); pass --enforce-admins to bind them too.

This sets the *full desired state* idempotently: re-running applies the same
config, and the branch-protection PUT replaces any existing protection on the
branch. Use --dry-run to print exactly what it would do without touching the
repo (pass --repo and --branch to preview fully offline).

Talks to GitHub through the authenticated ``gh`` CLI, so it carries no
dependencies and inherits your existing credentials — it needs admin rights on
the target repo. Output is itself agent context, so it is minimal: a single OK
line on success; on failure, the failing call's error and a concrete next
action. Self-contained via PEP 723 so it runs in any consuming repo with
``uv run --script``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# Allowed GitHub values that make the squash commit subject/body follow the PR,
# so the PR title is what lands (and what a Conventional-Commits / release
# pipeline reads).
SQUASH_TITLE = "PR_TITLE"
SQUASH_MESSAGE = "PR_BODY"


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
            "pass at least one check context, e.g. `... check commitlint`",
        )
    return seen


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


def protection_payload(
    contexts: Sequence[str], approvals: int, enforce_admins: bool
) -> dict:
    """The branch-protection PUT body for the default branch."""
    return {
        "required_status_checks": {"strict": True, "contexts": list(contexts)},
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
) -> list[GhCall]:
    """Build the ordered list of mutating calls (merge model, then protection)."""
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
            protection_payload(contexts, approvals, enforce_admins),
            f"branch protection on {branch}",
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


def _render_dry_run(repo: str, branch: str, calls: Sequence[GhCall]) -> str:
    lines = [f"DRY-RUN would configure {repo}@{branch}:"]
    for call in calls:
        lines.append(f"  {call.method} {call.path}  # {call.summary}")
        for body_line in json.dumps(call.body, indent=2).splitlines():
            lines.append(f"    {body_line}")
    return "\n".join(lines)


def _success_line(
    repo: str, branch: str, contexts: Sequence[str], enforce_admins: bool
) -> str:
    admins = "admins enforced" if enforce_admins else "admins can override"
    checks = ", ".join(contexts)
    return (
        f"OK    {repo}@{branch}: squash-only + auto-merge + delete-on-merge; "
        f"{len(contexts)} required check(s) [{checks}]; {admins}"
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
        "--dry-run",
        action="store_true",
        help="print what would change without modifying the repo",
    )
    return parser.parse_args(argv)


def main(argv: list[str], run: Runner | None = None) -> int:
    args = parse_args(argv)

    # With a real ``gh``, ensure it is installed before doing anything. A fully
    # offline dry-run (--repo and --branch both given) needs no ``gh`` at all.
    if run is None:
        needs_gh = not (args.dry_run and args.repo and args.branch)
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
        repo = resolve_repo(run, args.repo)
        branch = resolve_branch(run, repo, args.branch)
        calls = plan(repo, branch, contexts, args.approvals, args.enforce_admins)
        if args.dry_run:
            print(_render_dry_run(repo, branch, calls))
            return 0
        execute(run, calls)
    except GhError as exc:
        print(f"ERROR {exc.message}\n      fix: {exc.fix}", file=sys.stderr)
        return 1

    print(_success_line(repo, branch, contexts, args.enforce_admins))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
