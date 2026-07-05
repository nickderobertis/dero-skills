"""End-to-end tests for the create-repo plan composer.

Runs the PEP 723 script the way the skill tells an agent to — as a real
subprocess via ``uv run --script`` against the real ``references/`` tree — and
asserts on exit code, stdout (the document), and stderr (the notes/errors).
This exercises argument parsing, the stdout/stderr split, file output, and the
composition end to end, never a mocked stand-in.

The in-process unit and structural layers live beside this file's sibling,
``../test_compose_repo_plan.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2]
SCRIPT = SKILL_DIR / "scripts" / "compose_repo_plan.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the composer as a real subprocess, the way the skill invokes it:
    `uv run --script` so the PEP 723 script resolves exactly as documented."""
    return subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


# --- e2e: the happy path ---------------------------------------------------


def test_list_prints_catalog():
    result = run("--list")
    assert result.returncode == 0
    assert "--shape" in result.stdout
    assert "cli" in result.stdout
    assert "python" in result.stdout
    assert "python-cli" in result.stdout


def test_cli_python_composes_guidance_and_checklist():
    result = run("--shape", "cli", "--language", "python")
    assert result.returncode == 0
    doc = result.stdout

    # Document scaffold.
    assert doc.startswith("# Repo creation plan: cli + python")
    assert "## Guidance" in doc
    assert "## Verification checklist" in doc
    assert "### Automated gates (necessary, not sufficient)" in doc

    # Guidance from each composed reference is inlined.
    assert "### Base: always-applied invariants  (`base.md`)" in doc
    assert "### Shape: CLI  (`shapes/cli.md`)" in doc
    assert "### Language: Python  (`languages/python.md`)" in doc
    assert "### Cross-cutting: GitHub Actions / CI  (`ci.md`)" in doc

    # The intersection is auto-derived, announced on stderr.
    assert "intersections/python-cli.md" in doc
    assert "auto-included intersection python-cli" in result.stderr

    # The checklist carries items lifted from the references, plus the two
    # closing automated gates.
    assert "- [ ]" in doc
    assert "just check` passes locally" in doc
    assert "check_repo_baseline.py" in doc

    # The composition block records exactly what was composed (llmlint.md is
    # always pulled in on top of ci.md, like ci.md itself).
    assert (
        "**References composed:** base.md, shapes/cli.md, languages/python.md, "
        "intersections/python-cli.md, ci.md, llmlint.md" in doc
    )


def test_guidance_does_not_duplicate_verification_sections():
    # The per-reference `## Verification` blocks are lifted into the single
    # checklist; the guidance section must not repeat them.
    doc = run("--shape", "cli", "--language", "python").stdout
    guidance = doc.split("## Verification checklist", 1)[0]
    assert "## Verification" not in guidance
    # And the reference's own top-level `#` title is not duplicated under the
    # composer's `###` heading.
    assert "# Shape: CLI\n" not in guidance


def test_multiple_languages_each_appear():
    doc = run(
        "--shape", "library", "--language", "python", "--language", "typescript"
    ).stdout
    assert "languages/python.md" in doc
    assert "languages/typescript.md" in doc
    assert "### Language: Python" in doc
    assert "### Language: TypeScript" in doc


def test_releasing_and_monorepo_flags_pull_references():
    doc = run(
        "--shape", "library", "--language", "python", "--releasing", "--monorepo"
    ).stdout
    assert "### Cross-cutting: Releases & versioning  (`releasing.md`)" in doc
    assert "### Cross-cutting: Monorepo orchestration  (`monorepo.md`)" in doc
    # Their verification items land in the checklist too.
    checklist = doc.split("## Verification checklist", 1)[1]
    assert "PR-title lint is a required check" in checklist
    assert "Each deliverable is its own project" in checklist


def test_releasing_omitted_by_default():
    # Guard against composition, not stray prose: ci.md's body mentions
    # releasing.md, so check the composed-references line and section headings.
    doc = run("--shape", "library", "--language", "python").stdout
    composed = next(line for line in doc.splitlines() if "References composed" in line)
    assert "releasing.md" not in composed
    assert "monorepo.md" not in composed
    assert "(`releasing.md`)" not in doc
    assert "(`monorepo.md`)" not in doc


def test_react_pulls_webapp_and_assumes_typescript():
    result = run("--shape", "react", "--language", "typescript")
    doc = result.stdout
    assert "shapes/web-app.md" in doc
    assert "shapes/react.md" in doc
    assert "### Shape: React app  (`shapes/react.md`)" in doc
    assert "react builds on the web-app shape" in result.stderr
    # Order: the base-most shape (web-app) precedes the concrete shape (react).
    assert doc.index("shapes/web-app.md") < doc.index("shapes/react.md")


def test_react_auto_adds_typescript_when_missing():
    result = run("--shape", "react", "--language", "bash")
    assert result.returncode == 0
    assert "languages/typescript.md" in result.stdout
    assert "react assumes TypeScript" in result.stderr
    assert "react + bash, typescript" in result.stdout


def test_nextjs_pulls_react_webapp_and_assumes_typescript():
    result = run("--shape", "nextjs", "--language", "typescript")
    doc = result.stdout
    assert "shapes/web-app.md" in doc
    assert "shapes/react.md" in doc
    assert "shapes/nextjs.md" in doc
    assert "builds on the web-app shape" in result.stderr
    assert "builds on the react shape" in result.stderr
    # The build-on chain composes base-most first: web-app, then react, then nextjs.
    assert (
        doc.index("shapes/web-app.md")
        < doc.index("shapes/react.md")
        < doc.index("shapes/nextjs.md")
    )
    # The composition line records the full chain in order.
    assert (
        "base.md, shapes/web-app.md, shapes/react.md, shapes/nextjs.md, "
        "languages/typescript.md" in doc
    )


def test_nextjs_auto_adds_typescript_when_missing():
    result = run("--shape", "nextjs", "--language", "bash")
    assert result.returncode == 0
    assert "languages/typescript.md" in result.stdout
    assert "assumes TypeScript" in result.stderr
    # The resolved language list shows up in the title and composition block.
    assert "nextjs + bash, typescript" in result.stdout


def test_output_to_file_keeps_stdout_clean_and_matches():
    inline = run("--shape", "cli", "--language", "rust").stdout
    out = SKILL_DIR / "tests" / "_plan_output_probe.md"
    try:
        result = run("--shape", "cli", "--language", "rust", "-o", str(out))
        assert result.returncode == 0
        assert result.stdout == ""  # the document went to the file, not stdout
        assert "wrote" in result.stderr and "checklist items" in result.stderr
        assert out.read_text(encoding="utf-8") == inline
    finally:
        out.unlink(missing_ok=True)


def test_rust_cli_intersection_auto_derived():
    result = run("--shape", "cli", "--language", "rust")
    assert "intersections/rust-cli.md" in result.stdout
    assert "auto-included intersection rust-cli" in result.stderr


# --- e2e: failure and recovery --------------------------------------------


def test_invalid_shape_fails_with_choices():
    result = run("--shape", "frobnicate", "--language", "python")
    assert result.returncode == 2
    assert "invalid choice: 'frobnicate'" in result.stderr


def test_invalid_language_fails():
    result = run("--shape", "cli", "--language", "cobol")
    assert result.returncode == 2
    assert "invalid choice: 'cobol'" in result.stderr


def test_missing_shape_is_required():
    result = run("--language", "python")
    assert result.returncode == 2
    assert "--shape" in result.stderr


def test_missing_language_is_required():
    result = run("--shape", "cli")
    assert result.returncode == 2
    assert "--language" in result.stderr


# --- e2e: llmlint config emission ------------------------------------------


def test_llmlint_config_wires_selected_fragments_as_pinned_plugins(tmp_path):
    out = tmp_path / "llmlint.yml"
    result = run("--shape", "cli", "--language", "python", "--llmlint-config", str(out))
    assert result.returncode == 0
    cfg = out.read_text(encoding="utf-8")
    # config_lint plugin is always first; base + selected fragments follow, pinned.
    assert "assets/config_lint.yml@1" in cfg
    assert "/assets/llmlint/base.llmlint.yml@1" in cfg
    assert "/assets/llmlint/shapes/cli.llmlint.yml@1" in cfg
    assert "/assets/llmlint/languages/python.llmlint.yml@1" in cfg
    assert "/assets/llmlint/ci.llmlint.yml@1" in cfg
    # the default agent + schema modeline ship in the wrapper
    assert "harness: claude-code" in cfg
    assert "llmlint.schema.json" in cfg
    # an unselected language's fragment is NOT pulled in
    assert "languages/typescript.llmlint.yml" not in cfg
    # the ongoing config carries no buildout-only fragments
    assert "/buildout/" not in cfg
    # stderr records the composition; the plan stays on stdout
    assert "ongoing llmlint config" in result.stderr


def test_llmlint_buildout_config_carries_only_buildout_fragments(tmp_path):
    out = tmp_path / "llmlint.buildout.yml"
    result = run(
        "--shape",
        "cli",
        "--language",
        "python",
        "--llmlint-buildout-config",
        str(out),
    )
    assert result.returncode == 0
    cfg = out.read_text(encoding="utf-8")
    assert "TEMPORARY" in cfg  # the do-not-commit header
    assert "/buildout/ci.llmlint.yml@1" in cfg
    assert "/buildout/intersections/python-cli.llmlint.yml@1" in cfg
    # ongoing-only fragments (e.g. base) never land in the buildout config
    assert "/assets/llmlint/base.llmlint.yml@1" not in cfg


def test_llmlint_buildout_covers_base_language_and_shape(tmp_path):
    # The buildout tier now backs the universal (base), per-language, and
    # per-shape one-time structural invariants — not just ci/intersection.
    out = tmp_path / "b.yml"
    run(
        "--shape",
        "web-app",
        "--language",
        "typescript",
        "--llmlint-buildout-config",
        str(out),
    )
    cfg = out.read_text(encoding="utf-8")
    assert "/buildout/base.llmlint.yml@1" in cfg
    assert "/buildout/languages/typescript.llmlint.yml@1" in cfg
    assert "/buildout/shapes/web-app.llmlint.yml@1" in cfg


def test_llmlint_buildout_releasing_and_monorepo_gated_by_flags(tmp_path):
    out = tmp_path / "b.yml"
    run(
        "--shape",
        "library",
        "--language",
        "python",
        "--llmlint-buildout-config",
        str(out),
    )
    bare = out.read_text(encoding="utf-8")
    assert "/buildout/releasing.llmlint.yml" not in bare
    assert "/buildout/monorepo.llmlint.yml" not in bare

    run(
        "--shape",
        "library",
        "--language",
        "python",
        "--releasing",
        "--monorepo",
        "--llmlint-buildout-config",
        str(out),
    )
    flagged = out.read_text(encoding="utf-8")
    assert "/buildout/releasing.llmlint.yml@1" in flagged
    assert "/buildout/monorepo.llmlint.yml@1" in flagged


def test_llmlint_config_to_file_keeps_plan_stdout_clean(tmp_path):
    # With -o for the plan, stdout stays empty; the llmlint config is its own file.
    out_plan = tmp_path / "PLAN.md"
    out_cfg = tmp_path / "llmlint.yml"
    result = run(
        "--shape",
        "cli",
        "--language",
        "rust",
        "-o",
        str(out_plan),
        "--llmlint-config",
        str(out_cfg),
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert "plugins:" in out_cfg.read_text(encoding="utf-8")
    assert out_plan.read_text(encoding="utf-8").startswith("# Repo creation plan")


def test_plan_closes_with_both_llmlint_gates():
    doc = run("--shape", "cli", "--language", "python").stdout
    assert "llmlint (ongoing) passes once" in doc
    assert "llmlint (buildout) passes once" in doc
