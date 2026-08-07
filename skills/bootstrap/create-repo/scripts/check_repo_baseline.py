# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Audit a repository against the create-repo baseline invariants.

Usage:
    uv run --script scripts/check_repo_baseline.py [REPO_DIR]              # default: .
    uv run --script scripts/check_repo_baseline.py [REPO_DIR] --buildout   # + buildout tier

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
  * The session provisioner, when present, is correct: `scripts/session-setup.sh`
    provisions `just` (a cloud image often ships the language runtime but not
    `just`, and has no version manager there to read `.tool-versions`) and is wired
    into the SessionStart hook. Optional, so silent when neither shipped nor wired.
  * The suppressions review comment is wired: a workflow under
    .github/workflows/ uses the `nickderobertis/notignored` action on pull
    requests, with the `pull-requests: write` permission and a `fetch-depth: 0`
    checkout it needs to work, and the fork-PR skip guard that keeps it off the
    required-checks set. It posts every suppression a PR adds, so a high-level
    review sees the checks the change switched off.
  * The llmlint (LLM-judge) tier is set up: an `llmlint.yml` at the repo root
    that declares `plugins` (composed from rule fragments, not empty), a
    fallback-mode `oneharness.toml` selecting the harness the pinless config drives
    (a primary plus a required `claude-code` fallback), a `lint-llm` recipe and the diff-scoped
    `lint-llm-diff` recipe (the blocking PR check), an automated install
    (`scripts/setup-llmlint.sh` wired into a SessionStart hook — directly or via
    `session-setup.sh`), and a CI workflow that invokes it. The tier runs OUTSIDE
    `just check` (it is non-deterministic) but is a required PR check.
    Presence-only and deterministic: `audit()` never *runs* llmlint. (The `main`
    `--buildout` flag additionally composes and runs the one-time buildout tier —
    non-deterministic, so opt-in and never part of `audit()`; see run_buildout.)

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

# llmlint: ignore-file[async_typed_clients_at_boundaries] the only external call is invoking
# the `llmlint` CLI binary as a subprocess for the opt-in --buildout tier; a CLI has no async
# typed client, and this is a stdlib-only PEP 723 script (dependencies = []). A synchronous
# subprocess is the correct, intentional client — same rationale as the `gh` call in
# setup_github_governance.py.

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, Protocol

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
# parameters (including defaulted ones like `base="origin/main"`), and ends in a
# single ':'. Assignments (`name := value`) are excluded via the negative
# lookahead — the ':' before '=' fails `(?!=)` — so only the terminating recipe
# colon matches. The parameter span allows '=' so a defaulted parameter does not
# hide the recipe (it must, or e.g. `lint-llm-diff base="origin/main":` is missed).
RECIPE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[^\n:]*:(?!=)")

JUSTFILE_NAMES = ("justfile", "Justfile", ".justfile")

# llmlint config filenames (llmlint discovers `llmlint.yml`/`.yaml`, plain or
# dot-prefixed). The repo's root config is the composed LLM-judge tier.
LLMLINT_CONFIG_NAMES = (
    "llmlint.yml",
    "llmlint.yaml",
    ".llmlint.yml",
    ".llmlint.yaml",
)

# oneharness config filenames (oneharness discovers `oneharness.toml` or the
# dot-prefixed form, upward from the working directory). It selects the harness/
# model llmlint drives, since the composed `llmlint.yml` pins none.
ONEHARNESS_CONFIG_NAMES = (
    "oneharness.toml",
    ".oneharness.toml",
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

# The suppressions review comment: the notignored action, however it is pinned
# (the floating `@v0` major tag the skill templates, or an exact `@v0.1.11`).
NOTIGNORED_ACTION_RE = re.compile(r"uses:\s*nickderobertis/notignored@")

# What the action needs to do its job, and what keeps it off the required set:
#   * `pull-requests: write` — the token upserts the sticky comment;
#   * `fetch-depth: 0` — the scan diffs against the base branch, which a shallow
#     checkout omits, so without it the comment reports nothing;
#   * the fork guard — a fork's read-only token cannot upsert, so the job skips
#     there rather than failing on something the contributor cannot fix.
NOTIGNORED_COMMENT_PERM_RE = re.compile(r"pull-requests:\s*write")
NOTIGNORED_FULL_HISTORY_RE = re.compile(r"fetch-depth:\s*0\b")
NOTIGNORED_FORK_GUARD_RE = re.compile(
    r"head\.repo\.full_name\s*==\s*github\.repository"
    r"|github\.repository\s*==\s*[^\n]*head\.repo\.full_name"
)


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


def workflow_files(repo: Path) -> list[Path]:
    """Every GitHub Actions workflow file in the repo, sorted for stable output."""
    workflows = repo / ".github" / "workflows"
    if not workflows.is_dir():
        return []
    return sorted(
        p for p in workflows.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")
    )


def check_ci(repo: Path) -> list[Finding]:
    files = workflow_files(repo)
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


def check_notignored(repo: Path) -> list[Finding]:
    """Require the suppressions review comment, wired so it actually reports.

    The gate and the llmlint judge both decide pass/fail; neither shows a reviewer
    the checks a change *switched off*. The notignored action posts one sticky
    comment naming every suppression the pull request added, with its rules and
    stated reason — the artifact that makes a high-level review of agent-written
    code possible. Like the other invariants this goes past presence, because the
    two ways it silently reports nothing are both invisible in a green run: a
    shallow checkout (no base branch to diff against) and a token that cannot
    comment. The fork-PR skip guard is checked too — it is what justifies leaving
    the workflow *out* of the required-checks set, since a fork's read-only token
    can never upsert the comment.
    """
    workflow = next(
        (
            p
            for p in workflow_files(repo)
            if NOTIGNORED_ACTION_RE.search(p.read_text(encoding="utf-8"))
        ),
        None,
    )
    if workflow is None:
        return [
            Finding(
                "ERROR",
                "no workflow runs the notignored suppressions review comment",
                "add .github/workflows/notignored.yml from the create-repo skill's "
                "assets/notignored.yml.template (uses: nickderobertis/notignored@v0 "
                "on pull_request) — see references/ci.md",
            )
        ]

    rel = workflow.relative_to(repo).as_posix()
    text = workflow.read_text(encoding="utf-8")
    problems: list[Finding] = []
    if not NOTIGNORED_COMMENT_PERM_RE.search(text):
        problems.append(
            Finding(
                "ERROR",
                f"{rel} does not grant `pull-requests: write`, so the comment "
                "cannot be posted",
                "add `permissions:` with `contents: read` and "
                "`pull-requests: write` (least privilege — nothing else)",
            )
        )
    if not NOTIGNORED_FULL_HISTORY_RE.search(text):
        problems.append(
            Finding(
                "ERROR",
                f"{rel} checks out shallowly, so there is no base branch to diff "
                "against and the comment reports nothing",
                "pass `fetch-depth: 0` to actions/checkout in that workflow",
            )
        )
    if not NOTIGNORED_FORK_GUARD_RE.search(text):
        problems.append(
            Finding(
                "ERROR",
                f"{rel} has no fork-PR skip guard; a fork's read-only token cannot "
                "upsert the comment",
                "guard the job with `if: github.event.pull_request.head.repo."
                "full_name == github.repository`, and keep the workflow out of the "
                "required-checks set (it is a review artifact, not a gate)",
            )
        )
    return problems or [
        Finding("OK", f"{rel} posts the suppressions review comment on pull requests")
    ]


def find_llmlint_config(repo: Path) -> Path | None:
    for name in LLMLINT_CONFIG_NAMES:
        candidate = repo / name
        if candidate.is_file():
            return candidate
    return None


def find_oneharness_config(repo: Path) -> Path | None:
    for name in ONEHARNESS_CONFIG_NAMES:
        candidate = repo / name
        if candidate.is_file():
            return candidate
    return None


def oneharness_fallback_harnesses(text: str) -> list[str] | None:
    """Return the `harnesses` list when oneharness.toml is in fallback mode.

    A light scan, not a full TOML parse (stdlib-only, no tomllib guarantee on the
    consuming repo's runtime): find `run_mode = "fallback"` and read the inline
    `harnesses = [...]` array. Returns the ordered harness ids, or ``None`` when
    the config is not in fallback mode / declares no harness list — enough to tell
    a composed fallback config from a hand-rolled single-harness one.
    """
    if not re.search(r'^\s*run_mode\s*=\s*["\']fallback["\']', text, re.MULTILINE):
        return None
    match = re.search(r"^\s*harnesses\s*=\s*\[([^\]]*)\]", text, re.MULTILINE)
    if not match:
        return None
    ids = re.findall(r'["\']([^"\']+)["\']', match.group(1))
    return ids or None


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
    return any("llmlint" in p.read_text(encoding="utf-8") for p in workflow_files(repo))


def find_setup_llmlint_script(repo: Path) -> Path | None:
    """Return the idempotent llmlint toolchain installer, or None.

    The skill drops it at ``scripts/setup-llmlint.sh``; accept a couple of nearby
    spellings so the check is about the automation existing, not the exact path.
    """
    for rel in (
        "scripts/setup-llmlint.sh",
        "scripts/setup_llmlint.sh",
        "setup-llmlint.sh",
    ):
        candidate = repo / rel
        if candidate.is_file():
            return candidate
    return None


def find_session_setup_script(repo: Path) -> Path | None:
    """Return the idempotent session/dev-toolchain provisioner, or None.

    The skill drops it at ``scripts/session-setup.sh`` (from
    ``assets/session-setup.sh.template``); accept a couple of nearby spellings so
    the check is about the automation existing, not the exact path.
    """
    for rel in (
        "scripts/session-setup.sh",
        "scripts/session_setup.sh",
        "session-setup.sh",
    ):
        candidate = repo / rel
        if candidate.is_file():
            return candidate
    return None


# A deterministic signal that session-setup.sh actually provisions ``just``: the
# PyPI package that ships the binary, the template's install helper, or a ``just``
# token next to an install verb (so an alternative installer still matches).
SESSION_SETUP_PROVISIONS_JUST_RE = re.compile(
    r"rust-just|ensure_just|install[^\n]*\bjust\b|\bjust\b[^\n]*install",
    re.IGNORECASE,
)


def sessionstart_hook_commands(repo: Path) -> list[str]:
    """Return every SessionStart hook command string in .claude/settings.json.

    A light structural read of the settings JSON, tolerant of malformed files (a
    separate check reports invalid JSON); returns ``[]`` when absent or malformed.
    """
    settings = repo / ".claude" / "settings.json"
    if not settings.is_file():
        return []
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return []
    session_start = hooks.get("SessionStart")
    if not isinstance(session_start, list):
        return []
    commands: list[str] = []
    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        entry_hooks = entry.get("hooks")
        if not isinstance(entry_hooks, list):
            continue
        for hook in entry_hooks:
            if isinstance(hook, dict):
                commands.append(str(hook.get("command", "")))
    return commands


def settings_sessionstart_runs_setup(repo: Path) -> bool:
    """True when a SessionStart hook runs the session provisioner.

    The install is automated by wiring the Claude Code ``SessionStart`` hook at
    ``scripts/session-setup.sh`` (which provisions ``just`` and hands off to
    ``setup-llmlint.sh``) — or, in a llmlint-only layout, at ``setup-llmlint.sh``
    directly. Either wiring readies a web/cloud session with no manual step.
    """
    return any(
        "session-setup" in cmd or "setup-llmlint" in cmd
        for cmd in sessionstart_hook_commands(repo)
    )


def check_session_setup(repo: Path) -> list[Finding]:
    """Verify the session/dev-toolchain provisioner, when present.

    ``scripts/session-setup.sh`` is the idempotent SessionStart provisioner that
    makes a fresh web/cloud session able to run the ``just`` command surface: it
    must ensure ``just`` itself, since a cloud image often ships the language
    runtime but not ``just`` and has no version manager to read ``.tool-versions``,
    so the first ``just ...`` call would otherwise fail. The script is optional (a
    repo may rely on the image or a version manager alone), so this stays silent
    when it is absent and unreferenced — but if the repo ships one or wires the
    hook at it, it must be correct: provision ``just`` and be wired into the hook.
    """
    script = find_session_setup_script(repo)
    wired = any("session-setup" in cmd for cmd in sessionstart_hook_commands(repo))

    if script is None:
        if wired:
            return [
                Finding(
                    "ERROR",
                    "SessionStart hook runs session-setup.sh but the script is missing",
                    "add scripts/session-setup.sh from the skill's "
                    "assets/session-setup.sh.template (it provisions `just`, then "
                    "hands off to setup-llmlint.sh)",
                )
            ]
        return []  # optional provisioner, not shipped or wired — nothing to verify

    problems: list[Finding] = []
    if not SESSION_SETUP_PROVISIONS_JUST_RE.search(script.read_text(encoding="utf-8")):
        problems.append(
            Finding(
                "ERROR",
                f"{script.name} does not provision `just` (the command-surface entry point)",
                "install `just` in session-setup.sh (e.g. `uv tool install "
                "rust-just`, which ships the binary on PyPI) so a cloud session "
                "lacking it can run the `just` recipes",
            )
        )
    if not wired:
        problems.append(
            Finding(
                "ERROR",
                "session-setup.sh exists but is not wired into a SessionStart hook",
                "point the .claude/settings.json SessionStart hook at "
                "scripts/session-setup.sh so sessions are provisioned with no "
                "manual step",
            )
        )
    return problems or [
        Finding("OK", "session-setup.sh provisions the toolchain (`just`) and is wired")
    ]


def check_llmlint(repo: Path) -> list[Finding]:
    """Require the llmlint (LLM-judge) tier to be wired — presence-only.

    llmlint runs OUTSIDE the deterministic ``just check`` gate (it drives a real
    harness, so it is non-deterministic and credentialed); this check never runs
    it. It verifies the tier is set up: a composed ``llmlint.yml`` declaring the
    rule-fragment ``plugins``, a fallback-mode ``oneharness.toml`` selecting the
    harness the pinless config drives (a primary plus a required ``claude-code``
    fallback; the template's default is codex primary), a
    ``lint-llm`` recipe to run it on demand, a ``lint-llm-diff`` recipe for the
    diff-scoped PR check, a ``lint-llm-validate`` recipe for the deterministic
    model-free gate, and a CI workflow that invokes the tier as the blocking PR
    check. Compose the config with ``compose_repo_plan.py --llmlint-config`` rather
    than hand-rolling it.
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

    # The composed llmlint.yml pins no harness, so an oneharness.toml in fallback
    # mode must select one. Validate the repo-owned config's shape: fallback mode,
    # a primary plus a secondary, and — the load-bearing part — claude-code among
    # them, so a Claude Code session (where the primary like codex is absent) still
    # has a harness to fall through to. The template's default is codex primary +
    # claude-code secondary; the primary is the repo's choice, the fallback is not.
    oh = find_oneharness_config(repo)
    if oh is None:
        problems.append(
            Finding(
                "ERROR",
                "no oneharness.toml (the harness/model selection llmlint drives)",
                "the composer emits it beside llmlint.yml — rerun "
                "compose_repo_plan.py --llmlint-config, or copy the skill's "
                "assets/oneharness.toml.template (fallback: codex primary, "
                "claude-code secondary)",
            )
        )
    else:
        harnesses = oneharness_fallback_harnesses(oh.read_text(encoding="utf-8"))
        if harnesses is None:
            problems.append(
                Finding(
                    "ERROR",
                    f"{oh.name} is not in fallback mode with a harness list",
                    'set run_mode = "fallback" and harnesses = ["codex", '
                    '"claude-code"] so the run falls through to claude-code when '
                    "codex is unavailable (see references/llmlint.md)",
                )
            )
        elif len(harnesses) < 2:
            problems.append(
                Finding(
                    "ERROR",
                    f"{oh.name} lists only one harness ({harnesses[0]}); fallback "
                    "needs a secondary to fall through to",
                    'list at least two, e.g. harnesses = ["codex", "claude-code"], '
                    "so a session without the primary still runs the tier",
                )
            )
        elif "claude-code" not in harnesses:
            # The load-bearing invariant, validated on the repo-owned config: the
            # fallback must include claude-code so a Claude Code web/cloud session —
            # where the primary (e.g. codex) is absent — still has a harness it can
            # run. The primary is the repo's choice; the claude-code fallback is not.
            problems.append(
                Finding(
                    "ERROR",
                    f"{oh.name} fallback ({', '.join(harnesses)}) has no `claude-code` "
                    "target; a Claude Code session couldn't run the tier",
                    'include claude-code in harnesses, e.g. ["codex", "claude-code"], '
                    "so a session without the primary falls through to it",
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
    # The blocking PR check runs the merge-base-scoped `lint-llm-diff`, so a PR is
    # judged on the lines it changed. Its own recipe must exist, not just `lint-llm`.
    if "lint-llm-diff" not in recipes:
        problems.append(
            Finding(
                "ERROR",
                "no `lint-llm-diff` recipe in the justfile (the diff-scoped PR check)",
                "add a `lint-llm-diff:` recipe running "
                '`llmlint --diff --diff-base "origin/main"` (a plain ref is '
                "three-dot/merge-base) so CI judges only the lines the branch "
                "changed (see llmlint.md)",
            )
        )
    # The deterministic, model-free `llmlint validate` gate (config structure +
    # `llmlint: ignore` directives + fragment version bumps) runs in CI before the
    # paid model tier, so a config/suppression/version-bump slip fails fast with no
    # harness call. Its own recipe must exist.
    if "lint-llm-validate" not in recipes:
        problems.append(
            Finding(
                "ERROR",
                "no `lint-llm-validate` recipe in the justfile (the deterministic "
                "validate gate)",
                "add a `lint-llm-validate:` recipe running `llmlint validate` so CI "
                "can run the model-free config/ignore/version-bump checks before the "
                "paid model tier (see llmlint.md)",
            )
        )

    # Install must be automated: the setup script exists AND a SessionStart hook
    # runs it, so a web/cloud session can run the tier with no manual step.
    if find_setup_llmlint_script(repo) is None:
        problems.append(
            Finding(
                "ERROR",
                "no scripts/setup-llmlint.sh (the automated llmlint toolchain install)",
                "add scripts/setup-llmlint.sh (idempotent oneharness + llmlint "
                "install) from the skill's assets/setup-llmlint.sh.template",
            )
        )
    elif not settings_sessionstart_runs_setup(repo):
        problems.append(
            Finding(
                "ERROR",
                "the llmlint setup is not wired into a SessionStart hook",
                "add a SessionStart hook to .claude/settings.json that runs "
                "scripts/session-setup.sh (which hands off to setup-llmlint.sh) — "
                "or setup-llmlint.sh directly — so sessions are ready with no "
                "manual step",
            )
        )

    if not ci_references_llmlint(repo):
        problems.append(
            Finding(
                "ERROR",
                "no CI workflow runs llmlint (the blocking PR check)",
                "add a workflow job that runs `just lint-llm-diff`, separate from "
                "the `check` gate, requiring its credential and failing fast without "
                "it (see references/ci.md and llmlint.md)",
            )
        )

    return problems or [
        Finding(
            "OK",
            "llmlint tier configured (config + oneharness fallback + recipes + "
            "install + CI)",
        )
    ]


# --- buildout llmlint tier (opt-in, non-deterministic) ---------------------
# The `--buildout` mode composes the one-time llmlint *buildout* config for this
# repo's stack and RUNS it. Unlike everything in audit(), this is non-deterministic
# (it drives a real LLM harness through the `llmlint` binary and makes network
# calls), so it is opt-in and never part of audit() / the deterministic gate. It
# folds the buildout tier — previously a manual "compose, run, resolve, delete"
# dance — into the same command that runs the baseline, so the structural one-time
# checks actually run instead of being skipped. Requires the create-repo skill's
# assets (the buildout fragments) to sit next to this script, and `llmlint` on PATH.

# The composer records the composition in AGENTS.md on a "References composed:"
# line; the buildout run reads it back to know which fragments apply. Tolerant of
# the comma form the composer emits and the `+` form used in prose.
COMPOSED_REFS_RE = re.compile(r"references\s+composed", re.IGNORECASE)
REFERENCE_TOKEN_RE = re.compile(r"[\w./-]+\.md")
# A new markdown list item (up to 3 leading spaces, a bullet, a space) — where a
# wrapped "References composed" bullet ends.
LIST_ITEM_RE = re.compile(r"^\s{0,3}[-*+]\s")

# llmlint exit codes (the `lint-llm-diff` recipe surfaces these too): 0 clean,
# 1 violations, 2 config/harness error; 127 is our sentinel for "binary not found".
LLMLINT_MISSING = 127

# Per-judge ceiling for the buildout run. Buildout rules judge whole-repo
# structure (a judge may read many files), so they need more headroom than
# llmlint's 120s default or a typical ongoing config's value. Passed as the
# `--timeout` CLI flag, which beats any merged config's `oneharness.timeout` —
# a temp-config value would lose to the committed config under llmlint's
# first-config-wins merge.
BUILDOUT_JUDGE_TIMEOUT_SECS = 900


class LlmlintResult(NamedTuple):
    """The structured outcome of one llmlint run (unpacks like a plain tuple)."""

    returncode: int
    stdout: str
    stderr: str


class LlmlintRunner(Protocol):
    """A seam for running llmlint over the composed buildout config(s).

    Takes the config paths (merged in order by llmlint's repeatable ``-c``) and
    the repo, and returns an ``LlmlintResult``. Injected in tests so the
    orchestration is exercised without a real llmlint.
    """

    def __call__(self, config_paths: list[Path], repo: Path) -> LlmlintResult: ...


def parse_composed_references(agents_text: str) -> list[str]:
    """Return the reference relpaths recorded on the AGENTS.md composition line.

    Reads the ``References composed:`` bullet — one the composer emits on a single
    line (``base.md, shapes/cli.md, languages/python.md, ci.md``) or a hand-written
    one wrapped across several lines — and extracts each ``*.md`` token,
    separator-agnostic (comma or ``+``). Collection follows the wrapped bullet's
    continuation lines (indented, non-blank) until a blank line, a new list item,
    or a heading, so a reference on the second physical line is not lost. Stray
    ``*.md`` prose tokens are harmless: only tokens with a real buildout fragment
    survive the later mapping. De-duplicated, order preserved. Empty if no such
    bullet.
    """
    lines = agents_text.splitlines()
    for i, line in enumerate(lines):
        match = COMPOSED_REFS_RE.search(line)
        if not match:
            continue
        tokens = REFERENCE_TOKEN_RE.findall(line[match.end() :])
        for nxt in lines[i + 1 :]:
            if not nxt.strip() or LIST_ITEM_RE.match(nxt) or nxt.startswith("#"):
                break
            tokens += REFERENCE_TOKEN_RE.findall(nxt)
        seen: set[str] = set()
        return [t for t in tokens if not (t in seen or seen.add(t))]
    return []


def _default_llmlint_runner(config_paths: list[Path], repo: Path) -> LlmlintResult:
    """Run ``llmlint -c CONFIG [-c CONFIG ...]``; return the structured result."""
    try:
        proc = subprocess.run(
            [
                "llmlint",
                *(arg for p in config_paths for arg in ("-c", str(p))),
                "--timeout",
                str(BUILDOUT_JUDGE_TIMEOUT_SECS),
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return LlmlintResult(LLMLINT_MISSING, "", "llmlint binary not found on PATH")
    return LlmlintResult(proc.returncode, proc.stdout, proc.stderr)


def _render_buildout_config(fragments: list[Path]) -> str:
    """A throwaway llmlint config wiring the local buildout fragments in as plugins.

    Uses absolute local paths (not hosted ``@version`` URLs) so it reflects the
    in-tree fragments exactly and needs no network to resolve them.
    """
    lines = [
        "# TEMPORARY buildout config generated by check_repo_baseline --buildout.",
        "# One-time STRUCTURAL checks; not committed, not the ongoing PR check.",
        # No top-level `version:`: this config is never consumed as a plugin, so a
        # version would be meaningless (see render_llmlint_config in compose_repo_plan).
        "files:",
        "  exclude:",
        '    - "**/.git/**"',
        "rationales: true",
        # No `agents` block: the harness/model come from the repo's oneharness.toml
        # (fallback mode), same as the ongoing llmlint.yml — so a Claude Code session
        # falls through to claude-code. Pinning one here would beat that fallback.
        "plugins:",
    ]
    lines += [f'  - "{frag.resolve().as_posix()}"' for frag in fragments]
    return "\n".join(lines) + "\n"


def _trim(text: str, *, max_lines: int = 12, max_chars: int = 1200) -> str:
    """Keep the tail of llmlint output — where the findings summary sits — bounded."""
    text = text.strip()
    if not text:
        return ""
    tail = "\n".join(text.splitlines()[-max_lines:])
    return tail[-max_chars:]


def _buildout_findings(rc: int, out: str, err: str, n: int) -> list[Finding]:
    match rc:
        case 0:
            return [Finding("OK", f"buildout llmlint passed ({n} fragment(s))")]
        case 127:  # LLMLINT_MISSING sentinel from _default_llmlint_runner
            return [
                Finding(
                    "ERROR",
                    "llmlint is not installed, so the buildout tier could not run",
                    "install it (`just setup-llmlint` / scripts/setup-llmlint.sh), "
                    "or drop --buildout to run only the deterministic checks",
                )
            ]
        case 2:
            # A concrete next action first; the raw llmlint stderr is appended as
            # context, never used as the whole fix.
            fix = (
                "confirm the harness is authenticated (`llmlint doctor`) and the "
                "composition in AGENTS.md is valid, then re-run"
            )
            detail = _trim(err or out)
            return [
                Finding(
                    "ERROR",
                    "buildout llmlint could not run (config or harness error)",
                    fix + (f"; llmlint reported:\n{detail}" if detail else ""),
                )
            ]
        case _:  # 1 (violations), or any other non-zero exit
            detail = _trim(out or err)
            return [
                Finding(
                    "ERROR",
                    "buildout llmlint found structural issue(s) in the repo setup",
                    "resolve the buildout findings below, then re-run"
                    + (f":\n{detail}" if detail else ""),
                )
            ]


def run_buildout(
    repo: Path, skill_dir: Path, *, llmlint_runner: LlmlintRunner | None = None
) -> list[Finding]:
    """Compose the buildout config for this repo's stack and run llmlint against it.

    Reads the composition from AGENTS.md, maps each reference to its buildout
    fragment (the ones that have one), writes a throwaway config wiring those
    fragments in as local plugins, runs ``llmlint`` over the repo, and maps the
    exit code to a Finding. The repo's committed (ongoing) config is passed
    alongside the temp one — see the comment at the call site. The temp config
    is always removed. Non-deterministic and credentialed — callers reach it
    only via ``--buildout``.
    """
    runner = llmlint_runner or _default_llmlint_runner

    buildout_dir = skill_dir / "assets" / "llmlint" / "buildout"
    if not buildout_dir.is_dir():
        return [
            Finding(
                "ERROR",
                "buildout fragments not found next to the checker",
                "run check_repo_baseline.py from the create-repo skill checkout so "
                "assets/llmlint/buildout/ is available (or drop --buildout)",
            )
        ]

    agents = repo / "AGENTS.md"
    refs = (
        parse_composed_references(agents.read_text(encoding="utf-8"))
        if agents.is_file()
        else []
    )
    if not refs:
        return [
            Finding(
                "ERROR",
                "cannot determine the stack for the buildout run (no 'References "
                "composed' line in AGENTS.md)",
                "record the composition in AGENTS.md (the composer writes a "
                "'References composed:' line) so the buildout tier knows which "
                "fragments apply",
            )
        ]

    # base.md is always-applied (the universal invariants), so its buildout tier
    # runs regardless of whether the composition line happens to spell it out.
    if "base.md" not in refs:
        refs = ["base.md", *refs]

    # Map each recorded reference token to its buildout fragment, validating that
    # the token stays *inside* the buildout tree. AGENTS.md is in-repo, but the
    # token pattern admits `.`/`/`, so a stray or hostile `../…` token must not
    # escape buildout/ and pull an arbitrary file in as a plugin.
    buildout_root = buildout_dir.resolve()
    fragments: list[Path] = []
    seen: set[str] = set()
    for ref in refs:
        stem = ref[:-3] if ref.endswith(".md") else ref
        if stem in seen:
            continue
        frag = (buildout_dir / f"{stem}.llmlint.yml").resolve()
        if not frag.is_relative_to(buildout_root):
            continue  # token escaped the buildout tree — ignore it
        if frag.is_file():
            seen.add(stem)
            fragments.append(frag)
    if not fragments:
        return [Finding("OK", "no buildout rules apply to this stack")]

    # llmlint's preflight validates every inline `llmlint: ignore` directive
    # against the *configured* rule set before any rule runs. Source files
    # legitimately carry directives naming ongoing rules (from the committed
    # llmlint.yml); run the buildout config in isolation and those directives
    # name "unknown rules" — a hard error (exit 2) that kills the run at
    # preflight. Pass the committed config too so the merged rule set makes the
    # directives resolvable. It goes FIRST: llmlint's first config wins on
    # conflicting settings, so the repo's own harness/timeout choices beat the
    # temp config's defaults, which only fill what the repo leaves unset. The
    # merge also runs the ongoing rules alongside the buildout ones — acceptable:
    # the creation flow requires one full ongoing run anyway (see llmlint.md).
    ongoing = find_llmlint_config(repo)

    fd, tmp_name = tempfile.mkstemp(suffix=".llmlint.yml", prefix="buildout-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_render_buildout_config(fragments))
        rc, out, err = runner([ongoing, tmp] if ongoing else [tmp], repo)
    finally:
        tmp.unlink(missing_ok=True)

    return _buildout_findings(rc, out, err, len(fragments))


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
    findings += check_notignored(repo)
    findings += check_session_setup(repo)
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
    parser.add_argument(
        "--buildout",
        action="store_true",
        help="ALSO compose and RUN the one-time llmlint buildout tier (structural "
        "one-time checks) for this repo's stack. Non-deterministic and credentialed "
        "(drives the llmlint harness), so it is opt-in and NOT part of the "
        "deterministic checks; needs `llmlint` on PATH and the skill's assets.",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"ERROR not a directory: {repo}", file=sys.stderr)
        return 2

    findings = audit(repo)
    if args.buildout:
        skill_dir = Path(__file__).resolve().parent.parent
        findings += run_buildout(repo, skill_dir)
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
