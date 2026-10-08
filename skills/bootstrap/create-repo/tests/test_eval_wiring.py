"""The skill eval's path constants, checked by the tier the gate actually runs.

The eval itself never runs in the gate — it drives a real harness — so a broken
path constant inside it stays invisible until somebody spends 20-30 minutes
finding out. That is not hypothetical: moving the eval into its own project
shifted it one directory deeper, and `SKILL` (a `parents[...]` index) silently
started resolving to the `tests/` directory instead of the skill root.

So the constants are asserted from the fast tier. The module is loaded from its
real path — the way pytest loads it — and the paths it derives are resolved
against the real tree. The onebudgetspec judged eval is held further: what it
restates of llmlint (rule names, report envelope) and of onebudgetspec (budgets
files, result protocol) is reconciled here against the real binaries, offline.
"""

from __future__ import annotations

import importlib.util
import json
import os
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
    printed = json.loads(result.stdout)
    assert isinstance(printed, dict), printed
    config = printed.get("config")
    assert isinstance(config, dict) and isinstance(config.get("rules"), list), config
    declared = {rule.get("name") for rule in config["rules"] if isinstance(rule, dict)}
    assert len(declared) == len(config["rules"]), config["rules"]
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


def _onebudgetspec() -> str:
    # A dev dependency (`onebudgetspec-cli`), so it sits beside this interpreter.
    beside = Path(sys.executable).parent / "onebudgetspec"
    found = str(beside) if beside.is_file() else shutil.which("onebudgetspec")
    assert found is not None, "onebudgetspec is not installed — run `just bootstrap`"
    return found


# Needs PyYAML, which a consumer reading its own budgets.yaml would depend on and
# this repo does not; its budgets file is still validated below.
_UNMEASURABLE_HERE = {"reads-budgets-yaml"}


def test_the_judged_rules_fixtures_are_real_onebudgetspec_consumers(
    judged_rules_module, tmp_path: Path
) -> None:
    # The judged cases restate onebudgetspec's budgets-file schema and its result
    # protocol (`ONEBUDGETSPEC_RESULT`, `{"value": ...}`). Each tree goes through
    # the real CLI the way its consumer runs it — the test target first where the
    # budget analyses that target's telemetry — so a fixture that drifted from
    # onebudgetspec fails here instead of being judged as if it were valid.
    onebudgetspec = _onebudgetspec()
    module = judged_rules_module
    trees = {c.fixture: c.tree for c in module.CASES}
    for fixture, tree in trees.items():
        root = tmp_path / fixture
        module.write_tree(tree, root)
        validated = subprocess.run(
            [onebudgetspec, "validate", "--recursive", "."],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert validated.returncode == 0, (
            f"{fixture}: onebudgetspec refused its budgets files:\n"
            f"{validated.stdout}{validated.stderr}"
        )
        if fixture in _UNMEASURABLE_HERE:
            continue
        sources = (f"{module.ROOT}/src", "src", "testkit")
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(str(root / s) for s in sources),
        }
        if (root / module.ROOT / "tests" / "telemetry.py").is_file():
            tested = subprocess.run(
                ["uv", "run", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
                + [f"{module.ROOT}/tests"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            assert tested.returncode == 0, f"{fixture}:\n{tested.stdout}"
        checked = subprocess.run(
            [onebudgetspec, "check", "--recursive", ".", "--json"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        assert checked.returncode == 0, (
            f"{fixture}: onebudgetspec could not measure it within budget:\n"
            f"{checked.stdout}{checked.stderr}"
        )
        listed = subprocess.run(
            [onebudgetspec, "list", "--recursive", ".", "--json"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert listed.returncode == 0, listed.stderr
        declared = _ids(json.loads(listed.stdout), "budgets")
        within = _ids(json.loads(checked.stdout), "results", verdict="within")
        assert within == declared, (
            f"{fixture}: declares {declared}, measured within budget {within}"
        )


def _ids(report: object, key: str, **match: str) -> list[str]:
    """The ids of a onebudgetspec JSON report's entries, checking its shape."""
    assert isinstance(report, dict) and isinstance(report.get(key), list), report
    entries = report[key]
    assert all(isinstance(e, dict) and isinstance(e.get("id"), str) for e in entries)
    return sorted(
        e["id"] for e in entries if all(e.get(k) == v for k, v in match.items())
    )


# The eval runs the skill in this tree, never another checkout of it. skilltest
# hands the harness SKILL.md's text alone; the staged copy adds the base-directory
# line Claude Code itself adds, and the run's tool calls are held to it.

# What a run of another checkout recorded: the model searched the host and ran
# the first copy it found (the commands are the ones a failed run reported).
STALE = "/home/u/.cache/checkouts/dero-skills/skills/bootstrap/create-repo"
STALE_RUN = [
    {
        "command": 'find / -type d -name "assets" -path "*create-repo*" 2>/dev/null; '
        'echo "---"; find / -name "compose_repo_plan.py" 2>/dev/null'
    },
    {
        "command": f'SKILL={STALE} && uv run --script "$SKILL/scripts/'
        'compose_repo_plan.py" --shape cli --language rust -o REPO_PLAN.md'
    },
    {"file_path": f"{STALE}/assets/AGENTS.md.template"},
]


def under_test_run(skill: Path) -> list[object]:
    """The same steps against the skill in this tree, in the forms a model writes."""
    return [
        STALE_RUN[0],
        {
            "command": f'SKILL={skill} && uv run --script "$SKILL/scripts/'
            'compose_repo_plan.py" --shape cli --language rust -o REPO_PLAN.md'
        },
        {"file_path": f"{skill}/assets/AGENTS.md.template"},
        {"command": f"uv run --script {skill}/scripts/compose_repo_plan.py --wiring ."},
        {"command": "cd /tmp/w/create-repo-e2e-rust-cli && just check"},
    ]


def test_the_staged_skill_names_this_tree_and_is_a_valid_skill(
    eval_module, tmp_path
) -> None:
    from skilltest_pytest import validate_skill

    staged = eval_module.stage_skill(tmp_path)
    report = validate_skill(staged)
    assert report.valid, report.findings
    text = (staged / "SKILL.md").read_text(encoding="utf-8")
    original = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    line = f"Base directory for this skill: {SKILL}"
    assert line in text.splitlines()
    # Only the line is added: the frontmatter and every line of the body stay.
    assert [ln for ln in text.splitlines() if ln and ln != line] == [
        ln for ln in original.splitlines() if ln
    ]


def test_a_run_of_this_trees_skill_passes_the_isolation_check(eval_module) -> None:
    calls = under_test_run(SKILL)
    assert eval_module.skill_copies(calls) == {str(SKILL)}
    eval_module.assert_ran_the_skill_under_test(calls)


def test_a_run_of_another_checkout_fails_naming_it(eval_module) -> None:
    with pytest.raises(AssertionError, match="another copy of the skill") as caught:
        eval_module.assert_ran_the_skill_under_test(STALE_RUN)
    assert STALE in str(caught.value)
    # One stray read of the other copy is enough, beside a run of this one.
    with pytest.raises(AssertionError, match="another copy of the skill"):
        eval_module.assert_ran_the_skill_under_test(
            [*under_test_run(SKILL), STALE_RUN[2]]
        )


def test_a_link_to_the_skills_source_is_not_another_copy(eval_module) -> None:
    # What the model wrote into the produced repo, and a command quoting a URL:
    # neither runs or reads a copy of the skill. A failed run read one as such.
    url = (
        "https://github.com/nickderobertis/dero-skills/blob/main/skills/"
        "bootstrap/create-repo"
    )
    calls = [
        *under_test_run(SKILL),
        {"file_path": "/tmp/w/create-repo-e2e-rust-cli/AGENTS.md", "content": url},
        {"file_path": "/tmp/w/README.md", "old_string": STALE, "new_string": url},
        {"command": f'gh repo create x --description "built with {url}"'},
    ]
    assert eval_module.skill_copies(calls) == {str(SKILL)}
    eval_module.assert_ran_the_skill_under_test(calls)
    # Writing INTO another copy still names it: the path is the call's target.
    with pytest.raises(AssertionError, match="another copy of the skill"):
        eval_module.assert_ran_the_skill_under_test(
            [*calls, {"file_path": f"{STALE}/SKILL.md", "content": "x"}]
        )


def test_a_run_that_never_reaches_the_skill_fails(eval_module, tmp_path) -> None:
    staged = tmp_path / "create-repo"
    calls = [STALE_RUN[0], {"file_path": f"{staged}/SKILL.md"}]
    with pytest.raises(AssertionError, match="no tool call reached"):
        eval_module.assert_ran_the_skill_under_test(calls, staged=staged)


def test_a_failure_report_carries_how_each_run_ended(eval_module) -> None:
    from skilltest_sdk import Report

    report = Report.model_validate(
        {
            "passed": False,
            "summary": {"cases": 1, "failed": 1, "passed": 0, "runs": 1},
            "runs": [
                {
                    "case": "case",
                    "evals": [],
                    "model": "claude-opus-4-8",
                    "platform": "claude-code",
                    "passed": False,
                    "skill": str(SKILL),
                    "turns": 2,
                    "transcript": {
                        "messages": [
                            {"role": "user", "content": "set it up"},
                            {"role": "assistant", "content": "Composing the plan."},
                            {"role": "assistant", "content": "Which license?"},
                        ]
                    },
                }
            ],
        }
    )
    words = eval_module.last_words(report)
    assert "claude-code/claude-opus-4-8 ended with" in words
    assert words.rstrip().endswith("Which license?")
    assert "Composing the plan." not in words
