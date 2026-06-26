#!/usr/bin/env python3
"""Validate a single Agent Skill folder against the canonical repo rules.

Usage:
    uv run python tools/validate_skill.py skills/<scope>/<skill-name>
    python3 tools/validate_skill.py skills/<scope>/<skill-name>

Stdlib only, on purpose: this is an authoring/CI tool but must stay trivially
runnable without installing anything. It enforces the rules documented in
docs/authoring-skills.md.

Exit code is non-zero when any ERROR is found. WARNINGs never fail the run.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# --- Rules ------------------------------------------------------------------

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REQUIRED_FRONTMATTER = ("name", "description", "compatibility")

# Text extensions we scan inside scripts/ for forbidden runtime dependencies.
SCRIPT_TEXT_SUFFIXES = {".py", ".mjs", ".cjs", ".js", ".ts", ".sh", ".bash"}

# Patterns that indicate a runtime script depends on repo-root / authoring-only
# tooling. Each entry is (compiled_regex, human_message).
FORBIDDEN_RUNTIME = [
    (re.compile(r"\bnx\b", re.IGNORECASE), "references Nx (authoring/CI only)"),
    (re.compile(r"\basdf\b", re.IGNORECASE), "references asdf"),
    (re.compile(r"\bdirenv\b", re.IGNORECASE), "references direnv"),
    (
        re.compile(r"\.\./.*\b(pyproject\.toml|uv\.lock|package\.json|bun\.lock)\b"),
        "reaches outside the skill for a repo-root manifest/lockfile",
    ),
    (
        re.compile(r"^\s*(?:from|import)\s+(?:skills|tools)\b", re.MULTILINE),
        "imports from the skills repo source tree (skills/ or tools/)",
    ),
]

# Heuristic secret / sensitive-data markers. Kept conservative to avoid noise.
SECRET_PATTERNS = [
    (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
        "embedded private key",
    ),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "API secret key (sk-...)"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "value shaped like a US SSN"),
]


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """Parse a leading ``---`` YAML frontmatter block of simple key: value pairs.

    Returns None when no frontmatter block is present. Intentionally minimal so
    we do not depend on PyYAML; only flat string scalars are supported, which is
    all SKILL.md frontmatter needs.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    data: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return data
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip().strip('"').strip("'")
    # No closing delimiter found.
    return None


def validate_skill_md(skill_dir: Path, report: Report) -> None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        report.error("missing required SKILL.md")
        return

    text = skill_md.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    if fm is None:
        report.error("SKILL.md is missing a closing '---' YAML frontmatter block")
        return

    for key in REQUIRED_FRONTMATTER:
        if key not in fm or not fm[key]:
            report.error(f"frontmatter missing required key: {key}")

    name = fm.get("name", "")
    if name:
        if name != skill_dir.name:
            report.error(
                f"frontmatter name '{name}' must match directory basename "
                f"'{skill_dir.name}'"
            )
        if not NAME_RE.match(name):
            report.error(
                f"frontmatter name '{name}' must use lowercase letters, numbers, "
                "and single hyphens only"
            )

    description = fm.get("description", "")
    if description:
        if not description.lower().startswith("use when"):
            report.warn(
                "description should be trigger-oriented and start with 'Use when ...'"
            )
        if len(description) < 25:
            report.warn(
                "description is short; make it specific about when to use the skill"
            )

    # Sensitive-data scan of the prose body.
    scan_text_for_secrets(skill_md, text, report)


def scan_text_for_secrets(path: Path, text: str, report: Report) -> None:
    for pattern, label in SECRET_PATTERNS:
        if pattern.search(text):
            report.error(f"{path.name}: possible {label} — do not commit secrets/PII")


def scan_runtime_scripts(skill_dir: Path, report: Report) -> None:
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        return
    has_local_package_json = (skill_dir / "package.json").is_file()

    for path in sorted(scripts_dir.rglob("*")):
        if not path.is_file() or path.suffix not in SCRIPT_TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(skill_dir)

        for pattern, message in FORBIDDEN_RUNTIME:
            if pattern.search(text):
                report.error(f"{rel}: {message}")

        scan_text_for_secrets(path, text, report)

        # Python runtime scripts are expected to be self-contained via PEP 723
        # so they can run with `uv run --script`.
        if path.suffix == ".py" and "# /// script" not in text:
            report.warn(
                f"{rel}: Python runtime script has no PEP 723 inline metadata; "
                "add a '# /// script' block so it runs with `uv run --script`"
            )

    if has_local_package_json:
        # Allowed, but call it out so the project.json/bun story is intentional.
        report.warn(
            "skill ships a local package.json; ensure a bun.lock and "
            "scripts are documented (bun is allowed only for such skills)"
        )


def validate(skill_dir: Path) -> Report:
    report = Report()
    if not skill_dir.is_dir():
        report.error(f"not a directory: {skill_dir}")
        return report
    validate_skill_md(skill_dir, report)
    scan_runtime_scripts(skill_dir, report)
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: validate_skill.py <skill-dir>", file=sys.stderr)
        return 2
    skill_dir = Path(argv[0]).resolve()
    report = validate(skill_dir)

    rel = skill_dir
    try:
        rel = skill_dir.relative_to(Path.cwd())
    except ValueError:
        pass

    for w in report.warnings:
        print(f"WARN  {rel}: {w}")
    for e in report.errors:
        print(f"ERROR {rel}: {e}", file=sys.stderr)

    if report.errors:
        print(f"FAIL  {rel} ({len(report.errors)} error(s))", file=sys.stderr)
        return 1
    print(f"OK    {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
