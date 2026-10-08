"""The opt-in onebudgetspec llmlint fragment: its contract, held offline.

`assets/llmlint/tools/onebudgetspec.llmlint.yml` is adopted by URL — `@1` — by
repos that register budgets (the composer's `--tool onebudgetspec` opt-in lists
it), and by nothing else. Its path, major and rule names are what those
consumers pin, and keeping it out of every always-on config is what keeps repos
without budgets from loading it. The opt-in's own composition is held in
test_compose_repo_plan.py. Both are silent to break:
a renamed rule leaves a consumer's override dangling, and a stray reference
loads budget rules everywhere. The judged half (do the rules judge budgets
correctly?) lives in the `skilltest` project, since it needs a harness.

Parsing goes through the real `llmlint config`, adopting the fragment as a
consumer would, so "parses" means "llmlint accepts it", not "a YAML library does".
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR.parents[2]
LLMLINT_ASSETS = SKILL_DIR / "assets" / "llmlint"
FRAGMENT = LLMLINT_ASSETS / "tools" / "onebudgetspec.llmlint.yml"
COMPOSER = SKILL_DIR / "scripts" / "compose_repo_plan.py"
SHAPES = sorted(p.stem for p in (SKILL_DIR / "references" / "shapes").glob("*.md"))
LANGUAGES = sorted(
    p.stem for p in (SKILL_DIR / "references" / "languages").glob("*.md")
)

RULES = {
    "budget_descriptions_durable_and_terse",
    "budgets_scoped_to_minimal_tree",
    "onebudgetspec_is_the_only_judge",
    "budget_commands_measure_directly",
    "budgets_reuse_gate_telemetry",
    "tests_hold_no_nonfunctional_thresholds",
    "budgets_track_product_owner_outcomes",
}
# What any reference to the fragment would contain: its file name, or the tool's.
NEEDLE = "onebudgetspec"


def _llmlint() -> str:
    # `just bootstrap` installs llmlint via `uv tool`, into ~/.local/bin.
    installed = Path.home() / ".local" / "bin" / "llmlint"
    found = str(installed) if installed.is_file() else shutil.which("llmlint")
    if found is None:
        pytest.fail("llmlint is not installed — run `just bootstrap`")
    return found


def test_the_fragment_parses_and_declares_exactly_its_rules(
    tmp_path: Path,
) -> None:
    consumer = tmp_path / "llmlint.yml"
    consumer.write_text(f'plugins:\n  - "{FRAGMENT}"\n', encoding="utf-8")
    result = subprocess.run(
        [_llmlint(), "config", "--cwd", str(tmp_path), "-c", str(consumer)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"llmlint refused the fragment:\n{result.stderr}"
    printed = json.loads(result.stdout)
    assert isinstance(printed, dict), printed
    config = printed.get("config")
    assert isinstance(config, dict) and isinstance(config.get("rules"), list), config
    names = [rule.get("name") for rule in config["rules"] if isinstance(rule, dict)]
    assert len(names) == len(config["rules"]), config["rules"]
    assert sorted(names) == sorted(RULES), (
        f"{FRAGMENT.name} declares {names}; consumers pin exactly {sorted(RULES)} — "
        "renaming or adding one is a contract change, not an edit"
    )
    assert FRAGMENT.read_text(encoding="utf-8").count("\nversion: 1.") == 1, (
        "the fragment must stay on major 1: consumers adopt it as `@1`"
    )


def test_no_other_fragment_names_it() -> None:
    naming = [
        frag.relative_to(LLMLINT_ASSETS).as_posix()
        for frag in sorted(LLMLINT_ASSETS.rglob("*.llmlint.yml"))
        if frag != FRAGMENT and NEEDLE in frag.read_text(encoding="utf-8")
    ]
    assert naming == [], (
        f"always-on fragments name the opt-in onebudgetspec fragment: {naming} — "
        "repos without budgets would load its rules"
    )


def test_this_repos_own_llmlint_config_does_not_name_it() -> None:
    # dero-skills registers no budgets, so it has nothing for these rules to judge.
    assert NEEDLE not in (REPO_ROOT / "llmlint.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize("shape", SHAPES)
def test_the_composer_never_wires_it_without_the_opt_in(
    shape: str, tmp_path: Path
) -> None:
    # Every shape with every language and every optional concern bar `--tool`:
    # the widest selection short of the opt-in, in both tiers.
    ongoing = tmp_path / "llmlint.yml"
    buildout = tmp_path / "llmlint.buildout.yml"
    languages = [arg for lang in LANGUAGES for arg in ("--language", lang)]
    result = subprocess.run(
        [
            "uv",
            "run",
            "--script",
            str(COMPOSER),
            "--shape",
            shape,
            *languages,
            "--releasing",
            "-o",
            str(tmp_path / "plan.md"),
            "--llmlint-config",
            str(ongoing),
            "--llmlint-buildout-config",
            str(buildout),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    for written in (ongoing, buildout):
        text = written.read_text(encoding="utf-8")
        assert "plugins:" in text, f"{written.name} wired no plugins at all"
        assert NEEDLE not in text, (
            f"the composer wired the opt-in onebudgetspec fragment into "
            f"{written.name} for --shape {shape}"
        )
