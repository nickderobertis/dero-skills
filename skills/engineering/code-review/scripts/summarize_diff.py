# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Summarize a unified git diff to orient a code review.

Self-contained: pipe a diff in, or pass a file.

    git diff origin/main...HEAD | uv run --script scripts/summarize_diff.py
    uv run --script scripts/summarize_diff.py changes.patch

Reports per-file added/removed line counts and a total, and flags files that
are large (many changed lines) or binary so the reviewer knows where to focus.
Stdlib only.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

LARGE_FILE_THRESHOLD = 400  # changed (added + removed) lines


@dataclass
class FileStat:
    path: str
    added: int = 0
    removed: int = 0
    binary: bool = False

    @property
    def changed(self) -> int:
        return self.added + self.removed


@dataclass
class DiffSummary:
    files: list[FileStat] = field(default_factory=list)

    @property
    def total_added(self) -> int:
        return sum(f.added for f in self.files)

    @property
    def total_removed(self) -> int:
        return sum(f.removed for f in self.files)


def parse_diff(text: str) -> DiffSummary:
    summary = DiffSummary()
    current: FileStat | None = None

    for line in text.splitlines():
        if line.startswith("diff --git "):
            # "diff --git a/path b/path" — take the b/ path as canonical.
            parts = line.split(" b/", 1)
            path = parts[1] if len(parts) == 2 else line[len("diff --git "):]
            current = FileStat(path=path)
            summary.files.append(current)
        elif current is None:
            continue
        elif line.startswith("Binary files ") and line.rstrip().endswith("differ"):
            current.binary = True
        elif line.startswith("+++") or line.startswith("---"):
            continue  # file header lines, not content
        elif line.startswith("+"):
            current.added += 1
        elif line.startswith("-"):
            current.removed += 1

    return summary


def render(summary: DiffSummary) -> str:
    if not summary.files:
        return "No changes detected in the diff."

    lines = ["## Diff summary", ""]
    lines.append(f"Files changed: {len(summary.files)}  "
                 f"(+{summary.total_added} / -{summary.total_removed})")
    lines.append("")
    lines.append("| File | +added | -removed | notes |")
    lines.append("| --- | ---: | ---: | --- |")
    for f in sorted(summary.files, key=lambda s: s.changed, reverse=True):
        notes = []
        if f.binary:
            notes.append("binary")
        if f.changed >= LARGE_FILE_THRESHOLD:
            notes.append("large — review carefully")
        lines.append(f"| `{f.path}` | {f.added} | {f.removed} | {', '.join(notes)} |")

    flagged = [f.path for f in summary.files
               if f.binary or f.changed >= LARGE_FILE_THRESHOLD]
    if flagged:
        lines.append("")
        lines.append("Focus first on: " + ", ".join(f"`{p}`" for p in flagged))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize a unified git diff.")
    parser.add_argument("path", nargs="?", help="diff file; reads stdin if omitted")
    args = parser.parse_args(argv)

    text = open(args.path, encoding="utf-8").read() if args.path else sys.stdin.read()
    print(render(parse_diff(text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
