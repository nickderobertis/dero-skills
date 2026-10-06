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
import json
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


def test_every_composable_llmlint_fragment_maps_to_a_reference():
    # A fragment's path mirrors a reference relpath (buildout/ stripped), so every
    # fragment must correspond to a real references/<...>.md — no orphans. That
    # includes `tools/`: an opt-in fragment joins through its tool's reference.
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
        notes=notes,
    )
    assert relpaths == [
        "base.md",
        "project-graph.md",
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
    relpaths, langs = crp.select_relpaths(REFS, "library", ["terraform"], [], False, [])
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
        notes=notes,
    )
    assert relpaths[:6] == [
        "base.md",
        "project-graph.md",
        "shapes/web-app.md",
        "shapes/react.md",
        "shapes/nextjs.md",
        "languages/typescript.md",
    ]
    assert langs == ["typescript"]

    # react alone pulls in web-app + typescript, nothing more.
    relpaths, langs = crp.select_relpaths(REFS, "react", [], [], False, [])
    assert relpaths[:5] == [
        "base.md",
        "project-graph.md",
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
    relpaths, _ = crp.select_relpaths(REFS, "cli", ["python"], [], False, [])
    refs = [crp.load_reference(REFS, rel) for rel in relpaths]
    # Two closing automated gates plus at least one item per reference.
    assert crp.count_items(refs) >= 2 + len(refs)


def test_buildout_pins_track_each_fragment_current_major(tmp_path):
    # The pin is what carries a fragment's rules to a new repo, so it must follow
    # the fragment's *own* major: a fragment bumped to 2.x (a renamed rule, say)
    # that still composed as `@1` would silently drop its rules from every repo
    # created afterwards. Drive the real CLI, and read each expected major
    # straight out of the fragment file rather than through the composer's own
    # helper, so a composer that hard-codes a major fails here.
    out = tmp_path / "llmlint.buildout.yml"
    result = run(
        "--shape",
        "react",
        "--language",
        "typescript",
        "--llmlint-buildout-config",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    cfg = out.read_text(encoding="utf-8")

    pinned = dict(
        re.findall(r"/assets/llmlint/buildout/(\S+?\.llmlint\.yml)@([\w.]+)", cfg)
    )
    assert pinned, f"no buildout plugin pins in composed config:\n{cfg}"

    def declared_major(rel: str) -> str:
        text = (LLMLINT_ASSETS / "buildout" / rel).read_text(encoding="utf-8")
        match = re.search(r"^version:\s*(\S+)", text, re.MULTILINE)
        assert match, f"buildout/{rel} declares no version"
        return match.group(1).split(".", 1)[0]

    wrong = {
        rel: (pin, declared_major(rel))
        for rel, pin in pinned.items()
        if pin != declared_major(rel)
    }
    assert not wrong, f"buildout pins that do not match the fragment's major: {wrong}"

    # And the react buildout fragment is one of them, at whatever major it now
    # carries — the stack selected it, so a dropped pin is a hole, not a pass.
    assert pinned.get("shapes/react.llmlint.yml") == declared_major(
        "shapes/react.llmlint.yml"
    )


# The onebudgetspec opt-in (`--tool onebudgetspec`, `--wiring`), driven through the real CLI over real files. The wiring's behaviour in a real
# Nx workspace — budgets measured, scoped, failed and cached — is the external tier's
# (external/test_onebudgetspec_wiring_e2e.py).

ONEBUDGETSPEC_URL = (
    "https://raw.githubusercontent.com/nickderobertis/dero-skills/main/skills/"
    "bootstrap/create-repo/assets/llmlint/tools/onebudgetspec.llmlint.yml@1"
)
REPO_ROOT = SKILL_DIR.parents[2]


def compose_into(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """Compose a Rust CLI's plan and llmlint.yml into ``repo``, plus ``extra`` flags."""
    return run(
        "--shape",
        "cli",
        "--language",
        "rust",
        "-o",
        str(repo / "plan.md"),
        "--llmlint-config",
        str(repo / "llmlint.yml"),
        *extra,
    )


def template_repo(tmp_path: Path) -> Path:
    """A repository at the setup step's starting point: the template justfile."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "justfile").write_text(
        (SKILL_DIR / "assets" / "justfile.template").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return repo


def test_the_opt_in_composes_the_reference_and_adopts_the_lint_file(tmp_path):
    result = compose_into(tmp_path, "--tool", "onebudgetspec")
    assert result.returncode == 0, result.stderr
    plan = (tmp_path / "plan.md").read_text(encoding="utf-8")
    assert "tools/onebudgetspec.md" in plan
    assert "--tool onebudgetspec" in plan  # the recorded invocation
    llmlint = (tmp_path / "llmlint.yml").read_text(encoding="utf-8")
    assert f'  - "{ONEBUDGETSPEC_URL}"' in llmlint.splitlines()


def test_without_the_opt_in_nothing_names_onebudgetspec(tmp_path):
    repo = template_repo(tmp_path)
    before = (repo / "justfile").read_text(encoding="utf-8")
    result = compose_into(repo)
    assert result.returncode == 0, result.stderr
    assert "onebudgetspec" not in (repo / "llmlint.yml").read_text(encoding="utf-8")
    # rust-cli.md's guidance mentions the tool; the plan composes no part of it.
    plan = (repo / "plan.md").read_text(encoding="utf-8")
    [composed] = [
        line for line in plan.splitlines() if "**References composed:**" in line
    ]
    assert "tools/" not in composed
    assert "Tool: onebudgetspec" not in plan
    assert "--tool" not in plan
    assert (repo / "justfile").read_text(encoding="utf-8") == before
    assert not (repo / "package.json").exists()
    assert not (repo / "nx.json").exists()


def test_wiring_pins_the_release_and_adds_both_targets_to_the_gate(tmp_path):
    repo = template_repo(tmp_path)
    result = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert result.returncode == 0, result.stderr

    package = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    assert package["devDependencies"] == {
        "@onebudgetspec/cli": crp.ONEBUDGETSPEC_VERSION
    }
    nx = json.loads((repo / "nx.json").read_text(encoding="utf-8"))
    budgets = nx["targetDefaults"]["budgets"]
    assert budgets["cache"] is True
    assert {"externalDependencies": ["@onebudgetspec/cli"]} in budgets["inputs"]
    assert "^production" in budgets["inputs"]
    assert nx["namedInputs"]["production"] == ["default"]
    assert nx["targetDefaults"]["budgets-host"] == {"cache": False}

    justfile = (repo / "justfile").read_text(encoding="utf-8")
    assert 'check tier="affected": && (test-e2e tier) (budgets tier)\n' in justfile
    assert "-t budgets budgets-host" in justfile
    assert "bunx onebudgetspec check budgets.yaml" in justfile


def test_wiring_is_idempotent_and_keeps_what_the_repo_already_has(tmp_path):
    repo = template_repo(tmp_path)
    (repo / "package.json").write_text(
        json.dumps({"name": "x", "devDependencies": {"nx": "23.2.1"}}),
        encoding="utf-8",
    )
    (repo / "nx.json").write_text(
        json.dumps({"namedInputs": {"production": ["{projectRoot}/src/**/*"]}}),
        encoding="utf-8",
    )
    args = ("--tool", "onebudgetspec", "--wiring", str(repo))
    assert compose_into(repo, *args).returncode == 0
    wired = {
        name: (repo / name).read_text(encoding="utf-8")
        for name in ("package.json", "nx.json", "justfile")
    }
    second = compose_into(repo, *args)
    assert second.returncode == 0, second.stderr
    assert "already wired" in second.stderr
    for name, text in wired.items():
        assert (repo / name).read_text(encoding="utf-8") == text, name

    package = json.loads(wired["package.json"])
    assert package["devDependencies"]["nx"] == "23.2.1"
    nx = json.loads(wired["nx.json"])
    assert nx["namedInputs"]["production"] == ["{projectRoot}/src/**/*"]


def test_wiring_moves_an_older_pin_to_the_release(tmp_path):
    repo = template_repo(tmp_path)
    (repo / "package.json").write_text(
        json.dumps({"dependencies": {"@onebudgetspec/cli": "^0.1.0"}}),
        encoding="utf-8",
    )
    result = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert result.returncode == 0, result.stderr
    package = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"] == {"@onebudgetspec/cli": crp.ONEBUDGETSPEC_VERSION}
    assert "devDependencies" not in package


def test_wiring_without_a_tool_is_refused(tmp_path):
    repo = template_repo(tmp_path)
    result = compose_into(repo, "--wiring", str(repo))
    assert result.returncode == 2
    assert "--wiring needs a --tool" in result.stderr
    assert not (repo / "package.json").exists()


def test_wiring_without_a_justfile_names_the_template(tmp_path):
    result = compose_into(
        tmp_path, "--tool", "onebudgetspec", "--wiring", str(tmp_path)
    )
    assert result.returncode == 2
    assert "no justfile" in result.stderr
    assert "assets/justfile.template" in result.stderr


def test_wiring_refuses_a_package_json_it_cannot_read(tmp_path):
    repo = template_repo(tmp_path)
    (repo / "package.json").write_text("{not json", encoding="utf-8")
    result = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert result.returncode == 2
    assert "package.json is not valid JSON" in result.stderr
    assert (repo / "package.json").read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize(
    ("justfile", "refusal"),
    [
        ("check: lint test\n    echo gate\n", "no `check tier=...` recipe"),
        ('check tier="affected":\n    echo gate\n', "no `base` assignment"),
    ],
    ids=["check without a tier", "no merge base"],
)
def test_wiring_refuses_a_justfile_the_recipe_cannot_join(tmp_path, justfile, refusal):
    # The recipe runs at check's tier against the template's merge base; a
    # justfile without them is named, not quietly given a recipe that breaks it.
    (tmp_path / "justfile").write_text(justfile, encoding="utf-8")
    result = compose_into(
        tmp_path, "--tool", "onebudgetspec", "--wiring", str(tmp_path)
    )
    assert result.returncode == 2
    assert refusal in result.stderr
    assert "assets/justfile.template" in result.stderr
    assert (tmp_path / "justfile").read_text(encoding="utf-8") == justfile


@pytest.mark.parametrize(
    ("name", "body", "refusal"),
    [
        ("package.json", "[]", "package.json does not hold a JSON object"),
        (
            "package.json",
            '{"devDependencies": ["@onebudgetspec/cli"]}',
            "`devDependencies` is not a JSON object",
        ),
        ("nx.json", '{"targetDefaults": []}', "`targetDefaults` is not a JSON object"),
        ("nx.json", '{"namedInputs": "default"}', "`namedInputs` is not a JSON object"),
        (
            "nx.json",
            '{"namedInputs": {"production": "src"}}',
            "named input `production` is not a list",
        ),
        (
            "nx.json",
            '{"targetDefaults": {"budgets": true}}',
            "target default `budgets` is not a JSON object",
        ),
        ("package.json", '{"dependencies": []}', "`dependencies` is not a JSON object"),
        (
            "nx.json",
            '{"namedInputs": {"production": ["default", null]}}',
            "named input `production` is not a list of inputs",
        ),
    ],
    ids=[
        "package not an object",
        "devDependencies a list",
        "targetDefaults a list",
        "namedInputs a string",
        "production not a list",
        "budgets default not an object",
        "dependencies a list",
        "a named input entry that is no input",
    ],
)
def test_wiring_refuses_a_manifest_it_cannot_merge_into(tmp_path, name, body, refusal):
    repo = template_repo(tmp_path)
    (repo / name).write_text(body, encoding="utf-8")
    justfile = (repo / "justfile").read_text(encoding="utf-8")
    result = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert result.returncode == 2
    assert refusal in result.stderr
    assert "fix:" in result.stderr
    assert (repo / name).read_text(encoding="utf-8") == body
    assert (repo / "justfile").read_text(encoding="utf-8") == justfile


def test_a_refused_justfile_leaves_every_manifest_untouched(tmp_path):
    # The manifests are valid and would merge; nothing is written while any of
    # the three files is refused.
    repo = tmp_path
    (repo / "justfile").write_text('check tier="affected":\n    echo gate\n')
    (repo / "package.json").write_text('{"private":true}', encoding="utf-8")
    result = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert result.returncode == 2
    assert "no `base` assignment" in result.stderr
    assert (repo / "package.json").read_text(encoding="utf-8") == '{"private":true}'
    assert not (repo / "nx.json").exists()


def test_wiring_keeps_the_workspaces_own_budget_target_defaults(tmp_path):
    repo = template_repo(tmp_path)
    own = {"budgets": {"cache": False, "inputs": ["{projectRoot}/**/*"]}}
    (repo / "nx.json").write_text(json.dumps({"targetDefaults": own}), encoding="utf-8")
    result = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert result.returncode == 0, result.stderr
    defaults = json.loads((repo / "nx.json").read_text(encoding="utf-8"))
    assert defaults["targetDefaults"]["budgets"] == own["budgets"]
    assert defaults["targetDefaults"]["budgets-host"] == {"cache": False}


def test_wiring_leaves_an_already_pinned_manifest_byte_for_byte(tmp_path):
    repo = template_repo(tmp_path)
    compact = (
        f'{{"devDependencies":{{"@onebudgetspec/cli":"{crp.ONEBUDGETSPEC_VERSION}"}}}}'
    )
    (repo / "package.json").write_text(compact, encoding="utf-8")
    result = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert result.returncode == 0, result.stderr
    assert (repo / "package.json").read_text(encoding="utf-8") == compact


def test_wiring_keeps_an_existing_budgets_recipe_and_puts_it_in_check(tmp_path):
    repo = template_repo(tmp_path)
    own = 'budgets tier="affected":\n    echo our own budgets {{tier}}\n'
    justfile = repo / "justfile"
    justfile.write_text(justfile.read_text(encoding="utf-8") + "\n" + own)
    result = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert result.returncode == 0, result.stderr
    text = justfile.read_text(encoding="utf-8")
    assert text.endswith(own)
    assert text.count("budgets tier=") == 1
    assert 'check tier="affected": && (test-e2e tier) (budgets tier)\n' in text


@pytest.mark.parametrize(
    ("edit", "refusal", "repair"),
    [
        (
            lambda text: text + "\nbudgets:\n    echo our own budgets\n",
            "a `budgets` recipe without a `tier` parameter",
            lambda text: text.replace("\nbudgets:", '\nbudgets tier="affected":'),
        ),
        (
            lambda text: (
                text.replace(
                    'check tier="affected": &&', 'check tier="affected": budgets &&'
                )
                + '\nbudgets tier="affected":\n    echo {{tier}}\n'
            ),
            "`check` depends on `budgets` without passing its tier",
            lambda text: text.replace(": budgets &&", ": (budgets tier) &&"),
        ),
    ],
    ids=["budgets without a tier", "check's budgets dependency drops the tier"],
)
def test_wiring_refuses_a_budgets_recipe_check_cannot_pass_its_tier(
    tmp_path, edit, refusal, repair
):
    # `check all` has to reach every domain's budgets; a recipe or dependency
    # that drops the tier would quietly keep the affected tier instead.
    repo = template_repo(tmp_path)
    justfile = repo / "justfile"
    justfile.write_text(edit(justfile.read_text(encoding="utf-8")), encoding="utf-8")
    before = justfile.read_text(encoding="utf-8")
    result = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert result.returncode == 2
    assert refusal in result.stderr
    assert "fix:" in result.stderr
    assert justfile.read_text(encoding="utf-8") == before
    assert not (repo / "package.json").exists()

    # The fix the refusal names lets the wiring finish, and then it is settled.
    justfile.write_text(repair(before), encoding="utf-8")
    retried = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert retried.returncode == 0, retried.stderr
    text = justfile.read_text(encoding="utf-8")
    assert text.count("(budgets tier)") == 1
    assert text.count("\nbudgets tier=") == 1
    assert (repo / "package.json").is_file()
    again = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert again.returncode == 0, again.stderr
    assert "already wired" in again.stderr
    assert justfile.read_text(encoding="utf-8") == text


def test_wiring_keeps_a_comment_on_the_check_header(tmp_path):
    repo = template_repo(tmp_path)
    justfile = repo / "justfile"
    text = justfile.read_text(encoding="utf-8")
    header = 'check tier="affected": && (test-e2e tier)\n'
    justfile.write_text(
        text.replace(header, header.rstrip("\n") + "  # the one gate\n"),
        encoding="utf-8",
    )
    result = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert result.returncode == 0, result.stderr
    wired = justfile.read_text(encoding="utf-8")
    header = 'check tier="affected": && (test-e2e tier) (budgets tier) # the one gate\n'
    assert header in wired, wired


def test_a_repeated_tool_is_wired_once(tmp_path):
    repo = template_repo(tmp_path)
    result = compose_into(
        repo,
        "--tool",
        "onebudgetspec",
        "--tool",
        "onebudgetspec",
        "--wiring",
        str(repo),
    )
    assert result.returncode == 0, result.stderr
    [summary] = [line for line in result.stderr.splitlines() if "wired " in line]
    assert "already wired" not in summary
    assert result.stderr.count("(budgets tier)") == 1
    assert (repo / "justfile").read_text(encoding="utf-8").count("(budgets tier)") == 1
    llmlint = (repo / "llmlint.yml").read_text(encoding="utf-8")
    assert llmlint.count("tools/onebudgetspec.llmlint.yml@1") == 1


def test_wiring_a_directory_that_does_not_exist_is_refused(tmp_path):
    missing = tmp_path / "nowhere"
    result = compose_into(tmp_path, "--tool", "onebudgetspec", "--wiring", str(missing))
    assert result.returncode == 2
    assert f"--wiring {missing} is not a directory" in result.stderr
    assert "fix: pass the root of the repository" in result.stderr
    assert not missing.exists()


def test_wiring_finds_a_capitalised_justfile(tmp_path):
    repo = template_repo(tmp_path)
    (repo / "justfile").rename(repo / "Justfile")
    result = compose_into(repo, "--tool", "onebudgetspec", "--wiring", str(repo))
    assert result.returncode == 0, result.stderr
    assert not (repo / "justfile").exists()
    assert "(budgets tier)" in (repo / "Justfile").read_text(encoding="utf-8")


def test_a_parameter_merely_containing_tier_is_not_the_tier(tmp_path):
    justfile = 'base := "origin/main"\ncheck other_tier="x":\n    echo gate\n'
    (tmp_path / "justfile").write_text(justfile, encoding="utf-8")
    result = compose_into(
        tmp_path, "--tool", "onebudgetspec", "--wiring", str(tmp_path)
    )
    assert result.returncode == 2
    assert "no `check tier=...` recipe" in result.stderr


def test_the_composer_reads_recipes_with_the_baseline_checkers_grammar():
    checker = SKILL_DIR / "scripts" / "check_repo_baseline.py"
    spec_crb = importlib.util.spec_from_file_location("crb_grammar", checker)
    assert spec_crb is not None and spec_crb.loader is not None
    crb = importlib.util.module_from_spec(spec_crb)
    sys.modules[spec_crb.name] = crb
    spec_crb.loader.exec_module(crb)
    assert crp.JUST_RECIPE_RE.pattern == crb.RECIPE_RE.pattern


def test_the_onebudgetspec_release_is_named_once_in_lockstep():
    # The composer's pin, the reference's README link and this repo's own dev pin
    # (the CLI the judged fixtures run through) all name one release.
    version = crp.ONEBUDGETSPEC_VERSION
    reference = (REFS / "tools" / "onebudgetspec.md").read_text(encoding="utf-8")
    tags = set(re.findall(r"onebudgetspec/blob/v([\d.]+)/", reference))
    assert tags == {version}, tags
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.findall(r'"onebudgetspec-cli==([\d.]+)"', pyproject) == [version]
