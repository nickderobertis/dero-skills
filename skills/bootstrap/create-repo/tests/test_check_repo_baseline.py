"""Tests for the create-repo baseline checker.

Loads the PEP 723 script as a module so its functions can be exercised
directly. The module is registered in sys.modules before exec so the
``@dataclass`` in it can resolve ``__module__`` under all Python versions.

The end-to-end layer — running the script as a real ``uv run --script``
subprocess with a stubbed external ``llmlint`` — lives in
``e2e/test_check_repo_baseline_e2e.py``, which imports the repo-builder helpers
(``make_repo``/``_buildout_repo``) from this module.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "check_repo_baseline.py"

spec = importlib.util.spec_from_file_location("check_repo_baseline", SCRIPT)
assert spec is not None and spec.loader is not None
crb = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = crb
spec.loader.exec_module(crb)


# --- helpers ---------------------------------------------------------------

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
# invariant requires.
LLMLINT_CONFIG = (
    "version: 1\nplugins:\n"
    '  - "https://example.com/assets/llmlint/base.llmlint.yml@1"\n'
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
    to oneharness.toml.
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
    return repo


def levels(findings, level):
    return [f.message for f in findings if f.level == level]


# --- parse_just_recipes ----------------------------------------------------


def test_buildout_config_has_no_top_level_version():
    # The generated buildout config is never consumed as a plugin, so a top-level
    # `version:` would be inert (see render_llmlint_config in compose_repo_plan).
    # Guard against a regression that reintroduces it.
    frag = Path("/tmp/x/base.llmlint.yml")
    cfg = crb._render_buildout_config([frag])
    top_keys = {
        m.group(1)
        for line in cfg.splitlines()
        if (m := re.match(r"^([A-Za-z_][\w-]*):", line))
    }
    assert "version" not in top_keys, (
        f"buildout config carries a top-level version; keys={sorted(top_keys)}"
    )
    assert "plugins" in top_keys  # didn't accidentally gut the config


def test_parse_recipes_extracts_names_and_ignores_assignments_and_bodies():
    recipes = crb.parse_just_recipes(FULL_JUSTFILE)
    assert {
        "bootstrap",
        "check",
        "test",
        "lint",
        "format",
        "upgrade",
        "default",
    } <= recipes


def test_parse_recipes_recognizes_defaulted_parameters():
    # A recipe with a defaulted parameter carries an '=' before its ':'. The '='
    # must not hide the recipe (regression: `lint-llm-diff base="origin/main":`).
    text = 'lint-llm-diff base="origin/main":\n    ./scripts/x.sh {{base}}\n'
    recipes = crb.parse_just_recipes(text)
    assert recipes == {"lint-llm-diff"}
    details = crb.parse_just_recipe_details(text)
    assert "lint-llm-diff" in details


def test_parse_recipes_excludes_variable_assignments():
    text = (
        "version := '1.0'\nexport FOO := 'bar'\nbuild target:\n    @echo {{target}}\n"
    )
    recipes = crb.parse_just_recipes(text)
    assert recipes == {"build"}
    assert "version" not in recipes
    assert "export" not in recipes


# --- audit -----------------------------------------------------------------


def test_conformant_repo_has_no_errors(tmp_path):
    findings = crb.audit(make_repo(tmp_path))
    assert not crb.has_errors(findings), levels(findings, "ERROR")


def test_missing_agents_md_is_error(tmp_path):
    findings = crb.audit(make_repo(tmp_path, agents=False))
    assert crb.has_errors(findings)
    assert any("AGENTS.md" in m for m in levels(findings, "ERROR"))


def test_missing_claude_symlink_is_error(tmp_path):
    findings = crb.audit(make_repo(tmp_path, claude=None))
    assert crb.has_errors(findings)
    assert any("CLAUDE.md" in m for m in levels(findings, "ERROR"))


def test_claude_regular_file_is_error(tmp_path):
    findings = crb.audit(make_repo(tmp_path, claude="file"))
    assert crb.has_errors(findings)
    assert any("symlink" in m for m in levels(findings, "ERROR"))


def test_missing_claude_settings_is_error(tmp_path):
    findings = crb.audit(make_repo(tmp_path, settings=False))
    assert crb.has_errors(findings)
    assert any("settings.json" in m for m in levels(findings, "ERROR"))


def test_invalid_claude_settings_is_error(tmp_path):
    findings = crb.audit(make_repo(tmp_path, settings="{ not json"))
    assert crb.has_errors(findings)
    assert any("not valid JSON" in m for m in levels(findings, "ERROR"))


def test_missing_recipe_is_error(tmp_path):
    partial = "bootstrap:\n    @echo hi\ncheck:\n    @echo gate\n"
    findings = crb.audit(make_repo(tmp_path, justfile=partial))
    assert crb.has_errors(findings)
    msgs = levels(findings, "ERROR")
    assert any("missing required recipe" in m for m in msgs)
    assert any("upgrade" in m for m in msgs)


def test_missing_justfile_is_error(tmp_path):
    findings = crb.audit(make_repo(tmp_path, justfile=None))
    assert crb.has_errors(findings)
    assert any("justfile" in m for m in levels(findings, "ERROR"))


def test_missing_ci_is_error(tmp_path):
    findings = crb.audit(make_repo(tmp_path, ci=False))
    assert crb.has_errors(findings)
    assert any("workflow" in m for m in levels(findings, "ERROR"))


def test_ci_without_gate_is_error(tmp_path):
    # A workflow file exists but never runs `just check`: presence is not proof.
    findings = crb.audit(make_repo(tmp_path, ci="name: ci\njobs:\n  noop: {}\n"))
    assert crb.has_errors(findings)
    assert any("gate" in m for m in levels(findings, "ERROR"))


# --- pull-request template -------------------------------------------------


def test_missing_pr_template_is_error(tmp_path):
    findings = crb.audit(make_repo(tmp_path, pr_template=False))
    assert crb.has_errors(findings)
    assert any("pull-request template" in m for m in levels(findings, "ERROR"))


def test_pr_template_without_what_why_is_error(tmp_path):
    # A present-but-empty template proves nothing: it must name What and Why.
    findings = crb.audit(make_repo(tmp_path, pr_template="# Thanks for the PR!\n"))
    assert crb.has_errors(findings)
    msgs = levels(findings, "ERROR")
    assert any("What" in m and "Why" in m for m in msgs)


def test_pr_template_missing_only_why_is_error(tmp_path):
    # Naming What but not Why still fails — both required sections must be present.
    findings = crb.audit(make_repo(tmp_path, pr_template="## What\n\nThe change.\n"))
    assert crb.has_errors(findings)
    assert any("Why" in m for m in levels(findings, "ERROR"))


def test_pr_template_in_repo_root_is_accepted(tmp_path):
    # GitHub also renders a template from the repo root, not only .github/.
    repo = make_repo(tmp_path, pr_template=False)
    (repo / "pull_request_template.md").write_text(
        CONFORMANT_PR_TEMPLATE, encoding="utf-8"
    )
    findings = crb.audit(repo)
    assert not any("pull-request template" in m for m in levels(findings, "ERROR"))


def test_pr_template_in_docs_is_accepted(tmp_path):
    # docs/ is the third location GitHub recognizes.
    repo = make_repo(tmp_path, pr_template=False)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "PULL_REQUEST_TEMPLATE.md").write_text(
        CONFORMANT_PR_TEMPLATE, encoding="utf-8"
    )
    findings = crb.audit(repo)
    assert not any("pull-request template" in m for m in levels(findings, "ERROR"))


def test_pr_template_directory_form_is_accepted(tmp_path):
    # A PULL_REQUEST_TEMPLATE/ directory (multi-template form) counts too.
    repo = make_repo(tmp_path, pr_template=False)
    tdir = repo / ".github" / "PULL_REQUEST_TEMPLATE"
    tdir.mkdir(parents=True)
    (tdir / "default.md").write_text(CONFORMANT_PR_TEMPLATE, encoding="utf-8")
    findings = crb.audit(repo)
    assert not any("pull-request template" in m for m in levels(findings, "ERROR"))


def test_placeholder_recipe_body_is_error(tmp_path):
    # The unfilled justfile template: required recipes still hold TODO bodies.
    justfile = (
        "bootstrap:\n    @echo hi\n"
        "check: lint test\n    @echo gate\n"
        'test:\n    @echo "TODO: run tests"\n'
        "test-e2e:\n    @echo e2e\n"
        "lint:\n    @echo l\n"
        "format:\n    @echo f\n"
        "upgrade:\n    @just check\n"
    )
    findings = crb.audit(make_repo(tmp_path, justfile=justfile))
    assert crb.has_errors(findings)
    msgs = levels(findings, "ERROR")
    assert any("placeholder" in m for m in msgs)
    assert any("test" in m for m in msgs)


def test_check_not_running_test_is_error(tmp_path):
    # `test` exists but `check` neither depends on nor invokes it.
    justfile = (
        "bootstrap:\n    @echo hi\n"
        "check: lint\n    @echo gate\n"
        "test:\n    @echo t\n"
        "test-e2e:\n    @echo e2e\n"
        "lint:\n    @echo l\n"
        "format:\n    @echo f\n"
        "upgrade:\n    @just check\n"
    )
    findings = crb.audit(make_repo(tmp_path, justfile=justfile))
    assert crb.has_errors(findings)
    assert any("does not run `test`" in m for m in levels(findings, "ERROR"))


def test_check_running_test_in_body_is_ok(tmp_path):
    # `check` may invoke the suite in its body instead of as a dependency.
    justfile = (
        "bootstrap:\n    @echo hi\n"
        "check: lint\n    @just test\n"
        "test:\n    @echo t\n"
        "test-e2e:\n    @echo e2e\n"
        "lint:\n    @echo l\n"
        "format:\n    @echo f\n"
        "upgrade:\n    @just check\n"
    )
    findings = crb.audit(make_repo(tmp_path, justfile=justfile))
    assert not any("does not run `test`" in m for m in levels(findings, "ERROR"))


def test_missing_e2e_signal_is_error(tmp_path):
    # No e2e recipe, no e2e/ dir, and AGENTS.md never mentions e2e.
    no_e2e = FULL_JUSTFILE.replace("check: lint test test-e2e", "check: lint test")
    no_e2e = "\n".join(
        block for block in no_e2e.split("\n\n") if not block.startswith("test-e2e:")
    )
    findings = crb.audit(make_repo(tmp_path, justfile=no_e2e))
    assert crb.has_errors(findings)
    assert any("e2e" in m for m in levels(findings, "ERROR"))


def test_e2e_satisfied_by_agents_md_note(tmp_path):
    # An explicit documented opt-out in AGENTS.md counts as a deliberate decision.
    no_e2e = FULL_JUSTFILE.replace("check: lint test test-e2e", "check: lint test")
    no_e2e = "\n".join(
        block for block in no_e2e.split("\n\n") if not block.startswith("test-e2e:")
    )
    repo = make_repo(tmp_path, justfile=no_e2e)
    (repo / "AGENTS.md").write_text(
        "# AGENTS\n\nNo end-to-end tests: this library has no user-facing journey.\n",
        encoding="utf-8",
    )
    findings = crb.audit(repo)
    assert not any("e2e signal" in m for m in levels(findings, "ERROR"))


def test_e2e_satisfied_by_directory(tmp_path):
    no_e2e = FULL_JUSTFILE.replace("check: lint test test-e2e", "check: lint test")
    no_e2e = "\n".join(
        block for block in no_e2e.split("\n\n") if not block.startswith("test-e2e:")
    )
    repo = make_repo(tmp_path, justfile=no_e2e)
    (repo / "tests" / "e2e").mkdir(parents=True)
    findings = crb.audit(repo)
    assert not any("e2e signal" in m for m in levels(findings, "ERROR"))


# --- e2e realism (advisory) ------------------------------------------------


def test_mock_heavy_e2e_warns_but_does_not_error(tmp_path):
    # An e2e-tier test that mocks the boundary it should exercise: advisory WARN.
    repo = make_repo(tmp_path)
    e2e = repo / "tests" / "e2e"
    e2e.mkdir(parents=True)
    (e2e / "test_journey.py").write_text(
        "from unittest.mock import MagicMock\n\ndef test_runs():\n    pass\n",
        encoding="utf-8",
    )
    findings = crb.audit(repo)
    assert not crb.has_errors(findings), levels(findings, "ERROR")
    assert any("mocking library" in m for m in levels(findings, "WARN"))


def test_e2e_named_file_outside_dir_is_scanned(tmp_path):
    # A file merely named *e2e* counts as the e2e tier even without an e2e/ dir.
    repo = make_repo(tmp_path)
    tests = repo / "tests"
    tests.mkdir()
    (tests / "cli_e2e.test.ts").write_text(
        "import { vi } from 'vitest'\nvi.mock('node:fs')\n", encoding="utf-8"
    )
    findings = crb.audit(repo)
    assert any("mocking library" in m for m in levels(findings, "WARN"))


def test_real_e2e_emits_no_mock_warning(tmp_path):
    # An e2e test that drives the real boundary (subprocess + temp files) is clean.
    repo = make_repo(tmp_path)
    e2e = repo / "tests" / "e2e"
    e2e.mkdir(parents=True)
    (e2e / "test_journey.py").write_text(
        "import subprocess\n\n"
        "def test_runs(tmp_path):\n"
        "    out = subprocess.run(['mytool', '--version'], capture_output=True)\n"
        "    assert out.returncode == 0\n",
        encoding="utf-8",
    )
    findings = crb.audit(repo)
    assert not any("mocking library" in m for m in levels(findings, "WARN"))


def test_mock_in_unit_test_is_not_flagged(tmp_path):
    # Mocking outside the e2e tier (a plain unit test) is fine — not scanned.
    repo = make_repo(tmp_path)
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_unit.py").write_text(
        "from unittest.mock import patch\n\ndef test_x():\n    pass\n",
        encoding="utf-8",
    )
    findings = crb.audit(repo)
    assert not any("mocking library" in m for m in levels(findings, "WARN"))


# --- project graph ---------------------------------------------------------

# A command surface that runs its targets itself instead of through the graph.
NO_ORCHESTRATOR_JUSTFILE = """\
bootstrap:
    @echo hi

check: lint test test-e2e
    @echo gate

test:
    @echo "pytest --cov --cov-fail-under=95"

test-e2e:
    @echo e2e

lint:
    @echo l

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


def project_graph_findings(findings):
    """Every finding the project-graph check contributed (OK ones included)."""
    return [f for f in findings if "project graph" in f.message]


def test_missing_project_graph_warns_and_never_fails(tmp_path):
    # A repo with no orchestrator at all: one advisory finding, no error, and the
    # exit-code contract is unchanged (WARN so an already-bootstrapped repo is
    # guided onto the path rather than broken by it).
    findings = crb.audit(
        make_repo(tmp_path, project_graph=False, justfile=NO_ORCHESTRATOR_JUSTFILE)
    )
    reported = project_graph_findings(findings)
    assert len(reported) == 1, [f.message for f in reported]
    assert reported[0].level == "WARN"
    assert not crb.has_errors(findings), levels(findings, "ERROR")


def test_project_graph_warning_is_actionable_on_its_own(tmp_path):
    # The message has to teach a reader who has never seen this skill: the
    # standard (a monorepo orchestrator), what to do about it (modularize tests
    # and code into projects), and where it is written down.
    findings = crb.audit(
        make_repo(tmp_path, project_graph=False, justfile=NO_ORCHESTRATOR_JUSTFILE)
    )
    message = project_graph_findings(findings)[0].message.lower()
    assert "monorepo orchestrator" in message
    assert "modularize" in message
    assert "tests" in message and "code" in message
    assert "references/project-graph.md" in message


def test_graph_present_but_command_surface_bypasses_it_warns(tmp_path):
    # nx.json alone is not the invariant: the root recipes must actually run
    # targets through the graph, or nothing is ever skipped.
    findings = crb.audit(make_repo(tmp_path, justfile=NO_ORCHESTRATOR_JUSTFILE))
    reported = project_graph_findings(findings)
    assert len(reported) == 1, [f.message for f in reported]
    assert reported[0].level == "WARN"
    assert "bypass" in reported[0].message
    # The fix offers only the half that is missing: nx.json is already there.
    assert "add nx.json" not in reported[0].fix
    assert "nx affected" in reported[0].fix
    assert not crb.has_errors(findings), levels(findings, "ERROR")


def test_project_graph_with_delegating_recipes_does_not_warn(tmp_path):
    # The conformant fixture: nx.json plus gate recipes that delegate to it.
    findings = crb.audit(make_repo(tmp_path))
    assert not any(f.level == "WARN" for f in project_graph_findings(findings)), levels(
        findings, "WARN"
    )


def test_delegation_through_a_dependency_counts(tmp_path):
    # `check` delegates via the recipe it depends on; looking only at its own
    # body would miss the command doing the work.
    justfile = (
        "bootstrap:\n    @echo hi\n"
        "check: lint test\n    @echo gate\n"
        "test:\n    bunx nx affected -t test --base=origin/main  # --cov-fail-under=95\n"
        "test-e2e:\n    @echo e2e\n"
        "lint:\n    @echo l\n"
        "format:\n    @echo f\n"
        "upgrade:\n    @just check\n"
    )
    findings = crb.audit(make_repo(tmp_path, justfile=justfile))
    assert not any(f.level == "WARN" for f in project_graph_findings(findings))


def test_check_that_delegates_the_test_target_runs_test(tmp_path):
    # A delegating gate names no `test` dependency and never says `just test`; it
    # hands the `test` target to the orchestrator, and that is running the suite.
    justfile = (
        "bootstrap:\n    @echo hi\n"
        'check tier="affected":\n'
        "    bunx nx affected -t lint test build --base=origin/main\n"
        "test:\n    bunx nx affected -t test  # --cov-fail-under=95\n"
        "test-e2e:\n    @echo e2e\n"
        "lint:\n    @echo l\n"
        "format:\n    @echo f\n"
        "upgrade:\n    @just check\n"
    )
    findings = crb.audit(make_repo(tmp_path, justfile=justfile))
    assert not any("does not run `test`" in m for m in levels(findings, "ERROR"))


def test_check_delegating_without_the_test_target_still_errors(tmp_path):
    # Delegation is not a free pass: a fan-out that omits `test` leaves the suite
    # out of the gate exactly as a hand-rolled one would.
    justfile = (
        "bootstrap:\n    @echo hi\n"
        'check tier="affected":\n'
        "    bunx nx affected -t lint build --base=origin/main\n"
        "test:\n    bunx nx affected -t test  # --cov-fail-under=95\n"
        "test-e2e:\n    @echo e2e\n"
        "lint:\n    @echo l\n"
        "format:\n    @echo f\n"
        "upgrade:\n    @just check\n"
    )
    findings = crb.audit(make_repo(tmp_path, justfile=justfile))
    assert any("does not run `test`" in m for m in levels(findings, "ERROR"))


def test_orchestrator_targets_stop_at_the_next_flag():
    assert crb.orchestrator_targets(
        "bunx nx affected -t lint test build --base=origin/main --parallel=3"
    ) == {"lint", "test", "build"}
    assert crb.orchestrator_targets("bunx nx run-many -t test -p tag:type:e2e") == {
        "test"
    }
    assert crb.orchestrator_targets("uv run pytest") == set()


# --- coverage --------------------------------------------------------------


def test_missing_coverage_signal_is_error(tmp_path):
    # A justfile with no coverage flag, no coverage config, and an AGENTS.md
    # that never mentions coverage: dropping the default gate must be deliberate.
    findings = crb.audit(make_repo(tmp_path, justfile=NO_COVERAGE_JUSTFILE))
    assert crb.has_errors(findings)
    assert any("coverage signal" in m for m in levels(findings, "ERROR"))


def test_coverage_satisfied_by_justfile_flag(tmp_path):
    # The conformant fixture names the coverage bar in its `test` recipe.
    findings = crb.audit(make_repo(tmp_path))
    assert not any("coverage signal" in m for m in levels(findings, "ERROR"))


def test_coverage_satisfied_by_config_file(tmp_path):
    # A threshold declared in a config file counts even without a justfile flag.
    repo = make_repo(tmp_path, justfile=NO_COVERAGE_JUSTFILE)
    (repo / "pyproject.toml").write_text(
        "[tool.coverage.report]\nfail_under = 95\n", encoding="utf-8"
    )
    findings = crb.audit(repo)
    assert not any("coverage signal" in m for m in levels(findings, "ERROR"))


def test_coverage_satisfied_by_agents_md_note(tmp_path):
    # An explicit documented decision in AGENTS.md counts as deliberate.
    agents = (
        CONFORMANT_AGENTS
        + "\n## Excluded\n\nNo coverage gate: this is a tiny stdlib-only repo.\n"
    )
    findings = crb.audit(
        make_repo(tmp_path, justfile=NO_COVERAGE_JUSTFILE, composition=agents)
    )
    assert not any("coverage signal" in m for m in levels(findings, "ERROR"))


# --- composition -----------------------------------------------------------


def test_missing_composition_section_is_error(tmp_path):
    # AGENTS.md exists but never records how the repo was composed.
    findings = crb.audit(make_repo(tmp_path, composition=False))
    assert crb.has_errors(findings)
    assert any("composed" in m for m in levels(findings, "ERROR"))


def test_placeholder_composition_section_is_error(tmp_path):
    # The section was copied from the template but never filled in.
    agents = (
        "# AGENTS\n\n## Stack and composition\n\n"
        "- Product shape: <cli / web-app / library>\n"
    )
    findings = crb.audit(make_repo(tmp_path, composition=agents))
    assert crb.has_errors(findings)
    assert any("placeholder" in m for m in levels(findings, "ERROR"))


def test_empty_composition_section_is_error(tmp_path):
    # A heading with no body beneath it does not record anything.
    agents = "# AGENTS\n\n## Stack and composition\n\n## Next thing\n\nbody\n"
    findings = crb.audit(make_repo(tmp_path, composition=agents))
    assert crb.has_errors(findings)
    assert any("empty" in m for m in levels(findings, "ERROR"))


def test_composition_satisfied_by_stack_heading(tmp_path):
    # Any filled heading naming the stack/composition counts; "## Tech stack" too.
    agents = "# AGENTS\n\n## Tech stack\n\nA Rust CLI: shapes/cli.md + languages/rust.md + ci.md.\n"
    findings = crb.audit(make_repo(tmp_path, composition=agents))
    assert not any(
        "composed" in m or "composition" in m for m in levels(findings, "ERROR")
    )


def test_every_error_carries_a_suggested_fix(tmp_path):
    # A repo that fails every invariant: each ERROR must include an actionable fix.
    repo = make_repo(
        tmp_path,
        agents=False,
        claude=None,
        settings=False,
        justfile=None,
        ci=False,
        notignored=False,
        pr_template=False,
        llmlint=False,
    )
    errors = [f for f in crb.audit(repo) if f.level == "ERROR"]
    assert errors
    assert all(f.fix for f in errors)


# llmlint: ignore[comments_earn_their_place] this module groups its tests behind section banners (`# --- helpers ---`, `# --- audit ---`, `# --- llmlint (LLM-judge tier) ---`); without this one the notignored tests read as part of the llmlint section below, so it names a boundary rather than decorating one.
# --- notignored (the suppressions review comment) ---------------------------


def test_conformant_repo_has_notignored_ok(tmp_path):
    findings = crb.audit(make_repo(tmp_path))
    assert any("suppressions review comment" in m for m in levels(findings, "OK")), (
        levels(findings, "ERROR")
    )


def test_the_shipped_template_satisfies_the_notignored_invariant(tmp_path):
    # The asset the skill actually drops into a produced repo, checked by the
    # invariant it exists to satisfy — so the template and the checker cannot drift.
    template = (SKILL_DIR / "assets" / "notignored.yml.template").read_text(
        encoding="utf-8"
    )
    findings = crb.check_notignored(make_repo(tmp_path, notignored=template))
    assert not [f for f in findings if f.level == "ERROR"], findings


def test_missing_notignored_workflow_is_error(tmp_path):
    # CI runs the gate, but no workflow posts the suppressions a PR adds.
    findings = crb.audit(make_repo(tmp_path, notignored=False))
    assert crb.has_errors(findings)
    assert any("notignored" in m for m in levels(findings, "ERROR"))


def test_notignored_without_a_pull_request_trigger_is_error(tmp_path):
    # The comment lives on a pull request, so a push-only workflow runs where
    # there is nothing to comment on — green, and permanently silent.
    push_only = NOTIGNORED_WORKFLOW.replace(
        "on:\n  pull_request:\n", "on:\n  push:\n    branches: [main]\n"
    )
    findings = crb.audit(make_repo(tmp_path, notignored=push_only))
    assert crb.has_errors(findings)
    assert any("`pull_request` trigger" in m for m in levels(findings, "ERROR"))


def test_notignored_trigger_survives_a_types_filter_and_the_inline_form(tmp_path):
    # Every YAML shape of the trigger counts: the block form with a `types:`
    # filter, the inline list, the bare scalar, and the quoted `on` key GitHub
    # accepts because YAML 1.1 reads a bare `on` as the boolean true.
    workflows = {
        "filtered": NOTIGNORED_WORKFLOW.replace(
            "  pull_request:\n", "  pull_request:\n    types: [opened, synchronize]\n"
        ),
        "inline-list": NOTIGNORED_WORKFLOW.replace(
            "on:\n  pull_request:\n", "on: [pull_request]\n"
        ),
        "scalar": NOTIGNORED_WORKFLOW.replace(
            "on:\n  pull_request:\n", "on: pull_request\n"
        ),
        "quoted-key": NOTIGNORED_WORKFLOW.replace("on:\n", '"on":\n'),
        # YAML 1.1 reads a bare `on` as the boolean true, so a workflow written
        # (or round-tripped by a formatter) with `true:` is still valid to GitHub.
        "boolean-key": NOTIGNORED_WORKFLOW.replace("on:\n", "true:\n"),
        "sibling-triggers": NOTIGNORED_WORKFLOW.replace(
            "on:\n  pull_request:\n",
            "on:\n  push:\n    branches: [main]\n  pull_request:\n",
        ),
    }
    for name, workflow in workflows.items():
        repo = tmp_path / name
        repo.mkdir()
        findings = crb.audit(make_repo(repo, notignored=workflow))
        assert not crb.has_errors(findings), (name, levels(findings, "ERROR"))


def test_a_pull_request_key_outside_the_on_block_is_not_a_trigger(tmp_path):
    # The check is scoped to the top-level `on:` block, so neither the fork
    # guard's `github.event.pull_request...` expression (kept here) nor a step
    # input spelled `pull_request` stands in for the trigger the workflow lacks.
    impostor = NOTIGNORED_WORKFLOW.replace(
        "on:\n  pull_request:\n", "on:\n  workflow_dispatch:\n"
    ).replace(
        "      - uses: nickderobertis/notignored@v0\n",
        "      - uses: nickderobertis/notignored@v0\n"
        "        with:\n          pull_request: true\n",
    )
    findings = crb.audit(make_repo(tmp_path, notignored=impostor))
    assert crb.has_errors(findings)
    assert any("`pull_request` trigger" in m for m in levels(findings, "ERROR"))


def test_pull_request_target_alone_is_not_the_pull_request_trigger(tmp_path):
    # A different, far riskier trigger: it must not satisfy the invariant by
    # prefix match.
    retargeted = NOTIGNORED_WORKFLOW.replace(
        "  pull_request:\n", "  pull_request_target:\n"
    )
    findings = crb.audit(make_repo(tmp_path, notignored=retargeted))
    assert crb.has_errors(findings)
    assert any("`pull_request` trigger" in m for m in levels(findings, "ERROR"))


def test_notignored_without_comment_permission_is_error(tmp_path):
    # The default read-only token cannot upsert the comment: the run goes green
    # having posted nothing.
    read_only = NOTIGNORED_WORKFLOW.replace("  pull-requests: write\n", "")
    findings = crb.audit(make_repo(tmp_path, notignored=read_only))
    assert crb.has_errors(findings)
    assert any("pull-requests: write" in m for m in levels(findings, "ERROR"))


def test_notignored_with_shallow_checkout_is_error(tmp_path):
    # Without full history there is no base branch to diff against, so the comment
    # reports nothing — invisible in a green run, which is why it is checked.
    shallow = NOTIGNORED_WORKFLOW.replace(
        "        with:\n          fetch-depth: 0\n", ""
    )
    findings = crb.audit(make_repo(tmp_path, notignored=shallow))
    assert crb.has_errors(findings)
    assert any("shallowly" in m for m in levels(findings, "ERROR"))


def test_notignored_without_fork_guard_is_error(tmp_path):
    # No guard means a fork PR fails on a token the contributor cannot fix — and
    # the guard is what justifies leaving the workflow out of the required set.
    unguarded = NOTIGNORED_WORKFLOW.replace(
        "    if: github.event.pull_request.head.repo.full_name == github.repository\n",
        "",
    )
    findings = crb.audit(make_repo(tmp_path, notignored=unguarded))
    assert crb.has_errors(findings)
    assert any("fork-PR skip guard" in m for m in levels(findings, "ERROR"))


def test_notignored_accepts_the_fork_guard_written_the_other_way_round(tmp_path):
    # The comparison reads equally well from either side, and a repo that wrote it
    # `github.repository == ...` has the same guard, not a missing one.
    reversed_guard = NOTIGNORED_WORKFLOW.replace(
        "    if: github.event.pull_request.head.repo.full_name == github.repository\n",
        "    if: github.repository == github.event.pull_request.head.repo.full_name\n",
    )
    findings = crb.audit(make_repo(tmp_path, notignored=reversed_guard))
    assert not crb.has_errors(findings), levels(findings, "ERROR")


def test_notignored_accepts_an_exact_version_pin(tmp_path):
    # The skill templates the floating `@v0` major tag, but a repo that pins an
    # exact release for reproducibility is still wired correctly.
    pinned = NOTIGNORED_WORKFLOW.replace("notignored@v0", "notignored@v0.1.11")
    findings = crb.audit(make_repo(tmp_path, notignored=pinned))
    assert not crb.has_errors(findings), levels(findings, "ERROR")


def test_notignored_in_a_combined_workflow_is_accepted(tmp_path):
    # The asset ships a standalone workflow, but the invariant is that *some*
    # workflow runs the action correctly — a repo folding it into ci.yml passes.
    combined = GATE_WORKFLOW + NOTIGNORED_WORKFLOW
    findings = crb.audit(make_repo(tmp_path, ci=combined, notignored=False))
    assert not crb.has_errors(findings), levels(findings, "ERROR")


# --- llmlint (LLM-judge tier) ----------------------------------------------


def test_conformant_repo_has_llmlint_ok(tmp_path):
    findings = crb.audit(make_repo(tmp_path))
    assert any("llmlint tier configured" in m for m in levels(findings, "OK")), levels(
        findings, "ERROR"
    )


def test_missing_llmlint_config_is_error(tmp_path):
    findings = crb.audit(make_repo(tmp_path, llmlint=False))
    assert crb.has_errors(findings)
    assert any("llmlint.yml" in m for m in levels(findings, "ERROR"))


def test_llmlint_config_without_plugins_is_error(tmp_path):
    # A present config that declares no plugins is not the composed tier.
    findings = crb.audit(make_repo(tmp_path, llmlint="version: 1\nrules: []\n"))
    assert crb.has_errors(findings)
    assert any("no plugins" in m for m in levels(findings, "ERROR"))


def test_llmlint_inline_empty_plugins_is_error(tmp_path):
    findings = crb.audit(make_repo(tmp_path, llmlint="version: 1\nplugins: []\n"))
    assert crb.has_errors(findings)
    assert any("no plugins" in m for m in levels(findings, "ERROR"))


def test_missing_oneharness_config_is_error(tmp_path):
    # The pinless llmlint.yml needs oneharness.toml to select a harness.
    findings = crb.audit(make_repo(tmp_path, oneharness=False))
    assert crb.has_errors(findings)
    assert any("oneharness.toml" in m for m in levels(findings, "ERROR"))


def test_oneharness_not_fallback_mode_is_error(tmp_path):
    # A parallel-mode (or hand-rolled single-harness) config isn't the fallback the
    # composer emits — a Claude Code session couldn't fall through to claude-code.
    parallel = 'harnesses = ["codex", "claude-code"]\n'
    findings = crb.audit(make_repo(tmp_path, oneharness=parallel))
    assert crb.has_errors(findings)
    assert any("fallback mode" in m for m in levels(findings, "ERROR"))


def test_oneharness_single_harness_fallback_is_error(tmp_path):
    # Fallback mode with only one harness has nothing to fall through to.
    solo = 'run_mode = "fallback"\nharnesses = ["claude-code"]\n'
    findings = crb.audit(make_repo(tmp_path, oneharness=solo))
    assert crb.has_errors(findings)
    assert any("only one harness" in m for m in levels(findings, "ERROR"))


def test_oneharness_fallback_without_claude_code_is_error(tmp_path):
    # A two-harness fallback that omits claude-code breaks the Claude Code session
    # path: with codex absent and no claude-code target, the tier can't run there.
    no_cc = 'run_mode = "fallback"\nharnesses = ["codex", "goose"]\n'
    findings = crb.audit(make_repo(tmp_path, oneharness=no_cc))
    assert crb.has_errors(findings)
    assert any("no `claude-code`" in m for m in levels(findings, "ERROR"))


def test_missing_llmlint_recipe_is_error(tmp_path):
    no_recipe = FULL_JUSTFILE.replace("\nlint-llm:\n    @echo lint-llm\n", "\n")
    findings = crb.audit(make_repo(tmp_path, justfile=no_recipe))
    assert crb.has_errors(findings)
    assert any("`lint-llm` recipe" in m for m in levels(findings, "ERROR"))


def test_ci_without_llmlint_reference_is_error(tmp_path):
    # CI runs the gate but never the llmlint tier — the blocking PR check is absent.
    gate_only = "name: ci\njobs:\n  check:\n    steps:\n      - run: just check\n"
    findings = crb.audit(make_repo(tmp_path, ci=gate_only))
    assert crb.has_errors(findings)
    assert any("llmlint" in m and "CI" in m for m in levels(findings, "ERROR")), levels(
        findings, "ERROR"
    )


def test_missing_lint_llm_diff_recipe_is_error(tmp_path):
    # The blocking PR check runs the diff-scoped recipe; `lint-llm` alone is not it.
    no_diff = FULL_JUSTFILE.replace("\nlint-llm-diff:\n    @echo lint-llm-diff\n", "\n")
    findings = crb.audit(make_repo(tmp_path, justfile=no_diff))
    assert crb.has_errors(findings)
    assert any("lint-llm-diff" in m for m in levels(findings, "ERROR"))


def test_missing_lint_llm_validate_recipe_is_error(tmp_path):
    # The deterministic model-free gate has its own recipe; `lint-llm-diff` is not it.
    no_validate = FULL_JUSTFILE.replace(
        "\nlint-llm-validate:\n    @echo lint-llm-validate\n", "\n"
    )
    findings = crb.audit(make_repo(tmp_path, justfile=no_validate))
    assert crb.has_errors(findings)
    assert any("lint-llm-validate" in m for m in levels(findings, "ERROR"))


def test_missing_setup_llmlint_script_is_error(tmp_path):
    # The tier config/recipes/CI are all present, but install is not automated.
    repo = make_repo(tmp_path)
    (repo / "scripts" / "setup-llmlint.sh").unlink()
    findings = crb.audit(repo)
    assert crb.has_errors(findings)
    assert any("setup-llmlint.sh" in m for m in levels(findings, "ERROR"))


def test_setup_llmlint_not_wired_into_sessionstart_is_error(tmp_path):
    # The setup script exists but no SessionStart hook runs it — not automated.
    findings = crb.audit(make_repo(tmp_path, settings='{"permissions": {"allow": []}}'))
    assert crb.has_errors(findings)
    assert any("SessionStart" in m for m in levels(findings, "ERROR"))


# --- session-setup provisioner ---------------------------------------------

# A .claude/settings.json whose SessionStart hook runs the session provisioner
# (session-setup.sh), the recommended layout that hands off to setup-llmlint.sh.
SESSION_SETUP_SETTINGS = (
    '{"hooks": {"SessionStart": [{"matcher": "startup|resume", "hooks": '
    '[{"type": "command", "command": "bash scripts/session-setup.sh"}]}]}, '
    '"permissions": {"allow": []}}'
)
# A session-setup.sh that provisions `just` (via the rust-just PyPI package).
SESSION_SETUP_SCRIPT = (
    "#!/usr/bin/env bash\n# provision just for the session\n"
    'uv tool install --upgrade "rust-just>=1.51.0"\n'
)
# A session-setup.sh that never installs `just` — misconfigured.
SESSION_SETUP_SCRIPT_NO_JUST = "#!/usr/bin/env bash\necho hello\n"


def add_session_setup(repo, body=SESSION_SETUP_SCRIPT):
    scripts = repo / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "session-setup.sh").write_text(body, encoding="utf-8")
    return repo


def test_session_setup_wired_and_provisions_just_is_ok(tmp_path):
    repo = add_session_setup(make_repo(tmp_path, settings=SESSION_SETUP_SETTINGS))
    findings = crb.check_session_setup(repo)
    assert any("session-setup.sh provisions" in m for m in levels(findings, "OK"))
    assert not levels(findings, "ERROR")


def test_sessionstart_via_session_setup_satisfies_llmlint_wiring(tmp_path):
    # The hook runs session-setup.sh (which hands off to setup-llmlint.sh); the
    # llmlint tier's automated-install assertion is still satisfied.
    repo = add_session_setup(make_repo(tmp_path, settings=SESSION_SETUP_SETTINGS))
    findings = crb.audit(repo)
    assert not any(
        "llmlint setup is not wired" in m for m in levels(findings, "ERROR")
    ), levels(findings, "ERROR")


def test_session_setup_without_just_provisioning_is_error(tmp_path):
    repo = add_session_setup(
        make_repo(tmp_path, settings=SESSION_SETUP_SETTINGS),
        body=SESSION_SETUP_SCRIPT_NO_JUST,
    )
    findings = crb.check_session_setup(repo)
    assert any("does not provision `just`" in m for m in levels(findings, "ERROR"))


def test_session_setup_wired_but_missing_script_is_error(tmp_path):
    # The hook points at session-setup.sh but the script isn't there.
    repo = make_repo(tmp_path, settings=SESSION_SETUP_SETTINGS)
    findings = crb.check_session_setup(repo)
    assert any("script is missing" in m for m in levels(findings, "ERROR"))


def test_session_setup_present_but_not_wired_is_error(tmp_path):
    # session-setup.sh exists and provisions just, but the hook wires only
    # setup-llmlint.sh (CONFORMANT_SETTINGS), so the provisioner never runs.
    repo = add_session_setup(make_repo(tmp_path))
    findings = crb.check_session_setup(repo)
    assert any(
        "not wired into a SessionStart hook" in m for m in levels(findings, "ERROR")
    )


def test_session_setup_absent_and_unreferenced_is_silent(tmp_path):
    # Optional provisioner: not shipped and not referenced by the hook — no finding.
    assert crb.check_session_setup(make_repo(tmp_path)) == []


# --- AGENTS.md length (advisory) -------------------------------------------


def test_terse_agents_md_emits_no_length_warning(tmp_path):
    # The conformant fixture is short, so the advisory cap stays silent.
    findings = crb.audit(make_repo(tmp_path))
    assert not any("keep it terse" in m for m in levels(findings, "WARN"))


def test_overlong_agents_md_warns_but_does_not_error(tmp_path):
    # A bloated-but-valid AGENTS.md: it still records the composition, so the
    # length check is an advisory WARN, never an ERROR.
    padding = "\n".join(f"- durable note {i}" for i in range(crb.AGENTS_MD_MAX_LINES))
    agents = CONFORMANT_AGENTS + "\n## Notes\n\n" + padding + "\n"
    findings = crb.audit(make_repo(tmp_path, composition=agents))
    assert not crb.has_errors(findings), levels(findings, "ERROR")
    assert any("keep it terse" in m for m in levels(findings, "WARN"))


# --- main / output discipline ----------------------------------------------


def test_main_returns_zero_and_is_quiet_on_success(tmp_path, capsys):
    assert crb.main([str(make_repo(tmp_path))]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    # Minimal on success: a single OK line, nothing more.
    assert len([line for line in captured.out.splitlines() if line.strip()]) == 1


def test_main_returns_one_and_reports_fixes_on_failure(tmp_path, capsys):
    assert crb.main([str(make_repo(tmp_path, agents=False))]) == 1
    captured = capsys.readouterr()
    assert "fix:" in captured.err
    assert "FAIL" in captured.err


def test_main_returns_two_on_missing_directory(tmp_path):
    assert crb.main([str(tmp_path / "does-not-exist")]) == 2


# --- buildout llmlint run (--buildout, opt-in / non-deterministic) ----------


def test_parse_composed_references_handles_comma_and_plus():
    assert crb.parse_composed_references(
        "- **References composed:** base.md, shapes/cli.md, ci.md"
    ) == ["base.md", "shapes/cli.md", "ci.md"]
    assert crb.parse_composed_references(
        "- References composed: shapes/cli.md + languages/python.md + ci.md"
    ) == ["shapes/cli.md", "languages/python.md", "ci.md"]
    assert crb.parse_composed_references("no composition line here") == []


def test_parse_composed_references_follows_a_wrapped_bullet():
    # A hand-written AGENTS.md wraps the bullet across physical lines; references
    # on the continuation line must not be lost (regression: ci.md/monorepo.md).
    agents = (
        "- **References composed:** `shapes/skills-repo.md` + `languages/python.md` +\n"
        "  `ci.md`, with Nx from `monorepo.md` as an optional accelerator\n"
        "  (the gate runs on uv alone).\n"
        "- **Excluded, and why:** ty and coverage.\n"
    )
    refs = crb.parse_composed_references(agents)
    assert refs == [
        "shapes/skills-repo.md",
        "languages/python.md",
        "ci.md",
        "monorepo.md",
    ]


def _buildout_repo(tmp_path) -> Path:
    """A conformant repo whose AGENTS.md records a stack with buildout fragments."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    return make_repo(
        tmp_path,
        composition=(
            "# AGENTS\n\n## Stack and composition\n\n"
            "- References composed: base.md, shapes/cli.md, languages/python.md, ci.md\n"
        ),
    )


def test_run_buildout_wires_local_fragments_and_passes(tmp_path):
    seen = {}

    def fake(cfgs, repo):
        seen["cfg"] = Path(cfgs[-1]).read_text(encoding="utf-8")
        seen["existed"] = Path(cfgs[-1]).is_file()
        return (0, "clean", "")

    findings = crb.run_buildout(
        _buildout_repo(tmp_path), SKILL_DIR, llmlint_runner=fake
    )
    assert not crb.has_errors(findings), levels(findings, "ERROR")
    # The throwaway config wired the LOCAL buildout fragments in as plugins.
    assert "buildout/base.llmlint.yml" in seen["cfg"]
    assert "buildout/shapes/cli.llmlint.yml" in seen["cfg"]
    assert "buildout/languages/python.llmlint.yml" in seen["cfg"]
    assert seen["existed"]


def test_run_buildout_wires_terraform_language_fragment(tmp_path):
    repo = make_repo(
        tmp_path,
        composition=(
            "# AGENTS\n\n## Stack and composition\n\n"
            "- References composed: base.md, shapes/library.md, "
            "languages/terraform.md, ci.md\n"
        ),
    )
    seen = {}

    def fake(cfgs, repo_):
        seen["cfg"] = Path(cfgs[-1]).read_text(encoding="utf-8")
        return (0, "clean", "")

    findings = crb.run_buildout(repo, SKILL_DIR, llmlint_runner=fake)
    assert not crb.has_errors(findings), levels(findings, "ERROR")
    assert "buildout/languages/terraform.llmlint.yml" in seen["cfg"]


def test_run_buildout_always_wires_the_project_graph_fragment(tmp_path):
    # The project graph is mandatory in every repo, so its one-time structural
    # checks must run whatever stack the AGENTS.md composition line records —
    # including one written before the reference existed.
    repo = make_repo(
        tmp_path,
        composition=(
            "# AGENTS\n\n## Stack and composition\n\n"
            "- References composed: shapes/cli.md, languages/rust.md\n"
        ),
    )
    seen = {}

    def fake(cfgs, repo_):
        seen["cfg"] = Path(cfgs[-1]).read_text(encoding="utf-8")
        return (0, "clean", "")

    findings = crb.run_buildout(repo, SKILL_DIR, llmlint_runner=fake)
    assert not crb.has_errors(findings), levels(findings, "ERROR")
    assert "buildout/project-graph.llmlint.yml" in seen["cfg"]
    # ...alongside the always-applied base tier and the recorded stack's own.
    assert "buildout/base.llmlint.yml" in seen["cfg"]
    assert "buildout/languages/rust.llmlint.yml" in seen["cfg"]


def test_run_buildout_does_not_duplicate_a_recorded_project_graph(tmp_path):
    # A composition that already names it must not wire the fragment in twice.
    repo = make_repo(
        tmp_path,
        composition=(
            "# AGENTS\n\n## Stack and composition\n\n"
            "- References composed: base.md, project-graph.md, shapes/cli.md, ci.md\n"
        ),
    )
    seen = {}

    def fake(cfgs, repo_):
        seen["cfg"] = Path(cfgs[-1]).read_text(encoding="utf-8")
        return (0, "clean", "")

    crb.run_buildout(repo, SKILL_DIR, llmlint_runner=fake)
    assert seen["cfg"].count("buildout/project-graph.llmlint.yml") == 1


def test_run_buildout_passes_committed_config_before_the_temp_one(tmp_path):
    # llmlint preflight-validates inline ignore directives against the configured
    # rule set: without the committed (ongoing) config, a directive naming an
    # ongoing rule is an "unknown rule" hard error. It must come FIRST — llmlint's
    # first config wins conflicting settings, so the repo's own choices beat the
    # temp config's defaults.
    seen = {}
    repo = _buildout_repo(tmp_path)

    def fake(cfgs, repo_):
        seen["cfgs"] = [Path(c) for c in cfgs]
        return (0, "", "")

    crb.run_buildout(repo, SKILL_DIR, llmlint_runner=fake)
    assert seen["cfgs"][0] == repo / "llmlint.yml"
    assert len(seen["cfgs"]) == 2  # committed config, then the temp buildout one


def test_run_buildout_without_committed_config_runs_the_temp_one_alone(tmp_path):
    # A repo whose ongoing config isn't composed yet still gets the buildout run.
    seen = {}
    repo = make_repo(
        tmp_path,
        llmlint=False,
        composition=(
            "# AGENTS\n\n## Stack and composition\n\n"
            "- References composed: base.md, shapes/cli.md\n"
        ),
    )

    def fake(cfgs, repo_):
        seen["cfgs"] = [Path(c) for c in cfgs]
        return (0, "", "")

    crb.run_buildout(repo, SKILL_DIR, llmlint_runner=fake)
    assert len(seen["cfgs"]) == 1
    assert seen["cfgs"][0].name.startswith("buildout-")


def test_run_buildout_always_includes_base_even_when_omitted(tmp_path):
    # base.md is always-applied; its buildout runs even if the composition line
    # (hand-written here) never names it.
    seen = {}
    repo = make_repo(
        tmp_path,
        composition=(
            "# AGENTS\n\n## Stack and composition\n\n"
            "- References composed: shapes/cli.md, languages/python.md, ci.md\n"
        ),
    )

    def fake(cfgs, repo_):
        seen["cfg"] = Path(cfgs[-1]).read_text(encoding="utf-8")
        return (0, "", "")

    crb.run_buildout(repo, SKILL_DIR, llmlint_runner=fake)
    assert "buildout/base.llmlint.yml" in seen["cfg"]


def test_run_buildout_ignores_path_escaping_tokens(tmp_path):
    # A `../`-style token in the composition must not pull a file from outside the
    # buildout tree into the config; only in-tree fragments are wired in.
    seen = {}
    repo = make_repo(
        tmp_path,
        composition=(
            "# AGENTS\n\n## Stack and composition\n\n"
            "- References composed: base.md, ../../../etc/evil.md, shapes/cli.md\n"
        ),
    )

    def fake(cfgs, repo_):
        seen["cfg"] = Path(cfgs[-1]).read_text(encoding="utf-8")
        return (0, "", "")

    crb.run_buildout(repo, SKILL_DIR, llmlint_runner=fake)
    assert "etc/evil" not in seen["cfg"]
    assert "../" not in seen["cfg"]
    assert "buildout/base.llmlint.yml" in seen["cfg"]


def test_run_buildout_removes_the_temp_config(tmp_path):
    captured = {}

    def fake(cfgs, repo):
        captured["path"] = Path(cfgs[-1])
        return (0, "", "")

    crb.run_buildout(_buildout_repo(tmp_path), SKILL_DIR, llmlint_runner=fake)
    assert not captured["path"].exists()


def test_run_buildout_violations_are_error(tmp_path):
    findings = crb.run_buildout(
        _buildout_repo(tmp_path),
        SKILL_DIR,
        llmlint_runner=lambda c, r: (1, "gate_runs_every_stage: false\n", ""),
    )
    assert crb.has_errors(findings)
    assert any("structural issue" in m for m in levels(findings, "ERROR"))


def test_run_buildout_missing_binary_is_actionable(tmp_path):
    findings = crb.run_buildout(
        _buildout_repo(tmp_path),
        SKILL_DIR,
        llmlint_runner=lambda c, r: (crb.LLMLINT_MISSING, "", "not found"),
    )
    assert any("not installed" in m for m in levels(findings, "ERROR"))


def test_run_buildout_harness_error_is_error(tmp_path):
    findings = crb.run_buildout(
        _buildout_repo(tmp_path),
        SKILL_DIR,
        llmlint_runner=lambda c, r: (2, "", "harness unauthenticated"),
    )
    assert any("could not run" in m for m in levels(findings, "ERROR"))


def test_run_buildout_without_composition_line_is_error(tmp_path):
    repo = make_repo(tmp_path, composition="# AGENTS\n\n## Stack\n\nnothing composed\n")
    findings = crb.run_buildout(
        repo, SKILL_DIR, llmlint_runner=lambda c, r: (0, "", "")
    )
    assert any("determine the stack" in m for m in levels(findings, "ERROR"))
