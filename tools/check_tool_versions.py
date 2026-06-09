#!/usr/bin/env python3
"""Check that ``.tool-versions`` and the CI workflows pin consistent versions.

Usage:
    uv run python tools/check_tool_versions.py [REPO_DIR]   # default: .
    python3 tools/check_tool_versions.py [REPO_DIR]

``.tool-versions`` is the source of truth for the local dev toolchain; the CI
workflows under ``.github/workflows/`` pin some of the same tools through their
setup actions (``node-version``, ``python-version``, setup-uv's ``version``,
...). The two pin the toolchain independently, so they can drift silently — for
example ``.tool-versions`` bumps Node to ``24`` while CI still installs
``node-version: "22"``. This check fails when a tool is pinned in *both* places
with conflicting versions.

Semantics:
  * A tool in ``.tool-versions`` is compared to a CI pin only when CI pins it
    too. Tools CI installs unpinned (latest) are not flagged — pinning only
    locally is a deliberate choice (CI proves the artifact against latest).
  * CI may pin a coarser version than ``.tool-versions``: CI ``node-version
    "22"`` is consistent with ``.tool-versions`` ``nodejs 22.12.0``. The shorter
    dotted version must be a prefix of the longer (compared component-wise, so
    "22" matches "22.12.0" but "3.13" does not match "3.12.0").

Stdlib only, like the other authoring tools. Output is agent context: quiet on
success (a single line), and on failure it prints each conflict with the
file:line to fix and a concrete next action. Exit code is non-zero on any
conflict.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ``.tool-versions`` tool name -> the logical tool key used for CI pins. asdf
# spells Node "nodejs"; the setup-node action spells it "node".
TV_TO_CI = {
    "nodejs": "node",
    "node": "node",
    "uv": "uv",
    "just": "just",
    "python": "python",
}

# Unambiguous, tool-specific version inputs in a workflow -> logical tool key.
DIRECT_CI_KEYS = {
    "node-version": "node",
    "python-version": "python",
    "just-version": "just",
}

ACTION_RE = re.compile(r"uses:\s*(\S+)")
# A simple ``key: value`` line, tolerating quotes, trailing comments, and lists.
KV_RE = re.compile(r"^\s*([\w-]+):\s*[\"']?([^\"'#\s]+)")


@dataclass
class CIPin:
    tool: str
    version: str
    file: str
    line: int


@dataclass
class Finding:
    level: str  # "OK" | "ERROR"
    message: str
    fix: str = field(default="")


def parse_tool_versions(text: str) -> dict[str, str]:
    """Return ``{tool: version}`` from ``.tool-versions`` text."""
    versions: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            versions[parts[0]] = parts[1]
    return versions


def parse_ci_pins(text: str, filename: str) -> list[CIPin]:
    """Extract tool version pins from one workflow file.

    Tracks the current ``uses:`` action so the generic ``version:`` input can be
    attributed to setup-uv. Tool-specific keys (``node-version``, ...) are
    unambiguous and need no action context.
    """
    pins: list[CIPin] = []
    current_action = ""
    for i, line in enumerate(text.splitlines(), start=1):
        action = ACTION_RE.search(line)
        if action:
            current_action = action.group(1)
            continue
        kv = KV_RE.match(line)
        if not kv:
            continue
        key, value = kv.group(1), kv.group(2)
        if key in DIRECT_CI_KEYS:
            pins.append(CIPin(DIRECT_CI_KEYS[key], value, filename, i))
        elif key == "version" and "setup-uv" in current_action:
            pins.append(CIPin("uv", value, filename, i))
    return pins


def collect_ci_pins(repo: Path) -> list[CIPin]:
    workflows = repo / ".github" / "workflows"
    if not workflows.is_dir():
        return []
    pins: list[CIPin] = []
    for path in sorted(workflows.iterdir()):
        if path.is_file() and path.suffix in (".yml", ".yaml"):
            rel = str(path.relative_to(repo))
            pins.extend(parse_ci_pins(path.read_text(encoding="utf-8"), rel))
    return pins


def versions_consistent(a: str, b: str) -> bool:
    """True when the shorter dotted version is a prefix of the longer one."""
    pa, pb = a.split("."), b.split(".")
    n = min(len(pa), len(pb))
    return pa[:n] == pb[:n]


def audit(repo: Path) -> list[Finding]:
    tool_versions_file = repo / ".tool-versions"
    if not tool_versions_file.is_file():
        return [
            Finding(
                "ERROR",
                "missing .tool-versions (the source of truth for the toolchain)",
                "create .tool-versions pinning the dev toolchain (e.g. `just`, "
                "`uv`, `nodejs`)",
            )
        ]

    tv = parse_tool_versions(tool_versions_file.read_text(encoding="utf-8"))
    ci_pins = collect_ci_pins(repo)

    findings: list[Finding] = []
    for tv_name, tv_version in tv.items():
        ci_key = TV_TO_CI.get(tv_name)
        if ci_key is None:
            continue
        for pin in (p for p in ci_pins if p.tool == ci_key):
            if not versions_consistent(pin.version, tv_version):
                findings.append(
                    Finding(
                        "ERROR",
                        f"{pin.file}:{pin.line} pins {ci_key} "
                        f'"{pin.version}" but .tool-versions pins '
                        f"{tv_name} {tv_version}",
                        f"align them — update {pin.file} or .tool-versions so "
                        f"the {ci_key} versions agree",
                    )
                )

    if not findings:
        findings.append(Finding("OK", ".tool-versions and CI pins agree"))
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
    errors = [f for f in findings if f.level == "ERROR"]

    for finding in errors:
        _emit(finding, sys.stderr)

    if errors:
        print(f"FAIL  {repo} ({len(errors)} version conflict(s))", file=sys.stderr)
        return 1

    print(f"OK    .tool-versions and CI pins agree: {repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
