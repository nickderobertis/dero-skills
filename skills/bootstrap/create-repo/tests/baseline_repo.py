"""The repo builder the baseline checker's suites share: a conformant repository on disk.

Its own module so a suite that only needs the builder, such as the instrumented coverage
journeys, does not take the baseline checker's whole test module as an input.
"""

from __future__ import annotations

from pathlib import Path


FULL_JUSTFILE = """\
# a comment
default:
    @just --list

bootstrap:
    @echo hi

check tier="affected":
    bunx nx affected -t lint typecheck test build --base=origin/main

test:
    # each project's own `test` target enforces --cov-fail-under=95
    bunx nx affected -t test --base=origin/main

test-e2e:
    bunx nx run-many -t test -p "tag:type:e2e"

lint:
    bunx nx affected -t lint --base=origin/main

format:
    @echo f

upgrade:
    @just check

lint-llm:
    @echo lint-llm

lint-llm-diff:
    @echo lint-llm-diff

lint-llm-validate:
    @echo lint-llm-validate
"""

# The same command surface with the coverage bar stripped out: no flag, no
# threshold anywhere, so only a config file or an AGENTS.md statement can make
# the coverage decision deliberate.
NO_COVERAGE_JUSTFILE = FULL_JUSTFILE.replace(
    "    # each project's own `test` target enforces --cov-fail-under=95\n", ""
)
assert "cov" not in NO_COVERAGE_JUSTFILE

# A .claude/settings.json that wires the llmlint installer into a SessionStart
# hook (the automated-install invariant), with a narrow allowlist.
CONFORMANT_SETTINGS = (
    '{"hooks": {"SessionStart": [{"matcher": "startup|resume", "hooks": '
    '[{"type": "command", "command": "bash scripts/setup-llmlint.sh"}]}]}, '
    '"permissions": {"allow": []}}'
)

# A workflow that invokes the gate AND the llmlint tier (separate jobs), as the
# CI and llmlint invariants require.
GATE_WORKFLOW = (
    "name: ci\njobs:\n"
    "  check:\n    steps:\n      - run: just check\n"
    "  llmlint:\n    steps:\n      - run: just lint-llm-diff\n"
)

# The suppressions review comment: the notignored action on pull requests, with
# the comment permission, a full-history checkout, and the fork-PR skip guard the
# suppressions invariant requires. A separate workflow, as the asset ships it.
NOTIGNORED_WORKFLOW = """\
name: notignored
on:
  pull_request:
permissions:
  contents: read
  pull-requests: write
jobs:
  suppressions:
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: nickderobertis/notignored@v0
"""

# A composed llmlint.yml: declares plugins (the rule fragments), as the llmlint
# invariant requires, and adopts the onebudgetspec rules every repo's budgets
# setup requires.
ONEBUDGETSPEC_PLUGIN = (
    "https://raw.githubusercontent.com/nickderobertis/dero-skills/main/skills/"
    "bootstrap/create-repo/assets/llmlint/tools/onebudgetspec.llmlint.yml@1"
)
LLMLINT_CONFIG = (
    "version: 1\nplugins:\n"
    '  - "https://example.com/assets/llmlint/base.llmlint.yml@1"\n'
    f'  - "{ONEBUDGETSPEC_PLUGIN}"\n'
)

# The budgets setup's pin: package.json pins the release exactly and what
# `bun install` records for it (trimmed to its entry).
BUDGETS_PACKAGE_JSON = (
    '{"private": true, "devDependencies": {"@onebudgetspec/cli": "0.2.0"}}\n'
)
BUN_LOCK = (
    '{\n  "lockfileVersion": 1,\n  "packages": {\n'
    '    "@onebudgetspec/cli": ["@onebudgetspec/cli@0.2.0", "", {}, "sha512-x"],\n'
    "  }\n}\n"
)

# A fallback-mode oneharness.toml: the harness/model selection the pinless
# llmlint.yml relies on, as the oneharness invariant requires.
ONEHARNESS_CONFIG = (
    'run_mode = "fallback"\n'
    'harnesses = ["codex", "claude-code"]\n'
    '\n[harness.codex]\nmodel = "gpt-5.5"\n'
    '\n[harness.claude-code]\nmodel = "claude-opus-4-8"\n'
    'env = { IS_SANDBOX = "1" }\n'
)

# A conformant PR template: names both the What and Why sections the PR-template
# invariant requires (Additional info is optional).
CONFORMANT_PR_TEMPLATE = """\
## What

High-level behavior change.

## Why

The driver and impact.
"""

# A conformant AGENTS.md: it records the reference composition (a filled-in
# "Stack and composition" section), which the composition invariant requires.
CONFORMANT_AGENTS = """\
# AGENTS

## Stack and composition

- Product shape: cli
- Language(s): python
- References composed: shapes/cli.md + languages/python.md + ci.md
- Excluded: none worth noting.
"""


def make_repo(
    tmp_path: Path,
    *,
    agents=True,
    composition=True,
    claude="symlink",
    settings=True,
    justfile=FULL_JUSTFILE,
    project_graph=True,
    ci=True,
    notignored=True,
    pr_template=True,
    llmlint=True,
    oneharness=True,
    budgets=True,
) -> Path:
    """Build a repo fixture. With no overrides it is fully conformant.

    ``claude`` is "symlink", "file", or None; ``settings`` is True, False, or a
    raw string written verbatim to .claude/settings.json (to test bad JSON).
    ``ci`` is True (a gate-running workflow), False (none), or a raw string
    written verbatim to .github/workflows/ci.yml. ``notignored`` is True (the
    conformant suppressions-comment workflow), False (none), or a raw string
    written verbatim to .github/workflows/notignored.yml. ``composition`` is True (the
    conformant AGENTS.md with a filled composition section), False (a bare
    AGENTS.md with no such section), or a raw string written verbatim as
    AGENTS.md. ``pr_template`` is True (the conformant .github template), False
    (none), or a raw string written verbatim to .github/pull_request_template.md.
    ``project_graph`` writes the orchestrator's nx.json (the mandatory project
    graph). ``llmlint`` is True (a composed llmlint.yml with plugins), False
    (none), or a raw string written verbatim to llmlint.yml. ``oneharness`` is True (a
    fallback-mode oneharness.toml), False (none), or a raw string written verbatim
    to oneharness.toml. ``budgets`` pins onebudgetspec in package.json and
    bun.lock, the budgets setup's pin (the rules are adopted by LLMLINT_CONFIG).
    """
    repo = tmp_path
    if agents:
        if composition is True:
            agents_body = CONFORMANT_AGENTS
        elif composition is False:
            agents_body = "# AGENTS\n"
        else:
            agents_body = composition
        (repo / "AGENTS.md").write_text(agents_body, encoding="utf-8")
    if claude == "symlink":
        (repo / "CLAUDE.md").symlink_to("AGENTS.md")
    elif claude == "file":
        (repo / "CLAUDE.md").write_text("# not a symlink\n", encoding="utf-8")
    if settings is not False:
        (repo / ".claude").mkdir(exist_ok=True)
        body = settings if isinstance(settings, str) else CONFORMANT_SETTINGS
        (repo / ".claude" / "settings.json").write_text(body, encoding="utf-8")
    if justfile is not None:
        (repo / "justfile").write_text(justfile, encoding="utf-8")
    if project_graph:
        (repo / "nx.json").write_text(
            '{"targetDefaults": {"test": {"dependsOn": ["^build"]}}}\n',
            encoding="utf-8",
        )
    if ci is not False:
        wf = repo / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        body = ci if isinstance(ci, str) else GATE_WORKFLOW
        (wf / "ci.yml").write_text(body, encoding="utf-8")
    if notignored is not False:
        wf = repo / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        body = notignored if isinstance(notignored, str) else NOTIGNORED_WORKFLOW
        (wf / "notignored.yml").write_text(body, encoding="utf-8")
    if pr_template is not False:
        gh = repo / ".github"
        gh.mkdir(exist_ok=True)
        body = pr_template if isinstance(pr_template, str) else CONFORMANT_PR_TEMPLATE
        (gh / "pull_request_template.md").write_text(body, encoding="utf-8")
    if llmlint is not False:
        body = llmlint if isinstance(llmlint, str) else LLMLINT_CONFIG
        (repo / "llmlint.yml").write_text(body, encoding="utf-8")
        # The automated-install half of the llmlint tier: the setup script the
        # SessionStart hook (in CONFORMANT_SETTINGS) runs.
        scripts = repo / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "setup-llmlint.sh").write_text(
            "#!/usr/bin/env bash\n# idempotent llmlint install\n", encoding="utf-8"
        )
    if oneharness is not False:
        body = oneharness if isinstance(oneharness, str) else ONEHARNESS_CONFIG
        (repo / "oneharness.toml").write_text(body, encoding="utf-8")
    if budgets:
        (repo / "package.json").write_text(BUDGETS_PACKAGE_JSON, encoding="utf-8")
        (repo / "bun.lock").write_text(BUN_LOCK, encoding="utf-8")
    return repo
