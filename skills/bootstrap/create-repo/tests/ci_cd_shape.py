"""Compose a repository with the CI/CD shape, the way a bootstrap agent does.

Shared by ``e2e/test_ci_cd_shape_e2e.py`` and
``external/test_ci_cd_shape_external.py``: an otherwise-conformant repository
(``test_check_repo_baseline.make_repo``) whose justfile is the shipped template,
handed to the real composer, ``compose_repo_plan.py --ci-cd``, as a subprocess.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from test_check_repo_baseline import make_repo

SKILL_DIR = Path(__file__).resolve().parents[1]
COMPOSER = SKILL_DIR / "scripts" / "compose_repo_plan.py"
CHECKER = SKILL_DIR / "scripts" / "check_repo_baseline.py"
GOVERNANCE = SKILL_DIR / "scripts" / "setup_github_governance.py"
ASSETS = SKILL_DIR / "assets"
FIXTURES = SKILL_DIR / "tests" / "fixtures"
NAME = "demo"

# The compositions the shape is proven over: each language's toolchain, cross-platform
# and Linux-only, and a second visual-docs architecture.
COMPOSITIONS = {
    "rust-cross-platform": ("rust", "cross-platform", None),
    "rust-linux": ("rust", "linux", None),
    "typescript-cross-platform": ("typescript", "cross-platform", None),
    "typescript-linux": ("typescript", "linux", None),
    "rust-cross-platform-arm64-docs": ("rust", "cross-platform", "arm64"),
}

# The template's two stack-specific placeholders, filled the way a new repository
# fills them, so the checker's only findings are the shape's.
FILLED = {
    '@echo "TODO: install toolchain + dependencies (e.g. bun install, then uv sync / '
    'cargo fetch)" && exit 1': "bun install",
    "@echo \"TODO: upgrade each ecosystem's dependencies (e.g. bun update, uv lock "
    '--upgrade, cargo update)"': "bun update",
}


def run(args: list[str], cwd: Path | None = None, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, **kw)


def compose(
    root: Path,
    language: str = "rust",
    platforms: str = "cross-platform",
    visual_docs_arch: str | None = None,
    files: dict[str, str] | None = None,
    shape: str | None = None,
) -> Path:
    """A conformant repository at ``root`` with the shape written into it."""
    justfile = (ASSETS / "justfile.template").read_text(encoding="utf-8")
    for placeholder, body in FILLED.items():
        assert placeholder in justfile, placeholder
        justfile = justfile.replace(placeholder, body)
    root.mkdir(parents=True, exist_ok=True)
    repo = make_repo(root, justfile=justfile, ci=False, notignored=False)
    for relative, text in (files or {}).items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    args = ["uv", "run", "--script", str(COMPOSER), "--ci-cd", str(repo)]
    args += ["--language", language, "--name", NAME, "--platforms", platforms]
    if visual_docs_arch:
        args += ["--visual-docs-arch", visual_docs_arch]
    if shape:
        args += ["--shape", shape]
    done = run(args)
    assert done.returncode == 0, done.stderr
    lock_packages(repo)
    return repo


def lock_packages(repo: Path) -> None:
    """bun.lock as `bun install` writes it for package.json's exact pins."""
    declared = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    entries = "".join(
        f'    "{name}": ["{name}@{version}", "", {{}}, "sha512-x"],\n'
        for name, version in declared["devDependencies"].items()
    )
    (repo / "bun.lock").write_text(
        '{\n  "lockfileVersion": 1,\n  "packages": {\n' + entries + "  }\n}\n",
        encoding="utf-8",
    )


def baseline(repo: Path) -> subprocess.CompletedProcess:
    return run(["uv", "run", "--script", str(CHECKER), str(repo)])


def jobs(workflow: str) -> dict[str, str]:
    """Each job of a workflow to its block's text: the 2-space keys under `jobs:`."""
    lines = workflow[workflow.index("\njobs:\n") + 7 :].splitlines()
    found: dict[str, list[str]] = {}
    current = None
    for line in lines:
        if re.fullmatch(r"  [A-Za-z0-9_-]+:", line):
            current = line.strip()[:-1]
            found[current] = []
        elif current is not None:
            found[current].append(line)
    return {name: "\n".join(body) for name, body in found.items()}


def workflow(repo: Path, name: str) -> str:
    return (repo / ".github" / "workflows" / name).read_text(encoding="utf-8")


# The warm journey: a composed Rust workspace's real `just ci-warm`, then its real `just ci-test`.
# What setup-rust-toolchain exports into both jobs, which reach cargo's fingerprints.
RUST_ENV = {
    "RUSTFLAGS": "-D warnings",
    "CARGO_INCREMENTAL": "0",
    "CARGO_PROFILE_DEV_DEBUG": "0",
    "NX_DAEMON": "false",
}
OLD_PLACEHOLDER = (
    'ci-warm:\n    @echo "TODO: compile what ci-test compiles without running it '
    '(e.g. cargo test --workspace --all-features --no-run)" && exit 1\n'
)


def workspace_files(
    test_compile: str | None, test_command: str = "cargo test -p demo-lib"
) -> dict[str, str]:
    """A real Cargo workspace whose one project owns its test, build and test-compile."""
    targets = {
        "test": {"command": test_command},
        # Release, so it shares no unit with the test build and losing it to a replay shows.
        "build": {"command": "cargo build -p demo-lib --release", "cache": True},
    }
    if test_compile is not None:
        targets["test-compile"] = {"command": test_compile}
    return {
        "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\nresolver = "2"\n',
        "crates/dep/Cargo.toml": (
            '[package]\nname = "demo-dep"\nversion = "0.1.0"\nedition = "2021"\n'
            "[features]\nextra = []\n"
        ),
        "crates/dep/src/lib.rs": "pub fn value() -> u8 { 1 }\n",
        "crates/lib/Cargo.toml": (
            '[package]\nname = "demo-lib"\nversion = "0.1.0"\nedition = "2021"\n'
            '[features]\nextra = ["demo-dep/extra"]\n'
            '[dependencies]\ndemo-dep = { path = "../dep" }\n'
        ),
        "crates/lib/src/lib.rs": (
            "pub fn value() -> u8 { demo_dep::value() }\n"
            "#[cfg(test)]\nmod tests {\n    #[test]\n    fn value() {\n"
            "        assert_eq!(super::value(), 1);\n    }\n}\n"
        ),
        "crates/lib/project.json": json.dumps(
            {"name": "demo-lib", "root": "crates/lib", "targets": targets}
        ),
    }


GOOD_COMPILE = "cargo test -p demo-lib --no-run"


class WarmJourney:
    """A composed Rust repository, committed, with cargo's and Nx's real commands to run."""

    def __init__(
        self,
        root: Path,
        node_modules: Path | None = None,
        test_compile: str | None = GOOD_COMPILE,
        telemetry_plugin: bool = True,
        test_command: str = "cargo test -p demo-lib",
    ) -> None:
        for tool in ("cargo", "just", "bun"):
            assert shutil.which(tool), f"{tool} not on PATH: this runs the real build"
        self.repo = compose(
            root, "rust", "linux", files=workspace_files(test_compile, test_command)
        )
        if node_modules is None:
            # The composed pins, as `just bootstrap` installs them, with a real bun.lock.
            manifest_path = self.repo / "package.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = "1.0.0"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            (self.repo / "bun.lock").unlink()
            installed = run(["bun", "install"], cwd=self.repo, timeout=600)
            assert installed.returncode == 0, installed.stderr
        else:
            os.symlink(node_modules, self.repo / "node_modules")
        if not telemetry_plugin:
            nx_path = self.repo / "nx.json"
            nx = json.loads(nx_path.read_text(encoding="utf-8"))
            nx["plugins"].remove("./scripts/nx-remote-cache.mjs")
            nx_path.write_text(json.dumps(nx), encoding="utf-8")
        gitignore = self.repo / ".gitignore"
        gitignore.write_text(gitignore.read_text() + "\nnode_modules\n.nx/\ntarget/\n")
        self.env = {**os.environ, **RUST_ENV, **self._toolchain(root)}
        # This repository's own CI exports both for its own commits, which this repository's
        # temporary git history does not hold; `test_job` passes the base a pull request tests.
        self.env.pop("NX_BASE", None)
        self.env.pop("NX_HEAD", None)
        self._git("init", "-q")
        self.commit("chore: base")
        self.base = self._git("rev-parse", "HEAD")
        source = self.repo / "crates/lib/src/lib.rs"
        source.write_text(
            "// the change this pull request makes\n" + source.read_text()
        )
        self.commit("fix: a change")

    @staticmethod
    def _toolchain(root: Path) -> dict[str, str]:
        # The host's own compiler, outside the composed pin, so nothing here downloads one.
        rustup = shutil.which("rustup")
        if rustup is None:
            return {}
        shown = run([rustup, "show", "active-toolchain"], cwd=root.parent)
        return {"RUSTUP_TOOLCHAIN": shown.stdout.split()[0]} if shown.stdout else {}

    def _git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", "-c", "user.name=journey", "-c", "user.email=j@example.com", *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    def commit(self, message: str) -> None:
        self._git("add", "-A")
        self._git("commit", "-qm", message)

    def just(self, recipe: str, **env: str):
        return subprocess.run(
            ["just", recipe],
            cwd=self.repo,
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=600,
        )

    def warm(self):
        return self.just("ci-warm")

    def test_job(self):
        # Another runner: it gets the warm leg's target directory, and none of its Nx cache.
        shutil.rmtree(self.repo / ".nx", ignore_errors=True)
        return self.just("ci-test", NX_BASE=self.base)

    def built(self) -> dict[str, int]:
        """Every compiled unit under target/, to when it was written."""
        deps = self.repo / "target"
        return {
            str(path.relative_to(deps)): path.stat().st_mtime_ns
            for path in deps.glob("*/deps/*")
        }


def compiled(done) -> list[str]:
    return re.findall(r"Compiling (\S+)", done.stdout + done.stderr)


def warm_compiles_again_problems(journey: WarmJourney) -> list[str]:
    """A second warm run over an empty target directory builds all the first did: a replayed
    task compiles nothing, and rust-cache would then save nothing a pull request could reuse."""
    first = journey.warm()
    if first.returncode != 0:
        return [f"ci-warm failed: {first.stdout}{first.stderr}"]
    built = set(journey.built())
    if not built:
        return ["the first ci-warm compiled nothing"]
    shutil.rmtree(journey.repo / "target")
    again = journey.warm()
    if again.returncode != 0:
        return [f"ci-warm failed: {again.stdout}{again.stderr}"]
    lost = sorted(built - set(journey.built()))
    return [f"ci-warm replayed from an Nx cache, leaving out {lost}"] if lost else []


def warm_build_problems(journey: WarmJourney) -> list[str]:
    """What a test job finds after a warm leg: empty is a leg that saved what it needs."""
    warmed = journey.warm()
    if warmed.returncode != 0:
        return [f"ci-warm failed: {warmed.stdout}{warmed.stderr}"]
    built = journey.built()
    if not built:
        return ["ci-warm compiled nothing"]
    tested = journey.test_job()
    if tested.returncode != 0:
        return [f"ci-test failed: {tested.stdout}{tested.stderr}"]
    problems = []
    if "test result: ok" not in tested.stdout + tested.stderr:
        problems.append("ci-test ran no test")
    if names := compiled(tested):
        problems.append(f"ci-test compiled again after ci-warm: {names}")
    changed = sorted(
        name for name, at in journey.built().items() if built.get(name) != at
    )
    if changed:
        problems.append(f"ci-test wrote build outputs ci-warm did not: {changed}")
    return problems
