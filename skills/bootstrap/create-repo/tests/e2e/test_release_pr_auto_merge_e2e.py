"""The baseline checker's release-PR auto-merge check, driven as an author runs it.

Each case builds an otherwise-conformant repository (``make_repo``), adds a
release workflow in one of the shapes the fleet carries, and runs the real PEP
723 script as a ``uv run --script`` subprocess, reading its printed findings and
exit status. A release pull request nobody arms auto-merge on is a release a
person has to merge, so it fails the audit unless AGENTS.md opts out on purpose.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_check_repo_baseline import CONFORMANT_AGENTS, FULL_JUSTFILE, SCRIPT, make_repo

# onevcs's `.github/workflows/release-plz.yml`, trimmed to the release job: the
# release PR opened through the CLI, then armed in the step after it.
OPEN_RELEASE_PR = """\
      - name: Open or update the release PR
        env:
          GITHUB_TOKEN: ${{ secrets.RELEASE_PLZ_TOKEN }}
        run: |
          release-plz release-pr --git-token "$GITHUB_TOKEN" -o json \\
            > "$RUNNER_TEMP/release-pr.json"
"""

ONEVCS_ARMING_STEP = """\
      - name: Auto-merge the release PR when the gate is green
        env:
          GITHUB_TOKEN: ${{ secrets.RELEASE_PLZ_TOKEN }}
        run: |
          pr=$(gh pr list --repo "$GITHUB_REPOSITORY" --state open --json number,headRefName \\
                 --jq 'map(select(.headRefName | startswith("release-plz-"))) | .[0].number // empty')
          if [ -n "$pr" ]; then
            echo "Enabling auto-merge on release PR #$pr"
            gh pr merge "$pr" --repo "$GITHUB_REPOSITORY" --auto --squash --delete-branch \\
              || echo "::warning::could not enable auto-merge on #$pr"
          fi
"""

GRAPHQL_ARMING_STEP = """\
      - name: Arm auto-merge through the API
        env:
          GH_TOKEN: ${{ secrets.RELEASE_PLZ_TOKEN }}
        run: >-
          gh api graphql -f query='mutation($id: ID!) {
          enablePullRequestAutoMerge(input: {pullRequestId: $id, mergeMethod: SQUASH})
          { clientMutationId } }' -f id="$PR_NODE_ID"
"""

# A merge that is not queued: it merges now or fails, and never waits on checks.
MERGE_WITHOUT_AUTO_STEP = """\
      - name: Merge the release PR
        env:
          GITHUB_TOKEN: ${{ secrets.RELEASE_PLZ_TOKEN }}
        run: gh pr merge "$pr" --squash --delete-branch  # --auto left off
"""

# onejudge's shape: release-plz's action opens the PR, the arming step follows.
ACTION_RELEASE_PR = """\
      - uses: release-plz/action@v0.5
        with:
          command: release-pr
        env:
          GITHUB_TOKEN: ${{ secrets.RELEASE_PLZ_TOKEN }}
"""

# The same action cutting the release only — it opens no pull request.
ACTION_RELEASE = """\
      - uses: release-plz/action@v0.5
        with:
          command: release
        env:
          GITHUB_TOKEN: ${{ secrets.RELEASE_PLZ_TOKEN }}
"""


def workflow(*steps: str) -> str:
    return (
        "name: Release-plz\n"
        "on:\n  push:\n    branches: [main]\n"
        "jobs:\n"
        "  release-plz:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n" + "".join(steps)
    )


def release_repo(
    root: Path,
    *steps: str,
    agents: str = CONFORMANT_AGENTS,
    justfile: str = FULL_JUSTFILE,
) -> Path:
    root.mkdir()
    repo = make_repo(root, composition=agents, justfile=justfile)
    (repo / ".github" / "workflows" / "release-plz.yml").write_text(
        workflow(*steps), encoding="utf-8"
    )
    return repo


def run_checker(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), str(repo)],
        capture_output=True,
        text=True,
    )


def release_pr_errors(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [
        line
        for line in result.stderr.splitlines()
        if line.startswith("ERROR") and "release PR" in line
    ]


def assert_flagged(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 1, result.stdout + result.stderr
    assert release_pr_errors(result) == [
        "ERROR .github/workflows/release-plz.yml opens a release PR but never arms "
        "auto-merge on it, so a person has to merge every release"
    ], result.stderr
    assert "references/releasing.md" in result.stderr


def assert_passes(result: subprocess.CompletedProcess[str]) -> None:
    assert release_pr_errors(result) == [], result.stderr
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_release_pr_nobody_arms_fails_the_audit(tmp_path):
    assert_flagged(run_checker(release_repo(tmp_path / "repo", OPEN_RELEASE_PR)))


def test_a_merge_without_auto_is_not_arming(tmp_path):
    repo = release_repo(tmp_path / "repo", OPEN_RELEASE_PR, MERGE_WITHOUT_AUTO_STEP)
    assert_flagged(run_checker(repo))


@pytest.mark.parametrize(
    "arming", [ONEVCS_ARMING_STEP, GRAPHQL_ARMING_STEP], ids=["gh-pr-merge", "graphql"]
)
def test_arming_inline_passes(tmp_path, arming):
    assert_passes(run_checker(release_repo(tmp_path / "repo", OPEN_RELEASE_PR, arming)))


ARMING_RECIPES = {
    "gh-pr-merge": (
        "release-pr-arm pr:\n"
        '    gh pr merge {{pr}} --repo "$GITHUB_REPOSITORY" \\\n'
        "        --auto --squash --delete-branch\n"
    ),
    # Reached through a dependency of the recipe the workflow names.
    "graphql": (
        "release-pr-arm: arm-graphql\n"
        "arm-graphql:\n"
        "    gh api graphql -f query='mutation { enablePullRequestAutoMerge("
        "input: {pullRequestId: \"{{env_var('PR_ID')}}\"}) { clientMutationId } }'\n"
    ),
}

RECIPE_STEP = """\
      - name: Arm auto-merge on the release PR
        env:
          GH_TOKEN: ${{ secrets.RELEASE_PLZ_TOKEN }}
        run: just release-pr-arm "$PR"
"""


@pytest.mark.parametrize("recipe", ARMING_RECIPES.values(), ids=ARMING_RECIPES)
def test_arming_in_a_just_recipe_the_workflow_invokes_passes(tmp_path, recipe):
    repo = release_repo(
        tmp_path / "repo",
        OPEN_RELEASE_PR,
        RECIPE_STEP,
        justfile=FULL_JUSTFILE + "\n" + recipe,
    )
    assert_passes(run_checker(repo))


def test_a_recipe_that_arms_in_a_script_it_runs_is_flagged(tmp_path):
    # printobserver's shape: the arming lives in Python behind the recipe, which a
    # text check cannot read — an expected audit finding, not a special case.
    repo = release_repo(
        tmp_path / "repo",
        OPEN_RELEASE_PR,
        RECIPE_STEP,
        justfile=FULL_JUSTFILE
        + "\nrelease-pr-arm pr:\n    uv run scripts/arm.py {{pr}}\n",
    )
    assert_flagged(run_checker(repo))


def test_the_opt_out_phrase_in_agents_md_keeps_a_human_merge(tmp_path):
    # Mixed case, broken across a line, with extra spaces: still the phrase.
    agents = CONFORMANT_AGENTS + (
        "\n## Releases\n\nWe review every changelog by hand: The Release   PR\n"
        "is a  HUMAN checkpoint here.\n"
    )
    repo = release_repo(tmp_path / "repo", OPEN_RELEASE_PR, agents=agents)
    assert_passes(run_checker(repo))


def test_without_the_opt_out_the_same_repo_still_fails(tmp_path):
    agents = CONFORMANT_AGENTS + "\nA person reviews the release PR before it ships.\n"
    repo = release_repo(tmp_path / "repo", OPEN_RELEASE_PR, agents=agents)
    assert_flagged(run_checker(repo))


def test_release_plz_action_opening_a_release_pr_is_flagged_until_armed(tmp_path):
    assert_flagged(run_checker(release_repo(tmp_path / "bare", ACTION_RELEASE_PR)))
    armed = release_repo(tmp_path / "armed", ACTION_RELEASE_PR, ONEVCS_ARMING_STEP)
    assert_passes(run_checker(armed))


def test_workflows_opening_no_release_pr_get_no_finding(tmp_path):
    # The conformant repo's own workflows, plus a release-plz job that only tags.
    repo = release_repo(tmp_path / "repo", ACTION_RELEASE)
    result = run_checker(repo)
    assert_passes(result)
    assert "release PR" not in result.stdout + result.stderr
