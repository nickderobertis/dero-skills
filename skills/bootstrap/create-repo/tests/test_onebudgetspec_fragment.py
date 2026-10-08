"""The onebudgetspec llmlint fragment: its contract, held offline.

Budgets are part of every repository create-repo builds, so the composer lists
`assets/llmlint/tools/onebudgetspec.llmlint.yml` by URL — `@1` — in every
composed ongoing `llmlint.yml`: a generated repo adopts the rules by listing
them itself. Its path, major and rule names are what those consumers pin, and
keeping it out of `base.llmlint.yml` and every other fragment is what keeps an
existing llmlint consumer from getting budget rules it never listed. Both are
silent to break: a renamed rule leaves a consumer's override dangling, and a
stray reference loads budget rules into configs that did not choose them. The
judged half (do the rules judge budgets correctly?) lives in the `skilltest`
project, since it needs a harness.

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
        f"other fragments name the onebudgetspec fragment: {naming} — every "
        "consumer of them would load budget rules without listing them"
    )


def test_this_repo_adopts_it_as_an_in_tree_plugin() -> None:
    # dero-skills is wired like the repos it builds, and dogfoods the fragment
    # by its in-tree path, like the other fragments it hosts.
    text = (REPO_ROOT / "llmlint.yml").read_text(encoding="utf-8")
    entry = f'  - "{FRAGMENT.relative_to(REPO_ROOT).as_posix()}"'
    assert entry in text.splitlines()


@pytest.mark.parametrize("shape", SHAPES)
def test_the_composer_adopts_it_in_every_ongoing_config_only(
    shape: str, tmp_path: Path
) -> None:
    # Every shape with every language and every optional concern: the ongoing
    # config lists the `@1` URL once, the buildout config (no budget rules) not.
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
        assert "plugins:" in written.read_text(encoding="utf-8"), written.name
    url = (
        "https://raw.githubusercontent.com/nickderobertis/dero-skills/main/skills/"
        "bootstrap/create-repo/assets/llmlint/tools/onebudgetspec.llmlint.yml@1"
    )
    ongoing_text = ongoing.read_text(encoding="utf-8")
    assert f'  - "{url}"' in ongoing_text.splitlines(), shape
    assert ongoing_text.count(NEEDLE) == 1, shape
    assert NEEDLE not in buildout.read_text(encoding="utf-8"), shape
