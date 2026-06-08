#!/usr/bin/env python3
"""Smoke-check the runnable scripts bundled in an Agent Skill.

Usage:
    uv run python tools/smoke_skill_scripts.py skills/<scope>/<skill-name>
    python3 tools/smoke_skill_scripts.py skills/<scope>/<skill-name>

This does NOT execute scripts with side effects. It only checks that each
bundled script parses / compiles, so a broken script is caught in CI before a
consuming repo installs it:

  * .py            -> byte-compile (py_compile)
  * .mjs/.cjs/.js  -> `node --check` (skipped with a note if node is absent)
  * .sh/.bash      -> `bash -n`     (skipped with a note if bash is absent)

A skill with no scripts/ directory passes trivially. Exit code is non-zero if
any script fails to parse.
"""

from __future__ import annotations

import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

PY_SUFFIXES = {".py"}
NODE_SUFFIXES = {".mjs", ".cjs", ".js"}
SH_SUFFIXES = {".sh", ".bash"}
ALL_SUFFIXES = PY_SUFFIXES | NODE_SUFFIXES | SH_SUFFIXES


def check_python(path: Path) -> tuple[bool, str]:
    try:
        py_compile.compile(str(path), doraise=True)
        return True, "py_compile ok"
    except py_compile.PyCompileError as exc:
        return False, f"py_compile failed: {exc.msg.strip()}"


def check_with(cmd: list[str], path: Path, tool: str) -> tuple[bool | None, str]:
    if shutil.which(cmd[0]) is None:
        return None, f"{tool} not installed; skipped syntax check"
    proc = subprocess.run([*cmd, str(path)], capture_output=True, text=True)
    if proc.returncode == 0:
        return True, f"{tool} --check ok"
    detail = (proc.stderr or proc.stdout).strip().splitlines()
    return False, f"{tool} check failed: {detail[-1] if detail else 'unknown error'}"


def smoke(skill_dir: Path) -> int:
    scripts_dir = skill_dir / "scripts"
    rel_skill = _rel(skill_dir)
    if not scripts_dir.is_dir():
        print(f"OK    {rel_skill}: no scripts/ directory")
        return 0

    scripts = [
        p
        for p in sorted(scripts_dir.rglob("*"))
        if p.is_file() and p.suffix in ALL_SUFFIXES
    ]
    if not scripts:
        print(f"OK    {rel_skill}: scripts/ has no runnable scripts")
        return 0

    failed = 0
    for path in scripts:
        rel = _rel(path)
        if path.suffix in PY_SUFFIXES:
            ok, msg = check_python(path)
        elif path.suffix in NODE_SUFFIXES:
            ok, msg = check_with(["node", "--check"], path, "node")
        else:
            ok, msg = check_with(["bash", "-n"], path, "bash")

        if ok is None:
            print(f"SKIP  {rel}: {msg}")
        elif ok:
            print(f"OK    {rel}: {msg}")
        else:
            print(f"ERROR {rel}: {msg}", file=sys.stderr)
            failed += 1

    if failed:
        print(f"FAIL  {rel_skill} ({failed} script(s) failed)", file=sys.stderr)
        return 1
    print(f"OK    {rel_skill}: all scripts parsed")
    return 0


def _rel(path: Path) -> Path:
    try:
        return path.resolve().relative_to(Path.cwd())
    except ValueError:
        return path


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: smoke_skill_scripts.py <skill-dir>", file=sys.stderr)
        return 2
    skill_dir = Path(argv[0]).resolve()
    if not skill_dir.is_dir():
        print(f"ERROR not a directory: {skill_dir}", file=sys.stderr)
        return 1
    return smoke(skill_dir)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
