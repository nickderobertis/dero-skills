"""Tests for the create-repo plan composer.

Two layers, both driving the real reference files (never a mocked stand-in):

  * **E2E** — run the script the way the skill tells an agent to, as a real
    subprocess, and assert on exit code, stdout (the document), and stderr (the
    notes/errors). This exercises argument parsing, the stdout/stderr split, file
    output, and the real ``references/`` tree end to end.
  * **Unit** — load the PEP 723 script as a module (registered in sys.modules
    before exec so its ``@dataclass`` resolves ``__module__``) to exercise the
    parsing/selection helpers directly.

The structural test enforces the contract the rework rests on: every composable
reference carries a parseable ``## Verification`` section, so the composer can
assemble the checklist from items that live with each reference.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "compose_repo_plan.py"
REFS = SKILL_DIR / "references"
LLMLINT_ASSETS = SKILL_DIR / "assets" / "llmlint"

spec = importlib.util.spec_from_file_location("compose_repo_plan", SCRIPT)
assert spec is not None and spec.loader is not None
crp = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = crp
spec.loader.exec_module(crp)


# --- helpers ---------------------------------------------------------------


def run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the composer as a real subprocess, the way the skill invokes it."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def composable_references() -> list[Path]:
    """Every reference the composer can pull in (everything but the meta doc)."""
    return [p for p in sorted(REFS.rglob("*.md")) if p.name != "composing.md"]


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


def test_nextjs_pulls_webapp_and_assumes_typescript():
    result = run("--shape", "nextjs", "--language", "typescript")
    doc = result.stdout
    assert "shapes/web-app.md" in doc
    assert "shapes/nextjs.md" in doc
    assert "builds on the web-app shape" in result.stderr


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


# --- structural: llmlint rule fragments ------------------------------------


def _llmlint_fragments() -> list[Path]:
    return sorted(LLMLINT_ASSETS.rglob("*.llmlint.yml"))


def test_base_llmlint_fragment_exists():
    assert (LLMLINT_ASSETS / "base.llmlint.yml").is_file()


def test_every_llmlint_fragment_maps_to_a_reference():
    # A fragment's path mirrors a reference relpath (buildout/ stripped), so every
    # fragment must correspond to a real references/<...>.md — no orphans.
    missing: list[str] = []
    for frag in _llmlint_fragments():
        rel = frag.relative_to(LLMLINT_ASSETS).as_posix()
        if rel.startswith("buildout/"):
            rel = rel[len("buildout/") :]
        ref_rel = rel[: -len(".llmlint.yml")] + ".md"
        if not (REFS / ref_rel).is_file():
            missing.append(f"{rel} -> references/{ref_rel}")
    assert not missing, f"fragments with no matching reference: {missing}"


_NAME_LINE_RE = re.compile(r"^\s*-\s+name:\s*(\S+)\s*$")
_SNAKE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_TOP_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*):")


def test_every_llmlint_fragment_is_well_formed():
    # Each fragment declares only `version` + `rules` at the top level (no
    # fragment-level files/agents/plugins — "nearer-root wins" would ignore them),
    # carries a version for the URL pin, and uses snake_case rule names.
    bad: list[str] = []
    for frag in _llmlint_fragments():
        text = frag.read_text(encoding="utf-8")
        name = frag.relative_to(LLMLINT_ASSETS).as_posix()
        if not re.search(r"^version:\s*\S+", text, re.MULTILINE):
            bad.append(f"{name}: missing version")
        top_keys = {
            m.group(1) for line in text.splitlines() if (m := _TOP_KEY_RE.match(line))
        }
        extra = top_keys - {"version", "rules"}
        if extra:
            bad.append(f"{name}: unexpected top-level keys {sorted(extra)}")
        for line in text.splitlines():
            nm = _NAME_LINE_RE.match(line)
            if nm and not _SNAKE_RE.match(nm.group(1)):
                bad.append(f"{name}: non-snake_case rule name {nm.group(1)!r}")
    assert not bad, bad


# --- structural: verification items live with each reference --------------


def test_every_composable_reference_has_verification_items():
    missing: list[str] = []
    for path in composable_references():
        _, verification = crp.split_reference(path.read_text(encoding="utf-8"))
        items = [
            line
            for line in verification.splitlines()
            if crp.CHECKLIST_ITEM_RE.match(line)
        ]
        if not items:
            missing.append(path.relative_to(REFS).as_posix())
    assert not missing, f"references with no verification items: {missing}"


def test_composing_doc_is_not_composed():
    # composing.md is meta-guidance about *how* to compose; it should not be a
    # selectable reference and need not carry a Verification section.
    doc = run("--shape", "cli", "--language", "python").stdout
    assert "composing.md" not in doc


def _decompose_intersection(stem: str) -> tuple[str, str] | None:
    """Split an intersection stem into (shape, language) by the naming convention.

    Shapes may contain hyphens (web-app), so match against the real catalog
    rather than splitting on the first hyphen.
    """
    shapes = crp.discover(REFS, "shapes")
    languages = crp.discover(REFS, "languages")
    for lang in languages:
        for shape in shapes:
            if crp.intersection_name(shape, lang) == stem:
                return shape, lang
    return None


def test_every_intersection_follows_the_naming_convention():
    # The composer derives intersections from `<language>-<shape>`; every file in
    # intersections/ must decompose into a known shape + language, or it would be
    # invisible to auto-derivation.
    bad = [
        p.stem
        for p in sorted((REFS / "intersections").glob("*.md"))
        if _decompose_intersection(p.stem) is None
    ]
    assert not bad, f"intersection files not matching <language>-<shape>: {bad}"


def test_every_intersection_is_auto_included_for_its_pair():
    # Drive the real CLI: composing a shape+language whose intersection exists
    # must pull that intersection in automatically, without --intersection.
    for path in sorted((REFS / "intersections").glob("*.md")):
        decomposed = _decompose_intersection(path.stem)
        assert decomposed is not None
        shape, lang = decomposed
        result = run("--shape", shape, "--language", lang)
        assert result.returncode == 0
        assert f"intersections/{path.stem}.md" in result.stdout
        assert f"auto-included intersection {path.stem}" in result.stderr


# --- unit: the parsing/selection helpers ----------------------------------


def test_split_reference_separates_guidance_and_block():
    text = "# Title\n\nGuidance line.\n\n## Verification\n\n- [ ] item one\n- [ ] item two\n"
    guidance, verification = crp.split_reference(text)
    assert "Guidance line." in guidance
    assert "## Verification" not in guidance
    assert verification == "- [ ] item one\n- [ ] item two"


def test_split_reference_without_section():
    guidance, verification = crp.split_reference("# T\n\nbody\n")
    assert "body" in guidance
    assert verification == ""


def test_strip_leading_title():
    assert crp.strip_leading_title("# Title\n\nbody\n") == "body"
    assert crp.strip_leading_title("no title\nmore") == "no title\nmore"


def test_select_relpaths_order_and_dedup():
    notes: list[str] = []
    relpaths, langs = crp.select_relpaths(
        REFS,
        shape="cli",
        languages=["python"],
        intersections=[],
        releasing=True,
        monorepo=False,
        notes=notes,
    )
    assert relpaths == [
        "base.md",
        "shapes/cli.md",
        "languages/python.md",
        "intersections/python-cli.md",
        "ci.md",
        "llmlint.md",
        "releasing.md",
    ]
    assert langs == ["python"]
    assert any("python-cli" in n for n in notes)


def test_count_items_includes_closing_gates():
    relpaths, _ = crp.select_relpaths(REFS, "cli", ["python"], [], False, False, [])
    refs = [crp.load_reference(REFS, rel) for rel in relpaths]
    # Two closing automated gates plus at least one item per reference.
    assert crp.count_items(refs) >= 2 + len(refs)
