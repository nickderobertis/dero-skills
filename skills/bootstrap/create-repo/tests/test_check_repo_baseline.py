"""Tests for the create-repo baseline checker.

Loads the PEP 723 script as a module so its functions can be exercised
directly. The module is registered in sys.modules before exec so the
``@dataclass`` in it can resolve ``__module__`` under all Python versions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_repo_baseline.py"

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

check: lint test
    @echo gate

test:
    @echo t

lint:
    @echo l

format:
    @echo f

upgrade:
    @just check
"""


def make_repo(
    tmp_path: Path,
    *,
    agents=True,
    claude="symlink",
    settings=True,
    justfile=FULL_JUSTFILE,
    ci=True,
) -> Path:
    """Build a repo fixture. With no overrides it is fully conformant.

    ``claude`` is "symlink", "file", or None; ``settings`` is True, False, or a
    raw string written verbatim to .claude/settings.json (to test bad JSON).
    """
    repo = tmp_path
    if agents:
        (repo / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    if claude == "symlink":
        (repo / "CLAUDE.md").symlink_to("AGENTS.md")
    elif claude == "file":
        (repo / "CLAUDE.md").write_text("# not a symlink\n", encoding="utf-8")
    if settings is not False:
        (repo / ".claude").mkdir(exist_ok=True)
        body = (
            settings if isinstance(settings, str) else '{"permissions": {"allow": []}}'
        )
        (repo / ".claude" / "settings.json").write_text(body, encoding="utf-8")
    if justfile is not None:
        (repo / "justfile").write_text(justfile, encoding="utf-8")
    if ci:
        wf = repo / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    return repo


def levels(findings, level):
    return [f.message for f in findings if f.level == level]


# --- parse_just_recipes ----------------------------------------------------


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


def test_every_error_carries_a_suggested_fix(tmp_path):
    # A repo that fails every invariant: each ERROR must include an actionable fix.
    repo = make_repo(
        tmp_path, agents=False, claude=None, settings=False, justfile=None, ci=False
    )
    errors = [f for f in crb.audit(repo) if f.level == "ERROR"]
    assert errors
    assert all(f.fix for f in errors)


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
