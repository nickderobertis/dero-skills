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
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
EVAL = SKILL / "tests" / "skilltest" / "test_create_repo_skilltest.py"
JUDGED_RULES = SKILL / "tests" / "skilltest" / "test_onebudgetspec_rules_judged.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


@pytest.fixture(scope="module")
def eval_module():
    yield from _load(EVAL, "create_repo_eval_wiring")


@pytest.fixture(scope="module")
def judged_rules_module():
    yield from _load(JUDGED_RULES, "onebudgetspec_judged_wiring")


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


def test_the_judged_rules_eval_adopts_real_files(judged_rules_module) -> None:
    # A consumer fixture adopting a path that does not exist would have every
    # case error out only after a credentialed run.
    assert judged_rules_module.FRAGMENT.is_file(), judged_rules_module.FRAGMENT
    assert judged_rules_module.ONEHARNESS_TEMPLATE.is_file()


def _llmlint() -> str:
    # `just bootstrap` installs llmlint via `uv tool`, into ~/.local/bin.
    installed = Path.home() / ".local" / "bin" / "llmlint"
    found = str(installed) if installed.is_file() else shutil.which("llmlint")
    assert found is not None, "llmlint is not installed — run `just bootstrap`"
    return found


def test_the_judged_rules_eval_proves_every_fragment_rule_both_ways(
    judged_rules_module, tmp_path: Path
) -> None:
    # The eval selects rules by name. Renaming one in the fragment would leave its
    # cases selecting nothing, and a new rule would go unproven — both found only
    # after a credentialed run. The names come from llmlint's own reading.
    consumer = tmp_path / "llmlint.yml"
    consumer.write_text(
        f'plugins:\n  - "{judged_rules_module.FRAGMENT}"\n', encoding="utf-8"
    )
    result = subprocess.run(
        [_llmlint(), "config", "--cwd", str(tmp_path), "-c", str(consumer)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    declared = {rule["name"] for rule in json.loads(result.stdout)["config"]["rules"]}
    expected = judged_rules_module.Expected
    for outcome in (expected.PASS, expected.FAIL):
        proven = {c.rule for c in judged_rules_module.CASES if c.expected == outcome}
        assert proven == declared, (
            f"rules with a {outcome!r} case: {sorted(proven)}; "
            f"the fragment declares {sorted(declared)}"
        )


def test_the_judged_rules_eval_reads_llmlints_real_report(
    judged_rules_module, tmp_path: Path
) -> None:
    # The eval reads each verdict out of llmlint's JSON report, a shape llmlint
    # publishes no schema for. A run whose rule matches no file is offline and
    # still emits the whole report, so the eval's reader is held to the real one
    # here rather than first meeting a changed envelope after a credentialed run.
    found = _llmlint()
    rule = "budgets_scoped_to_minimal_tree"
    (tmp_path / "llmlint.yml").write_text(
        f'plugins:\n  - "{judged_rules_module.FRAGMENT}"\n', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("No budgets here.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = subprocess.run(
        [found, "--format", "json", "--progress", "never", "--no-history"]
        + ["--rule", rule],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    verdict = judged_rules_module._verdict(rule, result)
    assert verdict.outcome == "skipped", verdict.report
    assert verdict.violation_files == [], verdict.report
