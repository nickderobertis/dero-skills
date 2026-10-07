"""End-to-end: the checker reads a Poetry manifest's classifiers as poetry-core writes them.

The typed-packaging check reads `Typing :: Typed` and `Private :: Do Not Upload`
from the list a wheel's METADATA is written from — `[project].classifiers`, or
for a poetry-core manifest whose `[project]` declares none,
`[tool.poetry].classifiers`. That is poetry-core's contract, not the checker's,
so each case here builds the manifest's wheel with the real backend it names —
poetry-core, or hatchling for the backend that never reads `[tool.poetry]`, both
from this repo's dev group, called through the PEP 517 hook a build frontend
calls, so no registry is reached — and holds the checker's command-line verdict
to the classifiers the wheel ships.
"""

from __future__ import annotations

import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest
from test_check_repo_baseline import SCRIPT, make_repo
from test_check_repo_baseline_fleet import POETRY_MANIFEST, write_manifest

TYPED = '"Typing :: Typed",'
PRIVATE = '"Private :: Do Not Upload",'
PROJECT_CLASSIFIERS = 'dynamic = ["version"]\nclassifiers = [{}]\n'

# This repository, whose dev group pins the backends the wheels are built with.
REPO_ROOT = Path(__file__).resolve().parents[5]

# PEP 517's `build_wheel` hook of the backend named in argv[1], run in the project
# directory as a frontend runs it.
BUILD_WHEEL = (
    "import importlib, sys; "
    "print(importlib.import_module(sys.argv[1]).build_wheel(sys.argv[2]))"
)

# The same project built by hatchling: it reads `[project]` alone, so a typed
# classifier left under `[tool.poetry]` never reaches its METADATA.
HATCHLING_MANIFEST = """\
[project]
name = "fern-sdk"
version = "0.0.1"

[tool.poetry]
classifiers = ["Typing :: Typed"]

[tool.hatch.build.targets.wheel]
packages = ["src/fern"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""

MANIFESTS = {
    # crozier's shape: the classifiers only under [tool.poetry].
    "tool-poetry-only": POETRY_MANIFEST.format(classifier=TYPED),
    # Both tables: [project]'s list is the one written, so the typed one is lost.
    "both-untyped-project": POETRY_MANIFEST.format(classifier=TYPED).replace(
        'dynamic = ["version"]\n',
        PROJECT_CLASSIFIERS.format('"Intended Audience :: Developers"'),
    ),
    "both-typed-project": POETRY_MANIFEST.format(classifier="").replace(
        'dynamic = ["version"]\n', PROJECT_CLASSIFIERS.format('"Typing :: Typed"')
    ),
    "private-tool-poetry": POETRY_MANIFEST.format(classifier=PRIVATE),
    # Another backend: [tool.poetry] is not read whatever it declares.
    "hatchling-tool-poetry": HATCHLING_MANIFEST,
}


def backend(project: Path) -> str:
    manifest = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    return manifest["build-system"]["build-backend"]


def wheel_classifiers(project: Path, out: Path) -> set[str]:
    """Build ``project``'s wheel with its real backend; return its METADATA classifiers."""
    out.mkdir()
    built = subprocess.run(
        ["uv", "run", "--project", str(REPO_ROOT), "python", "-c", BUILD_WHEEL]
        + [backend(project), str(out)],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    [wheel] = out.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        [metadata] = [
            n for n in archive.namelist() if n.endswith(".dist-info/METADATA")
        ]
        text = archive.read(metadata).decode("utf-8")
    return {
        line.removeprefix("Classifier: ")
        for line in text.splitlines()
        if line.startswith("Classifier: ")
    }


@pytest.mark.parametrize("case", sorted(MANIFESTS))
def test_the_checker_reads_the_classifiers_the_wheel_ships(tmp_path, case):
    repo = tmp_path / "repo"
    repo.mkdir()
    make_repo(repo)
    manifest = write_manifest(
        repo, "sdks/fern", MANIFESTS[case], marker=True, package="src/fern"
    )
    shipped = wheel_classifiers(manifest.parent, tmp_path / "dist")
    owes_marker = "Private :: Do Not Upload" not in shipped
    typed = "Typing :: Typed" in shipped

    result = subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), str(repo)],
        capture_output=True,
        text=True,
    )
    flagged = f"sdks/fern/pyproject.toml ({backend(manifest.parent)})" in result.stderr
    assert flagged == (owes_marker and not typed), (shipped, result.stderr)
    assert result.returncode == (1 if flagged else 0), result.stderr
