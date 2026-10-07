"""The baseline checker over the shapes real repositories already use.

Each fixture reproduces, inside a temporary repository, a shape found in a
repository audited against the checker — a justfile reaching Nx through a
wrapper script or a variable, a Poetry manifest, a dogfooding action workflow,
a hook pointing outside `scripts/` — that the checker used to misreport. Each
false positive is pinned beside the genuine failure the same check must keep
reporting, so a fix that only silences the check fails here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_check_repo_baseline import (
    crb,
    levels,
    make_repo,
    typed_packaging_errors,
    write_package,
)


def surface(check: str, *, test: str, lint: str, preamble: str = "") -> str:
    """A full command surface whose gate recipes are written as given.

    ``check`` is the whole recipe (header and indented body); ``test`` and
    ``lint`` are body lines. The remaining required and llmlint recipes are
    filled with inert bodies, so only the gate recipes decide the findings.
    """
    return (
        f"{preamble}"
        "bootstrap:\n    @echo hi\n\n"
        f"{check}\n\n"
        f"test:\n    {test}\n\n"
        "test-e2e:\n    @echo e2e  # --cov-fail-under=95\n\n"
        f"lint:\n    {lint}\n\n"
        "format:\n    @echo f\n\n"
        "upgrade:\n    @just check\n\n"
        "lint-llm:\n    @echo lint-llm\n\n"
        "lint-llm-diff:\n    @echo lint-llm-diff\n\n"
        "lint-llm-validate:\n    @echo lint-llm-validate\n"
    )


def graph_warnings(findings) -> list[str]:
    return [m for m in levels(findings, "WARN") if "project graph" in m]


def check_skips_test(findings) -> list[str]:
    return [m for m in levels(findings, "ERROR") if "does not run `test`" in m]


# llmlint: ignore[comments_earn_their_place] this module groups its tests behind section banners, one per checker fix; without them the fleet shapes of unrelated checks read as one run-on section.
# --- Nx delegation ---------------------------------------------------------

# Each justfile is the gate of one audited repository, reduced to the lines that
# decide the project-graph and check-runs-test findings.
FLEET_GATES = {
    # onepipeline: the gate runs Nx through a repository wrapper script.
    "wrapper-script": surface(
        "check: fmt-check lint test doc\n    @bash scripts/nx.sh run-many -t check",
        test="@bash scripts/nx.sh run-many -t test",
        lint="@bash scripts/nx.sh run-many -t lint",
    )
    + "\nfmt-check:\n    @bash scripts/nx.sh run-many -t format-check\n"
    + "\ndoc:\n    @echo doc\n",
    # onepipeline's affected entry point: a wrapper named nx-<something>.sh.
    "affected-wrapper-script": surface(
        "check:\n    @bash scripts/nx-affected.sh -t lint test",
        test="@bash scripts/nx-affected.sh -t test",
        lint="@bash scripts/nx-affected.sh -t lint",
    ),
    # skilltest: a justfile variable holding a package-runner invocation.
    "runner-variable": surface(
        "check: test test-e2e",
        test="{{nx}} affected -t test",
        lint="{{nx}} affected -t lint",
        preamble='nx := "pnpm exec nx"\n\n',
    ),
    # onetaskgraph: a variable holding the wrapper script, behind a quiet `@`.
    "script-variable": surface(
        "check: format-check lint test",
        test="@{{nx}} affected -t test $(bash scripts/live-lane-selection.sh --nx-exclusions)",
        lint="@{{nx}} affected -t lint",
        preamble='nx := "./scripts/nx.sh"\n\n',
    )
    + "\nformat-check:\n    @{{nx}} affected -t format-check\n",
    # vibe-stl: an upper-case variable, and a subcommand slot holding a justfile
    # expression; `check` has no `test` dependency, only the `-t test` fan-out.
    "expression-subcommand": surface(
        'check BASE="":\n'
        "    uv run python -m vibe_stl_tooling.sync_check\n"
        '    {{NX}} {{ if BASE == "" { "run-many" } else { "affected --base=" + BASE } }}'
        " {{GATE_EXCLUDE}} -t lint\n"
        '    {{NX}} {{ if BASE == "" { "run-many" } else { "affected --base=" + BASE } }}'
        " {{GATE_EXCLUDE}} {{NX_SERIAL}} -t test\n"
        '    {{NX}} {{ if BASE == "" { "run-many" } else { "affected --base=" + BASE } }}'
        " {{GATE_EXCLUDE}} -t build",
        test="{{NX}} run-many {{NX_SERIAL}} -t test",
        lint="{{NX}} run-many -t lint",
        preamble=(
            'NX := "npx nx"\nNX_SERIAL := "--parallel=1"\n'
            'STUDY_PROJECT := "study"\nGATE_EXCLUDE := "--exclude=" + STUDY_PROJECT\n\n'
        ),
    ),
    # ai-orchestrator: a subcommand slot holding a shell array expansion, with
    # several invocations chained into one long line.
    "shell-expansion-subcommand": surface(
        "check:\n"
        "    @selection=$(./scripts/nx-selection.sh) || exit 1; read -ra selected"
        ' <<<"$selection"; failed=(); ./scripts/nx.sh "${selected[@]}" -t'
        " format-check,lint,typecheck,test,test-docs,test-recipes 2>&1 |"
        ' redact_secrets >>"$log" || failed+=("the diff selection");'
        " ./scripts/nx.sh run-many -t test-checkouts,coverage 2>&1 |"
        ' redact_secrets >>"$log" || failed+=("coverage")',
        test="./scripts/nx.sh run-many -t test,test-docs,coverage {{nx_args}}",
        lint="./scripts/nx.sh affected -t lint",
    ),
    # nick-derobertis-site: two invocations on one line, `test` only in the second.
    "second-invocation-on-a-line": surface(
        "check: lint-workflows\n"
        '    @base="${NX_BASE:-HEAD~1}"; log=$(mktemp); pnpm exec biome check . >"$log"'
        ' 2>&1 && CI=1 pnpm exec nx affected -t lint --base="$base" --parallel=3'
        ' >>"$log" 2>&1 && CI=1 pnpm exec nx affected -t typecheck,test,build,prerender'
        ' --base="$base" --parallel=3 >>"$log" 2>&1 || { cat "$log" >&2; exit 1; }',
        test='pnpm exec nx affected -t test,e2e --base="$base"',
        lint="pnpm exec nx run-many -t lint --all",
    )
    + "\nlint-workflows:\n    @actionlint\n",
}


@pytest.mark.parametrize("shape", sorted(FLEET_GATES))
def test_a_gate_reaching_nx_however_it_is_spelled_delegates_and_runs_test(
    tmp_path, shape
):
    findings = crb.audit(make_repo(tmp_path, justfile=FLEET_GATES[shape]))
    assert not graph_warnings(findings), graph_warnings(findings)
    assert not check_skips_test(findings), levels(findings, "ERROR")
    assert not crb.has_errors(findings), levels(findings, "ERROR")


def test_a_gate_running_no_nx_still_warns_whatever_its_variables_hold(tmp_path):
    # A variable expands to what it holds, and a runner that is not Nx — nor a
    # script that merely has `nx` inside its name — is no delegation.
    justfile = surface(
        "check: lint test",
        test="{{py}} pytest -t unit",
        lint="bash scripts/onyx.sh run-many -t lint && bash scripts/nxfoo.sh run -t lint",
        preamble='py := "uv run"\n\n',
    )
    findings = crb.audit(make_repo(tmp_path, justfile=justfile))
    warnings = graph_warnings(findings)
    assert len(warnings) == 1 and "bypass" in warnings[0], warnings
    assert not crb.has_errors(findings), levels(findings, "ERROR")


def test_an_nx_invocation_with_no_fan_out_is_no_delegation(tmp_path):
    # `nx graph` or `nx show projects` runs Nx without running any target.
    justfile = surface(
        "check: lint test\n    pnpm exec nx graph --file=graph.html",
        test="./scripts/nx.sh show projects",
        lint="uv run ruff check .",
    )
    warnings = graph_warnings(crb.audit(make_repo(tmp_path, justfile=justfile)))
    assert len(warnings) == 1 and "bypass" in warnings[0], warnings


@pytest.mark.parametrize(
    "check",
    [
        # Both invocations on the line fan out, and neither names `test`.
        "check: lint-workflows\n"
        "    CI=1 pnpm exec nx affected -t lint && CI=1 pnpm exec nx affected"
        " -t typecheck,build && echo test",
        # An expression in the subcommand slot, with a target list lacking `test`.
        'check BASE="":\n'
        '    {{NX}} {{ if BASE == "" { "run-many" } else { "affected" } }} -t lint build',
        # A wrapper script fed a shell expansion; `test` sits only after a redirect.
        "check:\n"
        '    ./scripts/nx.sh "${selected[@]}" -t lint,typecheck 2>&1 | tee test',
    ],
)
def test_a_check_whose_fan_out_omits_test_still_errors(tmp_path, check):
    justfile = (
        surface(
            check,
            test="{{NX}} affected -t test",
            lint="{{NX}} affected -t lint",
            preamble='NX := "npx nx"\n\n',
        )
        + "\nlint-workflows:\n    @actionlint\n"
    )
    findings = crb.audit(make_repo(tmp_path, justfile=justfile))
    assert check_skips_test(findings), levels(findings, "ERROR")
    assert not graph_warnings(findings), graph_warnings(findings)


def test_every_invocation_on_a_line_contributes_its_targets():
    line = (
        "CI=1 pnpm exec nx affected -t lint --base=x >>log 2>&1 && "
        "CI=1 pnpm exec nx affected -t typecheck,test --base=x; "
        './scripts/nx.sh "${selected[@]}" -t a,b 2>&1 | tee out'
    )
    assert crb.orchestrator_targets(line) == {"lint", "typecheck", "test", "a", "b"}


# llmlint: ignore[comments_earn_their_place] a section banner, as above: it names where the recipe-parsing fleet shapes begin.
# --- quiet recipes ---------------------------------------------------------


def test_a_quiet_recipe_header_is_a_recipe(tmp_path):
    # nick-derobertis-site declares the diff-scoped llmlint recipe quiet.
    justfile = surface(
        "check: lint test",
        test="bunx nx affected -t test",
        lint="bunx nx affected -t lint",
    ).replace(
        "lint-llm-diff:\n    @echo lint-llm-diff\n",
        "@lint-llm-diff *args:\n    llmlint --diff --diff-base origin/main {{args}}\n",
    )
    assert "@lint-llm-diff *args:" in justfile
    assert "lint-llm-diff" in crb.parse_just_recipes(justfile)
    assert "lint-llm-diff" in crb.parse_just_recipe_details(justfile)
    findings = crb.audit(make_repo(tmp_path, justfile=justfile))
    assert not any("lint-llm-diff" in m for m in levels(findings, "ERROR")), levels(
        findings, "ERROR"
    )


# llmlint: ignore[comments_earn_their_place] a section banner, as above: it names where the typed-packaging fleet shapes begin.
# --- typed packaging -------------------------------------------------------


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def write_manifest(
    repo: Path, project_dir: str, manifest: str, *, marker: bool, package: str
) -> Path:
    """Write ``manifest`` verbatim at ``project_dir`` beside package ``package``."""
    pkg = repo / project_dir / package
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    if marker:
        (pkg / "py.typed").write_text("", encoding="utf-8")
    path = repo / project_dir / "pyproject.toml"
    path.write_text(manifest, encoding="utf-8")
    return path


def untyped_manifest(name_line: str, table: str = "project") -> str:
    """A qualifying uv_build manifest with no classifier, naming itself as given."""
    return f'[{table}]\n{name_line}\nversion = "0.1.0"\n\n[build-system]\nbuild-backend = "uv_build"\n'


def test_a_manifest_git_ignores_is_not_audited_until_it_is_tracked(tmp_path):
    # ai-orchestrator's ignored `runs/` tree holds scratch checkouts whose
    # manifests are not the repo's; the same manifest tracked is the repo's.
    repo = make_repo(tmp_path)
    git(repo, "init", "-q")
    (repo / ".gitignore").write_text("runs/\nscratch/\n", encoding="utf-8")
    manifest = write_package(
        repo, marker=False, classifiers=(), project_dir="runs/plan-1/checkout"
    )
    assert not typed_packaging_errors(crb.audit(repo))

    git(repo, "add", "-f", manifest.relative_to(repo).as_posix())
    errors = typed_packaging_errors(crb.audit(repo))
    assert [e.message.split(" ")[0] for e in errors] == [
        "runs/plan-1/checkout/pyproject.toml"
    ]


def test_outside_a_git_work_tree_every_manifest_is_walked(tmp_path):
    # With no git to ask, an ignore file is just a file: nothing is left out.
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text("runs/\n", encoding="utf-8")
    write_package(
        repo, marker=False, classifiers=(), project_dir="runs/plan-1/checkout"
    )
    assert len(typed_packaging_errors(crb.audit(repo))) == 1


def test_manifests_under_test_directories_are_not_audited(tmp_path):
    # crozier's generator goldens: `tests/fixtures/<case>/expected/pyproject.toml`.
    repo = make_repo(tmp_path)
    for project_dir in ("tests/fixtures/zoonk/expected", "pkg/test/golden"):
        write_package(repo, marker=False, classifiers=(), project_dir=project_dir)
    write_package(repo, marker=False, classifiers=(), project_dir="packages/real")
    errors = typed_packaging_errors(crb.audit(repo))
    assert [e.message.split(" ")[0] for e in errors] == ["packages/real/pyproject.toml"]


@pytest.mark.parametrize(
    "name",
    [
        "@@CROZIER_SDK_NAME@@",  # crozier's scaffolding template
        "{{ project_name }}",  # a Jinja/cookiecutter placeholder
        "${NAME}",  # a shell-style placeholder
        "<name>",  # an angle-bracket placeholder
        "my package",  # a space
        "-demo",  # a leading separator
        "demo-",  # a trailing separator
        ".demo",
        "demo.",
        "_demo",
        "démo",  # not ASCII
    ],
)
def test_a_manifest_whose_name_is_no_distribution_name_is_not_audited(tmp_path, name):
    repo = make_repo(tmp_path)
    write_manifest(
        repo,
        "assets/scaffolding",
        untyped_manifest(f'name = "{name}"'),
        marker=False,
        package="sdk",
    )
    assert not typed_packaging_errors(crb.audit(repo))


@pytest.mark.parametrize(
    "name", ["a", "7", "MyPackage", "my.pkg_name-x", "Zope.Interface", "a-b_c.d9"]
)
def test_a_manifest_with_a_valid_name_is_still_audited(tmp_path, name):
    repo = make_repo(tmp_path)
    write_manifest(
        repo,
        "packages/demo",
        untyped_manifest(f'name = "{name}"'),
        marker=False,
        package="demo",
    )
    assert len(typed_packaging_errors(crb.audit(repo))) == 1


def test_the_poetry_name_decides_where_project_declares_none(tmp_path):
    repo = make_repo(tmp_path)
    write_manifest(
        repo,
        "packages/template",
        untyped_manifest('name = "@@SDK_NAME@@"', table="tool.poetry"),
        marker=False,
        package="sdk",
    )
    write_manifest(
        repo,
        "packages/real",
        untyped_manifest('name = "real-sdk"', table="tool.poetry"),
        marker=False,
        package="real",
    )
    errors = typed_packaging_errors(crb.audit(repo))
    assert [e.message.split(" ")[0] for e in errors] == ["packages/real/pyproject.toml"]


def test_a_manifest_declaring_no_name_is_still_audited(tmp_path):
    repo = make_repo(tmp_path)
    write_manifest(
        repo,
        "packages/anon",
        untyped_manifest('description = "x"'),
        marker=False,
        package="anon",
    )
    assert len(typed_packaging_errors(crb.audit(repo))) == 1


# crozier's generated SDK manifest: the name in `[project]`, the classifiers only
# under `[tool.poetry]`, built by poetry-core.
POETRY_MANIFEST = """\
[project]
name = "fern-sdk"
dynamic = ["version"]

[tool.poetry]
version = "0.0.1"
classifiers = [
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3",
    {classifier}
]
packages = [{{ include = "fern", from = "src" }}]

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
"""


def test_a_poetry_manifest_typed_through_tool_poetry_passes(tmp_path):
    repo = make_repo(tmp_path)
    write_manifest(
        repo,
        "sdks/fern",
        POETRY_MANIFEST.format(classifier='"Typing :: Typed",'),
        marker=True,
        package="src/fern",
    )
    findings = crb.audit(repo)
    assert not typed_packaging_errors(findings), levels(findings, "ERROR")
    assert any("typed packaging: 1 publishing" in m for m in levels(findings, "OK"))


def test_a_poetry_manifest_kept_private_through_tool_poetry_is_exempt(tmp_path):
    repo = make_repo(tmp_path)
    write_manifest(
        repo,
        "sdks/fern",
        POETRY_MANIFEST.format(classifier='"Private :: Do Not Upload",'),
        marker=False,
        package="src/fern",
    )
    assert not typed_packaging_errors(crb.audit(repo))


def test_a_poetry_manifest_without_the_classifier_still_errors(tmp_path):
    repo = make_repo(tmp_path)
    write_manifest(
        repo,
        "sdks/fern",
        POETRY_MANIFEST.format(classifier=""),
        marker=True,
        package="src/fern",
    )
    errors = typed_packaging_errors(crb.audit(repo))
    assert len(errors) == 1 and "Typing :: Typed" in errors[0].message


def test_an_unpublished_workspace_member_owes_a_stated_exemption(tmp_path):
    # vibe-stl's tooling member is never uploaded, but says so only in prose: the
    # checker reads the stated exemptions, nothing else, so it is still held.
    repo = make_repo(tmp_path)
    (repo / "README.md").write_text(
        "packages/tooling is never published.\n", encoding="utf-8"
    )
    write_package(
        repo,
        backend="uv_build",
        marker=False,
        classifiers=(),
        project_dir="packages/tooling",
    )
    errors = typed_packaging_errors(crb.audit(repo))
    assert [e.message.split(" ")[0] for e in errors] == [
        "packages/tooling/pyproject.toml"
    ]


# llmlint: ignore[comments_earn_their_place] a section banner, as above: it names where the notignored fleet shapes begin.
# --- notignored ------------------------------------------------------------

# notignored's tag-triggered release workflow, which names the action's
# consumption ref only in a comment above its `major-tag` job.
RELEASE_WORKFLOW_MENTIONING_THE_ACTION = """\
name: release
on:
  push:
    tags: ["v*"]
permissions: {}
jobs:
  # The GitHub Action's consumption ref. `uses: nickderobertis/notignored@v0` is
  # what the README tells consumers to write.
  major-tag:
    runs-on: ubuntu-latest
    steps:
      - run: git tag -f v0
"""

# notignored's dogfood workflow: the action built from the branch (`uses: ./`).
DOGFOOD_WORKFLOW = """\
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
          # --diff resolves the base branch, which a shallow checkout omits.
          fetch-depth: 0
      - uses: ./
        with:
          version: local
"""

NOTIGNORED_ACTION_MANIFEST = (
    "name: notignored\ndescription: Comment on a pull request with its suppressions.\n"
)


def notignored_repo(tmp_path: Path, dogfood: str, action: str | None) -> Path:
    repo = make_repo(tmp_path, notignored=dogfood)
    workflows = repo / ".github" / "workflows"
    (workflows / "release.yml").write_text(
        RELEASE_WORKFLOW_MENTIONING_THE_ACTION, encoding="utf-8"
    )
    if action is not None:
        (repo / "action.yml").write_text(action, encoding="utf-8")
    return repo


def test_notignored_dogfooding_its_own_action_passes(tmp_path):
    findings = crb.check_notignored(
        notignored_repo(tmp_path, DOGFOOD_WORKFLOW, NOTIGNORED_ACTION_MANIFEST)
    )
    assert levels(findings, "OK") == [
        ".github/workflows/notignored.yml posts the suppressions review comment on "
        "pull requests"
    ], findings
    assert not any("release.yml" in f.message for f in findings)


@pytest.mark.parametrize(
    "action",
    [None, "name: some-other-action\ndescription: notignored-alike\n"],
    ids=["no-action-manifest", "another-action"],
)
def test_a_local_action_elsewhere_is_not_the_notignored_action(tmp_path, action):
    # Outside notignored's own repository `uses: ./` runs some other action, and
    # the comment in release.yml runs nothing: no workflow posts the comment.
    findings = crb.check_notignored(notignored_repo(tmp_path, DOGFOOD_WORKFLOW, action))
    assert levels(findings, "ERROR") == [
        "no workflow runs the notignored suppressions review comment"
    ]


def test_the_dogfood_workflow_is_still_held_to_every_property(tmp_path):
    # Choosing the workflow is all `uses: ./` changes: a shallow, unguarded,
    # push-only dogfood workflow without the comment permission still fails.
    broken = (
        DOGFOOD_WORKFLOW.replace("  pull_request:", "  push:")
        .replace("  pull-requests: write\n", "")
        .replace("          fetch-depth: 0\n", "")
        .replace(
            "    if: github.event.pull_request.head.repo.full_name == github.repository\n",
            "",
        )
    )
    findings = crb.check_notignored(
        notignored_repo(tmp_path, broken, NOTIGNORED_ACTION_MANIFEST)
    )
    errors = levels(findings, "ERROR")
    assert len(errors) == 4, errors
    assert all(m.startswith(".github/workflows/notignored.yml ") for m in errors)


def test_a_property_named_only_in_a_comment_is_not_met(tmp_path):
    # Comment text configures nothing, for the properties as for the action.
    commented = make_repo(tmp_path).joinpath(".github/workflows/notignored.yml")
    commented.write_text(
        commented.read_text(encoding="utf-8").replace(
            "fetch-depth: 0", "fetch-depth: 1  # was fetch-depth: 0"
        ),
        encoding="utf-8",
    )
    errors = levels(crb.check_notignored(commented.parents[2]), "ERROR")
    assert errors == [
        ".github/workflows/notignored.yml checks out shallowly, so there is no base "
        "branch to diff against and the comment reports nothing"
    ]


# llmlint: ignore[comments_earn_their_place] a section banner, as above: it names where the session-provisioner fleet shapes begin.
# --- session provisioner outside scripts/ ----------------------------------

# nick-derobertis-site's hook: the provisioner kept under scripts/ci/, with a
# trailing shell comment on the command.
CI_DIR_HOOK_SETTINGS = (
    '{"hooks": {"SessionStart": [{"matcher": "startup|resume", "hooks": '
    '[{"type": "command", "command": "./scripts/ci/session-setup.sh '
    '# provisions just, then hands off to setup-llmlint.sh"}]}]}, '
    '"permissions": {"allow": []}}'
)
PROVISIONS_JUST = '#!/usr/bin/env bash\nuv tool install rust-just\nbash "$(dirname "$0")/setup-llmlint.sh"\n'


def ci_dir_repo(
    tmp_path: Path, *, session_setup: str | None, setup_llmlint: bool
) -> Path:
    repo = make_repo(tmp_path, settings=CI_DIR_HOOK_SETTINGS)
    (repo / "scripts" / "setup-llmlint.sh").unlink()  # only the scripts/ci/ copies
    ci = repo / "scripts" / "ci"
    ci.mkdir(parents=True)
    if session_setup is not None:
        (ci / "session-setup.sh").write_text(session_setup, encoding="utf-8")
    if setup_llmlint:
        (ci / "setup-llmlint.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    return repo


def test_a_hook_naming_a_provisioner_outside_scripts_resolves_to_it(tmp_path):
    repo = ci_dir_repo(tmp_path, session_setup=PROVISIONS_JUST, setup_llmlint=True)
    findings = crb.audit(repo)
    assert not crb.has_errors(findings), levels(findings, "ERROR")
    assert "session-setup.sh provisions the toolchain (`just`) and is wired" in levels(
        findings, "OK"
    )
    assert any("llmlint tier configured" in m for m in levels(findings, "OK"))


def test_the_provisioner_the_hook_names_must_still_provision_just(tmp_path):
    repo = ci_dir_repo(
        tmp_path, session_setup="#!/usr/bin/env bash\n", setup_llmlint=True
    )
    assert levels(crb.check_session_setup(repo), "ERROR") == [
        "session-setup.sh does not provision `just` (the command-surface entry point)"
    ]


def test_a_hook_naming_a_missing_provisioner_still_errors(tmp_path):
    # A scripts/session-setup.sh elsewhere does not stand in for the one the hook
    # runs: a session would still be provisioned by nothing.
    repo = ci_dir_repo(tmp_path, session_setup=None, setup_llmlint=True)
    (repo / "scripts" / "session-setup.sh").write_text(
        PROVISIONS_JUST, encoding="utf-8"
    )
    assert levels(crb.check_session_setup(repo), "ERROR") == [
        "SessionStart hook runs session-setup.sh but the script is missing"
    ]


def test_without_setup_llmlint_beside_the_provisioner_the_install_is_missing(tmp_path):
    repo = ci_dir_repo(tmp_path, session_setup=PROVISIONS_JUST, setup_llmlint=False)
    assert "no scripts/setup-llmlint.sh (the automated llmlint toolchain install)" in (
        levels(crb.audit(repo), "ERROR")
    )


def test_a_hook_path_through_the_project_dir_variable_resolves(tmp_path):
    # The shape this repository's own hook takes.
    settings = CI_DIR_HOOK_SETTINGS.replace(
        "./scripts/ci/session-setup.sh",
        'bash \\"$CLAUDE_PROJECT_DIR/scripts/ci/session-setup.sh\\"',
    )
    repo = ci_dir_repo(tmp_path, session_setup=PROVISIONS_JUST, setup_llmlint=True)
    (repo / ".claude" / "settings.json").write_text(settings, encoding="utf-8")
    assert (
        crb.find_session_setup_script(repo)
        == (repo / "scripts" / "ci" / "session-setup.sh").resolve()
    )
    assert not crb.has_errors(crb.audit(repo))


# llmlint: ignore[comments_earn_their_place] a section banner, as above: it names where the oneharness fleet shapes begin.
# --- oneharness extends chains ---------------------------------------------

# ai-orchestrator's three-file chain: the root config names the harnesses as
# variants, the dispatch layer adds nothing the check reads, and the identities
# parent sets the run mode.
CHAIN = {
    "oneharness.toml": (
        'extends = "oneharness.dispatch.toml"\n'
        'harnesses = ["claude-code:alternate", "claude-code:alternate2", "codex:primary",'
        ' "codex:alternate", "claude-code:primary-backup", "claude-code:primary"]\n'
    ),
    "oneharness.dispatch.toml": (
        'extends = "oneharness.identities.toml"\n\n[harness.codex]\ntimeout = 600\n'
    ),
    "oneharness.identities.toml": (
        'run_mode = "fallback"\n\n[harness.claude-code]\nmodel = "claude-opus-4-8"\n'
    ),
}


def oneharness_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = make_repo(tmp_path, oneharness=False)
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    return repo


def llmlint_errors(repo: Path) -> list[str]:
    return levels(crb.check_llmlint(repo), "ERROR")


def test_a_fallback_mode_inherited_through_extends_passes(tmp_path):
    assert llmlint_errors(oneharness_repo(tmp_path, CHAIN)) == []


def test_extends_resolves_against_the_declaring_files_directory(tmp_path):
    files = {
        "oneharness.toml": CHAIN["oneharness.toml"].replace(
            '"oneharness.dispatch.toml"', '"config/dispatch.toml"'
        ),
        "config/dispatch.toml": 'extends = "identities.toml"\n',
        "config/identities.toml": 'run_mode = "fallback"\n',
    }
    assert llmlint_errors(oneharness_repo(tmp_path, files)) == []


def test_a_child_overriding_the_run_mode_is_not_fallback(tmp_path):
    files = {**CHAIN}
    files["oneharness.toml"] += 'run_mode = "parallel"\n'
    assert llmlint_errors(oneharness_repo(tmp_path, files)) == [
        "oneharness.toml is not in fallback mode with a harness list"
    ]


def test_a_chain_setting_no_run_mode_is_not_fallback(tmp_path):
    files = {**CHAIN, "oneharness.identities.toml": "# identities only\n"}
    assert llmlint_errors(oneharness_repo(tmp_path, files)) == [
        "oneharness.toml is not in fallback mode with a harness list"
    ]


def test_variants_of_other_harnesses_are_no_claude_code_target(tmp_path):
    files = {
        **CHAIN,
        "oneharness.toml": 'extends = "oneharness.dispatch.toml"\n'
        'harnesses = ["codex:primary", "goose:alternate", "claude-codex:primary"]\n',
    }
    errors = llmlint_errors(oneharness_repo(tmp_path, files))
    assert len(errors) == 1 and "has no `claude-code` target" in errors[0], errors


def test_a_missing_parent_is_an_error_naming_the_file(tmp_path):
    files = {k: v for k, v in CHAIN.items() if k != "oneharness.identities.toml"}
    assert llmlint_errors(oneharness_repo(tmp_path, files)) == [
        "oneharness.toml cannot be resolved: oneharness.dispatch.toml extends "
        "'oneharness.identities.toml', which does not exist"
    ]


def test_an_extends_cycle_is_an_error_naming_the_file(tmp_path):
    files = {
        **CHAIN,
        "oneharness.identities.toml": 'extends = "oneharness.toml"\nrun_mode = "fallback"\n',
    }
    assert llmlint_errors(oneharness_repo(tmp_path, files)) == [
        "oneharness.toml cannot be resolved: the `extends` chain of oneharness.toml "
        "returns to oneharness.toml, a cycle"
    ]


def test_an_unparseable_parent_is_an_error_naming_the_file(tmp_path):
    files = {**CHAIN, "oneharness.identities.toml": 'run_mode = "fallback\n'}
    errors = llmlint_errors(oneharness_repo(tmp_path, files))
    assert len(errors) == 1, errors
    assert errors[0].startswith(
        "oneharness.toml cannot be resolved: oneharness.identities.toml does not parse"
    )


# llmlint: ignore[comments_earn_their_place] a section banner, as above: it names where the composition-section fleet shapes begin.
# --- composition placeholders ----------------------------------------------

SKILL_DIR = Path(crb.__file__).resolve().parents[1]

# nick-derobertis-site's composition section: literal path and URL templates in
# code spans (one wrapping across lines), and a maintainer's HTML comment.
LITERAL_TEMPLATES_AGENTS = """\
# AGENTS

## Stack and composition

- References composed: `shapes/web-app.md` + `languages/typescript.md` + `ci.md`.
Each lane is pinned to its own `apps/<app>/visual/baseline/x86_64.json` manifest,
equivalent to `screencomp classify --include project=<app>` scoping. Galleries
publish at `https://example.github.io/site-visual-docs/<project>/x86_64/` and
previews at `https://example.github.io/site-visual-docs/pr-<number>/<project>/x86_64/`;
the content-store branch holds the `apps/<app>/
` subtree only, as ``nx affected --with-target <target>`` selects it.
<!-- keep <project> paths in step with scripts/visual/verify.mjs -->

## Workflow

Run `just check`.
"""


def test_angle_text_in_code_spans_and_comments_is_no_placeholder(tmp_path):
    findings = crb.check_composition(
        make_repo(tmp_path, composition=LITERAL_TEMPLATES_AGENTS)
    )
    assert levels(findings, "OK") == ["AGENTS.md records the reference composition"]


def test_angle_text_in_the_prose_beside_a_code_span_is_still_a_placeholder(tmp_path):
    agents = LITERAL_TEMPLATES_AGENTS.replace(
        "- References composed:",
        "- Product shape: <cli / web-app> and `<kept>`\n- References composed:",
    )
    findings = crb.check_composition(make_repo(tmp_path, composition=agents))
    assert levels(findings, "ERROR") == [
        "the AGENTS.md composition section still holds template placeholders"
    ]


def test_the_shipped_template_composition_section_is_still_a_placeholder(tmp_path):
    template = (SKILL_DIR / "assets" / "AGENTS.md.template").read_text(encoding="utf-8")
    findings = crb.check_composition(make_repo(tmp_path, composition=template))
    assert levels(findings, "ERROR") == [
        "the AGENTS.md composition section still holds template placeholders"
    ]


# llmlint: ignore[comments_earn_their_place] a section banner, as above: it names where the e2e-realism fleet shapes begin.
# --- e2e mocking signal ----------------------------------------------------


def e2e_mock_warnings(tmp_path: Path, name: str, source: str) -> list[str]:
    repo = make_repo(tmp_path)
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / name).write_text(source, encoding="utf-8")
    return [m for m in levels(crb.audit(repo), "WARN") if "mocking library" in m]


def test_prose_naming_a_mock_server_is_no_mocking_import(tmp_path):
    # crozier's e2e suite cites where a meta-schema came from in a doc comment.
    source = (
        "/// from mock-server/mockserver-monorepo, whose draft-04 meta-schema `$ref` is\n"
        "/// fetched once and vendored; import mock/fixtures are not used here.\n"
        "#[test]\nfn generates() {}\n"
    )
    assert e2e_mock_warnings(tmp_path, "e2e.rs", source) == []


@pytest.mark.parametrize(
    "source",
    [
        "from mock import patch\n",
        "import mock\n",
        "from mock.mock import MagicMock\n",
        "from unittest.mock import patch\n",
        "import unittest.mock\n",
        "def test_x(monkeypatch):\n    pass\n",
        "def test_x(mocker):\n    pass\n",
        "@patch('mod.fn')\ndef test_x(_):\n    pass\n",
        "# requires pytest-mock\n",
        "vi.mock('node:fs')\n",
        "jest.mock('fs')\n",
        "import sinon from 'sinon'\n",
        "import nock from 'nock'\n",
        "// uses mockito\n",
    ],
)
def test_every_mocking_import_still_warns(tmp_path, source):
    assert len(e2e_mock_warnings(tmp_path, "test_e2e_journey.py", source)) == 1
