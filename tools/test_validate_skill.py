"""Tests for the skill validator's runtime-dependency rules.

Each case builds a real skill folder on disk and runs `validate_skill.py` as a
subprocess — the same way `scripts/validate-skills.sh` and CI invoke it — so the
exit code and the reported diagnostic are the real ones, not a stand-in's.
"""

from __future__ import annotations

import subprocess
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
    """Run the validator the way scripts/validate-skills.sh and CI do."""
    return subprocess.run(
        ["uv", "run", "python", str(VALIDATOR), str(skill_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


PEP723 = '# /// script\n# requires-python = ">=3.12"\n# dependencies = []\n# ///\n'


# A bundled script may *read* the orchestrator's world; it may not *run* it.
# These two tables are the line, and every case below is driven through the real
# validator so the line is where the tool actually draws it.
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
    # The tool is `Nx`; only the executable is `nx`. Prose that opens a line with
    # the tool's name is a mention, not a call.
    "opens a prose line with the tool's name": (
        PEP723
        + '"""Report on the graph.\n\nNx build targets are declared per project;\n'
        'this script only reads them.\n"""\nprint("ok")\n'
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
    # No subcommand allowlist: `nx <target>` is how a project's own target is
    # run, and it is a runtime dependency exactly like `nx run` is.
    "runs a project target by its shorthand": (
        PEP723 + 'import subprocess\nsubprocess.run("nx build app", shell=True)\n'
    ),
    "runs a shorthand target through a package runner": (
        PEP723 + 'import os\nos.system("bunx nx typecheck web")\n'
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
        "#!/usr/bin/env bash\ncd repo && nx build app\n",
    ],
    ids=[
        "runs a sweep",
        "captures output in a command substitution",
        "chains a shorthand target onto another command",
    ],
)
def test_a_shell_script_that_shells_out_to_the_orchestrator_is_rejected(
    tmp_path, script
):
    result = validate(make_skill(tmp_path, script, filename="gate.sh"))
    assert result.returncode == 1, result.stdout
    assert "invokes Nx at runtime" in result.stderr


# Narrowing one rule must not have loosened the rest of the runtime invariant.


def test_a_conformant_skill_passes(tmp_path):
    result = validate(make_skill(tmp_path, PEP723 + 'print("hello")\n'))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("OK")


def test_a_script_that_only_reads_the_graph_manifest_still_passes(tmp_path):
    # The whole point of narrowing the rule: a checker that looks for nx.json and
    # reports on it in prose is a *read*, and must survive alongside the rejections.
    script = (
        PEP723
        + "from pathlib import Path\n"
        + 'if Path("nx.json").is_file():\n'
        + '    print("OK project graph present")\n'
        + "else:\n"
        + '    print("WARN no project graph — Nx would run the targets")\n'
    )
    result = validate(make_skill(tmp_path, script))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("OK")


def test_reaching_for_the_repo_root_toolchain_is_still_rejected(tmp_path):
    result = validate(
        make_skill(tmp_path, PEP723 + 'import os\nos.system("asdf install")\n')
    )
    assert result.returncode == 1, result.stdout
    assert "references asdf" in result.stderr
