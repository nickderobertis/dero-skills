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
  * AGENTS.md is reasonably terse (advisory WARN only): it is always-loaded
    context, so a file well past the soft line cap is a nudge to tighten the
    prose and push folder-scoped or rarely-relevant content into a nested
    AGENTS.md or a linked reference doc.
  * CLAUDE.md is a symlink that resolves to AGENTS.md (no drift).
  * .claude/settings.json exists and is valid JSON (the agent allowlist).
  * AGENTS.md records how the repo was composed from the skill's reference
    pieces (a `stack`/`composition` section, filled in — not the template
    placeholder), so "build up from the component pieces" is a written,
    auditable decision rather than a step quietly skipped.
  * A justfile is present and defines the core command surface:
    bootstrap, check, test, lint, format, upgrade.
  * Required recipes have real bodies (no leftover `TODO` template
    placeholders) and `check` actually runs `test`.
  * An e2e signal exists: a `*e2e*` recipe, an `e2e/` test directory, or an
    explicit e2e statement in AGENTS.md (so skipping e2e is a deliberate,
    documented decision rather than a silent omission).
  * E2E realism (advisory WARN only): e2e-tier tests that import a mocking
    library are flagged, since a mocked "e2e" proves the mock, not the product.
    Realism can't be verified stack-agnostically, so this is a nudge, not a gate.
  * A coverage signal exists: a coverage tool/flag in the justfile, a coverage
    threshold in a config file, or an explicit coverage statement in AGENTS.md
    (coverage is a default gate, so dropping it must be a documented decision).
  * A CI workflow exists under .github/workflows/ AND runs the gate
    (`just check`) — a workflow that never invokes the gate proves nothing.
  * A GitHub pull-request template exists (`.github/pull_request_template.md`,
    or the root/docs variants GitHub also renders) AND names both a What and a
    Why section — so every PR states the behavior change and its driver, not a
    walkthrough of the diff. An empty or unrelated file fails.
  * The llmlint (LLM-judge) tier is set up: an `llmlint.yml` at the repo root
    that declares `plugins` (composed from rule fragments, not empty), a
    `llmlint` recipe that runs it, and a CI workflow that invokes it. The tier
    runs OUTSIDE `just check` (it is non-deterministic) but is a required PR
    check. Presence-only and deterministic: this never *runs* llmlint.

These go past mere presence: a do-nothing CI file, a placeholder `test`
recipe, or a missing e2e tier are the parts most often skipped when the skill
is applied loosely, so the baseline fails on them rather than passing a repo
that only looks set up. Stack-specific depth (does e2e exercise real journeys?)
still belongs in the repo's own `just check`, not here.

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

# Advisory soft cap on the root AGENTS.md. It is always-loaded context — every
# session reads it — so its length is a standing tax on the context budget. This
# is a nudge (WARN, never fails), set well above a terse, complete instruction
# layer so it only flags a file that has accreted folder-scoped or
# rarely-relevant prose better off in a nested AGENTS.md or a linked reference.
AGENTS_MD_MAX_LINES = 250

# The command that proves the artifact. CI must invoke it, and `check` is where
# the full gate (including e2e) is composed.
GATE_COMMAND = "just check"

# A justfile recipe header starts at column 0 with an identifier, may take
# parameters, and ends in a single ':'. Assignments (`name := value`) are
# excluded via the negative lookahead so they are not mistaken for recipes.
RECIPE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[^\n:=]*:(?!=)")

JUSTFILE_NAMES = ("justfile", "Justfile", ".justfile")

# llmlint config filenames (llmlint discovers `llmlint.yml`/`.yaml`, plain or
# dot-prefixed). The repo's root config is the composed LLM-judge tier.
LLMLINT_CONFIG_NAMES = (
    "llmlint.yml",
    "llmlint.yaml",
    ".llmlint.yml",
    ".llmlint.yaml",
)

# Case-insensitive marker that a recipe body is still the unfilled template.
PLACEHOLDER_RE = re.compile(r"\bTODO\b", re.IGNORECASE)

# Mocking-library signals. An e2e test that imports one of these may be mocking
# the very boundary it should exercise for real — the fast-and-mocked failure
# mode. Stack-agnostic: spans Python (unittest.mock, monkeypatch, pytest-mock,
# @patch), JS/TS (vi.mock, jest.mock, sinon, nock), and others (mockito). Used
# only for an advisory WARN, so a stub of a genuinely external third party (the
# one sanctioned use) costing a nudge is an acceptable trade.
MOCK_RE = re.compile(
    r"unittest\.mock|from\s+mock\b|import\s+mock\b|\bMagicMock\b|\bmonkeypatch\b|"
    r"pytest[_-]mock|\bmocker\b|@patch\b|\bvi\.mock\b|\bjest\.mock\b|\bsinon\b|"
    r"\bnock\b|\bmockito\b",
    re.IGNORECASE,
)

# Source suffixes worth scanning for e2e realism, and vendor/build directories
# never worth descending into.
E2E_SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".mjs", ".rs", ".go", ".rb", ".sh")
SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "target",
        "dist",
        "build",
        ".venv",
        "venv",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)

# A coverage signal: a coverage tool or threshold flag in the justfile / a
# config file, or the word "coverage" documenting the decision in AGENTS.md.
# Stack-agnostic on purpose — it spans pytest-cov, Vitest/c8/nyc, cargo-llvm-cov,
# tarpaulin, kcov/bashcov — so it forces coverage to be a *named* decision, not
# the specific 95% number (that is prescribed in the references and SKILL.md).
COVERAGE_RE = re.compile(
    r"--cov|cov-fail-under|coverage|fail[_-]under|llvm-cov|tarpaulin|"
    r"\bnyc\b|\bc8\b|kcov|bashcov",
    re.IGNORECASE,
)

# Config files that commonly declare a coverage threshold.
COVERAGE_CONFIG_NAMES = (
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    ".coveragerc",
    "package.json",
    "vitest.config.ts",
    "vitest.config.js",
    "vite.config.ts",
    "vite.config.js",
    "jest.config.js",
    "jest.config.ts",
    "Cargo.toml",
)

# An AGENTS.md heading that records how the repo was built up from the skill's
# reference axes (product shape + language(s) + cross-cutting/intersection
# references, plus what was excluded and why).
COMPOSITION_HEADING_RE = re.compile(r"\b(composition|composed|stack)\b", re.IGNORECASE)

# An unfilled `<...>` angle-bracket placeholder left over from a template
# section (the AGENTS.md template marks fill-in spots with `<like this>`).
ANGLE_PLACEHOLDER_RE = re.compile(r"<[^>\n]+>")

# GitHub renders a default pull-request template from a file named
# pull_request_template.* (case-insensitive, .md/.txt/extensionless) in the repo
# root, .github/, or docs/ — or from any file inside a PULL_REQUEST_TEMPLATE/
# directory in one of those locations (the multi-template form).
PR_TEMPLATE_DIRS = ("", ".github", "docs")
PR_TEMPLATE_STEM = "pull_request_template"

# The two required sections of the skill's PR template. Word-boundary and
# case-insensitive so "## What changed" / "Why" both count without prescribing
# the exact heading text; the third section ("Additional info") is optional and
# is deliberately not required.
PR_WHAT_RE = re.compile(r"\bwhat\b", re.IGNORECASE)
PR_WHY_RE = re.compile(r"\bwhy\b", re.IGNORECASE)


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


@dataclass
class Recipe:
    """A parsed justfile recipe: its dependency list and its body lines."""

    name: str
    deps: list[str] = field(default_factory=list)
    body: list[str] = field(default_factory=list)


def parse_just_recipe_details(text: str) -> dict[str, Recipe]:
    """Parse ``text`` into recipes keyed by name, with deps and body lines.

    Dependencies are the whitespace-separated tokens after the header ``:``
    (inline comments stripped). The body is the following indented lines, with
    blanks and surrounding whitespace removed. This is intentionally a light
    parser: it captures enough to tell a filled-in recipe from a placeholder
    and to see whether ``check`` wires in ``test``, not to emulate just.
    """
    recipes: dict[str, Recipe] = {}
    current: Recipe | None = None
    for line in text.splitlines():
        if line and line[0] not in (" ", "\t"):
            if line[0] == "#":
                current = None
                continue
            match = RECIPE_RE.match(line)
            if match:
                after = line.split(":", 1)[1].split("#", 1)[0]
                current = Recipe(name=match.group(1), deps=after.split())
                recipes[current.name] = current
            else:
                current = None  # assignment or other non-recipe line
        elif current is not None and line.strip():
            current.body.append(line.strip())
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


def check_agents_length(repo: Path) -> list[Finding]:
    """Advise (never fail) when the root AGENTS.md has grown too long.

    AGENTS.md is read every session, so length is a standing context-budget tax.
    The skill prescribes terse, pithy language, with folder-scoped rules pushed
    into nested AGENTS.md files and content that is neither always relevant nor
    cleanly scoped to one folder moved into a reference doc linked from
    AGENTS.md. This is a WARN, not an ERROR — the right bar is judgment, not a
    line count — but a file well past the cap is a reliable signal to tighten.
    """
    agents = repo / "AGENTS.md"
    if not agents.is_file():
        # Absence is already an ERROR from check_agents_md; don't pile on.
        return []
    lines = len(agents.read_text(encoding="utf-8").splitlines())
    if lines > AGENTS_MD_MAX_LINES:
        return [
            Finding(
                "WARN",
                f"AGENTS.md is {lines} lines — it is always-loaded context, so "
                "keep it terse",
                "tighten the prose; move folder-scoped rules into a nested "
                "AGENTS.md and content that is not always relevant into a "
                "reference doc linked from AGENTS.md",
            )
        ]
    return [Finding("OK", "AGENTS.md is reasonably terse")]


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
    name = justfile.name
    findings = [Finding("OK", f"{name} present")]
    details = parse_just_recipe_details(justfile.read_text(encoding="utf-8"))

    missing = [r for r in REQUIRED_RECIPES if r not in details]
    if missing:
        joined = ", ".join(missing)
        findings.append(
            Finding(
                "ERROR",
                f"{name} missing required recipe(s): {joined}",
                f"add recipe(s) to the {name}: {joined}",
            )
        )
    else:
        findings.append(Finding("OK", "justfile defines the full command surface"))

    # A required recipe that still carries a TODO placeholder body was copied
    # from the template but never filled in — the gate would pass while doing
    # nothing.
    placeholder = sorted(
        r
        for r in REQUIRED_RECIPES
        if r in details and any(PLACEHOLDER_RE.search(line) for line in details[r].body)
    )
    if placeholder:
        joined = ", ".join(placeholder)
        findings.append(
            Finding(
                "ERROR",
                f"{name} recipe(s) still hold template placeholders: {joined}",
                f"replace the TODO bodies with real commands: {joined}",
            )
        )

    # `check` is the full gate, so it must actually run the test suite — either
    # as a dependency (`check: ... test`) or by invoking it in the body.
    check = details.get("check")
    if check is not None and "test" in details:
        runs_test = "test" in check.deps or any(
            "just test" in line for line in check.body
        )
        if not runs_test:
            findings.append(
                Finding(
                    "ERROR",
                    "`check` does not run `test` (tests are absent from the gate)",
                    "make `check` depend on `test`, e.g. `check: lint test`",
                )
            )

    return findings


def check_e2e(repo: Path) -> list[Finding]:
    """Require a deliberate e2e decision: real coverage or a documented opt-out.

    E2E is the part most often dropped silently. This does not (and cannot,
    stack-agnostically) verify that e2e tests exercise real journeys; it only
    forces e2e to be a *named* part of the repo — a recipe, an `e2e/` test
    tree, or an explicit statement in AGENTS.md explaining the coverage or why
    it does not apply (e.g. a pure library with no user-facing journey).
    """
    justfile = find_justfile(repo)
    if justfile is not None:
        recipes = parse_just_recipes(justfile.read_text(encoding="utf-8"))
        if any("e2e" in r.lower() for r in recipes):
            return [Finding("OK", "e2e recipe present")]

    if (repo / "e2e").is_dir() or (repo / "tests" / "e2e").is_dir():
        return [Finding("OK", "e2e test directory present")]

    agents = repo / "AGENTS.md"
    if agents.is_file():
        text = agents.read_text(encoding="utf-8").lower()
        if "e2e" in text or "end-to-end" in text:
            return [Finding("OK", "AGENTS.md documents the e2e decision")]

    return [
        Finding(
            "ERROR",
            "no e2e signal (recipe, e2e/ directory, or AGENTS.md statement)",
            "add a `test-e2e` recipe wired into `just check`, or state in "
            "AGENTS.md what e2e covers or why it does not apply",
        )
    ]


def _iter_e2e_test_files(repo: Path):
    """Yield source files in the e2e tier: under an ``e2e/`` dir or e2e-named.

    Bounded on purpose — skips vendor/build trees and non-source suffixes — so it
    stays fast in a large repo and only ever looks at hand-written test code.
    """
    for path in repo.rglob("*"):
        rel_parts = path.relative_to(repo).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if not path.is_file() or path.suffix not in E2E_SOURCE_SUFFIXES:
            continue
        in_e2e_dir = any(part.lower() == "e2e" for part in rel_parts[:-1])
        if in_e2e_dir or "e2e" in path.name.lower():
            yield path


def check_e2e_realism(repo: Path) -> list[Finding]:
    """Advisory WARN when e2e-tier tests import a mocking library.

    A mocked "e2e" proves the mock, not the product — the fast-and-mocked failure
    mode the skill warns against. Whether a given mock is legitimate (a genuinely
    external third party, which belongs in the live tier) cannot be judged
    stack-agnostically, so this never fails the gate; it is a nudge to confirm the
    e2e suite drives the *real* boundary the way a user does. Files outside the
    e2e tier are not scanned — unit tests may mock freely.
    """
    mock_files: list[str] = []
    for path in _iter_e2e_test_files(repo):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if MOCK_RE.search(text):
            mock_files.append(path.relative_to(repo).as_posix())
    if not mock_files:
        return []
    mock_files.sort()
    shown = ", ".join(mock_files[:5])
    if len(mock_files) > 5:
        shown += f" (+{len(mock_files) - 5} more)"
    return [
        Finding(
            "WARN",
            f"e2e-tier test(s) import a mocking library: {shown}",
            "confirm these drive the real boundary (subprocess, real local "
            "server/DB, real temp files), not a mock of the layer under test; "
            "mock only a genuinely external third party, gated to the live tier",
        )
    ]


def check_coverage(repo: Path) -> list[Finding]:
    """Require a deliberate coverage decision: enforced in the gate, or opted out.

    The create-repo skill makes coverage a *default* gate (95% line coverage,
    enforced in `just check`) rather than an opt-in vanity metric — a repo that
    ships behavior its tests never execute has a hole, and the number makes it
    visible. Like the e2e check, this is stack-agnostic, so it cannot verify the
    threshold value; it only forces coverage to be a *named* part of the repo: a
    coverage tool/flag in the justfile, a coverage config, or an explicit
    statement in AGENTS.md (the documented lower bar or why coverage tooling
    doesn't fit this stack). Silent omission is what it catches.
    """
    justfile = find_justfile(repo)
    if justfile is not None and COVERAGE_RE.search(
        justfile.read_text(encoding="utf-8")
    ):
        return [Finding("OK", "coverage enforced in the command surface")]

    for name in COVERAGE_CONFIG_NAMES:
        cfg = repo / name
        if cfg.is_file() and COVERAGE_RE.search(cfg.read_text(encoding="utf-8")):
            return [Finding("OK", f"coverage configured in {name}")]

    agents = repo / "AGENTS.md"
    if agents.is_file() and "coverage" in agents.read_text(encoding="utf-8").lower():
        return [Finding("OK", "AGENTS.md documents the coverage decision")]

    return [
        Finding(
            "ERROR",
            "no coverage signal (recipe/flag, config threshold, or AGENTS.md statement)",
            "enforce coverage in `just check` (e.g. pytest --cov-fail-under=95, "
            "Vitest coverage.thresholds, cargo llvm-cov --fail-under-lines 95), or "
            "state in AGENTS.md the coverage bar or why it does not apply",
        )
    ]


def find_heading_section(text: str, heading_re: re.Pattern[str]) -> list[str] | None:
    """Return the body lines under the first markdown heading matching ``heading_re``.

    The body runs from just after the heading to the next heading of the same or
    higher level (a shallower or equal ``#`` count), exclusive. Returns ``None``
    if no matching heading is found. Light on purpose: enough to tell a filled-in
    section from a missing or placeholder one, not a full markdown parser.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#") and heading_re.search(line):
            level = len(line) - len(line.lstrip("#"))
            body: list[str] = []
            for nxt in lines[i + 1 :]:
                if nxt.startswith("#"):
                    nxt_level = len(nxt) - len(nxt.lstrip("#"))
                    if nxt_level <= level:
                        break
                body.append(nxt)
            return body
    return None


def check_composition(repo: Path) -> list[Finding]:
    """Require AGENTS.md to record how the repo was composed from the references.

    The create-repo skill builds a repo by *composing* component references — one
    product shape, the language(s), `ci.md` always, and `monorepo.md` /
    intersection references when they apply — and writing down what was excluded
    and why. That deliberate "build up from the pieces" step is the one most
    often skipped: an agent jumps straight to a justfile and misses the
    stack-specific gates the references prescribe. This makes the composition an
    auditable artifact — a filled-in `stack`/`composition` section in AGENTS.md —
    so the decision is recorded rather than silently omitted. It checks that the
    section exists and is real (non-empty, no leftover template placeholders); it
    cannot, stack-agnostically, judge whether the *right* pieces were chosen.
    """
    agents = repo / "AGENTS.md"
    if not agents.is_file():
        # Absence of AGENTS.md is already reported by check_agents_md; don't
        # pile on a second, more confusing error for the same root cause.
        return []
    body = find_heading_section(
        agents.read_text(encoding="utf-8"), COMPOSITION_HEADING_RE
    )
    if body is None:
        return [
            Finding(
                "ERROR",
                "AGENTS.md does not record how the repo was composed from the "
                "skill's reference pieces",
                "add a '## Stack and composition' section to AGENTS.md naming the "
                "product shape, the language(s), the references you pulled in "
                "(ci.md always; monorepo/intersection when they apply), and what "
                "you excluded and why",
            )
        ]
    content = [line for line in body if line.strip()]
    if not content:
        return [
            Finding(
                "ERROR",
                "the AGENTS.md composition section is empty",
                "fill it with the product shape, language(s), composed references, "
                "and what you excluded and why",
            )
        ]
    if any(
        ANGLE_PLACEHOLDER_RE.search(line) or PLACEHOLDER_RE.search(line)
        for line in content
    ):
        return [
            Finding(
                "ERROR",
                "the AGENTS.md composition section still holds template placeholders",
                "replace the <...>/TODO placeholders with the real shape, "
                "language(s), composed references, and exclusions + rationale",
            )
        ]
    return [Finding("OK", "AGENTS.md records the reference composition")]


def check_ci(repo: Path) -> list[Finding]:
    workflows = repo / ".github" / "workflows"
    files = (
        [
            p
            for p in workflows.iterdir()
            if p.is_file() and p.suffix in (".yml", ".yaml")
        ]
        if workflows.is_dir()
        else []
    )
    if not files:
        return [
            Finding(
                "ERROR",
                "no CI workflow under .github/workflows/",
                "add a workflow that runs `just check` on a clean checkout "
                "(see assets/ci.yml.template and references/ci.md)",
            )
        ]
    if not any(GATE_COMMAND in p.read_text(encoding="utf-8") for p in files):
        return [
            Finding(
                "ERROR",
                f"CI workflow(s) never run the gate (`{GATE_COMMAND}`)",
                f"have a workflow run `{GATE_COMMAND}` on a clean checkout "
                "(see assets/ci.yml.template)",
            )
        ]
    return [Finding("OK", "CI workflow runs the gate")]


def find_pr_template(repo: Path) -> Path | None:
    """Return the repo's GitHub pull-request template file, or None.

    Accepts a single default template named ``pull_request_template.*`` (any
    case; ``.md``/``.txt``/extensionless) in the repo root, ``.github/``, or
    ``docs/``, or — for the multi-template form — the first file inside a
    ``PULL_REQUEST_TEMPLATE/`` directory in one of those locations.
    """
    for sub in PR_TEMPLATE_DIRS:
        base = repo / sub if sub else repo
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            name = entry.name.lower()
            is_named = name == PR_TEMPLATE_STEM or name.startswith(
                PR_TEMPLATE_STEM + "."
            )
            if entry.is_file() and is_named:
                return entry
            if entry.is_dir() and name == PR_TEMPLATE_STEM:
                files = sorted(p for p in entry.iterdir() if p.is_file())
                if files:
                    return files[0]
    return None


def check_pr_template(repo: Path) -> list[Finding]:
    """Require a GitHub pull-request template naming What and Why sections.

    The create-repo skill makes a PR template a required deliverable: GitHub
    auto-populates a new PR from it, so the template is what makes the
    every-PR-states-its-intent discipline the default path. A PR should describe
    the behavior change (What) and its driver and impact (Why) in terse, pithy
    prose — not walk through the diff, which already shows the code. Going past
    mere presence (like the other invariants), an empty or unrelated file fails:
    the template must name both the What and Why sections. The third section,
    "Additional info", is optional and is not required here.
    """
    template = find_pr_template(repo)
    if template is None:
        return [
            Finding(
                "ERROR",
                "no GitHub pull-request template",
                "add .github/pull_request_template.md with What and Why sections "
                "(Additional info optional) — see the create-repo skill's "
                "assets/pull_request_template.md.template",
            )
        ]
    text = template.read_text(encoding="utf-8")
    missing = [
        label
        for label, pattern in (("What", PR_WHAT_RE), ("Why", PR_WHY_RE))
        if not pattern.search(text)
    ]
    if missing:
        rel = template.relative_to(repo).as_posix()
        joined = " and ".join(missing)
        return [
            Finding(
                "ERROR",
                f"{rel} is missing the {joined} section(s)",
                "structure the PR template as What (the behavior change) and Why "
                "(the driver and impact), terse and pithy — not a description of "
                "the code changes; Additional info is optional",
            )
        ]
    return [Finding("OK", "GitHub PR template present with What/Why")]


def find_llmlint_config(repo: Path) -> Path | None:
    for name in LLMLINT_CONFIG_NAMES:
        candidate = repo / name
        if candidate.is_file():
            return candidate
    return None


def llmlint_config_has_plugins(text: str) -> bool:
    """True when an llmlint config declares at least one plugin.

    Handles the block form (``plugins:`` then indented ``- ...`` entries) and the
    inline form (``plugins: ["..."]``). A light scan, not a YAML parse — enough to
    tell a composed config from an empty ``plugins: []`` or a missing key.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("plugins:"):
            continue
        rest = stripped[len("plugins:") :].strip()
        if rest.startswith("["):
            return rest not in ("[]", "[ ]")
        # Block form: an indented list entry before the next top-level key.
        for nxt in lines[i + 1 :]:
            if not nxt.strip():
                continue
            if re.match(r"^\s+-\s+\S", nxt):
                return True
            if not nxt.startswith((" ", "\t")):
                break
    return False


def ci_references_llmlint(repo: Path) -> bool:
    """True when any CI workflow file mentions llmlint (the blocking PR check)."""
    workflows = repo / ".github" / "workflows"
    if not workflows.is_dir():
        return False
    return any(
        p.is_file()
        and p.suffix in (".yml", ".yaml")
        and "llmlint" in p.read_text(encoding="utf-8")
        for p in workflows.iterdir()
    )


def check_llmlint(repo: Path) -> list[Finding]:
    """Require the llmlint (LLM-judge) tier to be wired — presence-only.

    llmlint runs OUTSIDE the deterministic ``just check`` gate (it drives a real
    harness, so it is non-deterministic and credentialed); this check never runs
    it. It verifies the tier is set up: a composed ``llmlint.yml`` declaring the
    rule-fragment ``plugins``, a ``lint-llm`` recipe to run it on demand, and a CI
    workflow that invokes it as the blocking PR check. Compose the config with
    ``compose_repo_plan.py --llmlint-config`` rather than hand-rolling it.
    """
    problems: list[Finding] = []

    cfg = find_llmlint_config(repo)
    if cfg is None:
        problems.append(
            Finding(
                "ERROR",
                "no llmlint.yml (the LLM-judge tier config)",
                "compose it: compose_repo_plan.py --llmlint-config llmlint.yml "
                "(see references/llmlint.md)",
            )
        )
    elif not llmlint_config_has_plugins(cfg.read_text(encoding="utf-8")):
        problems.append(
            Finding(
                "ERROR",
                f"{cfg.name} declares no plugins (its rules come from composed "
                "plugin fragments)",
                "regenerate it with compose_repo_plan.py --llmlint-config so it "
                "wires in the base + per-reference rule fragments",
            )
        )

    justfile = find_justfile(repo)
    recipes = (
        parse_just_recipes(justfile.read_text(encoding="utf-8")) if justfile else set()
    )
    if "lint-llm" not in recipes:
        problems.append(
            Finding(
                "ERROR",
                "no `lint-llm` recipe in the justfile",
                "add a `lint-llm:` recipe that runs `llmlint`, separate from `check` "
                "(the tier is non-deterministic, so it stays out of the gate)",
            )
        )

    if not ci_references_llmlint(repo):
        problems.append(
            Finding(
                "ERROR",
                "no CI workflow runs llmlint (the blocking PR check)",
                "add a workflow job that runs `just lint-llm-diff`, separate from "
                "the `check` gate and fork-safe (see references/ci.md and llmlint.md)",
            )
        )

    return problems or [Finding("OK", "llmlint tier configured (config + recipe + CI)")]


def audit(repo: Path) -> list[Finding]:
    findings: list[Finding] = []
    findings += check_agents_md(repo)
    findings += check_agents_length(repo)
    findings += check_claude_symlink(repo)
    findings += check_claude_settings(repo)
    findings += check_composition(repo)
    findings += check_justfile(repo)
    findings += check_e2e(repo)
    findings += check_e2e_realism(repo)
    findings += check_coverage(repo)
    findings += check_ci(repo)
    findings += check_pr_template(repo)
    findings += check_llmlint(repo)
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
