"""Tests for the skill validator's runtime-dependency rules.

Each case builds a real skill folder on disk and runs `validate_skill.py` as a
subprocess — the same way `scripts/validate-skills.sh` and CI invoke it — so the
exit code and the reported diagnostic are the real ones, not a stand-in's.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = REPO_ROOT / "tools" / "validate_skill.py"

SKILL_MD = """\
---
name: example-skill
description: Use when a test needs a real, conformant skill folder to validate.
compatibility: Bundled scripts need uv and Python 3.12+.
---

# Example skill

A minimal but conformant skill: enough frontmatter and prose for the validator
to accept it, so a test's only variable is the script it bundles.
"""


def make_skill(root: Path, script: str, *, filename: str = "run.py") -> Path:
    """A real skill folder named `example-skill`, bundling `script`."""
    skill_dir = root / "example-skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (skill_dir / "scripts" / filename).write_text(script, encoding="utf-8")
    return skill_dir


def validate(skill_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(skill_dir)],
        capture_output=True,
        text=True,
    )


PEP723 = '# /// script\n# requires-python = ">=3.12"\n# dependencies = []\n# ///\n'


# --- what a bundled script may say about the orchestrator ------------------

PERMITTED = {
    "names the graph manifest as a filename": (
        PEP723 + 'GRAPH_FILES = ("nx.json",)\nprint(GRAPH_FILES)\n'
    ),
    "names Nx in a diagnostic message": (
        PEP723 + 'print("WARN no project graph — add nx.json (Nx orchestrates it)")\n'
    ),
    "quotes an orchestrator command inside a fix message": (
        PEP723
        + 'print("      fix: point the root recipes at `nx affected` (a change) "\n'
        + '      "and `nx run-many` (the full sweep)")\n'
    ),
    "names Nx in prose": (
        PEP723
        + '"""Audit the repo layout.\n\nNx runs the targets; this script only reads\n'
        'the files it left behind.\n"""\nprint("ok")\n'
    ),
}

FORBIDDEN = {
    "runs a target through the orchestrator": (
        PEP723 + 'import subprocess\nsubprocess.run("nx run app:build", shell=True)\n'
    ),
    "runs affected detection": (
        PEP723 + 'import os\nos.system("nx affected -t test")\n'
    ),
    "sweeps every project": (PEP723 + 'import os\nos.system("nx run-many -t lint")\n'),
    "reaches the orchestrator through bunx": (
        PEP723 + 'import os\nos.system("bunx nx show projects")\n'
    ),
    "reaches the orchestrator through npx": (
        PEP723 + 'import os\nos.system("npx nx graph")\n'
    ),
    "chains the orchestrator onto another command": (
        PEP723 + 'import os\nos.system("cd repo && nx run-many -t build")\n'
    ),
    "spawns the orchestrator executable": (
        PEP723 + 'import subprocess\nsubprocess.run(["nx", "build", "app"])\n'
    ),
}


@pytest.mark.parametrize("script", PERMITTED.values(), ids=list(PERMITTED))
def test_reading_the_repo_is_not_a_runtime_dependency(tmp_path, script):
    result = validate(make_skill(tmp_path, script))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Nx" not in result.stderr


@pytest.mark.parametrize("script", FORBIDDEN.values(), ids=list(FORBIDDEN))
def test_invoking_the_orchestrator_is_rejected(tmp_path, script):
    result = validate(make_skill(tmp_path, script))
    assert result.returncode == 1, result.stdout
    assert "invokes Nx at runtime" in result.stderr
    assert "scripts/run.py" in result.stderr


@pytest.mark.parametrize(
    "script",
    [
        "#!/usr/bin/env bash\nset -euo pipefail\nbunx nx run-many -t test\n",
        '#!/usr/bin/env bash\nprojects="$(bunx nx show projects)"\necho "$projects"\n',
    ],
    ids=["runs a sweep", "captures output in a command substitution"],
)
def test_a_shell_script_that_shells_out_to_the_orchestrator_is_rejected(
    tmp_path, script
):
    result = validate(make_skill(tmp_path, script, filename="gate.sh"))
    assert result.returncode == 1, result.stdout
    assert "invokes Nx at runtime" in result.stderr


# --- the other runtime-dependency rules still hold -------------------------


def test_a_conformant_skill_passes(tmp_path):
    result = validate(make_skill(tmp_path, PEP723 + 'print("hello")\n'))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("OK")


def test_reaching_for_the_repo_root_toolchain_is_still_rejected(tmp_path):
    result = validate(
        make_skill(tmp_path, PEP723 + 'import os\nos.system("asdf install")\n')
    )
    assert result.returncode == 1, result.stdout
    assert "references asdf" in result.stderr
