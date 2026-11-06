# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Compose a tailored repo-creation plan from the create-repo references.

Usage:
    uv run --script scripts/compose_repo_plan.py --shape SHAPE --language LANG \
        [--language LANG ...] [--releasing] \
        [--intersection NAME ...] [-o OUT.md] \
        [--llmlint-config FILE] [--llmlint-buildout-config FILE] \
        [--oneharness-config FILE] [--wiring REPO_DIR]
    uv run --script scripts/compose_repo_plan.py --wiring REPO_DIR
    uv run --script scripts/compose_repo_plan.py --list

You describe the repo with flags — its product shape, the language(s) it is
built in, and the cross-cutting concerns that apply — and this emits a single
self-contained document for *that* stack: the composed guidance from each
selected reference, followed by one verification checklist assembled from the
``## Verification`` section that lives with each reference.

The reference catalog *is* the source of truth: shapes, languages, and
intersections are discovered by scanning ``references/`` next to this script, so
adding a reference file automatically extends the flags. ``base.md`` is always
included first (the shape/language-agnostic invariants), immediately followed by
``project-graph.md`` (the project graph is mandatory in every repo, so there is
no flag for it); ``ci.md`` is always included too (it applies on top of every
shape); ``releasing.md`` is pulled in by ``--releasing``. The baseline's tools
join every plan last, with no flag: onebudgetspec budgets are part of the
baseline, so ``references/tools/onebudgetspec.md`` is always composed and its
``tools/onebudgetspec.llmlint.yml`` fragment always joins the ongoing
``llmlint.yml`` (that fragment, not ``base.llmlint.yml``, carries the budget
rules, so a repo adopts them by listing its URL).

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
llmlint plugins; see ``references/llmlint.md``. The ongoing ``llmlint.yml`` pins
no harness, so ``--llmlint-config`` also writes an ``oneharness.toml`` beside it
(override the path with ``--oneharness-config``) — the fallback harness/model
selection (codex + gpt-5.5 primary, claude-code + opus-4.8 secondary) llmlint
reads to pick a harness.

``--wiring REPO_DIR`` applies the baseline tools' setup step to the repository
at REPO_DIR, idempotently, and needs no other flag (with ``--shape`` and
``--language`` it composes the plan too). For ``onebudgetspec`` that is: pin
``@onebudgetspec/cli`` exactly in ``package.json`` (the lockfile follows on the
next install), add the ``budgets``/``budgets-host`` target defaults to
``nx.json``, and add a ``budgets`` recipe to the justfile that ``check`` depends
on — every project's budgets at the gate's tier, the root ``budgets.yaml`` on
every run. The justfile must already exist (from ``assets/justfile.template``).

The document goes to stdout (or ``-o FILE``); notes and errors go to stderr, so
the two never mix. Self-contained via PEP 723 so it runs in any consuming repo
with ``uv run --script`` — no dependency on this repo's authoring toolchain.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Protocol

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
# A fragment version is `MAJOR[.MINOR[.PATCH]]`, digits only — the shape the `@`
# pin is built from. Validated at the read boundary so a malformed value fails
# loudly instead of emitting a broken pin.
FRAGMENT_VERSION_RE = re.compile(r"\d+(?:\.\d+){0,2}")

# The onebudgetspec release a consumer is pinned to: the npm package its `budgets`
# targets run, at the version references/tools/onebudgetspec.md documents (its
# README link names the same tag; tests hold the two, and this repo's own
# `onebudgetspec-cli` dev pin, in lockstep).
ONEBUDGETSPEC_VERSION = "0.1.3"
ONEBUDGETSPEC_NPM_PACKAGE = "@onebudgetspec/cli"
# The Nx target defaults of the two budget targets, copied from the release
# README's Nx example (tests/test_onebudgetspec_release.py holds it to the README
# the pinned release ships).
ONEBUDGETSPEC_TARGET_DEFAULTS: dict[str, dict[str, object]] = {
    "budgets": {
        "cache": True,
        "inputs": [
            "{projectRoot}/**/*",
            "^production",
            {"dependentTasksOutputFiles": "**/telemetry/*.json"},
            {"externalDependencies": [ONEBUDGETSPEC_NPM_PACKAGE]},
        ],
    },
    "budgets-host": {"cache": False},
}
# `^production` names an input that must exist; these are Nx's own conventional
# definitions, added only where the workspace defines none of its own.
NX_DEFAULT_NAMED_INPUTS: dict[str, list[str]] = {
    "default": ["{projectRoot}/**/*"],
    "production": ["default"],
}
# The input objects Nx accepts, each keyed by the one property that names its
# kind, to every property it may carry and the JSON types that property takes
# (an array is always of strings). An `input` object names `projects` or
# `dependencies`, never both. Nx's own `nx-schema.json` is the source, which
# tests/test_onebudgetspec_release.py reconciles this with.
NX_INPUT_OBJECTS: dict[str, dict[str, frozenset[str]]] = {
    "fileset": {"fileset": frozenset({"string"})},
    "input": {
        "input": frozenset({"string"}),
        "projects": frozenset({"string", "array"}),
        "dependencies": frozenset({"boolean"}),
    },
    "runtime": {"runtime": frozenset({"string"})},
    "env": {"env": frozenset({"string"})},
    "externalDependencies": {"externalDependencies": frozenset({"array"})},
    "dependentTasksOutputFiles": {
        "dependentTasksOutputFiles": frozenset({"string"}),
        "transitive": frozenset({"boolean"}),
    },
}
NX_INPUT_EXCLUSIVE = frozenset({"projects", "dependencies"})
BUDGETS_RECIPE_NAME = "budgets"
# The recipe itself, an asset so this script carries no orchestrator command.
ONEBUDGETSPEC_RECIPE = "assets/tools/onebudgetspec.justfile"
# The tools every repository's baseline includes, each composed from
# references/tools/<name>.md (and its llmlint fragment) into every plan.
BASELINE_TOOLS = ("onebudgetspec",)
# A justfile recipe header: its name, then its parameters up to the single
# terminating colon, dependencies after it; a leading `@` is just's quiet prefix,
# not part of the name. The baseline checker reads recipes
# with the same pattern (its RECIPE_RE); the test suite holds the two equal.
JUST_RECIPE_RE = re.compile(r"^@?([A-Za-z_][A-Za-z0-9_-]*)[^\n:]*:(?!=)")
JUST_BASE_ASSIGNMENT_RE = re.compile(r"^base\s*:=", re.MULTILINE)


# A shape can build on other shapes, composing their guidance first (base-most
# first, so the concrete shape's guidance lands last and specializes it). react
# builds on web-app; nextjs builds on react (and thus web-app). Each parent is
# included only if its reference file exists, so the chain degrades gracefully.
SHAPE_PARENTS: dict[str, list[str]] = {
    "react": ["web-app"],
    "nextjs": ["web-app", "react"],
}
# Shapes whose framework assumes TypeScript as the implementation language; the
# composer auto-includes languages/typescript.md when it isn't passed explicitly.
TYPESCRIPT_SHAPES = frozenset({"react", "nextjs"})


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
    notes: list[str],
) -> tuple[list[str], list[str]]:
    """Resolve the flags into an ordered, de-duplicated list of reference relpaths.

    Returns the relpaths plus the resolved language list (which may have grown,
    e.g. TypeScript auto-added for a Next.js shape). Order mirrors how the skill
    says to compose: base, the mandatory project graph, shape(s), language(s),
    intersection(s), then ci, the cross-cutting references, and the baseline tools.
    """
    ordered: list[str] = ["base.md", "project-graph.md"]

    # A shape composes any shape(s) it builds on first (base-most first), then
    # itself: react builds on web-app; nextjs builds on react (and thus web-app).
    for parent in SHAPE_PARENTS.get(shape, []):
        if (refs_dir / "shapes" / f"{parent}.md").is_file():
            ordered.append(f"shapes/{parent}.md")
            notes.append(
                f"{shape} builds on the {parent} shape — included shapes/{parent}.md"
            )
    ordered.append(f"shapes/{shape}.md")

    langs = list(languages)
    if shape in TYPESCRIPT_SHAPES and "typescript" not in langs:
        langs.append("typescript")
        notes.append(
            f"{shape} assumes TypeScript — auto-included languages/typescript.md"
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
    # The baseline's tools join every plan, so their fragments reach the ongoing
    # llmlint.yml through the same mapping as the rest.
    ordered.extend(f"tools/{tool}.md" for tool in BASELINE_TOOLS)

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
        "- [ ] llmlint (buildout) passes once: run "
        "`check_repo_baseline.py /path/to/repo --buildout` (composes and runs the "
        "buildout tier for the recorded stack, then cleans up), or by hand via "
        "`--llmlint-buildout-config` + `llmlint -c ...`, then delete — do not commit."
    )
    out.append(
        "- [ ] Repo governance matches: "
        "`setup_github_governance.py <every-required-check> --verify` reports no "
        "divergence (branch protection, merge model, fork-PR approval)."
    )
    out.append("")

    return "\n".join(out).rstrip("\n") + "\n"


def count_items(refs: list[Reference]) -> int:
    """Count checklist items across references, plus the five closing automated gates."""
    total = 5
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
    """Return a fragment's full published ``version`` (semver string); default ``1``.

    The value is read from a config file, so validate its shape at the boundary:
    a malformed version must fail loudly here, not silently produce a broken
    ``@...`` pin downstream.
    """
    match = LLMLINT_VERSION_RE.search(path.read_text(encoding="utf-8"))
    if match is None:
        return "1"
    version = match.group(1).strip()
    if not FRAGMENT_VERSION_RE.fullmatch(version):
        raise ValueError(
            f"{path}: llmlint fragment version {version!r} is not valid semver "
            "(expected MAJOR[.MINOR[.PATCH]], digits only)"
        )
    return version


def pin_range(version: str) -> str:
    """The ``@`` pin a consumer gets: the major component only.

    llmlint reads ``@N`` as "any ``N.x``", so pinning the major lets a fragment
    ship non-breaking rule changes (minor/patch bumps) that flow to pinned
    consumers automatically, while a breaking change (major bump) is opt-in. A
    bare version (``1``) is already its own major. Expects a version already
    validated by ``read_fragment_version``.
    """
    return version.split(".", 1)[0]


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
            urls.append(f"{url_prefix}/{frag_rel}@{pin_range(version)}")
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
            "# project-graph wiring); the ongoing rules live in the committed llmlint.yml.",
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
        # No top-level `version:` — that field only means anything when a config is
        # itself consumed as a plugin (a `@`-pinned URL). This is the repo's own
        # consumer config, pinned by no one, so a version here would be inert noise
        # that the validate gate still forces you to bump on every edit.
        "files:",
        "  # No `include`: llmlint lints the whole tree (exclude + .gitignore honored).",
        "  # List committed files that shouldn't be judged (lock files, generated output).",
        "  exclude:",
        '    - "**/.git/**"',
        "rationales: true",
        "# No `agents` block: the harness/model are NOT pinned here. `oneharness.toml`",
        "# (composed alongside this file) selects them in fallback mode — codex + gpt-5.5",
        "# primary, claude-code + opus-4.8 secondary — so a Claude Code session falls",
        "# through to claude-code with no config edit. Leaving the harness unset is what",
        "# lets that fallback (or an ONEHARNESS_<FIELD> env override) decide.",
        "plugins:",
        f'  - "{LLMLINT_CONFIG_LINT_URL}"',
    ]
    out += [f'  - "{url}"' for url in plugin_urls]
    return "\n".join(out) + "\n"


def render_oneharness_config(skill_dir: Path) -> str:
    """Return the oneharness.toml body: harness/model selection for llmlint.

    Read verbatim from ``assets/oneharness.toml.template`` (the single source of
    truth this repo also dogfoods) so the composed config never drifts from the
    template. It puts oneharness in **fallback** mode — codex + gpt-5.5 primary,
    claude-code + opus-4.8 secondary — so the same committed file runs the primary
    for a contributor with Codex authenticated and falls through to claude-code in
    a Claude Code session where codex is absent, with no env override.
    """
    template = skill_dir / "assets" / "oneharness.toml.template"
    return template.read_text(encoding="utf-8")


class WiringError(Exception):
    """The repository cannot take a tool's wiring as it stands.

    The message names the problem and, on its own line, the concrete fix.
    """


def _read_json_object(path: Path) -> dict[str, object]:
    """Read ``path`` as a JSON object, or an empty one when it does not exist."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WiringError(
            f"{path} is not valid JSON ({exc.msg} at line {exc.lineno})\n"
            "      fix: repair it, then re-run --wiring."
        ) from exc
    if not isinstance(data, dict):
        raise WiringError(
            f"{path} does not hold a JSON object\n"
            "      fix: make its top level an object, then re-run --wiring."
        )
    return data


def _json_table(data: dict[str, object], key: str, path: Path) -> dict[str, object]:
    """``data[key]`` as an object, created when absent; refused when not an object."""
    table = data.setdefault(key, {})
    if not isinstance(table, dict):
        raise WiringError(
            f"{path}: `{key}` is not a JSON object\n"
            f"      fix: make `{key}` an object, then re-run --wiring."
        )
    return table


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class RecipeHeader(NamedTuple):
    """A justfile recipe's header line, split where just splits it."""

    index: int  # its line number
    head: str  # the name, with just's quiet `@` prefix where it has one
    params: str  # between the name and the colon
    deps: str  # after the colon, without a trailing comment
    comment: str  # the trailing comment's text, or ""


def recipe_headers(text: str) -> dict[str, RecipeHeader]:
    headers: dict[str, RecipeHeader] = {}
    for index, line in enumerate(text.splitlines()):
        match = JUST_RECIPE_RE.match(line)
        if match:
            name = match.group(1)
            deps, _, comment = line[match.end() :].partition("#")
            params = line[match.end(1) : match.end() - 1]
            head = line[: match.end(1)]
            headers[name] = RecipeHeader(index, head, params, deps, comment)
    return headers


def parameter_names(params: str) -> set[str]:
    """The names of a recipe's parameters (``tier="affected"`` names ``tier``)."""
    return {token.split("=", 1)[0].lstrip("+*$") for token in params.split()}


class JustfilePlan(NamedTuple):
    """The justfile's new text, and a note per change it makes."""

    text: str
    changes: list[str]


def plan_justfile(path: Path, recipe: str) -> JustfilePlan:
    """The justfile with the ``budgets`` ``recipe`` added and ``check`` depending on it.

    The recipe runs at ``check``'s tier against the merge base, so the justfile
    must carry the template's ``tier`` parameter and ``base`` assignment. Returns
    the new text and a note per change; writes nothing.
    """
    template = "assets/justfile.template"
    if not path.is_file():
        raise WiringError(
            f"no justfile at {path}\n"
            f"      fix: copy the skill's {template} there first; the wiring adds a "
            "`budgets` recipe that its `check` recipe depends on."
        )
    text = path.read_text(encoding="utf-8")
    headers = recipe_headers(text)
    check = headers.get("check")
    if check is None or "tier" not in parameter_names(check.params):
        raise WiringError(
            f"{path} has no `check tier=...` recipe to run the budgets at\n"
            f"      fix: give `check` the tier parameter {template} declares, then "
            "re-run --wiring."
        )
    if JUST_BASE_ASSIGNMENT_RE.search(text) is None:
        raise WiringError(
            f"{path} has no `base` assignment for the affected tier to key off\n"
            f"      fix: add the `base :=` assignment {template} declares, then "
            "re-run --wiring."
        )
    own = headers.get(BUDGETS_RECIPE_NAME)
    if own is not None and "tier" not in parameter_names(own.params):
        raise WiringError(
            f"{path} has a `{BUDGETS_RECIPE_NAME}` recipe without a `tier` "
            "parameter for `check` to pass\n"
            f'      fix: give it `tier="affected"` (or remove it so the wiring adds '
            "its own), then re-run --wiring."
        )
    changes: list[str] = []
    call = f"({BUDGETS_RECIPE_NAME} tier)"
    wired = call in " ".join(check.deps.split())
    if not wired and BUDGETS_RECIPE_NAME in {
        tok.strip("()") for tok in check.deps.split()
    }:
        raise WiringError(
            f"{path}: `check` depends on `{BUDGETS_RECIPE_NAME}` without passing "
            "its tier, so `check all` would still run only the affected budgets\n"
            f"      fix: make that dependency `{call}`, then re-run --wiring."
        )
    if not wired:
        lines = text.splitlines(keepends=True)
        comment = f" #{check.comment.rstrip()}" if check.comment else ""
        lines[check.index] = (
            f"{check.head}{check.params}:{check.deps.rstrip()} {call}{comment}\n"
        )
        text = "".join(lines)
        changes.append(f"`check` depends on `{call}`")
    if BUDGETS_RECIPE_NAME not in headers:
        text = text.rstrip("\n") + "\n\n" + recipe
        changes.append("added the `budgets` recipe")
    return JustfilePlan(text, changes)


def plan_package_json(package: dict[str, object], path: Path) -> list[str]:
    """Pin the release in ``package`` (in place); a note per change."""
    deps = package.get("dependencies")
    if deps is not None and not isinstance(deps, dict):
        raise WiringError(
            f"{path}: `dependencies` is not a JSON object\n"
            "      fix: make `dependencies` an object, then re-run --wiring."
        )
    table_name = (
        "dependencies"
        if isinstance(deps, dict) and ONEBUDGETSPEC_NPM_PACKAGE in deps
        else "devDependencies"
    )
    table = _json_table(package, table_name, path)
    if table.get(ONEBUDGETSPEC_NPM_PACKAGE) == ONEBUDGETSPEC_VERSION:
        return []
    table[ONEBUDGETSPEC_NPM_PACKAGE] = ONEBUDGETSPEC_VERSION
    package.setdefault("private", True)
    return [
        f"package.json pins {ONEBUDGETSPEC_NPM_PACKAGE} {ONEBUDGETSPEC_VERSION} "
        "(install to record it in the lockfile)"
    ]


def _json_type(value: object) -> str | None:
    """The JSON-schema type of ``value`` an Nx input property can take, or None."""
    match value:
        case bool():
            return "boolean"
        case str():
            return "string"
        case list() if all(isinstance(item, str) for item in value):
            return "array"
    return None


def _is_nx_input(entry: object) -> bool:
    """Whether ``entry`` is an Nx input: a path pattern or a valid input object."""
    if isinstance(entry, str):
        return bool(entry)
    if not isinstance(entry, dict):
        return False
    kinds = NX_INPUT_OBJECTS.keys() & entry.keys()
    if len(kinds) != 1 or NX_INPUT_EXCLUSIVE <= entry.keys():
        return False
    [kind] = kinds
    properties = NX_INPUT_OBJECTS[kind]
    return all(
        _json_type(value) in properties.get(key, ()) for key, value in entry.items()
    )


def plan_nx_json(nx: dict[str, object], path: Path) -> list[str]:
    """Add the budget targets' defaults to ``nx`` (in place); a note per change.

    What the workspace already defines is kept as it is.
    """
    changes: list[str] = []
    named = _json_table(nx, "namedInputs", path)
    for name, inputs in NX_DEFAULT_NAMED_INPUTS.items():
        if name not in named:
            named[name] = list(inputs)
            changes.append(f"nx.json defines the `{name}` named input")
        elif not isinstance(named[name], list) or not all(
            map(_is_nx_input, named[name])
        ):
            raise WiringError(
                f"{path}: named input `{name}` is not a list of inputs (each a "
                "path pattern or an input object)\n"
                f"      fix: make `namedInputs.{name}` such a list, then re-run "
                "--wiring."
            )
    defaults = _json_table(nx, "targetDefaults", path)
    for target, config in ONEBUDGETSPEC_TARGET_DEFAULTS.items():
        if target not in defaults:
            defaults[target] = json.loads(json.dumps(config))
            changes.append(f"nx.json target default `{target}`")
        elif not isinstance(defaults[target], dict):
            raise WiringError(
                f"{path}: target default `{target}` is not a JSON object\n"
                f"      fix: make `targetDefaults.{target}` an object, then re-run "
                "--wiring."
            )
    return changes


def wire_onebudgetspec(repo: Path, skill_dir: Path) -> list[str]:
    """Pin onebudgetspec, give Nx its budget targets, and put them in ``check``.

    Every file is validated before any is written, so a refusal leaves the repo
    as it was. Idempotent: an already-wired repo is left byte-for-byte as it is.
    Returns a note per change made.
    """
    justfile = next(
        (
            repo / name
            for name in ("justfile", "Justfile", ".justfile")
            if (repo / name).is_file()
        ),
        repo / "justfile",
    )
    recipe = (skill_dir / ONEBUDGETSPEC_RECIPE).read_text(encoding="utf-8")
    just_text, just_changes = plan_justfile(justfile, recipe)

    package_json = repo / "package.json"
    package = _read_json_object(package_json)
    package_changes = plan_package_json(package, package_json)

    nx_json = repo / "nx.json"
    nx = _read_json_object(nx_json)
    nx_changes = plan_nx_json(nx, nx_json)

    if package_changes:
        _write_json(package_json, package)
    if nx_changes:
        _write_json(nx_json, nx)
    if just_changes:
        justfile.write_text(just_text, encoding="utf-8")
    return package_changes + nx_changes + just_changes


class ToolWiring(Protocol):
    """A tool's setup step: applied to ``repo`` from the skill at ``skill_dir``,
    returning a note per change and raising ``WiringError`` to refuse."""

    def __call__(self, repo: Path, skill_dir: Path) -> list[str]: ...


# The setup step of each baseline tool that has one, keyed by its name.
TOOL_WIRING: dict[str, ToolWiring] = {"onebudgetspec": wire_onebudgetspec}


# `--ci-cd REPO_DIR` writes the CI and CD workflows, the root budgets and the gate's
# timing wrapper every repository starts inside (references/ci.md, "CI and CD time
# budgets"), rendered from the templates under assets/ for the composition: its
# language, the platforms it supports and its visual-docs lanes. The shape is the one
# onepipeline, onetaskgraph and onepipeline-ui landed; the job ids and budget ids are
# shared interfaces other repositories build against, so they are fixed here.

# The onevcs release `publish`'s release tail installs: the first carrying the
# `onevcs host` queries the figures script reads at the contract it states.
ONEVCS_CLI_VERSION = "0.57.6"
# The onebudgetspec-github-action release ci.yml's `budgets` job uses. The action publishes
# its crate last, so a crate at this version means the release's whole payload is usable.
ONEBUDGETSPEC_ACTION_VERSION = "v0.1.0"
# The screencomp release whose composite actions the visual-docs steps use.
SCREENCOMP_VERSION = "v0.4.8"
# The languages whose toolchain the templates know how to install.
CI_CD_LANGUAGES = ("rust", "typescript")
PROJECT_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# The platforms a repository supports, as `--platforms` names them, to the CI test
# jobs each runs (one per platform, `test-<platform>`).
PLATFORM_SETS: dict[str, tuple[str, ...]] = {
    "cross-platform": ("linux", "windows", "macos"),
    "linux": ("linux",),
}


class CiPlatform(NamedTuple):
    """A CI test job's platform: its title and its hosted runner."""

    title: str
    runner: str

    def values(self, platform: str) -> dict[str, str]:
        return {
            "PLATFORM": platform,
            "PLATFORM_TITLE": self.title,
            "PLATFORM_RUNNER": self.runner,
        }


CI_PLATFORMS: dict[str, CiPlatform] = {
    "linux": CiPlatform("Linux", "ubuntu-latest"),
    "windows": CiPlatform("Windows", "windows-latest"),
    "macos": CiPlatform("macOS", "macos-latest"),
}


class CdBuild(NamedTuple):
    """A CD build job (`build-<build>`): what it releases, natively, on which runner."""

    title: str
    runner: str
    target: str
    note: str

    def values(self, build: str) -> dict[str, str]:
        return {
            "BUILD": build,
            "BUILD_TITLE": self.title,
            "BUILD_RUNNER": self.runner,
            "BUILD_TARGET": self.target,
            "BUILD_NOTE": self.note,
        }


# Linux arm64 is released on every repository that releases Linux, on GitHub's hosted
# ARM runner; macOS is Apple Silicon only.
CD_BUILDS: dict[str, CdBuild] = {
    "linux": CdBuild(
        "Linux", "ubuntu-latest", "x86_64-unknown-linux-gnu", "Linux x86_64."
    ),
    "linux-arm64": CdBuild(
        "Linux arm64",
        "ubuntu-24.04-arm",
        "aarch64-unknown-linux-gnu",
        "Linux arm64, natively on GitHub's hosted ARM Linux runner: the same "
        "kinds of artifact build-linux publishes, for aarch64.",
    ),
    "macos": CdBuild(
        "macOS",
        "macos-latest",
        "aarch64-apple-darwin",
        "macOS, Apple Silicon only: nothing here cross-compiles or emulates x86_64.",
    ),
    "windows": CdBuild(
        "Windows", "windows-latest", "x86_64-pc-windows-msvc", "Windows x86_64."
    ),
}
# The CD builds each CI platform releases.
PLATFORM_BUILDS: dict[str, tuple[str, ...]] = {
    "linux": ("linux", "linux-arm64"),
    "windows": ("windows",),
    "macos": ("macos",),
}
# A second visual-docs architecture, to the runner that captures it natively.
VISUAL_DOCS_ARCH_RUNNERS = {"arm64": "ubuntu-24.04-arm"}


class LanguageValues(NamedTuple):
    """The stack-specific supply-chain check title."""

    supply_chain_extra: str

    def values(self) -> dict[str, str]:
        return {"SUPPLY_CHAIN_EXTRA": self.supply_chain_extra}


LANGUAGE_VALUES: dict[str, LanguageValues] = {
    "rust": LanguageValues(" and the MSRV"),
    "typescript": LanguageValues(""),
}


def cache_workflow_values(skill_dir: Path) -> dict[str, str]:
    """Pins and manual branch naming have one source in the shared script."""
    script = (skill_dir / "assets/ci/nx-remote-cache.mjs").read_text()
    values = {}
    for key in ("OP_NX_COMMIT", "PROMOTION_BRANCH_PATTERN"):
        match = re.search(rf"export const {key} = '([^']+)';", script)
        if match is None:
            raise WiringError(f"shared cache script defines no {key}")
        values[key] = match.group(1)
    return values


class CiCdFile(NamedTuple):
    """A file the shape writes: where, from which asset, and how."""

    target: str
    source: str
    # False: copied byte for byte rather than rendered as a template.
    rendered: bool
    executable: bool


REPORT_JOB_PROJECT = ".github/reporting/project.json"
CI_CD_FILES: tuple[CiCdFile, ...] = (
    CiCdFile(".github/workflows/ci.yml", "assets/ci.yml.template", True, False),
    CiCdFile(
        ".github/workflows/release-plz.yml",
        "assets/release-plz.yml.template",
        True,
        False,
    ),
    CiCdFile(
        ".github/workflows/release.yml", "assets/release.yml.template", True, False
    ),
    CiCdFile("budgets.yaml", "assets/budgets.yaml.template", True, False),
    CiCdFile(
        ".github/reporting/budgets.yaml",
        "assets/reporting/budgets.yaml.template",
        True,
        False,
    ),
    CiCdFile(
        REPORT_JOB_PROJECT, "assets/reporting/project.json.template", False, False
    ),
    CiCdFile(
        ".github/reporting/.gitignore", "assets/reporting/gitignore", False, False
    ),
    CiCdFile(
        "scripts/gate-time.sh", "assets/budgets/gate-time.sh.template", True, True
    ),
    CiCdFile(
        "scripts/gate-time-budget.mjs",
        "assets/budgets/gate-time-budget.mjs",
        False,
        False,
    ),
    CiCdFile(
        "scripts/nx-remote-cache.mjs", "assets/ci/nx-remote-cache.mjs", False, False
    ),
    CiCdFile(
        "scripts/nx-version-free-inputs.mjs",
        "assets/ci/nx-version-free-inputs.mjs",
        False,
        False,
    ),
    CiCdFile(
        "scripts/actions-budget-figures.sh",
        "assets/budgets/actions-budget-figures.sh",
        False,
        True,
    ),
)
GOVERNANCE_FILE = ".github/governance.json"
BUDGET_POLICY_ASSET = "assets/budgets/budget-policy.md"
AGENTS_FILE = "AGENTS.md"
GATE_RECORD = ".gate-time.json"
# The recipes the workflows and the gate call, which assets/justfile.template declares.
CI_CD_RECIPES = ("gate", "gate-body", "supply-chain", "ci-test", "release")
ONEBUDGETSPEC_SDK_NPM_PACKAGE = "@onebudgetspec/sdk"
TEMPLATE_DIRECTIVE = "#%"
TEMPLATE_VALUE_RE = re.compile(r"@@([A-Z_]+)@@")


class CiCdShape(NamedTuple):
    """One composition's CI/CD shape: what the templates render for it."""

    name: str
    language: str
    platforms: tuple[str, ...]
    visual_docs: bool
    visual_docs_arch: str | None

    @property
    def ci_jobs(self) -> list[str]:
        """The pull-request CI jobs, in the order ci.yml declares them."""
        jobs = ["lint"]
        if self.visual_docs_arch:
            jobs.append(f"visual-docs-{self.visual_docs_arch}")
        return [*jobs, *(f"test-{platform}" for platform in self.platforms), "budgets"]

    @property
    def required_checks(self) -> list[str]:
        """The contexts branch protection requires: `lint`, the test jobs, `budgets`."""
        return ["lint", *(f"test-{p}" for p in self.platforms), "budgets"]

    @property
    def builds(self) -> list[str]:
        """The CD build jobs' platforms, in the order release.yml declares them."""
        wanted = {b for p in self.platforms for b in PLATFORM_BUILDS[p]}
        return [build for build in CD_BUILDS if build in wanted]

    @property
    def cd_jobs(self) -> list[str]:
        return ["release-plz", *(f"build-{b}" for b in self.builds), "publish"]

    def flags(self) -> set[str]:
        flags = {self.language, *self.platforms}
        if self.visual_docs or self.visual_docs_arch:
            flags.add("visual-docs")
        if self.visual_docs_arch:
            flags.add("visual-docs-arch")
        return flags

    def values(self) -> dict[str, str]:
        def prose(jobs: list[str]) -> str:
            named = [f"`{job}`" for job in jobs]
            return ", ".join(named[:-1]) + " and " + named[-1]

        arch = self.visual_docs_arch or ""
        return {
            "NAME": self.name,
            **LANGUAGE_VALUES[self.language].values(),
            "SCREENCOMP_VERSION": SCREENCOMP_VERSION,
            "VISUAL_DOCS_ARCH": arch,
            "VISUAL_DOCS_RUNNER": VISUAL_DOCS_ARCH_RUNNERS.get(arch, ""),
            "ONEVCS_CLI_VERSION": ONEVCS_CLI_VERSION,
            "ONEBUDGETSPEC_VERSION": ONEBUDGETSPEC_VERSION,
            "ONEBUDGETSPEC_ACTION_VERSION": ONEBUDGETSPEC_ACTION_VERSION,
            "CI_PREDECESSORS": ", ".join(self.ci_jobs[:-1]),
            "BUILD_JOBS": ", ".join(f"build-{b}" for b in self.builds),
            "CI_JOB_COUNT": str(len(self.ci_jobs)),
            "CD_JOB_COUNT": str(len(self.cd_jobs)),
            "CI_JOBS_PROSE": prose(self.ci_jobs),
            "CD_JOBS_PROSE": prose(self.cd_jobs),
        }

    def loops(self) -> dict[str, dict[str, dict[str, str]]]:
        return {
            "platform": {p: CI_PLATFORMS[p].values(p) for p in self.platforms},
            "build": {b: CD_BUILDS[b].values(b) for b in self.builds},
        }


def _template_block(lines: list[str], start: int, opener: str, closer: str) -> int:
    """The index of the directive closing the block opened at ``start``."""
    depth = 0
    for index in range(start, len(lines)):
        directive = lines[index].strip()
        if directive.startswith(f"{TEMPLATE_DIRECTIVE} {opener} "):
            depth += 1
        elif directive == f"{TEMPLATE_DIRECTIVE} {closer}":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"template line {start + 1}: `{opener}` has no `{closer}`")


def _expand_template(
    lines: list[str],
    flags: set[str],
    values: dict[str, str],
    loops: dict[str, dict[str, dict[str, str]]],
) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        directive = line.strip()
        if not directive.startswith(TEMPLATE_DIRECTIVE):
            out.append(
                TEMPLATE_VALUE_RE.sub(lambda match: values[match.group(1)], line)
            )
            index += 1
            continue
        match directive[len(TEMPLATE_DIRECTIVE) :].split():
            case ["if", flag]:
                end = _template_block(lines, index, "if", "endif")
                if (flag.removeprefix("!") in flags) != flag.startswith("!"):
                    body = lines[index + 1 : end]
                    out += _expand_template(body, flags, values, loops)
                index = end + 1
            case ["for", loop, "in", *names]:
                end = _template_block(lines, index, "for", "endfor")
                items = loops[loop]
                for item in names:
                    if item in items:
                        out += _expand_template(
                            lines[index + 1 : end], flags, values | items[item], loops
                        )
                index = end + 1
            case ["#", *_]:  # A comment about the template itself; nothing renders.
                index += 1
            case _:
                raise ValueError(f"template line {index + 1}: unknown `{directive}`")
    return out


def render_template(text: str, shape: CiCdShape) -> str:
    """``text`` rendered for ``shape``: its blocks chosen, its values filled."""
    lines = text.splitlines(keepends=True)
    return "".join(
        _expand_template(
            lines,
            shape.flags(),
            shape.values()
            | cache_workflow_values(Path(__file__).resolve().parent.parent),
            shape.loops(),
        )
    )


def governance_config(shape: CiCdShape) -> str:
    """The `.github/governance.json` setup_github_governance.py reads as its default."""
    return (
        json.dumps(
            {"required_checks": shape.required_checks, "strict": False}, indent=2
        )
        + "\n"
    )


def published_paths(repo: Path, shape: CiCdShape) -> str:
    """Artifact source paths derived from the packages present at composition."""
    paths: set[str] = set()
    if shape.language == "rust" and not (repo / "Cargo.toml").is_file():
        paths.update({"Cargo.toml", "Cargo.lock", "build.rs", "src/**"})
    for manifest in sorted(repo.rglob("Cargo.toml")):
        if any(
            part in {"node_modules", "target", ".git", "tests", "fixtures"}
            for part in manifest.relative_to(repo).parts
        ):
            continue
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        package = data.get("package")
        if not package or package.get("publish") is False:
            continue
        parent = manifest.parent.relative_to(repo).as_posix()
        prefix = "" if parent == "." else parent + "/"
        paths.update({prefix + "Cargo.toml", prefix + "src/**", prefix + "build.rs"})
        for kind in ("lib", "bin"):
            targets = data.get(kind, [])
            if isinstance(targets, dict):
                targets = [targets]
            for target in targets:
                if target.get("path"):
                    paths.add(prefix + target["path"])
        for included in package.get("include", []):
            paths.add(prefix + included)
        paths.update({"Cargo.toml", "Cargo.lock", "rust-toolchain.toml"})
    for manifest in sorted(repo.rglob("package.json")):
        if any(
            part in {"node_modules", ".git", "tests", "fixtures"}
            for part in manifest.relative_to(repo).parts
        ):
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("private") is True:
            continue
        prefix = manifest.parent.relative_to(repo).as_posix()
        prefix = "" if prefix == "." else prefix + "/"
        paths.update({prefix + "package.json", prefix + "src/**"})
        for included in data.get("files", []):
            paths.add(prefix + included)
        paths.update({"bun.lock", "package-lock.json", "pnpm-lock.yaml"})
    for manifest in sorted(repo.rglob("pyproject.toml")):
        if any(
            part in {"node_modules", ".git", ".venv", "tests", "fixtures"}
            for part in manifest.relative_to(repo).parts
        ):
            continue
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        if (
            "project" not in data
            or data.get("tool", {}).get("uv", {}).get("package") is False
        ):
            continue
        prefix = manifest.parent.relative_to(repo).as_posix()
        prefix = "" if prefix == "." else prefix + "/"
        paths.update({prefix + "pyproject.toml", prefix + "src/**", "uv.lock"})
    if not paths:
        # Composition may precede the first source file, but its chosen language
        # already determines the package whose artifact the plan creates.
        paths.update(
            {"src/**", "Cargo.toml", "Cargo.lock", "build.rs"}
            if shape.language == "rust"
            else {"src/**", "package.json", "bun.lock"}
        )
    return (
        "# Files the published artifacts are built from; git pathspecs.\n"
        + "\n".join(sorted(paths))
        + "\n"
    )


def wire_test_cache(repo: Path, shape: CiCdShape) -> list[str]:
    """Install test runtime inputs and the append-only Nx telemetry hook."""
    path = repo / "nx.json"
    nx = _read_json_object(path)
    plugins = nx.setdefault("plugins", [])
    plugin = "./scripts/nx-remote-cache.mjs"
    if plugin not in plugins:
        plugins.append(plugin)
    named = _json_table(nx, "namedInputs", path)
    original_named = dict(named)
    excluded = [
        "!{projectRoot}/**/CHANGELOG.md",
        "!{projectRoot}/**/Cargo.toml",
        "!{projectRoot}/**/Cargo.lock",
        "!{projectRoot}/**/package.json",
        "!{projectRoot}/**/bun.lock",
        "!{projectRoot}/**/package-lock.json",
        "!{projectRoot}/**/pnpm-lock.yaml",
        "!{projectRoot}/**/pyproject.toml",
        "!{projectRoot}/**/uv.lock",
    ]
    base = [
        "{projectRoot}/**/*",
        *excluded,
        *(item.replace("{projectRoot}", "{workspaceRoot}") for item in excluded),
        "{workspaceRoot}/.tool-versions",
        "{workspaceRoot}/rust-toolchain.toml",
        "{workspaceRoot}/scripts/nx-version-free-inputs.mjs",
        {"runtime": "node -p process.platform"},
        {"externalDependencies": []},
    ]
    version_files = repo / ".github/nx-version-files"
    literal_inputs: list[object] = []
    if version_files.is_file():
        for line in version_files.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                base.append("!{workspaceRoot}/" + line.strip())
                base.append("!{projectRoot}/" + line.strip())
        literal_inputs.append(
            {"runtime": "node scripts/nx-version-free-inputs.mjs --ecosystem files"}
        )
    for ecosystem in ("cargo", "npm", "python"):
        named[f"{ecosystem}TestInputs"] = [
            *base,
            *literal_inputs,
            {
                "runtime": f"node scripts/nx-version-free-inputs.mjs --ecosystem {ecosystem}"
            },
        ]
    ecosystem = "cargo" if shape.language == "rust" else "npm"
    named["testInputs"] = list(named[f"{ecosystem}TestInputs"])
    defaults = _json_table(nx, "targetDefaults", path)
    test = defaults.setdefault("test", {})
    original_test_inputs = list(test.get("inputs", ["default", "^production"]))

    def retained_inputs(
        inputs: list[object],
        definitions: dict[str, object],
        seen: frozenset[str] = frozenset(),
    ) -> list[object]:
        """Retain declared source/global/env inputs while replacing release metadata."""
        kept: list[object] = []
        for item in inputs:
            if isinstance(item, str) and item in {
                "cargoTestInputs",
                "npmTestInputs",
                "pythonTestInputs",
            }:
                continue
            if isinstance(item, str) and item.startswith("^"):
                continue  # Dependencies use each dependency project's testInputs below.
            match item:
                case str() if item in definitions:
                    if item in seen:
                        raise WiringError(f"{path}: cyclic named input {item}")
                    definition = definitions[item]
                    if isinstance(definition, list):
                        kept.extend(
                            retained_inputs(definition, definitions, seen | {item})
                        )
                case str():
                    if Path(item).name not in {
                        "Cargo.toml",
                        "Cargo.lock",
                        "package.json",
                        "bun.lock",
                        "package-lock.json",
                        "pnpm-lock.yaml",
                        "pyproject.toml",
                        "uv.lock",
                        "CHANGELOG.md",
                    }:
                        kept.append(item)
                case dict():
                    if item == {"runtime": "node -p process.platform"} or item == {
                        "externalDependencies": []
                    }:
                        continue
                    kept.append(item)
        return list({json.dumps(item, sort_keys=True): item for item in kept}.values())

    named["testInputs"] += retained_inputs(original_test_inputs, original_named)
    named["testInputs"] = list(
        {
            json.dumps(item, sort_keys=True): item for item in named["testInputs"]
        }.values()
    )
    test.update({"cache": True, "inputs": ["testInputs", "^testInputs"], "outputs": []})
    for target_name in ("build", "e2e", "budgets", "budgets-host"):
        target = defaults.setdefault(target_name, {})
        inputs = target.setdefault("inputs", ["default", "^production"])
        platform = {"runtime": "node -p process.platform"}
        if platform not in inputs:
            inputs.append(platform)
    for project_path in sorted(repo.rglob("project.json")):
        # The shape's own report-job project stays exactly as the shape writes it: it
        # runs no test, and a rewrite would make composing again refuse the file.
        if project_path == repo / REPORT_JOB_PROJECT:
            continue
        if any(
            part in {"node_modules", ".git", ".nx"}
            for part in project_path.relative_to(repo).parts
        ):
            continue
        project = _read_json_object(project_path)
        root = project_path.parent
        project_ecosystem = next(
            (
                kind
                for manifest, kind in (
                    ("Cargo.toml", "cargo"),
                    ("pyproject.toml", "python"),
                    ("package.json", "npm"),
                )
                if (root / manifest).is_file()
            ),
            ecosystem,
        )
        changed = True
        definitions = original_named | project.get("namedInputs", {})
        own_inputs = [f"{project_ecosystem}TestInputs"]
        for target_name, candidate in project.get("targets", {}).items():
            if target_name.startswith("test") or target_name in {"coverage", "e2e"}:
                own_inputs += retained_inputs(
                    candidate.get("inputs", original_test_inputs), definitions
                )
        if version_files.is_file():
            for literal in version_files.read_text().splitlines():
                file = repo / literal.strip()
                if (
                    literal.strip()
                    and not literal.lstrip().startswith("#")
                    and file.is_relative_to(root)
                ):
                    own_inputs.append(
                        "!{projectRoot}/" + file.relative_to(root).as_posix()
                    )
        project.setdefault("namedInputs", {})["testInputs"] = list(
            {json.dumps(item, sort_keys=True): item for item in own_inputs}.values()
        )
        for name, target in project.get("targets", {}).items():
            if (name.startswith("test") or name in {"coverage", "e2e"}) and target.get(
                "cache", True
            ):
                target["inputs"] = [
                    "testInputs",
                    "^testInputs",
                ]
                if version_files.is_file():
                    for literal in version_files.read_text().splitlines():
                        file = repo / literal.strip()
                        if (
                            literal.strip()
                            and not literal.lstrip().startswith("#")
                            and file.is_relative_to(root)
                        ):
                            target["inputs"].append(
                                "!{projectRoot}/" + file.relative_to(root).as_posix()
                            )
                # Existing small file patterns survive; generated directories and
                # build trees cannot enter the shared test-result store.
                outputs = target.get("outputs", [])
                target["outputs"] = [
                    output
                    for output in outputs
                    if isinstance(output, str)
                    and "." in Path(output).name
                    and Path(output).suffix
                    not in {".exe", ".dll", ".so", ".dylib", ".bin"}
                    and not any(
                        part in {"target", "build", "dist"}
                        for part in Path(output).parts
                    )
                ]
                changed = True
        for target_name, target in project.get("targets", {}).items():
            if target_name in {"test", "build", "e2e", "budgets", "budgets-host"}:
                inputs = target.setdefault("inputs", ["default", "^production"])
                platform = {"runtime": "node -p process.platform"}
                if platform not in inputs:
                    inputs.append(platform)
                changed = True
        if changed:
            _write_json(project_path, project)
    _write_json(path, nx)
    package_path = repo / "package.json"
    package = _read_json_object(package_path)
    package.setdefault("name", shape.name)
    _json_table(package, "devDependencies", package_path).update(nx="23.1.3")
    _write_json(package_path, package)
    return ["wired shared Nx test cache and telemetry"]


def plan_ci_cd_files(repo: Path, skill_dir: Path, shape: CiCdShape) -> dict[str, bytes]:
    """Every file the shape writes, by repo-relative path, refusing a differing one."""
    planned: dict[str, bytes] = {}
    for file in CI_CD_FILES:
        data = (skill_dir / file.source).read_bytes()
        if file.rendered:
            text = data.decode("utf-8")
            for key, value in cache_workflow_values(skill_dir).items():
                text = text.replace(f"@@{key}@@", value)
            data = render_template(text, shape).encode("utf-8")
        planned[file.target] = data
    planned[GOVERNANCE_FILE] = governance_config(shape).encode("utf-8")
    planned[".github/release-published-paths"] = published_paths(repo, shape).encode(
        "utf-8"
    )
    for target, data in planned.items():
        path = repo / target
        if path.exists() and path.read_bytes() != data:
            raise WiringError(
                f"{path} already exists and differs from what the CI/CD shape writes\n"
                "      fix: move it aside (or merge it into the shape's version by hand "
                "after this run), then re-run --ci-cd."
            )
    return planned


def plan_budget_policy(repo: Path, skill_dir: Path) -> str | None:
    """The AGENTS.md carrying the budget policy, or None when it already does."""
    policy = (skill_dir / BUDGET_POLICY_ASSET).read_text(encoding="utf-8")
    path = repo / AGENTS_FILE
    if not path.is_file():
        raise WiringError(
            f"no {AGENTS_FILE} in {repo}\n"
            f"      fix: copy the skill's assets/{AGENTS_FILE}.template there first."
        )
    text = path.read_text(encoding="utf-8")
    if policy in text:
        return None
    heading = policy.splitlines()[0]
    if heading in text.splitlines():
        raise WiringError(
            f"{path} has a `{heading}` section that differs from {BUDGET_POLICY_ASSET}\n"
            "      fix: replace that section with the skill's text, then re-run --ci-cd."
        )
    return text.rstrip("\n") + "\n\n" + policy


def apply_ci_cd(repo: Path, skill_dir: Path, shape: CiCdShape) -> list[str]:
    """Write the shape into ``repo``; a note per change. Validates before writing."""
    justfile = next(
        (
            repo / n
            for n in ("justfile", "Justfile", ".justfile")
            if (repo / n).is_file()
        ),
        None,
    )
    if justfile is None:
        raise WiringError(
            f"no justfile in {repo}\n"
            "      fix: copy the skill's assets/justfile.template there first."
        )
    missing = [
        name
        for name in CI_CD_RECIPES
        if name not in recipe_headers(justfile.read_text(encoding="utf-8"))
    ]
    if missing:
        raise WiringError(
            f"{justfile} has no {', '.join(f'`{n}`' for n in missing)} recipe(s), which "
            "the workflows and `gate` call\n"
            "      fix: add them as assets/justfile.template declares them, then re-run "
            "--ci-cd."
        )
    planned = plan_ci_cd_files(repo, skill_dir, shape)
    agents_with_policy = plan_budget_policy(repo, skill_dir)
    # The onebudgetspec setup step first (it validates before it writes), then the SDK
    # the gate-time budget's command imports, pinned beside the CLI.
    changes = wire_onebudgetspec(repo, skill_dir)
    changes += wire_test_cache(repo, shape)
    package_json = repo / "package.json"
    package = _read_json_object(package_json)
    dev = _json_table(package, "devDependencies", package_json)
    if dev.get(ONEBUDGETSPEC_SDK_NPM_PACKAGE) != ONEBUDGETSPEC_VERSION:
        dev[ONEBUDGETSPEC_SDK_NPM_PACKAGE] = ONEBUDGETSPEC_VERSION
        _write_json(package_json, package)
        changes.append(
            f"package.json pins {ONEBUDGETSPEC_SDK_NPM_PACKAGE} {ONEBUDGETSPEC_VERSION}"
        )
    gitignore = repo / ".gitignore"
    ignored = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    executable = {file.target for file in CI_CD_FILES if file.executable}
    for target, data in planned.items():
        path = repo / target
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if target in executable:
            path.chmod(0o755)
        changes.append(f"wrote {target}")
    if agents_with_policy is not None:
        (repo / AGENTS_FILE).write_text(agents_with_policy, encoding="utf-8")
        changes.append(f"{AGENTS_FILE} states the budget policy")
    if GATE_RECORD not in ignored.splitlines():
        gitignore.write_text(
            ignored
            + ("" if not ignored or ignored.endswith("\n") else "\n")
            + GATE_RECORD
            + "\n",
            encoding="utf-8",
        )
        changes.append(f".gitignore ignores {GATE_RECORD}")
    return changes


def build_parser(
    shapes: list[str],
    languages: list[str],
    intersections: list[str],
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
        "--wiring",
        metavar="REPO_DIR",
        help="apply the baseline tools' setup step (onebudgetspec budgets) to the "
        "repository at REPO_DIR (idempotent); needs no other flag",
    )
    parser.add_argument(
        "--ci-cd",
        metavar="REPO_DIR",
        help="write the CI/CD shape into the repository at REPO_DIR: ci.yml, "
        "release-plz.yml, release.yml, the root budgets.yaml, the report job's "
        ".github/reporting, the gate's timing wrapper, the figures script, "
        ".github/governance.json and AGENTS.md's budget policy (needs --language "
        "rust or typescript and --name; runs the --wiring setup step too)",
    )
    parser.add_argument(
        "--name",
        help="the project name the budget ids are prefixed with (with --ci-cd)",
    )
    parser.add_argument(
        "--platforms",
        choices=tuple(PLATFORM_SETS),
        default="cross-platform",
        help="the platforms the repository supports (with --ci-cd; default: "
        "cross-platform — Linux, Windows and macOS)",
    )
    parser.add_argument(
        "--visual-docs",
        action="store_true",
        help="the repository captures visual docs: the CI lint job runs screencomp's "
        "composite actions (with --ci-cd)",
    )
    parser.add_argument(
        "--visual-docs-arch",
        choices=tuple(VISUAL_DOCS_ARCH_RUNNERS),
        help="also capture the visual docs on this second architecture, in a "
        "`visual-docs-<arch>` CI job of its own (with --ci-cd; implies --visual-docs)",
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
        "--oneharness-config",
        metavar="FILE",
        help="path for the oneharness.toml emitted alongside --llmlint-config "
        "(default: oneharness.toml beside the llmlint config) — the fallback "
        "harness/model selection the pinless llmlint.yml relies on",
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
    # `--monorepo` was removed rather than renamed, so an invocation carrying it
    # fails. Parse leniently to name the concrete fix instead of leaving argparse
    # to report only that the flag is unknown.
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        # Every unrecognized argument gets a next action: the removed flag its
        # own concrete fix, anything else the generic one. A mixed invocation
        # (`--monorepo --bogus`) gets both, so neither half goes unreported.
        fixes: list[str] = []
        if "--monorepo" in unknown:
            fixes.append(
                "drop --monorepo — project-graph.md composes into every plan "
                "now, so there is no flag to select it."
            )
        if any(arg != "--monorepo" for arg in unknown):
            fixes.append("run --list to see the flags this reference set supports.")
        parser.error(
            "unrecognized arguments: "
            + " ".join(unknown)
            + "".join(f"\n      fix: {fix}" for fix in fixes)
        )

    if args.list:
        print("Available composition flags (from references/):")
        print(f"  --shape         {', '.join(shapes)}")
        print(f"  --language      {', '.join(languages)}")
        print(f"  --intersection  {', '.join(intersections) or '(none)'}")
        print("  --releasing     ships a versioned artifact (releasing.md)")
        print("  --wiring        a repo dir: apply the baseline's budgets setup step")
        return 0

    skill_dir = refs_dir.parent
    composing = any(
        (
            args.shape,
            args.language,
            args.intersection,
            args.releasing,
            args.output,
            args.llmlint_config,
            args.llmlint_buildout_config,
            args.oneharness_config,
        )
    )
    if args.wiring and not composing and not args.ci_cd:
        # The setup step on its own: nothing is composed, so no flag is needed.
        return wire(Path(args.wiring), skill_dir)
    shape_flags = [
        flag
        for flag, given in (
            ("--name", args.name),
            ("--platforms", args.platforms != "cross-platform"),
            ("--visual-docs", args.visual_docs),
            ("--visual-docs-arch", args.visual_docs_arch),
        )
        if given
    ]
    if shape_flags and not args.ci_cd:
        parser.error(
            f"{', '.join(shape_flags)} describe(s) the --ci-cd shape; pass --ci-cd"
        )
    if (
        args.ci_cd
        and not args.shape
        and not any((args.output, args.llmlint_config, args.llmlint_buildout_config))
    ):
        # The shape on its own: it needs the language, not a plan.
        return ci_cd(parser, args, skill_dir)

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
        # The ongoing llmlint.yml pins no harness, so it needs oneharness.toml to
        # select one. Emit the two together (beside the config, or at --oneharness-
        # config) so the pair can't drift: fallback mode, codex + gpt-5.5 primary /
        # claude-code + opus-4.8 secondary.
        oneharness_path = (
            Path(args.oneharness_config)
            if args.oneharness_config
            else Path(args.llmlint_config).parent / "oneharness.toml"
        )
        oneharness_path.write_text(
            render_oneharness_config(skill_dir), encoding="utf-8"
        )
        print(
            f"wrote {oneharness_path} (oneharness fallback config: codex + gpt-5.5 "
            "primary, claude-code + opus-4.8 secondary)",
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
    if args.wiring and (code := wire(Path(args.wiring), skill_dir)):
        return code
    if args.ci_cd:
        return ci_cd(parser, args, skill_dir)
    return 0


def ci_cd(
    parser: argparse.ArgumentParser, args: argparse.Namespace, skill_dir: Path
) -> int:
    """Write the CI/CD shape the flags describe into ``--ci-cd``; the exit code."""
    languages = [lang for lang in args.language or [] if lang in CI_CD_LANGUAGES]
    if len(languages) != 1:
        parser.error(
            "--ci-cd needs exactly one of --language "
            + " / --language ".join(CI_CD_LANGUAGES)
            + "\n      fix: name the language whose toolchain CI installs."
        )
    if not args.name or not PROJECT_NAME_RE.fullmatch(args.name):
        parser.error(
            f"--ci-cd needs --name, a lowercase kebab-case project name (got {args.name!r})"
            "\n      fix: pass the name the budget ids are prefixed with, e.g. --name my-tool."
        )
    repo = Path(args.ci_cd)
    if not repo.is_dir():
        print(
            f"ERROR --ci-cd {repo} is not a directory\n"
            "      fix: pass the root of the repository being set up.",
            file=sys.stderr,
        )
        return 2
    shape = CiCdShape(
        name=args.name,
        language=languages[0],
        platforms=PLATFORM_SETS[args.platforms],
        visual_docs=args.visual_docs,
        visual_docs_arch=args.visual_docs_arch,
    )
    try:
        changes = apply_ci_cd(repo, skill_dir, shape)
    except WiringError as exc:
        print(f"ERROR ci-cd shape: {exc}", file=sys.stderr)
        return 2
    summary = "; ".join(changes) if changes else "already in place"
    print(
        f"wrote the CI/CD shape into {repo} (CI jobs: {', '.join(shape.ci_jobs)}; CD "
        f"jobs: {', '.join(shape.cd_jobs)}): {summary}",
        file=sys.stderr,
    )
    return 0


def wire(repo: Path, skill_dir: Path) -> int:
    """Apply every baseline tool's setup step to ``repo``; the exit code."""
    if not repo.is_dir():
        print(
            f"ERROR --wiring {repo} is not a directory\n"
            "      fix: pass the root of the repository being set up.",
            file=sys.stderr,
        )
        return 2
    for tool, setup in TOOL_WIRING.items():
        try:
            changes = setup(repo, skill_dir)
        except WiringError as exc:
            print(f"ERROR {tool} wiring: {exc}", file=sys.stderr)
            return 2
        summary = "; ".join(changes) if changes else "already wired"
        print(f"wired {tool} into {repo}: {summary}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
