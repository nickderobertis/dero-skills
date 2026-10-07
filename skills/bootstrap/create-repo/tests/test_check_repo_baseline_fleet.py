"""The baseline checker over the shapes real repositories already use.

Each fixture reproduces, inside a temporary repository, a shape found in a
repository audited against the checker — a justfile reaching Nx through a
wrapper script or a variable, a Poetry manifest, a dogfooding action workflow,
a hook pointing outside `scripts/` — that the checker used to misreport. Each
false positive is pinned beside the genuine failure the same check must keep
reporting, so a fix that only silences the check fails here.
"""

from __future__ import annotations

import pytest
from test_check_repo_baseline import crb, levels, make_repo


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
