"""The release-PR opt-out phrase: one contract, held to its one statement.

``references/releasing.md`` defines the phrase a repository writes in its
AGENTS.md to keep a person merging its release PR; every repository recording it
and the baseline checker reading it depend on the two being the same words.
The command-line journeys are in ``e2e/test_release_pr_auto_merge_e2e.py``.
"""

from __future__ import annotations

import re

from test_check_repo_baseline import CONFORMANT_AGENTS, SKILL_DIR, crb, make_repo

RELEASING = SKILL_DIR / "references" / "releasing.md"

# The sentence in releasing.md that states the phrase, wrapped wherever it falls.
DEFINITION_RE = re.compile(r"opt-out\s+phrase\s+`([^`]+)`")


def test_the_checker_reads_the_phrase_releasing_md_defines():
    defined = DEFINITION_RE.findall(RELEASING.read_text(encoding="utf-8"))
    assert defined == [crb.RELEASE_PR_CHECKPOINT_PHRASE]


def test_the_opt_out_is_reported_by_name(tmp_path):
    repo = make_repo(
        tmp_path,
        composition=CONFORMANT_AGENTS + "\nThe release PR is a human checkpoint.\n",
    )
    (repo / ".github" / "workflows" / "release.yml").write_text(
        "jobs:\n  r:\n    steps:\n      - run: release-plz release-pr\n",
        encoding="utf-8",
    )
    [finding] = crb.check_release_pr_auto_merge(repo)
    assert finding.level == "OK"
    assert crb.RELEASE_PR_CHECKPOINT_PHRASE in finding.message
    assert ".github/workflows/release.yml" in finding.message
