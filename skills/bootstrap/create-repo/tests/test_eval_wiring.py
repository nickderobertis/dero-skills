"""The skill eval's path constants, checked by the tier the gate actually runs.

The eval itself never runs in the gate — it drives a real harness — so a broken
path constant inside it stays invisible until somebody spends 20-30 minutes
finding out. That is not hypothetical: moving the eval into its own project
shifted it one directory deeper, and `SKILL` (a `parents[...]` index) silently
started resolving to the `tests/` directory instead of the skill root.

So the constants are asserted from the fast tier. The module is loaded from its
real path — the way pytest loads it — and the paths it derives are resolved
against the real tree.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
EVAL = SKILL / "tests" / "skilltest" / "test_create_repo_skilltest.py"


@pytest.fixture(scope="module")
def eval_module():
    spec = importlib.util.spec_from_file_location("create_repo_eval_wiring", EVAL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_the_eval_points_at_the_skill_root(eval_module) -> None:
    assert eval_module.SKILL == SKILL, (
        f"the eval resolved the skill root to {eval_module.SKILL}; it moved "
        "without its parents[...] index moving with it"
    )


def test_the_eval_can_find_the_checker_it_asserts_with(eval_module) -> None:
    # The load-bearing assertion of the whole eval is that this script passes
    # against the produced repo. A path that does not exist would fail the run
    # long after the harness had done its work.
    assert eval_module.BASELINE_CHECKER.is_file(), eval_module.BASELINE_CHECKER
