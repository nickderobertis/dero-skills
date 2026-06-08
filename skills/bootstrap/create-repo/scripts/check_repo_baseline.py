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
  * CLAUDE.md is a symlink that resolves to AGENTS.md (no drift).
  * .claude/settings.json exists and is valid JSON (the agent allowlist).
  * A justfile is present and defines the core command surface:
    bootstrap, check, test, lint, format, upgrade.
  * At least one CI workflow exists under .github/workflows/.

Output is itself agent context, so it is minimal: on success it prints a single
line; on failure it prints only the failing invariants, each with a suggested
fix. Exit code is non-zero when any required invariant fails (ERROR); advisory
notes (WARN) never fail the run. Self-contained via PEP 723 so it runs in any
consuming repo with `uv run --script`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
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
    fix: str = field(default="")  # suggested action, shown only for non-OK findings


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
    return [
        Finding(
            "ERROR",
            "missing AGENTS.md (the durable instruction layer)",
            "create AGENTS.md at the repo root (see the create-repo skill's "
            "assets/AGENTS.md.template)",
        )
    ]


def check_claude_symlink(repo: Path) -> list[Finding]:
    claude = repo / "CLAUDE.md"
    if not claude.is_symlink():
        if claude.exists():
            return [
                Finding(
                    "ERROR",
                    "CLAUDE.md is a regular file, not a symlink to AGENTS.md",
                    "rm CLAUDE.md && ln -s AGENTS.md CLAUDE.md",
                )
            ]
        return [
            Finding(
                "ERROR",
                "missing CLAUDE.md symlink",
                "ln -s AGENTS.md CLAUDE.md",
            )
        ]
    target = (claude.parent / claude.readlink()).resolve()
    if target == (repo / "AGENTS.md").resolve():
        return [Finding("OK", "CLAUDE.md is a symlink to AGENTS.md")]
    return [
        Finding(
            "ERROR",
            f"CLAUDE.md symlink points to {target}, not AGENTS.md",
            "ln -sf AGENTS.md CLAUDE.md",
        )
    ]


def check_claude_settings(repo: Path) -> list[Finding]:
    settings = repo / ".claude" / "settings.json"
    if not settings.is_file():
        return [
            Finding(
                "ERROR",
                "missing .claude/settings.json (agent permission allowlist)",
                "add .claude/settings.json with a narrow permissions.allow list "
                "(see assets/claude-settings.json.template)",
            )
        ]
    try:
        json.loads(settings.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [
            Finding(
                "ERROR",
                f".claude/settings.json is not valid JSON ({exc.msg} at line {exc.lineno})",
                "fix the JSON syntax in .claude/settings.json",
            )
        ]
    return [Finding("OK", ".claude/settings.json present and valid")]


def check_justfile(repo: Path) -> list[Finding]:
    justfile = find_justfile(repo)
    if justfile is None:
        return [
            Finding(
                "ERROR",
                "no justfile (the skill's default command runner)",
                "add a justfile with the standard recipes "
                "(see assets/justfile.template)",
            )
        ]
    findings = [Finding("OK", f"{justfile.name} present")]
    recipes = parse_just_recipes(justfile.read_text(encoding="utf-8"))
    missing = [r for r in REQUIRED_RECIPES if r not in recipes]
    if missing:
        joined = ", ".join(missing)
        findings.append(
            Finding(
                "ERROR",
                f"{justfile.name} missing required recipe(s): {joined}",
                f"add recipe(s) to the {justfile.name}: {joined}",
            )
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
    return [
        Finding(
            "ERROR",
            "no CI workflow under .github/workflows/",
            "add a workflow that runs `just check` on a clean checkout "
            "(see references/ci.md)",
        )
    ]


def audit(repo: Path) -> list[Finding]:
    findings: list[Finding] = []
    findings += check_agents_md(repo)
    findings += check_claude_symlink(repo)
    findings += check_claude_settings(repo)
    findings += check_justfile(repo)
    findings += check_ci(repo)
    return findings


def has_errors(findings: list[Finding]) -> bool:
    return any(f.level == "ERROR" for f in findings)


def _emit(finding: Finding, stream) -> None:
    print(f"{finding.level:<5} {finding.message}", file=stream)
    if finding.fix:
        print(f"      fix: {finding.fix}", file=stream)


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
    warnings = [f for f in findings if f.level == "WARN"]
    errors = [f for f in findings if f.level == "ERROR"]

    # Output is agent context: stay quiet on success, be specific on failure.
    for finding in warnings:
        _emit(finding, sys.stdout)
    for finding in errors:
        _emit(finding, sys.stderr)

    if errors:
        print(f"FAIL  {repo} ({len(errors)} invariant(s) failed)", file=sys.stderr)
        return 1

    note = f" ({len(warnings)} advisory note(s))" if warnings else ""
    print(f"OK    baseline invariants satisfied{note}: {repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
