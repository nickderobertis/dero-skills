# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Compose a tailored repo-creation plan from the create-repo references.

Usage:
    uv run --script scripts/compose_repo_plan.py --shape SHAPE --language LANG \
        [--language LANG ...] [--releasing] [--monorepo] \
        [--intersection NAME ...] [-o OUT.md] \
        [--llmlint-config FILE] [--llmlint-buildout-config FILE]
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
  * an intersection reference is auto-included whenever one exists for a
    shape+language pair, by the ``<language>-<shape>`` naming convention (e.g.
    ``cli`` + ``python`` -> ``intersections/python-cli.md``). Adding a new
    ``intersections/<lang>-<shape>.md`` wires it in with no code change; pass
    ``--intersection`` only to force one that breaks the convention.

Optionally it also emits the repo's **llmlint** config — the LLM-as-judge tier
that runs *outside* ``just check``. ``--llmlint-config FILE`` writes the ongoing
``llmlint.yml`` (committed; the blocking PR check); ``--llmlint-buildout-config
FILE`` writes a temporary buildout config (run once at creation, then deleted).
Both wire the selected references' rule fragments in as ``@version``-pinned
llmlint plugins; see ``references/llmlint.md``.

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

# --- llmlint integration ----------------------------------------------------
# llmlint (https://github.com/nickderobertis/llmlint) is the LLM-as-judge tier:
# a non-deterministic linter that runs OUTSIDE the deterministic `just check`
# gate (via `just lint-llm` + a diff-scoped blocking PR check). The composer
# builds its config by wiring per-reference rule fragments in as llmlint
# *plugins*, pinned by URL.
#
# Two configs come out of the same selection:
#   * ongoing (`--llmlint-config`)  -> assets/llmlint/<ref>.llmlint.yml — the
#     permanent llmlint.yml committed to the repo and run on every PR.
#   * buildout (`--llmlint-buildout-config`) -> assets/llmlint/buildout/<ref>...
#     — a temporary config run once at creation to check the repo was set up
#     right, then deleted (never committed).
# Fragments are referenced by `@version`-pinned URL; the composer reads each
# fragment's `version:` locally only to build the pin.
LLMLINT_SCHEMA_URL = (
    "https://raw.githubusercontent.com/nickderobertis/llmlint/main/"
    "assets/llmlint.schema.json"
)
# The bundled config-lint plugin: lints this config's own rules for clear,
# mutually-exclusive true/false and descriptive names. Resolves offline.
LLMLINT_CONFIG_LINT_URL = (
    "https://raw.githubusercontent.com/nickderobertis/llmlint/main/"
    "assets/config_lint.yml@1"
)
# Where the hosted fragments live (this repo, raw on the default branch).
LLMLINT_BASE_URL = (
    "https://raw.githubusercontent.com/nickderobertis/dero-skills/main/"
    "skills/bootstrap/create-repo/assets/llmlint"
)
LLMLINT_VERSION_RE = re.compile(r"^version:\s*(\S+)", re.MULTILINE)


# Intersection references follow a `<language>-<shape>` naming convention
# (e.g. `python-cli.md` = python + cli, `rust-cli.md` = rust + cli). The composer
# derives the candidate name from each shape+language pair and auto-includes the
# intersection whenever that file exists — so adding `intersections/<lang>-<shape>.md`
# wires it in with no code change, no hardcoded pair list to keep in sync.
def intersection_name(shape: str, language: str) -> str:
    return f"{language}-{shape}"


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
        auto = intersection_name(shape, lang)
        if (
            auto not in resolved_intersections
            and (refs_dir / "intersections" / f"{auto}.md").is_file()
        ):
            resolved_intersections.append(auto)
            notes.append(f"auto-included intersection {auto} ({shape} + {lang})")
    ordered.extend(f"intersections/{name}.md" for name in resolved_intersections)

    ordered.append("ci.md")
    # llmlint applies on top of every shape (the LLM-judge tier), like ci.md.
    ordered.append("llmlint.md")
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
    out.append(
        "- [ ] llmlint (ongoing) passes once: run `just lint-llm` and resolve any "
        "findings."
    )
    out.append(
        "- [ ] llmlint (buildout) passes once: compose `llmlint.buildout.yml` "
        "(`--llmlint-buildout-config`), run `llmlint -c llmlint.buildout.yml`, "
        "resolve findings, then delete it — do not commit."
    )
    out.append("")

    return "\n".join(out).rstrip("\n") + "\n"


def count_items(refs: list[Reference]) -> int:
    """Count checklist items across references, plus the four closing automated gates."""
    total = 4
    for ref in refs:
        total += sum(
            1 for line in ref.verification.splitlines() if CHECKLIST_ITEM_RE.match(line)
        )
    return total


def fragment_relpath(reference_relpath: str) -> str:
    """Map a reference relpath to its llmlint fragment relpath.

    ``languages/python.md`` -> ``languages/python.llmlint.yml``. The naming mirrors
    the reference tree so selection is by convention, with no hand-maintained table.
    """
    stem = (
        reference_relpath[:-3]
        if reference_relpath.endswith(".md")
        else reference_relpath
    )
    return f"{stem}.llmlint.yml"


def read_fragment_version(path: Path) -> str:
    """Return a fragment's published ``version`` (for the ``@`` URL pin); default 1."""
    match = LLMLINT_VERSION_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1).strip() if match else "1"


def collect_llmlint_plugins(
    skill_dir: Path, relpaths: list[str], *, buildout: bool
) -> tuple[list[str], list[str]]:
    """Return (pinned plugin URLs, included fragment relpaths) for the selection.

    For each selected reference that has a fragment under ``assets/llmlint/``
    (ongoing) or ``assets/llmlint/buildout/``, emit its ``@version``-pinned hosted
    URL. References with no fragment (e.g. ``llmlint.md`` itself) are skipped.
    """
    frag_dir = skill_dir / "assets" / "llmlint"
    url_prefix = LLMLINT_BASE_URL
    if buildout:
        frag_dir = frag_dir / "buildout"
        url_prefix = f"{LLMLINT_BASE_URL}/buildout"

    urls: list[str] = []
    included: list[str] = []
    for rel in relpaths:
        frag_rel = fragment_relpath(rel)
        path = frag_dir / frag_rel
        if path.is_file():
            version = read_fragment_version(path)
            urls.append(f"{url_prefix}/{frag_rel}@{version}")
            included.append(frag_rel)
    return urls, included


def render_llmlint_config(plugin_urls: list[str], *, buildout: bool) -> str:
    """Render a top-level llmlint.yml that wires the selected fragments in as plugins.

    A thin wrapper: the rules live in the pinned-URL plugins. The bundled
    config-lint plugin is always first (it lints this config's own rules).
    """
    out: list[str] = [f"# yaml-language-server: $schema={LLMLINT_SCHEMA_URL}"]
    if buildout:
        out += [
            "#",
            "# TEMPORARY buildout config — run ONCE during repo creation, then DELETE.",
            "# Do NOT commit it. It checks the repo was *set up* right (CI/release/",
            "# monorepo wiring); the ongoing rules live in the committed llmlint.yml.",
            "#   llmlint -c llmlint.buildout.yml",
        ]
    else:
        out += [
            "#",
            "# The LLM-judge tier — separate from the deterministic `just check` gate.",
            "# Run with `just lint-llm` (or `just lint-llm-diff` for the merge-base",
            "# diff); the diff-scoped run is the blocking PR check, not part of `check`.",
            "# Rules come from the pinned plugins below; tune one in place with",
            "# `override: true`. Bump a plugin's `@version` pin to pull new rules.",
        ]
    out += [
        "version: 1",
        "files:",
        "  # No `include`: llmlint lints the whole tree (exclude + .gitignore honored).",
        "  # List committed files that shouldn't be judged (lock files, generated output).",
        "  exclude:",
        '    - "**/.git/**"',
        "rationales: true",
        "agents:",
        "  default:",
        "    harness: claude-code   # any id from `oneharness list`",
        "plugins:",
        f'  - "{LLMLINT_CONFIG_LINT_URL}"',
    ]
    out += [f'  - "{url}"' for url in plugin_urls]
    return "\n".join(out) + "\n"


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
        "--llmlint-config",
        metavar="FILE",
        help="also write the ongoing llmlint.yml (the committed, PR-checked config) "
        "for this stack, wiring the per-reference rule fragments in as pinned plugins",
    )
    parser.add_argument(
        "--llmlint-buildout-config",
        metavar="FILE",
        help="also write the temporary llmlint buildout config (run once at "
        "creation, then delete) of the buildout-only structural rules for this stack",
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
        print(
            f"ERROR references directory not found: {refs_dir}\n"
            "      fix: reinstall the create-repo skill so its references/ "
            "directory sits alongside scripts/.",
            file=sys.stderr,
        )
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
        print(
            f"ERROR missing reference file: {exc.filename}\n"
            "      fix: restore the create-repo skill's references/ tree; the "
            "reference set is incomplete for this composition.",
            file=sys.stderr,
        )
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

    skill_dir = refs_dir.parent
    if args.llmlint_config:
        urls, included = collect_llmlint_plugins(skill_dir, relpaths, buildout=False)
        Path(args.llmlint_config).write_text(
            render_llmlint_config(urls, buildout=False), encoding="utf-8"
        )
        print(
            f"wrote {args.llmlint_config} (ongoing llmlint config; "
            f"{len(included)} rule fragment(s): {', '.join(included) or 'none'})",
            file=sys.stderr,
        )
    if args.llmlint_buildout_config:
        urls, included = collect_llmlint_plugins(skill_dir, relpaths, buildout=True)
        Path(args.llmlint_buildout_config).write_text(
            render_llmlint_config(urls, buildout=True), encoding="utf-8"
        )
        print(
            f"wrote {args.llmlint_buildout_config} (TEMPORARY buildout config — run "
            f"once, then delete; {len(included)} rule fragment(s): "
            f"{', '.join(included) or 'none'})",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
