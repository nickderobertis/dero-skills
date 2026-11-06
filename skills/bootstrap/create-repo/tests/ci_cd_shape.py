"""Compose a repository with the CI/CD shape, the way a bootstrap agent does.

Shared by ``e2e/test_ci_cd_shape_e2e.py`` and
``external/test_ci_cd_shape_external.py``: an otherwise-conformant repository
(``test_check_repo_baseline.make_repo``) whose justfile is the shipped template,
handed to the real composer, ``compose_repo_plan.py --ci-cd``, as a subprocess.
"""

from __future__ import annotations

import json
import re
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
