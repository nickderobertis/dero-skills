"""Drift gate: this repo's root ``oneharness.toml`` must stay identical, in
parsed content, to the ``create-repo`` skill's ``oneharness.toml.template``.

The repo *hosts* the template and also *dogfoods* it (its own root config is a
hand-maintained copy with a repo-specific header comment). Those are two copies
of one contract — the fallback harness list and per-harness models — so without a
gate they can drift: an edit to the template's models would silently leave the
dogfooded config behind (or vice versa). tomllib ignores comments, so comparing
the parsed data pins the *values* while leaving each file free to keep its own
prose. If this fails, reconcile the two so their TOML content matches.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT_CONFIG = REPO_ROOT / "oneharness.toml"
TEMPLATE = (
    REPO_ROOT
    / "skills"
    / "bootstrap"
    / "create-repo"
    / "assets"
    / "oneharness.toml.template"
)


def _load(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def test_root_oneharness_matches_template_contract() -> None:
    root = _load(ROOT_CONFIG)
    template = _load(TEMPLATE)
    assert root == template, (
        "oneharness.toml drifted from assets/oneharness.toml.template — reconcile "
        "the fallback harness list / models so their parsed content matches"
    )


def test_contract_is_fallback_codex_then_claude_code() -> None:
    # Guard the specific contract both files must encode, so a change that edits
    # both in lockstep to something wrong still trips here.
    template = _load(TEMPLATE)
    assert template["run_mode"] == "fallback"
    assert template["harnesses"] == ["codex", "claude-code"]
    assert template["harness"]["codex"]["model"] == "gpt-5.5"
    assert template["harness"]["claude-code"]["model"] == "claude-opus-4-8"
    assert template["harness"]["claude-code"]["env"]["IS_SANDBOX"] == "1"
