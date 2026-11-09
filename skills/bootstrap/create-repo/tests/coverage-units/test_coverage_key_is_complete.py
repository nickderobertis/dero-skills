"""A unit's key against what its run reads: recorded by tracing a real run, and non-file inputs."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from coverage_fixture import UNIT_ENV, UNITS, Fixture

HASHER = Path(__file__).with_name("nx_task_hashes.cjs")
STRACE_OPEN = re.compile(
    r'openat\(AT_FDCWD<(?P<cwd>[^>]*)>, "(?P<rel>[^"]+)", [^)]*\) = (?P<rc>-?\d+)'
)
ALWAYS = ("Cargo.lock", "rust-toolchain.toml", ".github/ci-tool-versions")
TRACE_FILTER = re.compile(r"^(target|\.git|\.nx|node_modules)/")


def task_hashes(workspace: Fixture) -> dict[str, dict[str, str]]:
    workspace.forget_file_hashes()
    done = subprocess.run(
        ["node", str(HASHER), str(workspace.repo), *UNITS],
        env=workspace.env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    nodes = json.loads(done.stdout.strip().splitlines()[-1])
    return {
        # A tool-version input's value is the tool's, not the file under test; the
        # version-free manifest runtime stays, being how a Cargo.toml reaches the key.
        unit: {
            key: value for key, value in inputs.items() if "--tool-version" not in key
        }
        for unit, inputs in nodes.items()
    }


def traced_reads(workspace: Fixture, unit: str, log: Path) -> set[str]:
    command = json.loads((workspace.repo / project_path(unit)).read_text())["targets"][
        "test"
    ]["command"]
    shutil.rmtree(workspace.repo / "target", ignore_errors=True)
    if unit == "tool-e2e":
        built = subprocess.run(
            [
                "bash",
                "scripts/coverage-unit.sh",
                "--cargo",
                "--",
                "build",
                "--locked",
                "-p",
                "tool",
            ],
            cwd=workspace.repo,
            env=workspace.env,
            capture_output=True,
            text=True,
        )
        assert built.returncode == 0, built.stderr
    traced = subprocess.run(
        [
            "strace",
            "-f",
            "-y",
            "-e",
            "trace=open,openat",
            "-o",
            str(log),
            "bash",
            "-c",
            command,
        ],
        cwd=workspace.repo,
        env=workspace.env,
        capture_output=True,
        text=True,
    )
    assert traced.returncode == 0, traced.stdout + traced.stderr
    root = workspace.repo.resolve()
    reads: set[str] = set()
    for line in log.read_text(errors="replace").splitlines():
        match = STRACE_OPEN.search(line)
        if not match or int(match["rc"]) < 0:
            continue
        rel = match["rel"]
        full = Path(rel if rel.startswith("/") else f"{match['cwd']}/{rel}").resolve()
        if root not in full.parents:
            continue
        reads.add(full.relative_to(root).as_posix())
    tracked = set(workspace.git("ls-files").splitlines())
    return {
        path for path in reads if path in tracked and not TRACE_FILTER.match(path)
    } | set(ALWAYS)


def project_path(unit: str) -> str:
    return f"crates/{unit}/project.json"


def hash_mutation(path: Path, relative: str) -> None:
    """A change the key must see, even through the version-free manifest form."""
    text = path.read_text()
    match Path(relative).name:
        case "Cargo.toml":
            table = "workspace" if relative == "Cargo.toml" else "package"
            path.write_text(text + f"\n[{table}.metadata.keyprobe]\nprobe = true\n")
        case "Cargo.lock":
            path.write_text(
                text.replace(
                    'name = "base"\n', 'name = "base"\nchecksum = "probe"\n', 1
                )
            )
        case "project.json":
            path.write_text(json.dumps({**json.loads(text), "tags": ["probe"]}))
        case _:
            path.write_text(text + "\n// key probe\n")


def uncovered_reads(workspace: Fixture, tmp_path: Path) -> list[str]:
    """Every traced file of each unit whose edit leaves that unit's Nx task hash unchanged."""
    problems = []
    reads = {
        unit: traced_reads(workspace, unit, tmp_path / f"{unit}.trace")
        for unit in UNITS
    }
    base = task_hashes(workspace)
    for relative in sorted(set().union(*reads.values())):
        path = workspace.repo / relative
        original = path.read_bytes()
        try:
            hash_mutation(path, relative)
            moved = task_hashes(workspace)
        finally:
            path.write_bytes(original)
        for unit in UNITS:
            if relative in reads[unit] and moved[unit] == base[unit]:
                problems.append(f"{unit} reads {relative}, which its key does not hash")
    return problems


def test_every_workspace_file_a_run_reads_is_in_that_units_key(workspace, tmp_path):
    assert shutil.which("strace"), "this proof traces the real run: install strace"
    assert uncovered_reads(workspace, tmp_path) == []
    workspace.reset()


def exclude_data(workspace: Fixture) -> None:
    path = workspace.repo / project_path("tool-e2e")
    project = json.loads(path.read_text())
    project["namedInputs"]["testInputs"].append("!{projectRoot}/data/**")
    path.write_text(json.dumps(project))


def test_a_unit_whose_inputs_omit_a_file_its_run_reads_is_reported(workspace, tmp_path):
    try:
        exclude_data(workspace)
        problems = uncovered_reads(workspace, tmp_path)
        wanted = "tool-e2e reads crates/tool-e2e/data/expected.txt"
        assert any(wanted in problem for problem in problems), problems
    finally:
        workspace.reset()


# Every wrapper reports another version and passes every other invocation to the real tool.
def wrappers(workspace: Fixture, directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)

    def resolve(name: str, env: dict[str, str]) -> str:
        return shutil.which(name, path=env["PATH"]) or name

    sysroot = subprocess.run(
        ["rustc", "--print", "sysroot"],
        env=workspace.env,
        capture_output=True,
        text=True,
    ).stdout.strip()
    host = subprocess.run(
        ["rustc", "-vV"], env=workspace.env, capture_output=True, text=True
    ).stdout
    llvm_dir = f"{sysroot}/lib/rustlib/{re.search(r'host: (\S+)', host).group(1)}/bin"
    return {
        "rustc": _script(
            directory / "rustc",
            resolve("rustc", workspace.env),
            '[ "$*" = "-vV" ] && { "$REAL" -vV | sed "s/^rustc /rustc wrapped-/"; exit; }',
        ),
        "cargo-llvm-cov": _script(
            directory / "cargo-llvm-cov",
            resolve("cargo-llvm-cov", workspace.env),
            '[ "$*" = "llvm-cov --version" ] && { echo "cargo-llvm-cov wrapped"; exit; }',
        ),
        "cargo-nextest": _script(
            directory / "cargo-nextest",
            resolve("cargo-nextest", workspace.env),
            '[ "$*" = "nextest --version" ] && { echo "cargo-nextest wrapped"; exit; }',
        ),
        "llvm-profdata": _script(
            directory / "llvm-profdata",
            f"{llvm_dir}/llvm-profdata",
            '[ "$*" = "--version" ] && { echo "llvm-profdata wrapped"; exit; }',
        ),
        "llvm-cov": _script(
            directory / "llvm-cov",
            f"{llvm_dir}/llvm-cov",
            '[ "$*" = "--version" ] && { echo "llvm-cov wrapped"; exit; }',
        ),
        "llvm-readobj": _script(
            directory / "llvm-readobj",
            f"{llvm_dir}/llvm-readobj",
            '[ "$*" = "--version" ] && { echo "llvm-readobj wrapped"; exit; }',
        ),
        "node": _script(
            directory / "node",
            resolve("node", workspace.env),
            '[ "$*" = "-p process.platform" ] && { echo "wrappedos"; exit; }',
        ),
    }


def _script(path: Path, real: str, branch: str) -> str:
    path.write_text(
        f'#!/usr/bin/env bash\nREAL="{real}"\n{branch}\nexec "$REAL" "$@"\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path.parent)


def with_path_prefix(workspace: Fixture, bin_dir: str) -> dict[str, str]:
    return {"PATH": os.pathsep.join([bin_dir, workspace.env["PATH"]])}


TOOL_INPUTS = [
    "rustc",
    "cargo-llvm-cov",
    "cargo-nextest",
    "llvm-profdata",
    "llvm-cov",
    "llvm-readobj",
    "node",
]


def changed_input_problems(
    workspace: Fixture, env: dict[str, str], expected: set[str], name: str
) -> list[str]:
    done = workspace.coverage(env=env)
    executed = {u for u, hit in workspace.summary(done.stdout).items() if not hit}
    missing = sorted(expected - executed)
    workspace.reset()
    return [f"{unit} replayed after {name} changed" for unit in missing]


@pytest.mark.parametrize("tool", TOOL_INPUTS)
def test_changing_a_tool_version_re_runs_every_unit_naming_it(
    workspace, tmp_path, tool
):
    bins = tmp_path / "bin"
    wrappers(workspace, bins)
    for other in TOOL_INPUTS:
        if other != tool:
            (bins / other).unlink()
    env = with_path_prefix(workspace, str(bins))
    assert changed_input_problems(workspace, env, set(UNITS), tool) == []


def test_changing_the_selecting_environment_variable_re_runs_every_unit(workspace):
    env = {UNIT_ENV: "alt"}
    assert changed_input_problems(workspace, env, set(UNITS), UNIT_ENV) == []


def test_changing_the_ignore_regex_re_runs_every_unit(workspace):
    env = {"COVERAGE_UNIT_IGNORE_REGEX": "never-matches"}
    assert changed_input_problems(workspace, env, set(UNITS), "the ignore regex") == []


def test_changing_the_package_version_re_runs_the_unit_that_keeps_it(workspace):
    """The manifest stays put, so only the version can move the key and fail the comparison."""
    try:
        (workspace.repo / "Cargo.toml").write_text(
            (workspace.repo / "Cargo.toml").read_text().replace('"1.0.0"', '"1.0.9"')
        )
        lock = workspace.repo / "Cargo.lock"
        lock.write_text(lock.read_text().replace('"1.0.0"', '"1.0.9"'))
        done = workspace.coverage(check=False)
        assert workspace.summary(done.stdout) == {
            **dict.fromkeys(UNITS, True),
            "release-check": False,
        }, "package version changed"
        assert done.returncode != 0, done.stdout
        assert "release/manifest.txt states another version" in done.stdout
    finally:
        workspace.reset()


def test_a_key_without_the_input_replays_and_the_proof_fails(workspace, tmp_path):
    bins = tmp_path / "bin"
    wrappers(workspace, bins)
    for other in TOOL_INPUTS:
        if other != "rustc":
            (bins / other).unlink()
    env = with_path_prefix(workspace, str(bins))
    path = workspace.repo / project_path("base")
    original = path.read_text()
    project = json.loads(original)
    project["targets"]["test"]["inputs"] = [
        item for item in project["targets"]["test"]["inputs"] if item != "coverageTools"
    ] + [
        item
        for item in json.loads((workspace.repo / "nx.json").read_text())["namedInputs"][
            "coverageTools"
        ]
        if "--tool-version rustc" not in json.dumps(item)
    ]
    path.write_text(json.dumps(project))
    try:
        workspace.coverage()
        problems = changed_input_problems(workspace, env, {"base"}, "rustc")
        assert problems == ["base replayed after rustc changed"]
    finally:
        path.write_text(original)
        workspace.reset()
