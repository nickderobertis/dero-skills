"""End-to-end file-selection tests for the composed ongoing llmlint config.

A judge rule's `files.include` decides which files it is ever asked about, and a
glob that over-selects is invisible to every other check here: the config still
parses, `llmlint validate` still passes, and the cost lands on consumers as
findings about files the rule has nothing to say on.

So this drives llmlint's *own* selection rather than re-implementing globbing in
Python. A scratch tree gets the real composed `llmlint.yml` (from the skill's
composer, with the dero-skills plugin URLs rewritten to the in-tree fragments so
the run is offline and reflects the working copy), and `llmlint lint --plan-only`
— deterministic, no harness or model call — reports exactly the files a rule
would be judged over.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = SKILL_DIR.parents[2]
COMPOSER = SKILL_DIR / "scripts" / "compose_repo_plan.py"

# `scripts/setup-llmlint.sh` installs the binary here; the llmlint-tier project's
# targets prepend the same directory, so mirror it rather than assuming PATH.
_LLMLINT_BIN_DIR = Path.home() / ".local" / "bin"

# A `plugins:` entry naming a dero-skills fragment by pinned URL, as the composer
# emits it: `  - "https://.../<path under the repo>.llmlint.yml@1"`.
_DERO_PLUGIN_RE = re.compile(
    r'^(\s*-\s*)"https://raw\.githubusercontent\.com/nickderobertis/dero-skills/main/'
    r'(skills/.+?)@\d+"$'
)
# The plan's per-batch file line: `      3 files: a, b, c`.
_PLAN_FILES_RE = re.compile(r"^\s*\d+ files?: (.+)$")


def llmlint_bin() -> str:
    """The real llmlint binary, or an actionable failure.

    It is not a dev dependency — `just bootstrap` installs it as a uv tool — and
    the gate already requires it (`llmlint-tier:validate` shells out to it), so a
    missing binary is a broken setup rather than a reason to skip and go green.
    """
    path = os.pathsep.join([str(_LLMLINT_BIN_DIR), os.environ.get("PATH", "")])
    found = shutil.which("llmlint", path=path)
    assert found is not None, (
        "llmlint not found on PATH or in "
        f"{_LLMLINT_BIN_DIR} — run `just bootstrap` (or `just session-setup`) "
        "to install it"
    )
    return found


def compose_config_into(tree: Path) -> Path:
    """Write the composed ongoing llmlint config into `tree`, wired to this
    working copy's fragments instead of the pinned `main` URLs."""
    config = tree / "llmlint.yml"
    result = subprocess.run(
        [
            "uv",
            "run",
            "--script",
            str(COMPOSER),
            "--shape",
            "cli",
            "--language",
            "python",
            "--llmlint-config",
            str(config),
            "-o",
            str(tree / "plan.md"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    lines: list[str] = []
    rewritten = 0
    for line in config.read_text(encoding="utf-8").splitlines():
        match = _DERO_PLUGIN_RE.match(line)
        if match is not None:
            lines.append(f'{match.group(1)}"{REPO_ROOT / match.group(2)}"')
            rewritten += 1
        elif "llmlint/main/assets/config_lint.yml" in line:
            # The one upstream plugin; dropping it keeps the run network-free and
            # it declares no rule this module selects over.
            continue
        else:
            lines.append(line)
    assert rewritten, (
        f"composer emitted no dero-skills plugin URLs:\n{config.read_text()}"
    )
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config


def selected_files(tree: Path, rule: str) -> set[str]:
    """The files llmlint would judge `rule` over in `tree` — its own selection,
    read off `--plan-only` (no harness call, no model, no credential)."""
    result = subprocess.run(
        [
            llmlint_bin(),
            "lint",
            "-c",
            "llmlint.yml",
            "--plan-only",
            "--no-history",
            "--rule",
            rule,
        ],
        cwd=tree,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    selected: set[str] = set()
    for line in result.stdout.splitlines():
        match = _PLAN_FILES_RE.match(line)
        if match is not None:
            selected.update(p.strip() for p in match.group(1).split(","))
    return selected


def write(tree: Path, relative: str, body: str) -> str:
    path = tree / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return relative


def build_tree(tmp_path: Path) -> Path:
    """A repo whose live tier sits in the conventional directories, alongside
    ordinary source files whose *names* merely contain `live`."""
    tree = tmp_path / "consumer"
    tree.mkdir()
    compose_config_into(tree)
    write(
        tree,
        ".github/workflows/live.yml",
        "name: live\non: [workflow_dispatch]\njobs:\n"
        "  live:\n    runs-on: ubuntu-latest\n    steps:\n      - run: just test-live\n",
    )
    write(tree, "tests/live/test_live_api.py", "def test_live():\n    pass\n")
    write(
        tree, "tests/integration/test_api_client.py", "def test_client():\n    pass\n"
    )
    write(tree, "src/deliver.rs", "pub fn deliver() {}\n")
    write(tree, "src/live_view.ts", "export const liveView = 1\n")
    return tree


RULE = "live_tier_compiles_and_requires_credential"


def test_live_tier_rule_selects_workflows_and_conventional_test_directories(tmp_path):
    selected = selected_files(build_tree(tmp_path), RULE)
    assert ".github/workflows/live.yml" in selected
    assert "tests/live/test_live_api.py" in selected
    assert "tests/integration/test_api_client.py" in selected


def test_live_tier_rule_ignores_files_whose_name_merely_contains_live(tmp_path):
    # The `**/*live*` substring glob this replaced drew both of these in, so every
    # consuming repo paid for credential findings on ordinary source files.
    selected = selected_files(build_tree(tmp_path), RULE)
    assert "src/deliver.rs" not in selected
    assert "src/live_view.ts" not in selected
