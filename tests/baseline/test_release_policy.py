"""AGENTS.md's bump policy, held to the release configuration that enforces it.

`references/releasing.md` has the policy written in AGENTS.md, while
`.releaserc.json` is what semantic-release actually runs. This reads each
mapping AGENTS.md states, writes the commit it describes, and asks the real
commit-analyzer — configured from `.releaserc.json`, run through bun from
`node_modules` — what release that commit cuts. A policy line that drifts from
the configuration fails here, naming the commit.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYZE = Path(__file__).resolve().parent / "release_type.mjs"
BULLET = "- **Conventional Commits are required.**"
MAPPING_RE = re.compile(r"((?:`[^`]+`/?)+)(?: footer)? → (major|minor|patch)")


def policy() -> dict[str, str | None]:
    """The commit each AGENTS.md mapping describes, and the release it states."""
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    start = text.index(BULLET)
    end = text.find("\n- ", start + 1)
    bullet = " ".join(text[start:end].split())
    stated: dict[str, str | None] = {}
    for tokens, release in MAPPING_RE.findall(bullet):
        for token in re.findall(r"`([^`]+)`", tokens):
            message = (
                "fix: change\n\nBREAKING CHANGE: it breaks"
                if token == "BREAKING CHANGE:"
                else f"{token} change"
            )
            stated[message] = release
    quiet = bullet[: bullet.index("ship no release")].rsplit(";", 1)[1]
    for kind in re.findall(r"`([a-z]+)`", quiet):
        stated[f"{kind}: change"] = None
    return stated


def analyzed(messages: list[str]) -> dict[str, str | None]:
    result = subprocess.run(
        ["bun", "run", str(ANALYZE), json.dumps(messages)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_bump_policy_in_agents_md_is_what_semantic_release_does():
    stated = policy()
    # Every release type, and some types that release nothing, are stated.
    assert {"major", "minor", "patch", None} <= set(stated.values()), stated
    assert analyzed(list(stated)) == stated


def test_a_policy_line_the_configuration_contradicts_is_caught():
    # The gate's failure mode: a bare `!`, which the configured preset does not
    # read, stated as a major.
    assert analyzed(["feat!: change"]) == {"feat!: change": None}
