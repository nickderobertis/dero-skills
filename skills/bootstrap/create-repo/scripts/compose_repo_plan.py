# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Compose a tailored repo-creation plan from the create-repo references.

Usage:
    uv run --script scripts/compose_repo_plan.py --shape SHAPE --language LANG \
        [--language LANG ...] [--releasing] [--monorepo] \
        [--intersection NAME ...] [-o OUT.md]
    uv run --script scripts/compose_repo_plan.py --list

You describe the repo with flags — its product shape, the language(s) it is
built in, and the cross-cutting concerns that apply — and this emits a single
self-contained document for *that* stack: the composed guidance from each
selected reference, followed by one verification checklist assembled from the
``## Verification`` section that lives with each reference.

The reference catalog *is* the source of truth: shapes, languages, and
intersections are discovered by scanning ``references/`` next to this script, so
adding a reference file automatically extends the flags. ``ci.md`` is always
included (it applies on top of every shape); ``base.md`` is always included
first (the shape/language-agnostic invariants); ``releasing.md`` and
``monorepo.md`` are pulled in by ``--releasing`` / ``--monorepo``.

Convenience derivations, each announced on stderr so the composition stays
auditable:
  * ``--shape nextjs`` also pulls in ``shapes/web-app.md`` (Next.js builds on it)
    and assumes ``languages/typescript.md`` (auto-added if you didn't pass it).
  * a shape+language with an intersection reference (e.g. ``cli`` + ``python``
    -> ``intersections/python-cli.md``) auto-includes it.

The document goes to stdout (or ``-o FILE``); notes and errors go to stderr, so
the two never mix. Self-contained via PEP 723 so it runs in any consuming repo
with ``uv run --script`` — no dependency on this repo's authoring toolchain.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# The Verification section heading every composable reference carries. Its body
# (the ``- [ ]`` checklist items) is lifted out of the guidance and assembled
# into the plan's single checklist.
VERIFICATION_HEADING_RE = re.compile(r"^##\s+Verification\s*$", re.IGNORECASE)
HEADING_RE = re.compile(r"^#{1,2}\s")
CHECKLIST_ITEM_RE = re.compile(r"^- \[ \]")

# Cross-cutting references that are not a shape/language/intersection: included
# unconditionally (base, ci) or behind a flag (releasing, monorepo). composing.md
# is meta-guidance about *how* to compose and is never itself composed into a repo.
ALWAYS = ("base.md", "ci.md")

# Shape+language pairs that have a dedicated intersection reference. Keyed so the
# composer can auto-include the intersection when both axes are present. The file
# must exist on disk (checked before adding) — this map only records the pairing.
AUTO_INTERSECTIONS = {
    ("cli", "python"): "python-cli",
    ("cli", "rust"): "rust-cli",
}


@dataclass(frozen=True)
class Reference:
    """A composable reference: its repo-relative path, title, and split content."""

    relpath: str
    title: str
    guidance: str
    verification: str  # the raw ``- [ ]`` block, or "" if the section is absent


def discover(refs_dir: Path, sub: str) -> list[str]:
    """Return the sorted stems of ``references/<sub>/*.md`` (the valid flag values)."""
    folder = refs_dir / sub
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.md"))


def split_reference(text: str) -> tuple[str, str]:
    """Split a reference into (guidance, verification-block).

    The verification block is the content under the first ``## Verification``
    heading, up to the next top-level (``#``/``##``) heading or end of file. The
    guidance is everything before that heading. A reference with no Verification
    section yields an empty block.
    """
    lines = text.splitlines()
    idx = next(
        (i for i, line in enumerate(lines) if VERIFICATION_HEADING_RE.match(line)),
        None,
    )
    if idx is None:
        return text.strip("\n"), ""
    guidance = "\n".join(lines[:idx]).strip("\n")
    block: list[str] = []
    for line in lines[idx + 1 :]:
        if HEADING_RE.match(line):
            break
        block.append(line)
    return guidance, "\n".join(block).strip("\n")


def title_of(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def strip_leading_title(text: str) -> str:
    """Drop a leading ``# Title`` line (and the blank after it) from guidance.

    The composer re-emits each reference's title as its own ``###`` heading, so
    keeping the original top-level ``#`` line would duplicate it.
    """
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        if lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip("\n")


def load_reference(refs_dir: Path, relpath: str) -> Reference:
    text = (refs_dir / relpath).read_text(encoding="utf-8")
    guidance, verification = split_reference(text)
    return Reference(
        relpath=relpath,
        title=title_of(text, fallback=relpath),
        guidance=strip_leading_title(guidance),
        verification=verification,
    )


def select_relpaths(
    refs_dir: Path,
    shape: str,
    languages: list[str],
    intersections: list[str],
    releasing: bool,
    monorepo: bool,
    notes: list[str],
) -> tuple[list[str], list[str]]:
    """Resolve the flags into an ordered, de-duplicated list of reference relpaths.

    Returns the relpaths plus the resolved language list (which may have grown,
    e.g. TypeScript auto-added for a Next.js shape). Order mirrors how the skill
    says to compose: base, shape(s), language(s), intersection(s), then ci and
    the cross-cutting references.
    """
    ordered: list[str] = ["base.md"]

    # Next.js builds on the web-app shape and assumes TypeScript.
    if shape == "nextjs" and (refs_dir / "shapes" / "web-app.md").is_file():
        ordered.append("shapes/web-app.md")
        notes.append("nextjs builds on the web-app shape — included shapes/web-app.md")
    ordered.append(f"shapes/{shape}.md")

    langs = list(languages)
    if shape == "nextjs" and "typescript" not in langs:
        langs.append("typescript")
        notes.append(
            "nextjs assumes TypeScript — auto-included languages/typescript.md"
        )
    ordered.extend(f"languages/{lang}.md" for lang in langs)

    resolved_intersections = list(intersections)
    for lang in langs:
        auto = AUTO_INTERSECTIONS.get((shape, lang))
        if (
            auto
            and auto not in resolved_intersections
            and (refs_dir / "intersections" / f"{auto}.md").is_file()
        ):
            resolved_intersections.append(auto)
            notes.append(f"auto-included intersection {auto} ({shape} + {lang})")
    ordered.extend(f"intersections/{name}.md" for name in resolved_intersections)

    ordered.append("ci.md")
    if releasing:
        ordered.append("releasing.md")
    if monorepo:
        ordered.append("monorepo.md")

    seen: set[str] = set()
    deduped = [r for r in ordered if not (r in seen or seen.add(r))]
    return deduped, langs


def render_plan(
    shape: str,
    languages: list[str],
    refs: list[Reference],
    invocation: str,
) -> str:
    """Render the composed guidance + assembled verification checklist."""
    lang_label = ", ".join(languages)
    out: list[str] = []
    out.append(f"# Repo creation plan: {shape} + {lang_label}")
    out.append("")
    out.append(f"> Generated by compose_repo_plan.py — `{invocation}`.")
    out.append(
        "> The composed guidance and a single verification checklist for THIS "
        "repo's stack."
    )
    out.append(
        "> Read the guidance, apply it, then walk the checklist before handing off. The"
    )
    out.append(
        "> automated gates at the end are necessary but not sufficient — most skipped"
    )
    out.append("> steps are a checklist item assumed rather than confirmed.")
    out.append("")

    # The composition block to paste into AGENTS.md — satisfies the
    # composition-recorded invariant the baseline checker enforces.
    out.append("## Record this composition in AGENTS.md")
    out.append("")
    out.append(
        'Fill in the exclusions and paste into the "Stack and composition" '
        "section of AGENTS.md:"
    )
    out.append("")
    out.append(f"- **Product shape:** {shape}")
    out.append(f"- **Language(s):** {lang_label}")
    composed = ", ".join(r.relpath for r in refs)
    out.append(f"- **References composed:** {composed}")
    out.append(
        "- **Excluded, and why:** <optional tooling/layout that did not fit — "
        "each with a one-line rationale. The non-negotiable invariants (strict "
        "gate, real e2e, CI that proves the artifact) are never excluded.>"
    )
    out.append("")

    out.append("## Guidance")
    out.append("")
    for ref in refs:
        out.append(f"### {ref.title}  (`{ref.relpath}`)")
        out.append("")
        if ref.guidance:
            out.append(ref.guidance)
            out.append("")

    out.append("## Verification checklist")
    out.append("")
    out.append(
        "Walk in order. Verification items live with each reference; this list "
        "is assembled from the references composed above."
    )
    out.append("")
    for ref in refs:
        if not ref.verification:
            continue
        out.append(f"### {ref.title}")
        out.append("")
        out.append(ref.verification)
        out.append("")

    # The closing automated gates always come last — necessary, not sufficient.
    out.append("### Automated gates (necessary, not sufficient)")
    out.append("")
    out.append("- [ ] `just check` passes locally from a clean state.")
    out.append(
        "- [ ] The baseline checker passes: "
        "`uv run --script scripts/check_repo_baseline.py /path/to/repo`."
    )
    out.append("")

    return "\n".join(out).rstrip("\n") + "\n"


def count_items(refs: list[Reference]) -> int:
    """Count checklist items across references, plus the two closing automated gates."""
    total = 2
    for ref in refs:
        total += sum(
            1 for line in ref.verification.splitlines() if CHECKLIST_ITEM_RE.match(line)
        )
    return total


def build_parser(
    shapes: list[str], languages: list[str], intersections: list[str]
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compose_repo_plan.py",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument("--shape", choices=shapes, help="the product shape")
    parser.add_argument(
        "--language",
        action="append",
        choices=languages,
        metavar="LANG",
        help="an implementation language (repeatable)",
    )
    parser.add_argument(
        "--intersection",
        action="append",
        choices=intersections,
        default=[],
        metavar="NAME",
        help="a shape+language intersection reference (repeatable; usually auto-derived)",
    )
    parser.add_argument(
        "--releasing",
        action="store_true",
        help="the repo ships a versioned artifact (pull in releasing.md)",
    )
    parser.add_argument(
        "--monorepo",
        action="store_true",
        help="the repo holds more than one deliverable (pull in monorepo.md)",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="write the plan to FILE instead of stdout",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list the available shapes, languages, and intersections, then exit",
    )
    return parser


def main(argv: list[str]) -> int:
    refs_dir = Path(__file__).resolve().parent.parent / "references"
    if not refs_dir.is_dir():
        print(f"ERROR references directory not found: {refs_dir}", file=sys.stderr)
        return 2

    shapes = discover(refs_dir, "shapes")
    languages = discover(refs_dir, "languages")
    intersections = discover(refs_dir, "intersections")

    parser = build_parser(shapes, languages, intersections)
    args = parser.parse_args(argv)

    if args.list:
        print("Available composition flags (from references/):")
        print(f"  --shape         {', '.join(shapes)}")
        print(f"  --language      {', '.join(languages)}")
        print(f"  --intersection  {', '.join(intersections) or '(none)'}")
        print("  --releasing     ships a versioned artifact (releasing.md)")
        print("  --monorepo      more than one deliverable (monorepo.md)")
        return 0

    missing = [
        name
        for name, val in (("--shape", args.shape), ("--language", args.language))
        if not val
    ]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")

    notes: list[str] = []
    relpaths, resolved_langs = select_relpaths(
        refs_dir,
        args.shape,
        args.language,
        args.intersection,
        args.releasing,
        args.monorepo,
        notes,
    )

    try:
        refs = [load_reference(refs_dir, rel) for rel in relpaths]
    except FileNotFoundError as exc:
        print(f"ERROR missing reference file: {exc.filename}", file=sys.stderr)
        return 2

    flags = [f"--shape {args.shape}"]
    flags += [f"--language {lang}" for lang in args.language]
    flags += [f"--intersection {name}" for name in args.intersection]
    if args.releasing:
        flags.append("--releasing")
    if args.monorepo:
        flags.append("--monorepo")
    invocation = "compose_repo_plan.py " + " ".join(flags)

    document = render_plan(args.shape, resolved_langs, refs, invocation)

    for note in notes:
        print(f"note: {note}", file=sys.stderr)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(document, encoding="utf-8")
        print(
            f"wrote {out_path} ({len(refs)} references, {count_items(refs)} "
            "checklist items)",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
