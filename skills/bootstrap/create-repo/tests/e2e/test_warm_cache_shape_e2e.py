"""End-to-end: the cache shape a composed repository starts inside.

The real composer (`compose_repo_plan.py --ci-cd`, as a subprocess) writes a repository, and
`shape_problems` reads its workflows, budgets and Nx configuration as GitHub, onebudgetspec and
Nx would: parsed YAML, the real `onebudgetspec`, the written `nx.json`. Each property is then
taken away from a fresh composed copy, one at a time, and must be reported, so no property here
is one the reader cannot see.

The cache's point is a build, so the warm journey runs one: a composed Rust workspace's real
`just ci-warm`, then its real `just ci-test` through real Nx and cargo, under the settings
setup-rust-toolchain exports to both jobs. Nothing is compiled twice or the leg saved the wrong
thing. It needs cargo, just and bun, and takes Nx from this repository's node_modules, so it
stays offline; the journey itself is in `ci_cd_shape.py`, which the external tier shares.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from ci_cd_shape import (
    GOOD_COMPILE,
    NAME,
    OLD_PLACEHOLDER,
    WarmJourney,
    compiled,
    compose,
    run,
    warm_build_problems,
    warm_compiles_again_problems,
    workspace_files,
)

WORKFLOWS = ".github/workflows"
PINS = ".github/ci-tool-versions"
PLATFORMS = {
    "cross-platform": ("linux", "windows", "macos"),
    "linux": ("linux",),
}
WARM = "rust-cache-warm.yml"
RUST_SETUP = "actions-rust-lang/setup-rust-toolchain@"
PRUNE_NAME = "Delete this family's superseded copies"
TOKEN_RE = re.compile(r"github\.token|secrets\.|GH_TOKEN|GITHUB_TOKEN")
PINNED_OUTPUT_RE = re.compile(r"\$\{\{\s*steps\.pins\.outputs\.([a-z0-9-]+)\s*\}\}")
EXACT_VERSION_RE = re.compile(r"v?\d+\.\d+\.\d+")
# The expression that is true exactly when a run's head repository is not the base.
FOREIGN_HEAD = (
    "${{ github.event.pull_request.head.repo.full_name != github.repository }}"
)
# Actions a pull-request job runs that write no cache of their own. A new action is a
# question this list answers on purpose: does it write a cache, and is that guarded?
WRITES_NO_CACHE = (
    "actions/checkout@",
    "actions/upload-artifact@",
    "amannn/action-semantic-pull-request@",
    "extractions/setup-just@",
    "nickderobertis/notignored@",
    "nickderobertis/onebudgetspec-github-action@",
    "nickderobertis/screencomp",
    "taiki-e/install-action@",
)


def steps_of(job: dict) -> list[dict]:
    return job.get("steps", [])


def read_workflows(repo: Path) -> dict[str, dict]:
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted((repo / WORKFLOWS).iterdir())
    }


def uses(step: dict) -> str:
    return str(step.get("uses", ""))


def warm_problems(repo: Path, flows: dict[str, dict], platforms) -> list[str]:
    problems = []
    if WARM not in flows:
        return ["no warm workflow"]
    warm = flows[WARM]
    # PyYAML reads the bare key `on` as true.
    if warm.get(True) != {"push": {"branches": ["main"]}}:
        problems.append("warm workflow runs on more than a push to main")
    if set(warm["jobs"]) != {f"warm-{p}" for p in platforms}:
        problems.append("warm workflow's legs are not one per tested platform")
    for platform in platforms:
        leg = warm["jobs"].get(f"warm-{platform}")
        test = flows["ci.yml"]["jobs"][f"test-{platform}"]
        if leg is None:
            continue
        if leg.get("permissions") != {"contents": "read", "actions": "write"}:
            problems.append(f"warm-{platform} holds other permissions")
        if leg.get("runs-on") != test.get("runs-on"):
            problems.append(f"warm-{platform} runs on another runner than its test job")
        steps = steps_of(leg)
        checkout = next((s for s in steps if "actions/checkout@" in uses(s)), {})
        if checkout.get("with", {}).get("persist-credentials") is not False:
            problems.append(f"warm-{platform}'s checkout persists credentials")
        saved = next((s for s in steps if RUST_SETUP in uses(s)), {}).get("with", {})
        restored = next((s for s in steps_of(test) if RUST_SETUP in uses(s)), {}).get(
            "with", {}
        )
        if saved.get("cache-shared-key") != f"test-{platform}":
            problems.append(f"warm-{platform} saves under another key")
        if restored.get("cache-shared-key") != saved.get("cache-shared-key"):
            problems.append(f"test-{platform} restores another key than its leg saves")
        if "cache-save-if" in saved or saved.get("cache") is False:
            problems.append(f"warm-{platform} does not save")
        # Beyond saving, each input reaches the key or the build, so they must all agree.
        if {k: v for k, v in restored.items() if k != "cache-save-if"} != saved:
            problems.append(
                f"warm-{platform} sets the toolchain up unlike its test job"
            )
        for job, label in ((leg, "warm"), (test, "test")):
            for step in [job, *steps_of(job)]:
                if any(k.startswith(("RUST", "CARGO")) for k in step.get("env") or {}):
                    problems.append(
                        f"{label}-{platform} sets cargo's environment alone"
                    )
        runs = [str(s.get("run", "")) for s in steps]
        if "just ci-warm" not in runs:
            problems.append(f"warm-{platform} does not build what the test job builds")
        if not any("just ci-test" == str(s.get("run", "")) for s in steps_of(test)):
            problems.append(f"test-{platform} no longer runs ci-test")
        if not steps or steps[-1].get("name") != PRUNE_NAME:
            problems.append(f"warm-{platform}'s last step is not the cleanup")
            continue
        last = steps[-1]
        if last.get("run") != (
            f"node scripts/actions-cache-prune.mjs --family v0-rust-test-{platform}-"
        ):
            problems.append(f"warm-{platform}'s cleanup names another family")
        if last.get("env") != {"GH_TOKEN": "${{ github.token }}"}:
            problems.append(f"warm-{platform}'s cleanup is not handed the token")
        for step in steps[:-1]:
            if TOKEN_RE.search(json.dumps(step)):
                problems.append(f"warm-{platform}: another step is handed the token")
    justfile = (repo / "justfile").read_text(encoding="utf-8")
    if not re.search(r"^ci-warm:", justfile, re.M):
        problems.append("the justfile has no ci-warm recipe")
    elif "nx run-many --skip-nx-cache -t build test-compile" not in justfile:
        problems.append("ci-warm can replay a task instead of compiling it")
    nx = json.loads((repo / "nx.json").read_text())
    if nx["targetDefaults"].get("test-compile", {}).get("cache") is not False:
        problems.append("test-compile can be served from an Nx cache")
    return problems


def permission_problems(flows: dict[str, dict]) -> list[str]:
    return [
        f"{name} {job_name} holds actions: write"
        for name, flow in flows.items()
        for job_name, job in flow["jobs"].items()
        if name != WARM and (job.get("permissions") or {}).get("actions") == "write"
    ]


def rust_save_problems(flows: dict[str, dict]) -> list[str]:
    problems = []
    for name, flow in flows.items():
        if name == WARM:
            continue
        for job_name, job in flow["jobs"].items():
            for step in steps_of(job):
                if "Swatinem/rust-cache" in uses(step):
                    problems.append(f"{name} {job_name} runs rust-cache directly")
                if RUST_SETUP not in uses(step):
                    continue
                given = step.get("with", {})
                if given.get("cache-save-if") is not False and (
                    given.get("cache") is not False
                ):
                    problems.append(f"{name} {job_name} saves a Rust cache")
    return problems


def budget_problems(repo: Path, flows: dict[str, dict], platforms, rust: bool):
    tool = shutil.which("onebudgetspec")
    assert tool, "onebudgetspec not on PATH: run `just bootstrap`"
    valid = run([tool, "validate", "budgets.yaml"], cwd=repo)
    if valid.returncode != 0:
        return [f"budgets file is invalid: {valid.stderr or valid.stdout}"]
    listed = json.loads(run([tool, "list", "--json", "budgets.yaml"], cwd=repo).stdout)[
        "budgets"
    ]
    found = {b["id"]: b for b in listed}
    budget = found.get(f"{NAME}-cache-headroom")
    if budget is None:
        return ["no cache-headroom budget"]
    problems = []
    if budget.get("threshold") != 3.05:
        problems.append("headroom threshold is not the template's")
    if budget.get("labels") != ["release-tail"]:
        problems.append("headroom is not labelled release-tail")
    if budget.get("direction") != "min":
        problems.append("headroom is not a minimum")
    command = budget.get("command", [])
    if command[:3] != ["bash", "scripts/actions-budget-figures.sh", "cache-headroom"]:
        problems.append("headroom does not run cache-headroom")
    named = command[4::2]
    cleaned = []
    for job in flows.get(WARM, {}).get("jobs", {}).values():
        cleaned.append(str(steps_of(job)[-1].get("run", "")).rsplit(" ", 1)[-1])
    if sorted(named) != sorted(cleaned):
        problems.append("headroom names other families than the cleanup steps")
    for figure in ("cd-job-time", "cd-job-count", "release-read-requests"):
        wanted = ["--exclude-workflow", "ci"]
        if rust:
            wanted = ["--exclude-workflow", "rust cache warm", *wanted]
        if found[f"{NAME}-{figure}"]["command"][-len(wanted) :] != wanted:
            problems.append(
                f"{figure} does not exclude the delivering-nothing workflows"
            )
    return problems


def tool_problems(repo: Path, flows: dict[str, dict], rust: bool) -> list[str]:
    problems = []
    pins = {}
    for line in (repo / PINS).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, _, version = line.partition("=")
            pins[name] = version
            if not EXACT_VERSION_RE.fullmatch(version):
                problems.append(f"{PINS} pins {name} to the inexact {version!r}")
    if rust:
        toolchain = repo / "rust-toolchain.toml"
        if not toolchain.is_file() or not re.search(
            r'^channel = "\d+\.\d+\.\d+"$', toolchain.read_text(), re.M
        ):
            problems.append("rust-toolchain.toml names no exact channel")
    nx = json.loads((repo / "nx.json").read_text())
    inputs = {
        name: target.get("inputs", [])
        for name, target in nx["targetDefaults"].items()
        if name in {"build", "e2e", "budgets", "budgets-host", "lint", "typecheck"}
    }
    inputs["test"] = nx["namedInputs"]["testInputs"]
    for name, declared in inputs.items():
        for file in (PINS, "rust-toolchain.toml"):
            if f"{{workspaceRoot}}/{file}" not in declared:
                problems.append(f"nx target {name} does not take {file} as an input")

    def pinned(step: dict, text: str, tool: str, where: str) -> None:
        names = PINNED_OUTPUT_RE.findall(text)
        if tool not in names:
            problems.append(f"{where} installs {tool} at no pinned version")
        elif tool not in pins:
            problems.append(f"{where} reads {tool}, which {PINS} does not pin")

    for flow_name, flow in flows.items():
        for job_name, job in flow["jobs"].items():
            where = f"{flow_name} {job_name}"
            steps = steps_of(job)
            reads_pins = -1
            for index, step in enumerate(steps):
                if step.get("id") == "pins":
                    reads_pins = index
                    if PINS not in str(step.get("run", "")):
                        problems.append(f"{where}'s pins step reads no {PINS}")
                text = json.dumps(step)
                if "steps.pins." in text and not 0 <= reads_pins < index:
                    problems.append(f"{where} reads a pin before the pins step")
                given = json.dumps(step.get("with", {}))
                action = uses(step)
                if action.startswith("extractions/setup-just@"):
                    pinned(step, given, "just", where)
                elif action.startswith("oven-sh/setup-bun@"):
                    pinned(step, given, "bun", where)
                elif action.startswith("taiki-e/install-action@"):
                    for tool in step["with"]["tool"].split(","):
                        pinned(step, tool, tool.split("@")[0], where)
                elif action.startswith(RUST_SETUP):
                    if "toolchain" in step.get("with", {}):
                        problems.append(f"{where} overrides rust-toolchain.toml")
                run_text = str(step.get("run", ""))
                if "setup-llmlint.sh" in run_text:
                    pinned(step, text, "llmlint-cli", where)
                if "npm i " in run_text or "npm install" in run_text:
                    for package in re.findall(r'"([^" ]+@\$[A-Z_]+)"', run_text):
                        if package.split("@$")[1] not in step.get("env", {}):
                            problems.append(f"{where}: {package} reads no pinned env")
                    if not re.search(r'@\$[A-Z_]+"', run_text):
                        problems.append(f"{where} runs an unpinned npm install")
    return problems


def fork_problems(flows: dict[str, dict]) -> list[str]:
    problems = []
    for job_name, job in flows["ci.yml"]["jobs"].items():
        for step in steps_of(job):
            action = uses(step)
            if not action:
                continue
            given = step.get("with", {})
            if action.startswith("oven-sh/setup-bun@"):
                if given.get("no-cache") != FOREIGN_HEAD:
                    problems.append(f"ci.yml {job_name}: setup-bun writes for a fork")
            elif action.startswith(RUST_SETUP):
                if given.get("cache-save-if") is not False:
                    problems.append(f"ci.yml {job_name}: Rust cache writes for a fork")
            elif action.startswith("op-nx/github-cache/start-cache-server@"):
                read_only = step.get("env", {}).get("CACHE_READ_ONLY", "")
                if "nx-cache-trust.outputs.read_only" not in read_only:
                    problems.append(f"ci.yml {job_name}: Nx sidecar writes for a fork")
            elif not action.startswith(WRITES_NO_CACHE):
                problems.append(f"ci.yml {job_name}: {action} may write a cache")
            if action.startswith("actions/upload-artifact@") and (
                "promotable" not in str(step.get("if", ""))
            ):
                problems.append(f"ci.yml {job_name}: an artifact uploads for a fork")
    return problems


def shape_problems(repo: Path, language: str, platforms) -> list[str]:
    flows = read_workflows(repo)
    rust = language == "rust"
    problems = [
        *permission_problems(flows),
        *budget_problems(repo, flows, platforms, rust),
        *tool_problems(repo, flows, rust),
        *fork_problems(flows),
    ]
    if rust:
        problems += [
            *warm_problems(repo, flows, platforms),
            *rust_save_problems(flows),
        ]
    elif WARM in flows:
        problems.append("a repository with no Rust has a warm workflow")
    return problems


@pytest.fixture(scope="module")
def rust_repo(tmp_path_factory) -> Path:
    return compose(tmp_path_factory.mktemp("rust") / "repo", "rust", "cross-platform")


@pytest.mark.parametrize(
    ("language", "platforms"),
    [
        ("rust", "cross-platform"),
        ("rust", "linux"),
        ("typescript", "cross-platform"),
    ],
)
def test_a_composed_repository_has_the_shape(tmp_path, language, platforms):
    repo = compose(tmp_path / "repo", language, platforms)
    assert shape_problems(repo, language, PLATFORMS[platforms]) == []
    if language == "typescript":
        assert not (repo / "scripts/actions-cache-prune.mjs").exists()


def pins_steps(repo: Path) -> list[tuple[str, str]]:
    """Every composed job's pins step, by where it is, as the runner would run it."""
    return [
        (f"{path.name} {name}", step["run"])
        for path in sorted((repo / WORKFLOWS).glob("*.yml"))
        for name, job in yaml.safe_load(path.read_text(encoding="utf-8"))[
            "jobs"
        ].items()
        for step in steps_of(job)
        if step.get("id") == "pins"
    ]


def run_pins_step(repo: Path, script: str, output: Path):
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=repo,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_every_pins_step_outputs_the_pins_and_drops_comments(rust_repo, tmp_path):
    steps = pins_steps(rust_repo)
    # ci.yml's three pinned jobs, release's two, release-plz's and a warm leg per platform.
    assert len(steps) >= 7
    pins = (rust_repo / PINS).read_text(encoding="utf-8")
    wanted = [
        line for line in pins.splitlines() if line.strip() and not line.startswith("#")
    ]
    for where, script in steps:
        output = tmp_path / "output"
        output.write_text("")
        done = run_pins_step(rust_repo, script, output)
        assert done.returncode == 0, (where, done.stderr)
        assert output.read_text().splitlines() == wanted, where


@pytest.mark.parametrize(
    "line",
    ["just=1.59.0 && curl evil", "EOF<<EOF", "no-equals-sign", "bun=", "just=1.59.0=2"],
)
def test_every_pins_step_refuses_a_line_that_is_not_a_pin(tmp_path, line):
    repo = compose(tmp_path / "repo", "rust", "cross-platform")
    pins = repo / PINS
    pins.write_text(f"{pins.read_text(encoding='utf-8')}{line}\r\n", encoding="utf-8")
    for where, script in pins_steps(repo):
        output = tmp_path / "output"
        output.write_text("")
        done = run_pins_step(repo, script, output)
        assert done.returncode != 0, where
        assert f"malformed pin: {line}" in done.stderr, where
        assert line not in output.read_text().splitlines(), where


def test_a_second_visual_docs_architecture_keeps_the_shape(tmp_path):
    repo = compose(tmp_path / "repo", "rust", "cross-platform", "arm64")
    assert shape_problems(repo, "rust", PLATFORMS["cross-platform"]) == []
    assert "screencomp=" in (repo / PINS).read_text(encoding="utf-8")


def test_a_floating_rust_channel_is_refused_and_an_exact_one_is_kept(tmp_path):
    floating = '[toolchain]\nchannel = "stable"\n'
    with pytest.raises(AssertionError, match="names no exact channel"):
        compose(tmp_path / "floating", "rust", files={"rust-toolchain.toml": floating})
    exact = '[toolchain]\nchannel = "1.90.0"\n'
    repo = compose(tmp_path / "exact", "rust", files={"rust-toolchain.toml": exact})
    assert (repo / "rust-toolchain.toml").read_text() == exact
    assert shape_problems(repo, "rust", PLATFORMS["cross-platform"]) == []


def test_the_prune_script_is_a_verbatim_copy_of_the_skills(rust_repo):
    skill = Path(__file__).resolve().parents[2]
    assert (rust_repo / "scripts/actions-cache-prune.mjs").read_bytes() == (
        skill / "assets/ci/actions-cache-prune.mjs"
    ).read_bytes()


def edit(relative: str, old: str, new: str) -> Callable[[Path], None]:
    def apply(repo: Path) -> None:
        path = repo / relative
        text = path.read_text(encoding="utf-8")
        assert old in text, (relative, old)
        path.write_text(text.replace(old, new), encoding="utf-8")

    return apply


def remove(relative: str) -> Callable[[Path], None]:
    return lambda repo: (repo / relative).unlink()


def nx_without(input_file: str, target: str | None) -> Callable[[Path], None]:
    def apply(repo: Path) -> None:
        path = repo / "nx.json"
        nx = json.loads(path.read_text())
        wanted = f"{{workspaceRoot}}/{input_file}"
        inputs = (
            nx["namedInputs"]["testInputs"]
            if target is None
            else nx["targetDefaults"][target]["inputs"]
        )
        inputs.remove(wanted)
        path.write_text(json.dumps(nx))

    return apply


CI = f"{WORKFLOWS}/ci.yml"
RELEASE = f"{WORKFLOWS}/release.yml"
PLZ = f"{WORKFLOWS}/release-plz.yml"
WARM_PATH = f"{WORKFLOWS}/{WARM}"
TEST_LINUX_SAVE = "cache-shared-key: test-linux\n          cache-save-if: false"
# Each mutation removes one property a composed repository has, and what must then be said.
MUTATIONS = {
    "ci-saves-rust": (
        edit(CI, TEST_LINUX_SAVE, "cache-shared-key: test-linux"),
        "ci.yml",
    ),
    "release-saves-rust": (
        edit(RELEASE, "          cache-save-if: false\n", ""),
        "release.yml",
    ),
    "release-plz-saves-rust": (
        edit(PLZ, "        with:\n          cache: false\n", ""),
        "release-plz.yml",
    ),
    "no-warm-workflow": (remove(WARM_PATH), "no warm workflow"),
    "warm-on-pull-requests": (
        edit(WARM_PATH, "on:\n  push:\n", "on:\n  pull_request:\n  push:\n"),
        "more than a push to main",
    ),
    "warm-leg-missing": (
        edit(WARM_PATH, "warm-macos:", "warm-other:"),
        "one per tested platform",
    ),
    "warm-key-differs": (
        edit(WARM_PATH, "cache-shared-key: test-windows", "cache-shared-key: other"),
        "saves under another key",
    ),
    "warm-does-not-build": (
        edit(WARM_PATH, "run: just ci-warm", "run: just lint"),
        "does not build what the test job builds",
    ),
    "warm-toolchain-differs": (
        edit(
            WARM_PATH,
            "cache-shared-key: test-linux\n",
            "cache-shared-key: test-linux\n          rustflags: ''\n",
        ),
        "sets the toolchain up unlike its test job",
    ),
    "warm-env-differs": (
        edit(
            WARM_PATH,
            "run: just ci-warm\n",
            "run: just ci-warm\n        env:\n          RUSTFLAGS: -Copt-level=3\n",
        ),
        "sets cargo's environment alone",
    ),
    "warm-recipe-replays": (
        edit(
            "justfile", "nx run-many --skip-nx-cache -t build", "nx run-many -t build"
        ),
        "can replay a task",
    ),
    "test-compile-cached": (
        edit(
            "nx.json",
            '"test-compile": {\n      "cache": false',
            '"test-compile": {\n      "cache": true',
        ),
        "served from an Nx cache",
    ),
    "warm-recipe-missing": (
        edit("justfile", "\nci-warm:", "\nci-cold:"),
        "no ci-warm recipe",
    ),
    "warm-without-write": (
        edit(WARM_PATH, "      actions: write", "      actions: read"),
        "holds other permissions",
    ),
    "warm-checkout-persists": (
        edit(WARM_PATH, "persist-credentials: false", "persist-credentials: true"),
        "persists credentials",
    ),
    "warm-cleanup-not-last": (
        edit(
            WARM_PATH,
            "GH_TOKEN: ${{ github.token }}\n",
            "GH_TOKEN: ${{ github.token }}\n      - run: echo after\n",
        ),
        "last step is not the cleanup",
    ),
    "warm-cleanup-family": (
        edit(WARM_PATH, "--family v0-rust-test-windows-", "--family v0-other-"),
        "names another family",
    ),
    "warm-token-elsewhere": (
        edit(
            WARM_PATH,
            "run: just bootstrap\n",
            "run: just bootstrap\n        env:\n          GH_TOKEN: ${{ github.token }}\n",
        ),
        "another step is handed the token",
    ),
    "other-job-holds-actions-write": (
        edit(RELEASE, "      actions: read\n", "      actions: write\n"),
        "holds actions: write",
    ),
    "budgets-invalid": (
        edit("budgets.yaml", "direction: min\n    threshold: 3.05", "threshold: 3.05"),
        "budgets file is invalid",
    ),
    "headroom-missing": (
        edit("budgets.yaml", "-cache-headroom\n", "-cache-other\n"),
        "no cache-headroom budget",
    ),
    "headroom-threshold": (
        edit("budgets.yaml", "threshold: 3.05", "threshold: 1"),
        "threshold is not the template's",
    ),
    "headroom-label": (
        edit(
            "budgets.yaml",
            "threshold: 3.05\n    labels: [release-tail]",
            "threshold: 3.05\n    labels: [other]",
        ),
        "not labelled release-tail",
    ),
    "headroom-family": (
        edit("budgets.yaml", ", --family, v0-rust-test-macos-", ""),
        "other families than the cleanup",
    ),
    "cd-budget-counts-warm": (
        edit(
            "budgets.yaml",
            'longest-job, --stage, cd, --exclude-workflow, "rust cache warm", --exclude-workflow, ci',
            "longest-job, --stage, cd, --exclude-workflow, ci",
        ),
        "cd-job-time does not exclude",
    ),
    "just-unpinned": (
        edit(CI, "          just-version: ${{ steps.pins.outputs.just }}\n", ""),
        "installs just at no pinned version",
    ),
    "bun-unpinned": (
        edit(
            WARM_PATH,
            "          bun-version: ${{ steps.pins.outputs.bun }}\n",
            "          bun-version: latest\n",
        ),
        "installs bun at no pinned version",
    ),
    "cargo-deny-unpinned": (
        edit(CI, "cargo-deny@${{ steps.pins.outputs.cargo-deny }}", "cargo-deny"),
        "installs cargo-deny at no pinned version",
    ),
    "release-plz-unpinned": (
        edit(PLZ, "release-plz@${{ steps.pins.outputs.release-plz }}", "release-plz"),
        "installs release-plz at no pinned version",
    ),
    "claude-code-unpinned": (
        edit(
            CI,
            '"@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION"',
            "@anthropic-ai/claude-code",
        ),
        "unpinned npm install",
    ),
    "llmlint-unpinned": (
        edit(
            CI,
            "          LLMLINT_CLI_VERSION: ${{ steps.pins.outputs.llmlint-cli }}\n",
            "          LLMLINT_CLI_VERSION: ''\n",
        ),
        "installs llmlint-cli at no pinned version",
    ),
    "pin-inexact": (
        edit(PINS, "just=1.59.0", "just=1"),
        "inexact",
    ),
    "pin-gone": (
        edit(PINS, "bun=1.4.4\n", ""),
        "which .github/ci-tool-versions does not pin",
    ),
    "pin-read-late": (
        edit(
            WARM_PATH,
            "      - name: Read the pinned tool versions\n        id: pins\n",
            "      - name: Read the pinned tool versions\n        id: other\n",
        ),
        "reads a pin before the pins step",
    ),
    "pins-not-a-test-input": (
        nx_without(PINS, None),
        "nx target test does not take .github/ci-tool-versions",
    ),
    "pins-not-a-build-input": (
        nx_without(PINS, "build"),
        "nx target build does not take .github/ci-tool-versions",
    ),
    "toolchain-not-an-input": (
        nx_without("rust-toolchain.toml", "e2e"),
        "nx target e2e does not take rust-toolchain.toml",
    ),
    "toolchain-inexact": (
        edit("rust-toolchain.toml", 'channel = "1.99.1"', 'channel = "stable"'),
        "names no exact channel",
    ),
    "toolchain-overridden": (
        edit(
            CI,
            "cache-shared-key: test-macos",
            "toolchain: stable\n          cache-shared-key: test-macos",
        ),
        "overrides rust-toolchain.toml",
    ),
    "fork-setup-bun": (
        edit(
            CI,
            "          no-cache: ${{ github.event.pull_request.head.repo.full_name != github.repository }}\n",
            "",
        ),
        "setup-bun writes for a fork",
    ),
    "fork-sidecar": (
        edit(
            CI,
            "CACHE_READ_ONLY: ${{ steps.nx-cache-trust.outputs.read_only == 'true' && '1' || '' }}",
            "CACHE_READ_ONLY: ''",
        ),
        "Nx sidecar writes for a fork",
    ),
    "fork-artifact": (
        edit(
            CI,
            "if: always() && steps.nx-cache-trust.outputs.promotable == 'true'",
            "if: always()",
        ),
        "an artifact uploads for a fork",
    ),
    "fork-new-writer": (
        edit(
            CI,
            "      - name: Bootstrap\n",
            "      - uses: actions/cache@v4\n        with:\n          path: x\n          key: y\n      - name: Bootstrap\n",
        ),
        "actions/cache@v4 may write a cache",
    ),
}


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
def test_taking_any_one_property_away_is_reported(rust_repo, tmp_path, mutation):
    apply, expected = MUTATIONS[mutation]
    repo = tmp_path / "repo"
    shutil.copytree(rust_repo, repo, ignore=shutil.ignore_patterns("node_modules"))
    assert shape_problems(repo, "rust", PLATFORMS["cross-platform"]) == []
    apply(repo)
    problems = shape_problems(repo, "rust", PLATFORMS["cross-platform"])
    assert any(expected in problem for problem in problems), problems


REPO_ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture
def warm_journey(tmp_path) -> Callable[..., WarmJourney]:
    """A composed journey on this repository's Nx, which cannot load the ESM telemetry plugin.

    The plugin records only under GITHUB_ACTIONS, so leaving it out changes no compile.
    `external/test_nx_cache_e2e.py` runs the same journey on the pinned Nx with it loaded.
    """

    def start(
        test_compile: str | None = GOOD_COMPILE,
        host_env: dict[str, str] | None = None,
    ) -> WarmJourney:
        return WarmJourney(
            tmp_path / "repo",
            REPO_ROOT / "node_modules",
            test_compile,
            telemetry_plugin=False,
            host_env=host_env,
        )

    return start


def test_warm_journey_leaves_the_test_job_nothing_to_compile(warm_journey):
    journey = warm_journey()
    assert warm_build_problems(journey) == []


def replace_recipe(journey: WarmJourney, recipe: str) -> None:
    justfile = journey.repo / "justfile"
    text = justfile.read_text(encoding="utf-8")
    new, count = re.subn(r"^ci-warm:\n(?:    .*\n)+", recipe, text, flags=re.M)
    assert count == 1
    justfile.write_text(new, encoding="utf-8")


def test_warm_journey_rejects_the_old_placeholder(warm_journey):
    journey = warm_journey()
    replace_recipe(journey, OLD_PLACEHOLDER)
    [problem] = warm_build_problems(journey)
    assert problem.startswith("ci-warm failed") and "TODO" in problem


def test_warm_journey_holds_under_a_github_actions_pull_request_job(
    warm_journey, tmp_path
):
    # What the runner and nx-set-shas export while this repository's own gate runs the journey:
    # the commits they name exist in this repository's clone and not in the journey's.
    host_env = {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": "refs/pull/1/merge",
        "GITHUB_SHA": "1" * 40,
        "GITHUB_BASE_REF": "main",
        "GITHUB_HEAD_REF": "a-branch",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "NX_BASE": "2" * 40,
        "NX_HEAD": "3" * 40,
    }
    assert warm_build_problems(warm_journey(host_env=host_env)) == []


@pytest.mark.parametrize(
    "mismatch",
    [
        "cargo test -p demo-lib --no-run --release",
        "cargo test -p demo-lib --no-run --features extra",
        "cargo check -p demo-lib",
    ],
    ids=["other-profile", "other-features", "not-the-test-build"],
)
def test_warm_journey_rejects_a_test_compile_that_builds_something_else(
    warm_journey, mismatch
):
    journey = warm_journey(mismatch)
    problems = warm_build_problems(journey)
    assert any("compiled again" in p and "demo-lib" in p for p in problems), problems


def test_warm_journey_rejects_a_test_job_needing_dependencies_the_leg_did_not_build(
    tmp_path,
):
    # What costs every pull request: rust-cache keeps dependencies and not workspace crates.
    journey = WarmJourney(
        tmp_path / "repo",
        REPO_ROOT / "node_modules",
        telemetry_plugin=False,
        test_command="cargo test -p demo-lib --features extra",
    )
    problems = warm_build_problems(journey)
    assert any("compiled again" in p and "demo-dep" in p for p in problems), problems


def test_warm_journey_rejects_a_recipe_that_bypasses_the_projects_own_target(
    warm_journey,
):
    journey = warm_journey()
    replace_recipe(journey, "ci-warm:\n    cargo build --workspace --release\n")
    problems = warm_build_problems(journey)
    assert any("compiled again" in problem for problem in problems), problems


def test_warm_refuses_a_repository_where_no_project_declares_test_compile(warm_journey):
    journey = warm_journey(None)
    done = journey.warm()
    assert done.returncode != 0
    assert "test-compile" in done.stderr
    assert "demo-dep" not in compiled(done)


def test_warm_compiles_again_whatever_nx_remembers(warm_journey):
    # A replayed target would leave the runner's target directory empty, and rust-cache would
    # then save nothing a pull request could reuse.
    journey = warm_journey()
    assert warm_compiles_again_problems(journey) == []


def test_the_composer_keeps_test_compile_out_of_every_nx_cache(tmp_path):
    files = workspace_files(GOOD_COMPILE)
    project = json.loads(files["crates/lib/project.json"])
    project["targets"]["test-compile"]["cache"] = True
    files["crates/lib/project.json"] = json.dumps(project)
    repo = compose(tmp_path / "repo", "rust", "linux", files=files)
    written = json.loads((repo / "crates/lib/project.json").read_text())
    target = written["targets"]["test-compile"]
    assert target["cache"] is False
    assert target["command"] == GOOD_COMPILE
    # The test target beside it keeps its cached inputs.
    assert written["targets"]["test"]["inputs"][0] == "testInputs"
    nx = json.loads((repo / "nx.json").read_text())
    assert nx["targetDefaults"]["test-compile"]["cache"] is False
