"""The composed Rust coverage shape and what `check_repo_baseline.py` holds it to."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from ci_cd_shape import CHECKER, GOOD_COMPILE, NAME, compose, workspace_files
from test_check_repo_baseline import crb

CI = Path(__file__).resolve().parents[1] / "assets" / "ci"


@pytest.mark.parametrize("script", ["coverage-unit.sh", "coverage-merge.mjs"])
def test_each_script_header_matches_its_body_and_versions_file(script):
    path = CI / script
    header = crb.parse_figures_header(path.read_bytes())
    assert header is not None and header.version >= 1
    assert crb.figures_digest(header.body) == header.sha256
    entries = crb.read_figures_versions(path.with_suffix(".versions"))
    assert entries[-1] == crb.FiguresVersion(header.version, header.sha256)
    text = path.read_text()
    for needle in (
        "Exit:",
        "Reads",
        "Writes" if script.endswith("sh") else "Writes nothing",
    ):
        assert needle in text
    assert (
        "--fail-under-lines" if script.endswith("mjs") else "<cargo llvm-cov arguments>"
    ) in text


@pytest.mark.parametrize("script", ["coverage-unit.sh", "coverage-merge.mjs"])
def test_editing_a_script_without_its_header_fails_the_digest(script):
    original = (CI / script).read_bytes()
    edited = original + b"\n# an edit left unstamped\n"
    header = crb.parse_figures_header(edited)
    assert crb.figures_digest(header.body) != header.sha256


def test_the_composer_writes_cached_units_and_a_merge_naming_every_one(tmp_path):
    repo = compose(
        tmp_path / "repo", "rust", "linux", files=workspace_files(GOOD_COMPILE)
    )
    project = json.loads((repo / "crates/lib/project.json").read_text())
    target = project["targets"]["test"]
    assert target["cache"] is True
    assert target["outputs"] == ["{workspaceRoot}/target/coverage-units/demo-lib.lcov"]
    root = json.loads((repo / "project.json").read_text())
    assert root["name"] == f"{NAME}-coverage"
    assert (
        "coverage-merge.mjs --fail-under-lines 96 --dir target/coverage-units --unit demo-lib"
        in root["targets"]["coverage"]["command"]
    )
    assert root["targets"]["coverage"]["dependsOn"] == [
        {"projects": ["demo-lib"], "target": "test"}
    ]
    assert (repo / "scripts/coverage-unit.sh").stat().st_mode & 0o111
    assert (repo / "scripts/coverage-merge.mjs").is_file()


def baseline(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "--script", str(CHECKER), str(repo)],
        capture_output=True,
        text=True,
    )


def findings(repo: Path) -> str:
    done = baseline(repo)
    return done.stdout + done.stderr


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    files = {
        **workspace_files(GOOD_COMPILE),
        ".cargo/config.toml": '[build]\ntarget-dir = "target"\n[profile.dev]\ndebug = 1\n',
    }
    return compose(tmp_path / "repo", "rust", "linux", files=files)


def test_the_composers_fresh_output_has_no_error(repo):
    assert "ERROR" not in findings(repo)


def _mutate(repo: Path, relative: str, change) -> None:
    path = repo / relative
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def uncached(project) -> None:
    project["targets"]["test"]["cache"] = False


def extra_output(project) -> None:
    project["targets"]["test"]["outputs"].append("{workspaceRoot}/target/x.profdata")


def misses_pins(project) -> None:
    project["targets"]["test"]["inputs"] = ["{projectRoot}/**/*"]


def spawned_through_a_coverage_run(project) -> None:
    project["targets"]["test"]["dependsOn"] = [
        {"projects": ["demo-lib"], "target": "test"}
    ]


CASES = {
    "uncached": ("crates/lib/project.json", uncached, "is not cached", "cache: true"),
    "not only its LCOV": (
        "crates/lib/project.json",
        extra_output,
        "declares outputs",
        "one LCOV report",
    ),
    "inputs miss the pins": (
        "crates/lib/project.json",
        misses_pins,
        "inputs miss",
        "tool pin",
    ),
    "depends on a coverage run": (
        "crates/lib/project.json",
        spawned_through_a_coverage_run,
        "coverage run for a binary",
        "coverage-build",
    ),
}


@pytest.mark.parametrize("name", CASES, ids=list(CASES))
def test_a_unit_that_breaks_the_shape_is_one_error_naming_the_remedy(repo, name):
    relative, change, message, remedy = CASES[name]
    _mutate(repo, relative, change)
    out = findings(repo)
    errors = [line for line in out.splitlines() if line.startswith("ERROR")]
    relevant = [line for line in errors if "coverage" in line]
    assert len(relevant) == 1, errors
    assert message in relevant[0]
    assert remedy in out


def test_a_check_that_omits_a_unit_is_one_error_naming_the_remedy(repo):
    def drop_unit(root) -> None:
        target = root["targets"]["coverage"]
        target["command"] = target["command"].replace(
            " --unit demo-lib", " --unit other"
        )

    _mutate(repo, "project.json", drop_unit)
    out = findings(repo)
    assert "no combined coverage check names every unit" in out and "--unit" in out
