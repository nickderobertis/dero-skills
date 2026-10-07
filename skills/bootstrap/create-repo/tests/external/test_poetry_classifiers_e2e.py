"""End-to-end: the checker reads a Poetry manifest's classifiers as poetry-core writes them.

The typed-packaging check reads `Typing :: Typed` and `Private :: Do Not Upload`
from the list a wheel's METADATA is written from — `[project].classifiers`, or
for a poetry-core manifest whose `[project]` declares none,
`[tool.poetry].classifiers`. That is poetry-core's contract, not the checker's,
so each case here builds the manifest's wheel with the real backend (`uv build`,
which fetches poetry-core from PyPI — the reason this sits in the external tier)
and holds the checker's command-line verdict to the classifiers the wheel ships.
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest
from test_check_repo_baseline import SCRIPT, make_repo
from test_check_repo_baseline_fleet import POETRY_MANIFEST, write_manifest

TYPED = '"Typing :: Typed",'
PRIVATE = '"Private :: Do Not Upload",'
PROJECT_CLASSIFIERS = 'dynamic = ["version"]\nclassifiers = [{}]\n'

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
}


def wheel_classifiers(project: Path, out: Path) -> set[str]:
    """Build ``project``'s wheel with its real backend; return its METADATA classifiers."""
    built = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out), str(project)],
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
    flagged = "sdks/fern/pyproject.toml (poetry.core.masonry.api)" in result.stderr
    assert flagged == (owes_marker and not typed), (shipped, result.stderr)
    assert result.returncode == (1 if flagged else 0), result.stderr
