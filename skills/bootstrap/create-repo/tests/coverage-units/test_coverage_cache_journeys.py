"""A composed Rust workspace's coverage units, through real Nx, cargo-llvm-cov and both scripts.

Every merge proof compares results over one and the same set of LCOV files, because a coverage
figure can move between identical runs and a cached result freezes one such sample.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from coverage_fixture import (
    UNITS,
    Fixture,
    figures,
    merge_figures,
    read_lcov,
    ROOT_PROJECT,
)


_edits = iter(range(1, 100000))


def unique(text: str = "edited") -> str:
    """Never the same bytes twice: a repeated edit is a real cache hit on an earlier key."""
    return f"{text} {next(_edits)}"


def edit(workspace: Fixture, relative: str, old: str, new: str) -> None:
    path = workspace.repo / relative
    text = path.read_text()
    assert old in text, relative
    path.write_text(text.replace(old, new, 1))


def report_paths(workspace: Fixture) -> list[Path]:
    return [workspace.reports / f"{unit}.lcov" for unit in UNITS]


def assert_merge_matches_own_sum(workspace: Fixture, floor: str = "100") -> None:
    done = workspace.merge(floor=floor)
    covered, total, uncovered = figures(read_lcov(report_paths(workspace)))
    assert merge_figures(done.stdout) == (covered, total, uncovered)
    below = covered * 10000 < int(floor) * 100 * total
    assert done.returncode == (1 if below else 0)


def test_a_second_run_replays_every_unit(workspace):
    assert workspace.run_coverage_summary() == dict.fromkeys(UNITS, True)


def test_editing_a_file_no_unit_reads_replays_every_unit(workspace):
    try:
        (workspace.repo / "README.md").write_text(f"# {unique()}\n")
        assert workspace.run_coverage_summary() == dict.fromkeys(UNITS, True)
    finally:
        workspace.reset()


LIB = "crates/tool/src/lib.rs"
BASE = "crates/base/src/lib.rs"
EVERY = set(UNITS)
EDITS = {
    "a crate's source": (LIB, "+ 1\n", "+ 1 // edited\n", {"tool", "tool-e2e"}),
    "a crate's test": (
        "crates/tool-e2e/tests/spawn.rs",
        "#[test]",
        "// edited\n#[test]",
        {"tool-e2e"},
    ),
    "a test of a crate others depend on": (
        "crates/base/tests/base.rs",
        "#[test]",
        "// edited\n#[test]",
        {"base"},
    ),
    "a source of the crate another depends on": (
        BASE,
        "pub fn uncovered",
        "// edited\npub fn uncovered",
        {"base", "tool", "tool-e2e"},
    ),
    "Cargo.lock": ("Cargo.lock", "[[package]]", "# edited\n[[package]]", EVERY),
    "rust-toolchain.toml": (
        "rust-toolchain.toml",
        "[toolchain]",
        "# edited\n[toolchain]",
        EVERY,
    ),
    "the cargo-llvm-cov pin": (
        ".github/ci-tool-versions",
        "cargo-llvm-cov=0.9.1",
        "cargo-llvm-cov=0.9.2",
        EVERY,
    ),
    "the nextest pin": (
        ".github/ci-tool-versions",
        "cargo-nextest=0.9.150",
        "cargo-nextest=0.9.151",
        EVERY,
    ),
    "a data file only one unit's run reads": (
        "crates/tool-e2e/data/expected.txt",
        "1782",
        "1782",
        {"tool-e2e"},
    ),
}


@pytest.mark.parametrize("name", EDITS, ids=list(EDITS))
def test_an_edit_re_runs_exactly_the_units_that_read_it(workspace, name):
    relative, old, new, expected = EDITS[name]
    try:
        if old == new:
            (workspace.repo / relative).write_text(
                (workspace.repo / relative).read_text() + "\n"
            )
        else:
            edit(workspace, relative, old, new.replace("edited", unique()))
        assert workspace.run_coverage_executed() == expected, name
    finally:
        workspace.reset()


def bump_version(workspace: Fixture) -> None:
    version = f"1.0.{next(_edits)}"
    edit(workspace, "Cargo.toml", 'version = "1.0.0"', f'version = "{version}"')
    lock = workspace.repo / "Cargo.lock"
    lock.write_text(
        lock.read_text().replace('version = "1.0.0"', f'version = "{version}"')
    )


def test_a_version_only_bump_replays_all_but_the_unit_keeping_the_version(workspace):
    try:
        bump_version(workspace)
        assert workspace.run_coverage_executed() == {"release-check"}
    finally:
        workspace.reset()


def test_the_spawning_e2e_unit_counts_lines_only_its_journeys_execute(workspace):
    try:
        edit(
            workspace,
            "crates/tool-e2e/tests/spawn.rs",
            "#[test]",
            f"// {unique()}\n#[test]",
        )
        done = workspace.coverage()
        replayed = workspace.summary(done.stdout)
        assert replayed["tool"] is True and replayed["tool-e2e"] is False
        tool_only = read_lcov([workspace.reports / "tool.lcov"])
        spawned = read_lcov([workspace.reports / "tool-e2e.lcov"])
        main = "crates/tool/src/main.rs"
        assert spawned[main][2] is True
        assert not tool_only.get(main, {}).get(2)
        merged = read_lcov(report_paths(workspace))
        assert merged[main][2] is True
    finally:
        workspace.reset()


def test_the_merge_equals_a_line_wise_sum_when_every_unit_but_one_replayed(workspace):
    try:
        edit(
            workspace, "crates/base/tests/base.rs", "#[test]", f"// {unique()}\n#[test]"
        )
        assert workspace.run_coverage_executed() == {"base"}
        assert_merge_matches_own_sum(workspace)
    finally:
        workspace.reset()


def test_the_merge_equals_a_line_wise_sum_across_a_version_only_bump(workspace):
    try:
        bump_version(workspace)
        assert workspace.run_coverage_executed() == {"release-check"}
        assert_merge_matches_own_sum(workspace)
    finally:
        workspace.reset()


def test_every_unit_executed_equals_cargo_llvm_covs_own_report_and_needs_no_build(
    workspace,
):
    try:
        ran = workspace.nx(
            "run",
            f"{ROOT_PROJECT}:coverage",
            "--outputStyle=static",
            "--skip-nx-cache",
        )
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert not any(workspace.summary(ran.stdout).values())
        combined = workspace.repo / "target/combined.lcov"
        own = subprocess.run(
            ["cargo", "llvm-cov", "report", "--lcov", "--output-path", str(combined)],
            cwd=workspace.repo,
            env=workspace.env,
            capture_output=True,
            text=True,
        )
        assert own.returncode == 0, own.stderr
        merged = read_lcov(report_paths(workspace))
        report = {
            re.sub(rf"^{re.escape(str(workspace.repo.resolve()))}/", "", path): lines
            for path, lines in read_lcov([combined]).items()
        }
        assert merged == report, "zero files may differ"
        before = workspace.merge(floor="100")
        cov = workspace.repo / "target/llvm-cov-target"
        shutil.rmtree(cov)
        after = workspace.merge(floor="100")
        assert (after.returncode, after.stdout) == (before.returncode, before.stdout)
    finally:
        shutil.rmtree(workspace.repo / "target", ignore_errors=True)
        workspace.git("checkout", ".")
        workspace.coverage()
        workspace.reset()


def test_a_stale_report_of_another_commit_never_reaches_the_merge(workspace):
    fresh = {unit: (workspace.reports / f"{unit}.lcov").read_bytes() for unit in UNITS}
    planted = "SF:crates/base/src/lib.rs\nDA:1,0\nDA:2,0\nDA:90,0\nend_of_record\n"
    try:
        edit(
            workspace,
            "crates/tool-e2e/tests/spawn.rs",
            "#[test]",
            f"// {unique()}\n#[test]",
        )
        for unit in UNITS:
            (workspace.reports / f"{unit}.lcov").write_text(planted)
        summary = workspace.run_coverage_summary()
        assert summary["tool-e2e"] is False and summary["base"] is True
        for unit in ("base", "tool", "release-check"):
            assert (workspace.reports / f"{unit}.lcov").read_bytes() == fresh[unit]
        e2e = (workspace.reports / "tool-e2e.lcov").read_text()
        assert "DA:90,0" not in e2e and "end_of_record" in e2e
        assert_merge_matches_own_sum(workspace)
        stray = workspace.reports / "orphan.lcov"
        stray.write_text(planted)
        refused = workspace.merge()
        assert refused.returncode == 2 and "orphan.lcov" in refused.stderr
        stray.unlink()
        (workspace.reports / "base.lcov").rename(workspace.reports / "base.lcov.gone")
        (workspace.reports / "base.lcov.gone").unlink()
        missing = workspace.merge()
        assert missing.returncode == 2 and "base" in missing.stderr
    finally:
        shutil.rmtree(workspace.reports, ignore_errors=True)
        workspace.git("checkout", ".")
        workspace.coverage()
        workspace.reset()


def _reports_trimmed_to(workspace: Fixture, target: Path, total: int) -> str:
    """The fixture's real reports, copied with their lines cut to ``total`` in file order."""
    seen: set[tuple[str, str]] = set()
    remaining = total
    text = ""
    for path in report_paths(workspace):
        kept = []
        current = ""
        for line in path.read_text().splitlines():
            if line.startswith("SF:"):
                current = line[3:]
            if line.startswith("DA:"):
                key = (current, line[3:].split(",")[0])
                if key not in seen:
                    if remaining == 0:
                        continue
                    remaining -= 1
                    seen.add(key)
            kept.append(line)
        text = "\n".join(kept) + "\n"
        (target / path.name).write_text(text)
    assert remaining == 0, "the fixture holds at least that many lines"
    return text


def _line_covered_by_only_one_report(directory: Path) -> tuple[Path, str]:
    reports = sorted(directory.glob("*.lcov"))
    coverage = {path: read_lcov([path]) for path in reports}
    for path, lines in coverage.items():
        for file, by_line in lines.items():
            for number, hit in by_line.items():
                others = [
                    other
                    for other, entries in coverage.items()
                    if other != path and entries.get(file, {}).get(number)
                ]
                if hit and not others:
                    return path, str(number)
    raise AssertionError("every covered line is shared")


def test_merge_at_an_exact_floor_passes_and_one_line_under_fails_naming_it(
    workspace, tmp_path
):
    _reports_trimmed_to(workspace, tmp_path, 80)
    done = workspace.merge(directory=tmp_path, floor="0")
    covered, total, uncovered = merge_figures(done.stdout)
    assert total == 80
    floor = f"{covered * 100 // total}.{covered * 10000 // total % 100:02d}"
    assert workspace.merge(directory=tmp_path, floor=floor).returncode == 0
    # A line only one report covers, turned uncovered there.
    sole = _line_covered_by_only_one_report(tmp_path)
    name, line = sole
    source = name.read_text()
    name.write_text(
        re.sub(rf"(?m)^DA:{line},[1-9]\d*", f"DA:{line},0", source, count=1)
    )
    below = workspace.merge(directory=tmp_path, floor=floor)
    assert below.returncode == 1
    assert re.search(rf": .*\b{line}\b", below.stdout)


def test_merge_counts_a_line_covered_by_only_one_unit_once_as_covered(
    workspace, tmp_path
):
    real = (workspace.reports / "base.lcov").read_text()
    number = re.search(r"^DA:(\d+),[1-9]", real, re.M).group(1)
    (tmp_path / "a.lcov").write_text(
        re.sub(rf"(?m)^DA:{number},\d+", f"DA:{number},0", real)
    )
    (tmp_path / "b.lcov").write_text(real)
    merged = workspace.merge("a", "b", directory=tmp_path)
    expected = read_lcov([workspace.reports / "base.lcov"])
    merged_figures = merge_figures(merged.stdout)
    assert (merged_figures.covered, merged_figures.total) == (
        figures(expected).covered,
        figures(expected).total,
    )


def test_merge_refuses_an_absolute_path(workspace, tmp_path):
    real = (workspace.reports / "base.lcov").read_text()
    path = real.split("\n")[0][3:]
    (tmp_path / "a.lcov").write_text(
        real.replace(f"SF:{path}", "SF:/root/elsewhere.rs", 1)
    )
    done = workspace.merge("a", directory=tmp_path)
    assert done.returncode == 2 and "/root/elsewhere.rs" in done.stderr


def test_the_ignore_regex_a_repository_adds_drops_those_files_from_the_unit(workspace):
    try:
        done = subprocess.run(
            [
                "bash",
                "scripts/coverage-unit.sh",
                "base",
                "--",
                "nextest",
                "--locked",
                "-p",
                "base",
            ],
            cwd=workspace.repo,
            env={**workspace.env, "COVERAGE_UNIT_IGNORE_REGEX": "crates/base/src/"},
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr
        assert not any(
            "crates/base/src/" in path
            for path in read_lcov([workspace.reports / "base.lcov"])
        )
    finally:
        workspace.reset()


def test_a_unit_whose_run_finds_no_instrumented_object_fails_naming_it(
    workspace, tmp_path
):
    real_cargo = shutil.which("cargo", path=workspace.env["PATH"])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    double = bin_dir / "cargo"
    double.write_text(
        f'#!/usr/bin/env bash\n"{real_cargo}" "$@"\nstatus=$?\n'
        'rm -rf target/llvm-cov-target/debug\nexit "$status"\n'
    )
    double.chmod(0o755)
    done = subprocess.run(
        [
            "bash",
            "scripts/coverage-unit.sh",
            "base",
            "--",
            "nextest",
            "--locked",
            "-p",
            "base",
        ],
        cwd=workspace.repo,
        env={**workspace.env, "PATH": f"{bin_dir}:{workspace.env['PATH']}"},
        capture_output=True,
        text=True,
    )
    try:
        assert done.returncode == 1
        assert "no object" in done.stderr and "base" in done.stderr
    finally:
        workspace.reset()


def test_the_composer_holds_the_generated_check_to_the_lcov_floor(workspace):
    command = json.loads((workspace.repo / "project.json").read_text())["targets"][
        "coverage"
    ]["command"]
    assert "--fail-under-lines 96 " in command
    for unit in UNITS:
        assert unit in command
