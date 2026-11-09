"""The composed Rust coverage shape and what `check_repo_baseline.py` holds it to."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from ci_cd_shape import (
    CHECKER,
    GOOD_COMPILE,
    NAME,
    compose,
    pinned_coverage_tools,
    workspace_files,
)
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


CI_YML = Path(__file__).resolve().parents[4] / ".github" / "workflows" / "ci.yml"


def test_this_repositorys_ci_installs_the_composed_coverage_pins():
    text = CI_YML.read_text()
    for tool, version in pinned_coverage_tools().items():
        assert f"{tool}@{version}" in text, f"{tool} drifted from the composed pin"


def test_a_target_running_instrumented_tests_with_cache_false_is_an_error(repo):
    def plain_llvm_cov(project) -> None:
        project["targets"]["test"]["command"] = (
            "cargo llvm-cov --no-report nextest -p demo-lib"
        )
        project["targets"]["test"]["cache"] = False

    _mutate(repo, "crates/lib/project.json", plain_llvm_cov)
    out = findings(repo)
    assert "runs instrumented tests with `cache: false`" in out
    assert "coverage-unit.sh" in out


def _doubled_cargo(tmp_path: Path, body: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cargo = bin_dir / "cargo"
    cargo.write_text(f"#!/usr/bin/env bash\n{body}\n")
    cargo.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _unit(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "scripts/coverage-unit.sh", *args],
        cwd=root,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )


@pytest.fixture
def unit_root(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(CI / "coverage-unit.sh", tmp_path / "scripts/coverage-unit.sh")
    return tmp_path


def test_coverage_unit_refuses_a_bad_unit_name_and_usage(unit_root):
    named = _unit(unit_root, {}, "bad.name", "--", "nextest")
    assert named.returncode == 2 and "is not a unit name" in named.stderr
    assert _unit(unit_root, {}, "unit-only").returncode == 2
    assert _unit(unit_root, {}, "--tool-version", "gcc").returncode == 2


def test_coverage_unit_refuses_a_run_that_writes_no_profile(unit_root, tmp_path):
    env = _doubled_cargo(tmp_path, "exit 0")
    done = _unit(unit_root, env, "demo", "--", "nextest")
    assert done.returncode == 1 and "wrote no profile" in done.stderr


def test_coverage_unit_refuses_when_every_profile_is_unmergeable(unit_root, tmp_path):
    env = _doubled_cargo(
        tmp_path,
        "mkdir -p target/llvm-cov-target && printf garbage > target/llvm-cov-target/demo.1-1.profraw",
    )
    env["LLVM_PROFDATA"] = _profdata_rejecting_everything(tmp_path)
    done = _unit(unit_root, env, "demo", "--", "nextest")
    assert done.returncode == 1 and "unmergeable" in done.stderr


def _profdata_rejecting_everything(tmp_path: Path) -> str:
    double = tmp_path / "bin" / "fake-profdata"
    double.write_text('#!/usr/bin/env bash\necho "invalid profile" >&2\nexit 1\n')
    double.chmod(0o755)
    return str(double)


def _doubled_report_tools(unit_root: Path, tmp_path: Path) -> dict[str, str]:
    """Doubles past the point a real instrumented build can be spelled, so the report step runs."""
    env = _doubled_cargo(
        tmp_path,
        "mkdir -p target/llvm-cov-target/debug\n"
        "printf garbage > target/llvm-cov-target/demo.1-1.profraw\nexit 0",
    )
    lcov = tmp_path / "bin" / "fake-cov"
    lcov.write_text(
        '#!/usr/bin/env bash\ncase "$1" in export) printf "SF:/elsewhere/x.rs\\nend_of_record\\n";;'
        " *) echo llvm 1;; esac\n"
    )
    readobj = tmp_path / "bin" / "llvm-readobj"
    readobj.write_text('#!/usr/bin/env bash\necho "Build ID: abc"\n')
    profdata = tmp_path / "bin" / "fake-profdata"
    profdata.write_text(
        '#!/usr/bin/env bash\ncase "$1" in merge) shift; while [ $# -gt 0 ]; do if [ "$1" = -o ]; '
        'then : > "$2"; shift; fi; shift; done;; show) printf "Binary IDs:\\nabc\\n";; esac\n'
    )
    for tool in (lcov, readobj, profdata):
        tool.chmod(0o755)
    object_dir = unit_root / "target/llvm-cov-target/debug"
    object_dir.mkdir(parents=True)
    obj = object_dir / "real"
    obj.write_bytes(b"\x7fELF" + b"\0" * 12)
    obj.chmod(0o755)
    env["LLVM_COV"] = str(lcov)
    env["LLVM_PROFDATA"] = str(profdata)
    return env


def test_coverage_unit_refuses_a_file_outside_the_workspace(unit_root, tmp_path):
    env = _doubled_report_tools(unit_root, tmp_path)
    done = _unit(unit_root, env, "demo", "--", "nextest")
    assert done.returncode == 1 and "/elsewhere/x.rs" in done.stderr


def _find_failing_with(tmp_path: Path, message: str) -> None:
    double = tmp_path / "bin" / "find"
    double.write_text(
        '#!/usr/bin/env bash\ncase "$*" in *-perm*) ;; *) exec /usr/bin/find "$@";; esac\n'
        "printf '%s\\0' target/llvm-cov-target/debug/real\n"
        f"echo 'find: x: {message}' >&2\nexit 1\n"
    )
    double.chmod(0o755)


def test_a_file_vanishing_during_the_object_scan_is_tolerated(unit_root, tmp_path):
    env = _doubled_report_tools(unit_root, tmp_path)
    _find_failing_with(tmp_path, "No such file or directory")
    # Reaches the export step past the scan, which the outside-workspace refusal proves.
    assert "/elsewhere/x.rs" in _unit(unit_root, env, "demo", "--", "nextest").stderr


def test_another_find_error_during_the_object_scan_stops_the_report(
    unit_root, tmp_path
):
    env = _doubled_report_tools(unit_root, tmp_path)
    _find_failing_with(tmp_path, "Permission denied")
    done = _unit(unit_root, env, "demo", "--", "nextest")
    assert done.returncode == 1 and "could not list" in done.stderr


def test_off_linux_the_unit_runs_uninstrumented_and_writes_no_report(
    unit_root, tmp_path
):
    log = tmp_path / "cargo.args"
    env = _doubled_cargo(tmp_path, f'echo "$*" >> "{log}"')
    uname = tmp_path / "bin" / "uname"
    uname.write_text("#!/usr/bin/env bash\necho Darwin\n")
    uname.chmod(0o755)
    assert _unit(unit_root, env, "demo", "--", "nextest", "-p", "x").returncode == 0
    assert _unit(unit_root, env, "--cargo", "--", "build", "-p", "x").returncode == 0
    versions = _unit(unit_root, env, "--tool-version", "llvm-cov")
    assert versions.returncode == 0 and "unused off Linux" in versions.stdout
    assert log.read_text().splitlines() == ["nextest run -p x", "build -p x"]
    assert not (unit_root / "target/coverage-units/demo.lcov").exists()


def _merge(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(CI / "coverage-merge.mjs"), *args],
        cwd=root,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--fail-under-lines", "96"),
        ("--fail-under-lines", "96.123", "--dir", "d", "--unit", "a"),
        ("--fail-under-lines", "101", "--dir", "d", "--unit", "a"),
        ("--fail-under-lines", "96", "--dir", "d", "--unit", "a", "--unit", "a"),
        ("--fail-under-lines", "96", "--dir", "d", "--unit"),
    ],
    ids=[
        "none",
        "partial",
        "too-precise",
        "above-100",
        "duplicate-unit",
        "no-unit-values",
    ],
)
def test_coverage_merge_refuses_a_bad_command_line(tmp_path, args):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.lcov").write_text("SF:x.rs\nDA:1,1\nend_of_record\n")
    assert _merge(tmp_path, *args).returncode == 2


@pytest.mark.parametrize(
    ("body", "complaint"),
    [
        ("SF:x.rs\nDA:0,1\nend_of_record\n", "malformed"),
        ("DA:1,1\nend_of_record\n", "malformed"),
        ("SF:x.rs\nNOPE:1\nend_of_record\n", "malformed"),
        ("end_of_record\n", "outside a record"),
        ("SF:x.rs\nDA:1,1\n", "truncated"),
        ("TN:\n", "hold no line"),
    ],
    ids=[
        "line-zero",
        "da-outside-sf",
        "unknown-record",
        "stray-end",
        "truncated",
        "no-lines",
    ],
)
def test_coverage_merge_refuses_a_malformed_report(tmp_path, body, complaint):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.lcov").write_text(body)
    done = _merge(tmp_path, "--fail-under-lines", "0", "--dir", "d", "--unit", "a")
    assert done.returncode == 2 and complaint in done.stderr


def test_an_edited_copy_of_either_script_is_an_error(repo):
    for script in ("scripts/coverage-unit.sh", "scripts/coverage-merge.mjs"):
        path = repo / script
        original = path.read_bytes()
        path.write_bytes(original + b"\n# edited in place\n")
        out = findings(repo)
        assert script in out and "ERROR" in out
        path.write_bytes(original)


@pytest.mark.parametrize("name", ["nx.json", "crates/lib/project.json"])
def test_a_non_json_config_is_a_finding_not_a_traceback(repo, name):
    (repo / name).write_text("not json")
    out = findings(repo)
    assert "not a JSON object" in out and "Traceback" not in out


def test_a_workspace_inherited_path_dependency_reaches_the_units_key(tmp_path):
    files = {
        **workspace_files(GOOD_COMPILE),
        "Cargo.toml": (
            '[workspace]\nmembers = ["crates/*"]\nresolver = "2"\n'
            '[workspace.dependencies]\ndemo-dep = { path = "crates/dep" }\n'
        ),
        "crates/lib/Cargo.toml": (
            '[package]\nname = "demo-lib"\nversion = "0.1.0"\nedition = "2021"\n'
            "[dependencies]\ndemo-dep = { workspace = true }\n"
        ),
        "crates/dep/project.json": json.dumps(
            {"name": "demo-dep", "root": "crates/dep"}
        ),
    }
    repo = compose(tmp_path / "repo", "rust", "linux", files=files)
    project = json.loads((repo / "crates/lib/project.json").read_text())
    assert project["implicitDependencies"] == ["demo-dep"]


@pytest.mark.parametrize(
    ("relative", "text", "message"),
    [
        (
            "crates/lib/project.json",
            '{"root": "crates/lib", "targets": {}}',
            "names no project",
        ),
        ("crates/lib/Cargo.toml", "[package]\n", "names no package"),
    ],
    ids=["nameless-project", "nameless-package"],
)
def test_the_composer_refuses_a_workspace_it_cannot_wire(
    tmp_path, relative, text, message
):
    files = {**workspace_files(GOOD_COMPILE), relative: text}
    with pytest.raises(AssertionError, match=message):
        compose(tmp_path / "repo", "rust", "linux", files=files)


def test_every_doc_stating_the_rust_floor_names_the_composers_floor():
    composer = (CI.parents[1] / "scripts" / "compose_repo_plan.py").read_text()
    floor = re.search(r'RUST_COVERAGE_FLOOR = "(\d+)"', composer).group(1)
    for relative in (
        "references/languages/rust.md",
        "references/ci.md",
        "references/intersections/rust-cli.md",
        "assets/justfile.template",
    ):
        text = (CI.parents[1] / relative).read_text()
        assert (
            f"--fail-under-lines {floor}" in text
            or f"Rust's default is {floor}" in text
            or f"({floor} on" in text
        ), relative


def test_a_failing_test_propagates_its_status_and_writes_no_report(unit_root, tmp_path):
    env = _doubled_cargo(tmp_path, "exit 7")
    done = _unit(unit_root, env, "demo", "--", "nextest")
    assert done.returncode == 7
    assert not (unit_root / "target/coverage-units/demo.lcov").exists()


def test_a_failing_llvm_profdata_show_is_reported(unit_root, tmp_path):
    env = _doubled_report_tools(unit_root, tmp_path)
    double = Path(env["LLVM_PROFDATA"])
    double.write_text(
        double.read_text().replace(
            'show) printf "Binary IDs:\\nabc\\n";;', "show) exit 3;;"
        )
    )
    done = _unit(unit_root, env, "demo", "--", "nextest")
    assert done.returncode == 1 and "could not list the binaries" in done.stderr


def test_a_failing_llvm_cov_export_is_reported(unit_root, tmp_path):
    env = _doubled_report_tools(unit_root, tmp_path)
    Path(env["LLVM_COV"]).write_text("#!/usr/bin/env bash\nexit 4\n")
    done = _unit(unit_root, env, "demo", "--", "nextest")
    assert done.returncode == 1 and "could not export" in done.stderr
