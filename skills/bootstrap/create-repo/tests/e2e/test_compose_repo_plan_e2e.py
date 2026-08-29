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

import re
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
    assert (
        "### Cross-cutting: Project graph & Nx orchestration  (`project-graph.md`)"
        in doc
    )
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
        "**References composed:** base.md, project-graph.md, shapes/cli.md, "
        "languages/python.md, intersections/python-cli.md, ci.md, llmlint.md" in doc
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


def test_terraform_composes_guidance_checklist_and_both_llmlint_tiers(tmp_path):
    ongoing = tmp_path / "llmlint.yml"
    buildout = tmp_path / "llmlint.buildout.yml"
    result = run(
        "--shape",
        "library",
        "--language",
        "terraform",
        "--llmlint-config",
        str(ongoing),
        "--llmlint-buildout-config",
        str(buildout),
    )
    assert result.returncode == 0
    doc = result.stdout
    assert "### Language: Terraform / Infrastructure as Code" in doc
    assert "languages/terraform.md" in doc
    checklist = doc.split("## Verification checklist", 1)[1]
    assert "Native gate wired" in checklist
    assert "State protected" in checklist
    assert "Declarations reconcile safely" in checklist
    assert "/assets/llmlint/languages/terraform.llmlint.yml@1" in ongoing.read_text(
        encoding="utf-8"
    )
    assert (
        "/assets/llmlint/buildout/languages/terraform.llmlint.yml@1"
        in buildout.read_text(encoding="utf-8")
    )


def test_releasing_flag_pulls_reference():
    doc = run("--shape", "library", "--language", "python", "--releasing").stdout
    assert "### Cross-cutting: Releases & versioning  (`releasing.md`)" in doc
    checklist = doc.split("## Verification checklist", 1)[1]
    assert "PR-title lint is a required check" in checklist


def test_project_graph_composes_for_a_single_deliverable_stack():
    doc = run("--shape", "library", "--language", "python").stdout
    composed = next(line for line in doc.splitlines() if "References composed" in line)
    assert "base.md, project-graph.md," in composed
    assert (
        "### Cross-cutting: Project graph & Nx orchestration  (`project-graph.md`)"
        in doc
    )
    checklist = doc.split("## Verification checklist", 1)[1]
    for item in (
        "The repo has an Nx project graph",
        "Split by test tier and by cost",
        "Expensive work sits behind an unreachable edge",
        "Each deliverable is its own project",
        "Nx and the language workspace both wired",
        "Root commands delegate",
        "Affected-only in CI",
        "Caching never hides a broken clean build",
        "Project boundaries enforced",
        "Instruction layer localized",
        "Scripts stay orchestrator-independent",
    ):
        assert item in checklist, item


def test_monorepo_flag_is_gone():
    result = run("--shape", "library", "--language", "python", "--monorepo")
    assert result.returncode == 2
    assert "unrecognized arguments: --monorepo" in result.stderr
    assert "fix: drop --monorepo" in result.stderr
    # The removed flag alone needs no catalog lookup.
    assert "fix: run --list" not in result.stderr
    assert "--monorepo" not in run("--list").stdout


def test_other_unknown_flags_still_fail_without_the_monorepo_hint():
    result = run("--shape", "library", "--language", "python", "--frobnicate")
    assert result.returncode == 2
    assert "unrecognized arguments: --frobnicate" in result.stderr
    assert "fix: run --list" in result.stderr
    assert "fix: drop --monorepo" not in result.stderr


def test_monorepo_mixed_with_another_unknown_flag_reports_both_fixes():
    result = run(
        "--shape", "library", "--language", "python", "--monorepo", "--frobnicate"
    )
    assert result.returncode == 2
    assert "unrecognized arguments: --monorepo --frobnicate" in result.stderr
    assert "fix: drop --monorepo" in result.stderr
    assert "fix: run --list" in result.stderr


def test_releasing_omitted_by_default():
    # Guard against composition, not stray prose: ci.md's body mentions
    # releasing.md, so check the composed-references line and section headings.
    doc = run("--shape", "library", "--language", "python").stdout
    composed = next(line for line in doc.splitlines() if "References composed" in line)
    assert "releasing.md" not in composed
    assert "(`releasing.md`)" not in doc


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
        "base.md, project-graph.md, shapes/web-app.md, shapes/react.md, "
        "shapes/nextjs.md, languages/typescript.md" in doc
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
    # the schema modeline ships in the wrapper
    assert "llmlint.schema.json" in cfg
    # the harness is NOT pinned here: oneharness.toml (composed alongside) selects
    # it in fallback mode, so a Claude Code session can fall through to claude-code.
    assert "harness:" not in cfg
    assert "agents:" not in cfg
    # an unselected language's fragment is NOT pulled in
    assert "languages/typescript.llmlint.yml" not in cfg
    # the ongoing config carries no buildout-only fragments
    assert "/buildout/" not in cfg
    # stderr records the composition; the plan stays on stdout
    assert "ongoing llmlint config" in result.stderr


def test_llmlint_config_emits_fallback_oneharness_alongside(tmp_path):
    # The pinless llmlint.yml needs oneharness.toml to select a harness, so
    # --llmlint-config emits the fallback config beside it: codex primary,
    # claude-code secondary, per-harness models.
    out = tmp_path / "llmlint.yml"
    result = run("--shape", "cli", "--language", "python", "--llmlint-config", str(out))
    assert result.returncode == 0
    oh = tmp_path / "oneharness.toml"
    assert oh.is_file(), "oneharness.toml should be emitted beside llmlint.yml"
    toml = oh.read_text(encoding="utf-8")
    assert 'run_mode = "fallback"' in toml
    assert 'harnesses = ["codex", "claude-code"]' in toml
    assert 'model = "gpt-5.5"' in toml
    assert 'model = "claude-opus-4-8"' in toml
    assert 'IS_SANDBOX = "1"' in toml
    assert "oneharness fallback config" in result.stderr


def test_oneharness_config_path_override(tmp_path):
    # --oneharness-config relocates the emitted file.
    out = tmp_path / "llmlint.yml"
    oh = tmp_path / "custom-oneharness.toml"
    run(
        "--shape",
        "cli",
        "--language",
        "python",
        "--llmlint-config",
        str(out),
        "--oneharness-config",
        str(oh),
    )
    assert oh.is_file()
    assert not (tmp_path / "oneharness.toml").exists()
    assert 'run_mode = "fallback"' in oh.read_text(encoding="utf-8")


def test_llmlint_ongoing_project_graph_fragment_is_unconditional(tmp_path):
    out = tmp_path / "llmlint.yml"
    run("--shape", "library", "--language", "python", "--llmlint-config", str(out))
    cfg = out.read_text(encoding="utf-8")
    assert "/assets/llmlint/project-graph.llmlint.yml@1" in cfg
    assert "/buildout/project-graph.llmlint.yml" not in cfg


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


def test_llmlint_buildout_releasing_gated_by_flag_project_graph_always(tmp_path):
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
    assert "/buildout/project-graph.llmlint.yml@1" in bare

    run(
        "--shape",
        "library",
        "--language",
        "python",
        "--releasing",
        "--llmlint-buildout-config",
        str(out),
    )
    flagged = out.read_text(encoding="utf-8")
    assert "/buildout/releasing.llmlint.yml@1" in flagged


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


# --- e2e: the staged-gate guidance reaches a composed plan ------------------


def test_plan_carries_staged_gate_guidance_and_its_verification_items(tmp_path):
    # The staged-gate guidance is only useful if it survives composition: an
    # agent reads the plan, never the reference tree.
    out_cfg = tmp_path / "llmlint.yml"
    result = run(
        "--shape",
        "library",
        "--language",
        "python",
        "--releasing",
        "--llmlint-config",
        str(out_cfg),
    )
    assert result.returncode == 0
    doc = result.stdout
    guidance, checklist = doc.split("## Verification checklist", 1)

    # ci.md's staged model: both tiers named, placement derived from the release
    # model, the measurement inputs, and the unconditional external-contact rule.
    assert "## Staged gates: the affected tier and the broader tier" in guidance
    assert "### Gate a given commit once" in guidance
    assert "### Where the broader tier runs, and what decides it" in guidance
    assert "### Measure, then derive your own threshold" in guidance
    assert "### External contact promotes unconditionally" in guidance
    assert "nx affected" in guidance and "run-many" in guidance
    for measured in ("cache hit rate", "affected rate", "p95"):
        assert measured in guidance

    # releasing.md's half: where the release sits relative to the sweep.
    assert "## Where the release sits relative to the broader tier" in guidance
    assert "**A release re-gates nothing.**" in guidance

    # Every verification item the staged model adds, lifted into the checklist.
    for item in (
        "**Two tiers, named and wired.**",
        "**The broader tier runs at exactly one lifecycle point.**",
        "**No commit is gated twice.**",
        "**Thresholds are measured, not inherited.**",
        "**External contact promotes unconditionally.**",
        "**Promotion never weakened coverage.**",
        "**Required set matches the PR-context jobs.**",
        "**The broader tier's placement matches the release driver.**",
        "**The release re-gates nothing.**",
    ):
        assert f"- [ ] {item}" in checklist, item

    # The pre-existing items the staged model reconciles with are still there.
    assert "**CI proves the artifact.**" in checklist
    assert "**Fully automated, no manual deploy step.**" in checklist

    # The judge tier that enforces the same guidance is wired in as a plugin.
    assert "/assets/llmlint/releasing.llmlint.yml@1" in out_cfg.read_text(
        encoding="utf-8"
    )


def test_plan_without_releasing_keeps_ci_tiers_and_drops_the_release_half(tmp_path):
    # ci.md is always composed, so the tiers travel with every plan — but a repo
    # that ships no versioned artifact must not be handed release-sweep items it
    # has no pipeline for.
    out_cfg = tmp_path / "llmlint.yml"
    doc = run(
        "--shape",
        "library",
        "--language",
        "python",
        "--llmlint-config",
        str(out_cfg),
    ).stdout

    assert "## Staged gates: the affected tier and the broader tier" in doc
    assert "- [ ] **The broader tier runs at exactly one lifecycle point.**" in doc

    assert "## Where the release sits relative to the broader tier" not in doc
    assert "**The broader tier's placement matches the release driver.**" not in doc
    assert "**The release re-gates nothing.**" not in doc
    assert "/assets/llmlint/releasing.llmlint.yml" not in out_cfg.read_text(
        encoding="utf-8"
    )


# --- e2e: the judge rules the composed configs pull in ----------------------
#
# The emitted config is a thin wrapper: every rule reaches the repo through a
# pinned plugin URL. So "what the config carries" is the union of the rules in
# the fragments it wires — resolve each URL back to the fragment it names and
# read the rule names out of it. A fragment that stops being wired, or a rule
# that stops being defined, both fail here.

_PLUGIN_URL_RE = re.compile(r'^\s*-\s+"(\S+)"\s*$')
_RULE_NAME_RE = re.compile(r"^\s*-\s+name:\s*(\S+)\s*$")
_FRAGMENT_ROOT = "/assets/llmlint/"


def wired_rule_names(config_path: Path) -> set[str]:
    """Every judge rule an emitted llmlint config actually pulls in."""
    names: set[str] = set()
    for line in config_path.read_text(encoding="utf-8").splitlines():
        match = _PLUGIN_URL_RE.match(line)
        if match is None or _FRAGMENT_ROOT not in match.group(1):
            continue  # the config-lint plugin ships with llmlint, not this skill
        url = match.group(1).split("@")[0]
        frag_rel = url.split(_FRAGMENT_ROOT, 1)[1]
        fragment = SKILL_DIR / "assets" / "llmlint" / frag_rel
        assert fragment.is_file(), f"config wires a missing fragment: {frag_rel}"
        for frag_line in fragment.read_text(encoding="utf-8").splitlines():
            rule = _RULE_NAME_RE.match(frag_line)
            if rule is not None:
                names.add(rule.group(1))
    return names


def test_composed_configs_carry_the_staged_gate_rules(tmp_path):
    # The judge half of the staged model: the buildout tier checks the wiring
    # once at creation, the ongoing tier keeps later PRs from re-introducing a
    # second sweep.
    ongoing = tmp_path / "llmlint.yml"
    buildout = tmp_path / "llmlint.buildout.yml"
    result = run(
        "--shape",
        "library",
        "--language",
        "python",
        "--releasing",
        "--llmlint-config",
        str(ongoing),
        "--llmlint-buildout-config",
        str(buildout),
    )
    assert result.returncode == 0

    buildout_rules = wired_rule_names(buildout)
    assert "pr_gate_runs_the_affected_tier" in buildout_rules
    assert "broader_sweep_runs_at_exactly_one_lifecycle_point" in buildout_rules
    assert "required_checks_name_only_pr_context_jobs" in buildout_rules
    assert "broader_sweep_placement_matches_the_release_driver" in buildout_rules

    ongoing_rules = wired_rule_names(ongoing)
    assert "external_service_suite_stays_out_of_the_affected_tier" in ongoing_rules
    assert "no_second_broader_sweep_over_an_already_gated_commit" in ongoing_rules
    assert "release_does_not_regate_an_already_swept_commit" in ongoing_rules

    # The tiers are distinct: a one-time structural check never becomes an
    # ongoing PR rule, and vice versa.
    assert "pr_gate_runs_the_affected_tier" not in ongoing_rules
    assert "no_second_broader_sweep_over_an_already_gated_commit" not in buildout_rules


def test_composed_configs_without_releasing_drop_the_release_sweep_rules(tmp_path):
    # The release-tied rules ride on the releasing fragments, so a repo that
    # ships no versioned artifact must not be judged against them — while the
    # always-composed ci rules still travel.
    ongoing = tmp_path / "llmlint.yml"
    buildout = tmp_path / "llmlint.buildout.yml"
    result = run(
        "--shape",
        "library",
        "--language",
        "python",
        "--llmlint-config",
        str(ongoing),
        "--llmlint-buildout-config",
        str(buildout),
    )
    assert result.returncode == 0

    buildout_rules = wired_rule_names(buildout)
    assert "pr_gate_runs_the_affected_tier" in buildout_rules
    assert "broader_sweep_placement_matches_the_release_driver" not in buildout_rules

    ongoing_rules = wired_rule_names(ongoing)
    assert "no_second_broader_sweep_over_an_already_gated_commit" in ongoing_rules
    assert "release_does_not_regate_an_already_swept_commit" not in ongoing_rules
