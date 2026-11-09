"""The Rust workspace, composed for real, that the coverage-unit journeys run.

Everything below this module is real: the composer writes the shape, `coverage-unit.sh` and
`coverage-merge.mjs` run through this repository's Nx, and cargo-llvm-cov instruments real
crates, at the versions the composed `.github/ci-tool-versions` pins.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

from ci_cd_shape import NAME, compose, ensure_pinned_tools, run

SKILL = Path(__file__).resolve().parents[2]
WORKTREE = SKILL.parents[2]
ROOT_PROJECT = f"{NAME}-coverage"
PINS = ".github/ci-tool-versions"
UNIT_ENV = "FIXTURE_SELECT"
UNITS = ("base", "release-check", "tool", "tool-e2e")
KEEPS_VERSION_RUNTIME = (
    "node scripts/nx-version-free-inputs.mjs --ecosystem cargo --keep-version"
)


def _manifest(name: str, body: str = "") -> str:
    return f'[package]\nname = "{name}"\nversion.workspace = true\nedition = "2021"\n{body}'


def _project(name: str, directory: str, inputs: list[object] | None = None) -> str:
    return json.dumps(
        {
            "name": name,
            "root": directory,
            "targets": {"test": {"inputs": [{"env": UNIT_ENV}, *(inputs or [])]}},
        }
    )


SELECTED = 'std::env::var("FIXTURE_SELECT").unwrap_or_default()'
WORKSPACE = {
    "Cargo.toml": (
        '[workspace]\nmembers = ["crates/*"]\nresolver = "2"\n'
        '[workspace.package]\nversion = "1.0.0"\n'
    ),
    ".cargo/config.toml": '[build]\ntarget-dir = "target"\n[profile.dev]\ndebug = 1\n',
    "README.md": "# A workspace no unit reads\n",
    "crates/base/Cargo.toml": _manifest("base"),
    "crates/base/project.json": _project("base", "crates/base"),
    "crates/base/src/lib.rs": (
        "pub fn value(input: u32) -> u32 {\n    if input > 1 {\n        input\n    } else {\n"
        "        0\n    }\n}\npub fn uncovered() -> u32 {\n    7\n}\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn value() {\n"
        "        assert_eq!(super::value(2), 2);\n    }\n}\n"
    ),
    "crates/base/tests/base.rs": (
        "#[test]\nfn base_low() {\n"
        f"    let selected = {SELECTED};\n"
        '    assert_eq!(base::value(if selected == "alt" { 1 } else { 0 }), 0);\n}\n'
    ),
    "crates/tool/Cargo.toml": _manifest(
        "tool",
        '[lib]\nname = "tool"\n[[bin]]\nname = "tool"\npath = "src/main.rs"\n'
        '[dependencies]\nbase = { path = "../base" }\n',
    ),
    "crates/tool/project.json": _project("tool", "crates/tool"),
    "crates/tool/src/lib.rs": (
        "pub fn greet(input: u32) -> u32 {\n"
        + "".join(f"    let input = input + {i};\n" for i in range(60))
        + "    base::value(input) + 1\n}\n"
        "#[cfg(test)]\nmod tests {\n    #[test]\n    fn greet() {\n"
        "        assert_eq!(super::greet(0), 1771);\n    }\n}\n"
    ),
    "crates/tool/src/main.rs": (
        "fn only_through_a_spawn(value: u32) -> u32 {\n    value + 10\n}\n\n"
        'fn main() {\n    println!("{}", only_through_a_spawn(tool::greet(1)));\n}\n'
    ),
    "crates/tool-e2e/Cargo.toml": _manifest(
        "tool-e2e", 'publish = false\n[dev-dependencies]\ntool = { path = "../tool" }\n'
    ),
    "crates/tool-e2e/project.json": _project("tool-e2e", "crates/tool-e2e"),
    "crates/tool-e2e/src/lib.rs": "",
    "crates/tool-e2e/data/expected.txt": "1782\n",
    "crates/tool-e2e/tests/spawn.rs": (
        "use std::{env, fs, process::Command};\n\n#[test]\nfn the_binary_prints_the_expected_value() {\n"
        '    let mut binary = env::current_exe().unwrap();\n    binary.pop();\n    binary.pop();\n    binary.push("tool");\n'
        '    let run = Command::new(&binary).output().expect("spawn the instrumented tool");\n'
        "    assert!(run.status.success());\n"
        '    let expected = fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/data/expected.txt")).unwrap();\n'
        "    assert_eq!(String::from_utf8_lossy(&run.stdout).trim(), expected.trim());\n"
        "}\n"
    ),
    "crates/release-check/Cargo.toml": _manifest("release-check", "publish = false\n"),
    "crates/release-check/project.json": _project(
        "release-check",
        "crates/release-check",
        ["{workspaceRoot}/release/manifest.txt", {"runtime": KEEPS_VERSION_RUNTIME}],
    ),
    "crates/release-check/src/lib.rs": "pub fn present() -> bool {\n    true\n}\n",
    "crates/release-check/tests/version.rs": (
        "#[test]\nfn the_release_manifest_names_this_build() {\n"
        "    assert!(release_check::present());\n"
        '    let manifest = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../release/manifest.txt")).unwrap();\n'
        '    assert!(manifest.contains("1."), "{manifest}");\n}\n'
    ),
    "release/manifest.txt": "version 1.0.0\n",
}


class Fixture:
    """One composed, committed workspace, with Nx's run summary to read."""

    def __init__(self, root: Path, files: dict[str, str] | None = None) -> None:
        self.repo = compose(root, "rust", "linux", files={**WORKSPACE, **(files or {})})
        os.symlink(WORKTREE / "node_modules", self.repo / "node_modules")
        rustup = shutil.which("rustup")
        toolchain = {}
        if rustup:
            shown = run([rustup, "show", "active-toolchain"], cwd=root.parent)
            if shown.stdout:
                toolchain["RUSTUP_TOOLCHAIN"] = shown.stdout.split()[0]
        self.env = {
            **{
                key: value
                for key, value in os.environ.items()
                if not key.startswith("NX_") and not key.startswith("CARGO_LLVM_COV")
            },
            **toolchain,
            "PATH": os.pathsep.join([*ensure_pinned_tools(), os.environ["PATH"]]),
            "NX_DAEMON": "false",
            # sccache compiles in a daemon, outside the traced process and its fingerprints.
            "RUSTC_WRAPPER": "",
            "CARGO_BUILD_RUSTC_WRAPPER": "",
            UNIT_ENV: "default",
        }
        # The telemetry plugin needs the composed Nx pin; this repository's Nx runs the units.
        nx_path = self.repo / "nx.json"
        nx = json.loads(nx_path.read_text())
        nx["plugins"].remove("./scripts/nx-remote-cache.mjs")
        nx_path.write_text(json.dumps(nx))
        self.git("init", "-q")
        generated = subprocess.run(
            ["cargo", "generate-lockfile"],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
        )
        assert generated.returncode == 0, generated.stderr
        gitignore = self.repo / ".gitignore"
        gitignore.write_text(gitignore.read_text() + "\nnode_modules\n.nx/\ntarget/\n")
        self.commit("chore: base")

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", "-c", "user.name=journey", "-c", "user.email=j@example.com", *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def nx(self, *args: str, env: dict[str, str] | None = None, timeout: int = 900):
        return subprocess.run(
            ["node_modules/.bin/nx", *args],
            cwd=self.repo,
            env={**self.env, **(env or {})},
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def coverage(self, env: dict[str, str] | None = None, check: bool = True):
        done = self.nx(
            "run", f"{ROOT_PROJECT}:coverage", "--outputStyle=static", env=env
        )
        if check:
            assert done.returncode == 0, done.stdout + done.stderr
        return done

    @staticmethod
    def summary(stdout: str) -> dict[str, bool]:
        ran = re.findall(r"^> nx run (\S+):test(?:\s+\[([^\]]*)\])?", stdout, re.M)
        return {project: "cache" in status for project, status in ran}

    def run_coverage_summary(
        self, env: dict[str, str] | None = None
    ) -> dict[str, bool]:
        return self.summary(self.coverage(env=env).stdout)

    def run_coverage_executed(self, env: dict[str, str] | None = None) -> set[str]:
        return {
            unit for unit, hit in self.run_coverage_summary(env=env).items() if not hit
        }

    def reset(self) -> None:
        """Back to the committed tree, every unit replaying."""
        self.git("checkout", ".")
        self.git("clean", "-fdq", "-e", "target", "-e", ".nx", "-e", "node_modules")
        again = self.coverage()
        assert all(self.summary(again.stdout).values()), again.stdout

    @property
    def reports(self) -> Path:
        return self.repo / "target/coverage-units"

    def merge(self, *units: str, floor: str = "0", directory: Path | None = None):
        return subprocess.run(
            [
                "node",
                "scripts/coverage-merge.mjs",
                "--fail-under-lines",
                floor,
                "--dir",
                str(directory or self.reports),
                "--unit",
                *(units or UNITS),
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )


def read_lcov(paths) -> dict[str, dict[int, bool]]:
    """Every line of ``paths``, per file, to whether any report counts it above zero."""
    lines: dict[str, dict[int, bool]] = {}
    for path in paths:
        current = None
        for line in Path(path).read_text().splitlines():
            if line.startswith("SF:"):
                current = lines.setdefault(line[3:], {})
            elif line.startswith("DA:"):
                number, count = line[3:].split(",")[:2]
                assert current is not None
                current[int(number)] = current.get(int(number), False) or int(count) > 0
    return lines


class CoverageFigures(NamedTuple):
    covered: int
    total: int
    uncovered: dict[str, list[int]]


def figures(lines: dict[str, dict[int, bool]]) -> CoverageFigures:
    """Lines covered, lines total and uncovered lines per file."""
    total = covered = 0
    uncovered: dict[str, list[int]] = {}
    for path, by_line in lines.items():
        for number, hit in sorted(by_line.items()):
            total += 1
            covered += hit
            if not hit:
                uncovered.setdefault(path, []).append(number)
    return CoverageFigures(covered, total, uncovered)


def merge_figures(stdout: str) -> CoverageFigures:
    """The figures `coverage-merge.mjs` printed."""
    covered = int(re.search(r"lines covered: (\d+)", stdout).group(1))
    total = int(re.search(r"lines total: (\d+)", stdout).group(1))
    uncovered: dict[str, list[int]] = {}
    if "Uncovered lines:" in stdout:
        for line in stdout.split("Uncovered lines:", 1)[1].strip().splitlines():
            path, _, numbers = line.rpartition(": ")
            uncovered[path] = [int(n) for n in numbers.split(",")]
    return CoverageFigures(covered, total, uncovered)
