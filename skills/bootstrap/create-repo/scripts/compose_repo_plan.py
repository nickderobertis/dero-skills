# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Compose a tailored repo-creation plan from the create-repo references.

Usage:
    uv run --script scripts/compose_repo_plan.py --shape SHAPE --language LANG \
        [--language LANG ...] [--releasing] \
        [--intersection NAME ...] [--tool NAME ...] [-o OUT.md] \
        [--llmlint-config FILE] [--llmlint-buildout-config FILE] \
        [--oneharness-config FILE] [--wiring REPO_DIR]
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
shape); ``releasing.md`` is pulled in by ``--releasing``. ``--tool NAME`` opts
the repo into a tool the baseline does not assume — ``references/tools/NAME.md``
joins the plan and its ``tools/NAME.llmlint.yml`` fragment joins the ongoing
``llmlint.yml`` — and nothing about a tool is emitted without it.

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

``--wiring REPO_DIR`` applies the opted-in tools' setup step to the repository
at REPO_DIR, idempotently. For ``onebudgetspec`` that is: pin
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
# README's Nx example (the e2e tier holds the wired nx.json to it).
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
BUDGETS_RECIPE_NAME = "budgets"
# The recipe itself, an asset so this script carries no orchestrator command.
ONEBUDGETSPEC_RECIPE = "assets/tools/onebudgetspec.justfile"
# A justfile recipe header: its name, then its parameters up to the single
# terminating colon, dependencies after it. The baseline checker reads recipes
# with the same pattern (its RECIPE_RE); the test suite holds the two equal.
JUST_RECIPE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[^\n:]*:(?!=)")
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
    tools: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve the flags into an ordered, de-duplicated list of reference relpaths.

    Returns the relpaths plus the resolved language list (which may have grown,
    e.g. TypeScript auto-added for a Next.js shape). Order mirrors how the skill
    says to compose: base, the mandatory project graph, shape(s), language(s),
    intersection(s), then ci, the cross-cutting references, and the opted-in tools.
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
    # A tool is opt-in: only the ones named by `--tool` join the plan, so their
    # fragments reach the ongoing llmlint.yml through the same mapping as the rest.
    ordered.extend(f"tools/{tool}.md" for tool in tools or [])

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
            params = line[len(name) : match.end() - 1]
            headers[name] = RecipeHeader(index, params, deps, comment)
    return headers


def parameter_names(params: str) -> set[str]:
    """The names of a recipe's parameters (``tier="affected"`` names ``tier``)."""
    return {token.split("=", 1)[0].lstrip("+*$") for token in params.split()}


def plan_justfile(path: Path, recipe: str) -> tuple[str, list[str]]:
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
            f"check{check.params}:{check.deps.rstrip()} {call}{comment}\n"
        )
        text = "".join(lines)
        changes.append(f"`check` depends on `{call}`")
    if BUDGETS_RECIPE_NAME not in headers:
        text = text.rstrip("\n") + "\n\n" + recipe
        changes.append("added the `budgets` recipe")
    return text, changes


def plan_package_json(package: dict[str, object], path: Path) -> list[str]:
    """Pin the release in ``package`` (in place); a note per change."""
    deps = package.get("dependencies")
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
        elif not isinstance(named[name], list):
            raise WiringError(
                f"{path}: named input `{name}` is not a list of inputs\n"
                f"      fix: make `namedInputs.{name}` a list, then re-run --wiring."
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


# The setup step of each tool that has one, keyed by its `--tool` name.
TOOL_WIRING: dict[str, ToolWiring] = {"onebudgetspec": wire_onebudgetspec}


def build_parser(
    shapes: list[str],
    languages: list[str],
    intersections: list[str],
    tools: list[str] | None = None,
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
        "--tool",
        action="append",
        choices=tools or [],
        default=[],
        metavar="NAME",
        help="opt into a tool the baseline does not assume (repeatable): pulls in "
        "references/tools/NAME.md and its llmlint fragment",
    )
    parser.add_argument(
        "--wiring",
        metavar="REPO_DIR",
        help="apply the opted-in tools' setup step to the repository at REPO_DIR "
        "(idempotent); needs a --tool that has one",
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
    tools = discover(refs_dir, "tools")

    parser = build_parser(shapes, languages, intersections, tools)
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
        print(f"  --tool          {', '.join(tools) or '(none)'}")
        return 0

    missing = [
        name
        for name, val in (("--shape", args.shape), ("--language", args.language))
        if not val
    ]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")
    wired = [tool for tool in args.tool if tool in TOOL_WIRING]
    if args.wiring and not wired:
        parser.error(
            "--wiring needs a --tool that has a setup step"
            f"\n      fix: pass --tool {' / '.join(sorted(TOOL_WIRING))} with it."
        )

    notes: list[str] = []
    relpaths, resolved_langs = select_relpaths(
        refs_dir,
        args.shape,
        args.language,
        args.intersection,
        args.releasing,
        notes,
        args.tool,
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
    flags += [f"--tool {tool}" for tool in args.tool]
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
    if args.wiring:
        repo = Path(args.wiring)
        if not repo.is_dir():
            print(
                f"ERROR --wiring {repo} is not a directory\n"
                "      fix: pass the root of the repository being set up.",
                file=sys.stderr,
            )
            return 2
        for tool in dict.fromkeys(wired):
            try:
                changes = TOOL_WIRING[tool](repo, skill_dir)
            except WiringError as exc:
                print(f"ERROR {tool} wiring: {exc}", file=sys.stderr)
                return 2
            summary = "; ".join(changes) if changes else "already wired"
            print(f"wired {tool} into {repo}: {summary}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
