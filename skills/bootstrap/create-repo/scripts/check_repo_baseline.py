# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Audit a repository against the create-repo baseline invariants.

Usage:
    uv run --script scripts/check_repo_baseline.py [REPO_DIR]   # default: .

Stack-agnostic on purpose: this checks the invariants the create-repo skill
prescribes for *every* repository, regardless of language. Stack-specific gates
(ruff, biome, clippy, shellcheck, ...) belong in the repo's own `just check`,
not here.

Checks:
  * AGENTS.md exists at the repo root (the durable instruction layer).
  * If CLAUDE.md exists, it is a symlink that resolves to AGENTS.md (no drift).
  * A justfile is present and defines the core command surface:
    bootstrap, check, test, lint, format, upgrade.
  * At least one CI workflow exists under .github/workflows/.

Prints one line per finding. Exit code is non-zero when any required invariant
fails (ERROR); advisory notes (WARN) never fail the run. Self-contained via
PEP 723 so it runs in any consuming repo with `uv run --script`.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Recipes the skill's command surface must define.
REQUIRED_RECIPES = ("bootstrap", "check", "test", "lint", "format", "upgrade")

# A justfile recipe header starts at column 0 with an identifier, may take
# parameters, and ends in a single ':'. Assignments (`name := value`) are
# excluded via the negative lookahead so they are not mistaken for recipes.
RECIPE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[^\n:=]*:(?!=)")

JUSTFILE_NAMES = ("justfile", "Justfile", ".justfile")


@dataclass
class Finding:
    level: str  # "OK" | "WARN" | "ERROR"
    message: str


def parse_just_recipes(text: str) -> set[str]:
    """Return the set of recipe names defined in justfile ``text``."""
    recipes: set[str] = set()
    for line in text.splitlines():
        if not line or line[0] in (" ", "\t", "#"):
            # Recipe bodies are indented; comments and blanks are not headers.
            continue
        match = RECIPE_RE.match(line)
        if match:
            recipes.add(match.group(1))
    return recipes


def find_justfile(repo: Path) -> Path | None:
    for name in JUSTFILE_NAMES:
        candidate = repo / name
        if candidate.is_file():
            return candidate
    return None


def check_agents_md(repo: Path) -> list[Finding]:
    if (repo / "AGENTS.md").is_file():
        return [Finding("OK", "AGENTS.md present")]
    return [Finding("ERROR", "missing AGENTS.md (the durable instruction layer)")]


def check_claude_symlink(repo: Path) -> list[Finding]:
    claude = repo / "CLAUDE.md"
    if not claude.exists() and not claude.is_symlink():
        return [Finding("WARN", "no CLAUDE.md; symlink it to AGENTS.md if you use Claude")]
    if not claude.is_symlink():
        return [Finding("ERROR", "CLAUDE.md is a regular file; it must be a symlink to AGENTS.md")]
    # Resolve the link target relative to the repo and compare to AGENTS.md.
    target = (claude.parent / claude.readlink()).resolve()
    if target == (repo / "AGENTS.md").resolve():
        return [Finding("OK", "CLAUDE.md is a symlink to AGENTS.md")]
    return [Finding("ERROR", f"CLAUDE.md symlink points to {target}, not AGENTS.md")]


def check_justfile(repo: Path) -> list[Finding]:
    justfile = find_justfile(repo)
    if justfile is None:
        return [Finding("ERROR", "no justfile (the skill's default command runner)")]
    findings = [Finding("OK", f"{justfile.name} present")]
    recipes = parse_just_recipes(justfile.read_text(encoding="utf-8"))
    missing = [r for r in REQUIRED_RECIPES if r not in recipes]
    if missing:
        findings.append(
            Finding("ERROR", f"{justfile.name} missing required recipe(s): {', '.join(missing)}")
        )
    else:
        findings.append(Finding("OK", "justfile defines the full command surface"))
    return findings


def check_ci(repo: Path) -> list[Finding]:
    workflows = repo / ".github" / "workflows"
    if workflows.is_dir() and any(
        p.suffix in (".yml", ".yaml") for p in workflows.iterdir() if p.is_file()
    ):
        return [Finding("OK", "CI workflow present under .github/workflows/")]
    return [Finding("ERROR", "no CI workflow under .github/workflows/")]


def audit(repo: Path) -> list[Finding]:
    findings: list[Finding] = []
    findings += check_agents_md(repo)
    findings += check_claude_symlink(repo)
    findings += check_justfile(repo)
    findings += check_ci(repo)
    return findings


def has_errors(findings: list[Finding]) -> bool:
    return any(f.level == "ERROR" for f in findings)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "repo", nargs="?", default=".", help="path to the repository (default: .)"
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"ERROR not a directory: {repo}", file=sys.stderr)
        return 2

    findings = audit(repo)
    for finding in findings:
        stream = sys.stderr if finding.level == "ERROR" else sys.stdout
        print(f"{finding.level:<5} {finding.message}", file=stream)

    if has_errors(findings):
        errors = sum(1 for f in findings if f.level == "ERROR")
        print(f"FAIL  {repo} ({errors} invariant(s) failed)", file=sys.stderr)
        return 1
    print(f"OK    {repo} (baseline invariants satisfied)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
