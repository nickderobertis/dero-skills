"""In-process tests for the create-repo plan composer.

  * **Unit** — load the PEP 723 script as a module (registered in sys.modules
    before exec so its ``@dataclass`` resolves ``__module__``) to exercise the
    parsing/selection helpers directly.
  * **Structural** — enforce the contract the rework rests on: every composable
    reference carries a parseable ``## Verification`` section, so the composer
    can assemble the checklist from items that live with each reference. A few
    of these also drive the real CLI (via ``run``) to check the composed output.

The end-to-end layer — running the script as a real ``uv run --script``
subprocess and asserting on exit code / stdout / stderr across the real
``references/`` tree — lives in ``e2e/test_compose_repo_plan_e2e.py``.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

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
    """Run the composer as a real subprocess, the way the skill invokes it:
    `uv run --script` so the PEP 723 script resolves exactly as documented."""
    return subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def composable_references() -> list[Path]:
    """Every reference the composer can pull in (everything but the meta doc)."""
    return [p for p in sorted(REFS.rglob("*.md")) if p.name != "composing.md"]


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


def test_select_relpaths_discovers_terraform_language():
    relpaths, langs = crp.select_relpaths(
        REFS, "library", ["terraform"], [], False, False, []
    )
    assert "languages/terraform.md" in relpaths
    assert langs == ["terraform"]


def test_select_relpaths_shape_build_on_chain():
    # nextjs builds on react builds on web-app; all assume TypeScript. The parent
    # shapes compose base-most first, then the concrete shape, then the language.
    notes: list[str] = []
    relpaths, langs = crp.select_relpaths(
        REFS,
        shape="nextjs",
        languages=[],
        intersections=[],
        releasing=False,
        monorepo=False,
        notes=notes,
    )
    assert relpaths[:5] == [
        "base.md",
        "shapes/web-app.md",
        "shapes/react.md",
        "shapes/nextjs.md",
        "languages/typescript.md",
    ]
    assert langs == ["typescript"]

    # react alone pulls in web-app + typescript, nothing more.
    relpaths, langs = crp.select_relpaths(REFS, "react", [], [], False, False, [])
    assert relpaths[:4] == [
        "base.md",
        "shapes/web-app.md",
        "shapes/react.md",
        "languages/typescript.md",
    ]
    assert langs == ["typescript"]


def test_react_llmlint_fragment_wired_when_composing_react(tmp_path):
    out = tmp_path / "llmlint.yml"
    result = run(
        "--shape", "react", "--language", "typescript", "--llmlint-config", str(out)
    )
    assert result.returncode == 0
    cfg = out.read_text(encoding="utf-8")
    # react's own fragment plus the web-app fragment it builds on are both wired.
    assert "/assets/llmlint/shapes/react.llmlint.yml@1" in cfg
    assert "/assets/llmlint/shapes/web-app.llmlint.yml@1" in cfg


def test_pin_range_keeps_major_only():
    # Consumers pin the major so non-breaking (minor/patch) fragment bumps flow
    # to them; a bare version is already its own major.
    assert crp.pin_range("1") == "1"
    assert crp.pin_range("1.1.0") == "1"
    assert crp.pin_range("2.3.4") == "2"


def test_read_fragment_version_rejects_malformed_version(tmp_path):
    # A version read from a config file is a boundary input: a malformed value
    # must fail loudly, not flow into a broken `@...` pin.
    frag = tmp_path / "bad.llmlint.yml"
    frag.write_text("version: not-a-version\nrules: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid semver"):
        crp.read_fragment_version(frag)


def test_semver_fragment_still_pins_to_major(tmp_path):
    # base is versioned past 1.0 (1.1.0); a consumer must still get `@1`, not a
    # frozen `@1.1.0` exact pin, or non-breaking bumps would never reach them.
    version = crp.read_fragment_version(LLMLINT_ASSETS / "base.llmlint.yml")
    assert "." in version, "base should carry a full semver, exercising pin_range"
    out = tmp_path / "llmlint.yml"
    result = run("--shape", "cli", "--language", "python", "--llmlint-config", str(out))
    assert result.returncode == 0
    cfg = out.read_text(encoding="utf-8")
    assert "/assets/llmlint/base.llmlint.yml@1" in cfg
    assert f"/assets/llmlint/base.llmlint.yml@{version}" not in cfg


def test_composed_config_has_no_top_level_version():
    # A top-level `version:` only means anything when a config is itself consumed
    # as a plugin (a `@`-pinned URL); the composed *consumer* config is pinned by
    # no one, so a version there is inert AND makes the validate gate demand a bump
    # on every edit. Guard both the ongoing and buildout variants — a subtle
    # regression here stays green everywhere else, since the field is schema-valid.
    for buildout in (False, True):
        cfg = crp.render_llmlint_config(
            ["https://example.test/base.llmlint.yml@1"], buildout=buildout
        )
        top_keys = {
            m.group(1) for line in cfg.splitlines() if (m := _TOP_KEY_RE.match(line))
        }
        variant = "buildout" if buildout else "ongoing"
        assert "version" not in top_keys, (
            f"composed {variant} config carries an inert top-level version; "
            f"keys={sorted(top_keys)}"
        )
        # ...but the plugin pins still carry `@version`, and the config is intact.
        assert "plugins" in top_keys
        assert "@1" in cfg


def test_count_items_includes_closing_gates():
    relpaths, _ = crp.select_relpaths(REFS, "cli", ["python"], [], False, False, [])
    refs = [crp.load_reference(REFS, rel) for rel in relpaths]
    # Two closing automated gates plus at least one item per reference.
    assert crp.count_items(refs) >= 2 + len(refs)
