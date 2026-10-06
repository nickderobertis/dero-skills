"""End-to-end: a repository set up through `--tool onebudgetspec` really measures.

Nothing is stubbed. Each case generates a repository the way the skill does —
the justfile template, then the composer with the onebudgetspec opt-in and
`--wiring` — adds three budget files a consumer would write (a root file and two
domain projects), installs what the composer pinned with the real `bun install`,
and drives the generated repository's own `just check` and its affected run
(`just budgets`, the recipe the wiring added) through the real Nx and the real
onebudgetspec release.

The domains follow references/tools/onebudgetspec.md:

* `services/api` measures where its test already runs: `tests/sync.test.mjs`
  records each upstream request as telemetry, and the deterministic `reported`
  budget `api-requests-per-sync` only analyses it, through one generic runner
  that dispatches on `ONEBUDGETSPEC_BUDGET_ID` and reports with the TypeScript
  SDK's `report`. `api-queries-per-page` has no test to ride on, so the same
  runner measures it on its own. Both are cached.
* `services/web` has the `elapsed` budget `web-cold-start`, labelled `host`, so
  only the uncached `budgets-host` target runs it.
* The root `budgets.yaml` holds `workspace-walk`, checked on every run.

Every measurement appends its budget id to the file `BUDGET_MARKERS` names,
outside the repository so it is no input of any target: the markers are what
says a command ran, as opposed to Nx replaying it from cache.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[2]
COMPOSER = SKILL_DIR / "scripts" / "compose_repo_plan.py"
JUSTFILE_TEMPLATE = SKILL_DIR / "assets" / "justfile.template"
LINT_URL = (
    "https://raw.githubusercontent.com/nickderobertis/dero-skills/main/skills/"
    "bootstrap/create-repo/assets/llmlint/tools/onebudgetspec.llmlint.yml@1"
)
# The consumer's own Nx. The `budgets` target's `externalDependencies` input
# needs an Nx that reads `bun.lock`; Nx 20 refuses the target there.
NX_VERSION = "23.2.1"

ALL_BUDGETS = {
    "api-requests-per-sync",
    "api-queries-per-page",
    "web-cold-start",
    "workspace-walk",
}

API_PROJECT = {
    "name": "api",
    "targets": {
        "test": {
            "command": "node services/api/tests/sync.test.mjs",
            "cache": True,
            "inputs": ["{projectRoot}/**/*"],
            "outputs": ["{projectRoot}/dist/telemetry"],
        },
        "budgets": {
            "command": "onebudgetspec check services/api/budgets.yaml "
            "--exclude-label host",
            "dependsOn": ["test"],
        },
        "budgets-host": {
            "command": "onebudgetspec check services/api/budgets.yaml --label host"
        },
    },
}
API_BUDGETS = """\
schema_version: 1
budgets:
  - id: api-requests-per-sync
    description: "Upstream requests one sync of the recorded fixture makes"
    measure: reported
    command: ["node", "budgets/measure.mjs"]
    unit: requests
    direction: max
    threshold: 5
  - id: api-queries-per-page
    description: "Database queries one page of results makes"
    measure: reported
    command: ["node", "budgets/measure.mjs"]
    unit: queries
    direction: max
    threshold: 3
"""
API_SYNC_TEST = """\
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { sync } from "../src/sync.mjs";

const requests = [];
const synced = sync((path) => requests.push(path));
assert.equal(synced, 3);
// Temporary telemetry for api-requests-per-sync; never committed.
mkdirSync("services/api/dist/telemetry", { recursive: true });
writeFileSync("services/api/dist/telemetry/sync-requests.json", JSON.stringify(requests));
"""
API_SYNC = """\
export function sync(fetch) {
  for (const page of ["a", "b", "c"]) fetch(`/items/${page}`);
  return 3;
}
"""
API_MEASURE = """\
import { appendFileSync, readFileSync } from "node:fs";
import { report } from "@onebudgetspec/sdk";

const measurements = {
  // Analyses what tests/sync.test.mjs recorded.
  "api-requests-per-sync": () =>
    JSON.parse(readFileSync("dist/telemetry/sync-requests.json", "utf8")).length,
  // No test pages through results, so this one measures on its own.
  "api-queries-per-page": () => 2,
};
const id = process.env.ONEBUDGETSPEC_BUDGET_ID;
if (!Object.hasOwn(measurements, id)) throw new Error(`no measurement for budget ${id}`);
appendFileSync(process.env.BUDGET_MARKERS, `${id}\\n`);
report(measurements[id]());
"""

WEB_PROJECT = {
    "name": "web",
    "targets": {
        "budgets": {
            "command": "onebudgetspec check services/web/budgets.yaml "
            "--exclude-label host"
        },
        "budgets-host": {
            "command": "onebudgetspec check services/web/budgets.yaml --label host"
        },
    },
}
WEB_BUDGETS = """\
schema_version: 1
budgets:
  - id: web-cold-start
    description: "Seconds from process start to the first rendered page"
    labels: [host]
    measure: elapsed
    command: ["node", "budgets/start.mjs"]
    unit: seconds
    direction: max
    threshold: 30
"""
WEB_START = """\
import { appendFileSync } from "node:fs";

appendFileSync(process.env.BUDGET_MARKERS, `${process.env.ONEBUDGETSPEC_BUDGET_ID}\\n`);
"""

ROOT_BUDGETS = """\
schema_version: 1
budgets:
  - id: workspace-walk
    description: "Seconds to read the workspace's project graph"
    measure: elapsed
    command: ["node", "budgets/walk.mjs"]
    unit: seconds
    direction: max
    threshold: 60
"""
ROOT_WALK = """\
import { appendFileSync, readdirSync } from "node:fs";

readdirSync("services", { recursive: true });
appendFileSync(process.env.BUDGET_MARKERS, `${process.env.ONEBUDGETSPEC_BUDGET_ID}\\n`);
"""


def _require(*tools: str) -> None:
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        pytest.fail(
            f"{', '.join(missing)} not on PATH — this journey drives the generated "
            "repository's real toolchain; run `just bootstrap`"
        )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Generated:
    """A generated repository and the way its user drives it."""

    def __init__(self, root: Path, markers: Path) -> None:
        self.root = root
        self.markers = markers

    def env(self, **overrides: str) -> dict[str, str]:
        # NX_BASE is the generated justfile's input, so it comes from the case,
        # never from this repo's own CI environment.
        ambient = {k: v for k, v in os.environ.items() if not k.startswith("NX_")}
        return {
            **ambient,
            "NX_DAEMON": "false",
            "NX_NO_CLOUD": "true",
            "NX_TUI": "false",
            "BUDGET_MARKERS": str(self.markers),
            "GIT_AUTHOR_NAME": "dev",
            "GIT_AUTHOR_EMAIL": "dev@example.com",
            "GIT_COMMITTER_NAME": "dev",
            "GIT_COMMITTER_EMAIL": "dev@example.com",
            **overrides,
        }

    def run(self, *argv: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv),
            cwd=self.root,
            capture_output=True,
            text=True,
            env=self.env(**env),
            timeout=600,
        )

    def commit(self, message: str) -> str:
        assert self.run("git", "add", "-A").returncode == 0
        done = self.run("git", "commit", "-q", "-m", message)
        assert done.returncode == 0, done.stderr
        return self.run("git", "rev-parse", "HEAD").stdout.strip()

    def measured(self) -> set[str]:
        """The budget ids whose command ran since the last call, then reset."""
        if not self.markers.exists():
            return set()
        ids = set(self.markers.read_text(encoding="utf-8").split())
        self.markers.unlink()
        return ids

    def set_threshold(self, budgets: str, budget_id: str, threshold: int) -> None:
        path = self.root / budgets
        lines = path.read_text(encoding="utf-8").splitlines()
        at = lines.index(f"  - id: {budget_id}")
        end = next(i for i in range(at + 1, len(lines)) if "threshold:" in lines[i])
        lines[end] = f"    threshold: {threshold}"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def generated(tmp_path: Path) -> tuple[Generated, str]:
    """A repository generated with the opt-in, installed, its base commit."""
    _require("bun", "just", "git", "node", "uv")
    root = tmp_path / "repo"
    root.mkdir()
    repo = Generated(root, tmp_path / "markers.txt")
    assert repo.run("git", "init", "-q", "-b", "main").returncode == 0
    _write(root / ".gitignore", "node_modules/\n.nx/\ndist/\n")
    base = repo.commit("chore: initial commit")

    shutil.copy(JUSTFILE_TEMPLATE, root / "justfile")
    composed = repo.run(
        "uv",
        "run",
        "--script",
        str(COMPOSER),
        "--shape",
        "web-app",
        "--language",
        "typescript",
        "--tool",
        "onebudgetspec",
        "-o",
        str(tmp_path / "plan.md"),
        "--llmlint-config",
        str(root / "llmlint.yml"),
        "--wiring",
        str(root),
    )
    assert composed.returncode == 0, composed.stderr
    assert LINT_URL in (root / "llmlint.yml").read_text(encoding="utf-8")

    # The consumer's own dependencies beside the pin the wiring wrote: Nx, and the
    # SDK its measurements report through, at the release the wiring pinned.
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    pinned = package["devDependencies"]["@onebudgetspec/cli"]
    package["devDependencies"] |= {"nx": NX_VERSION, "@onebudgetspec/sdk": pinned}
    package["trustedDependencies"] = ["nx"]
    _write(root / "package.json", json.dumps(package, indent=2) + "\n")

    _write(root / "budgets.yaml", ROOT_BUDGETS)
    _write(root / "budgets" / "walk.mjs", ROOT_WALK)
    api = root / "services" / "api"
    _write(api / "project.json", json.dumps(API_PROJECT, indent=2))
    _write(api / "budgets.yaml", API_BUDGETS)
    _write(api / "budgets" / "measure.mjs", API_MEASURE)
    _write(api / "src" / "sync.mjs", API_SYNC)
    _write(api / "tests" / "sync.test.mjs", API_SYNC_TEST)
    web = root / "services" / "web"
    _write(web / "project.json", json.dumps(WEB_PROJECT, indent=2))
    _write(web / "budgets.yaml", WEB_BUDGETS)
    _write(web / "budgets" / "start.mjs", WEB_START)

    installed = repo.run("bun", "install")
    assert installed.returncode == 0, installed.stderr
    lock = (root / "bun.lock").read_text(encoding="utf-8")
    assert f'"@onebudgetspec/cli@{pinned}"' in lock, "the lockfile records the pin"
    version = repo.run("node_modules/.bin/onebudgetspec", "--version")
    assert version.stdout.split() == ["onebudgetspec", pinned], version
    repo.commit("feat: register budgets")
    return repo, base


def test_check_executes_every_applicable_measurement(generated):
    repo, base = generated
    result = repo.run("just", "check", NX_BASE=base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert repo.measured() == ALL_BUDGETS


def test_the_affected_run_reaches_only_the_changed_project_and_the_root(generated):
    repo, _base = generated
    before = repo.run("git", "rev-parse", "HEAD").stdout.strip()
    start = repo.root / "services" / "web" / "budgets" / "start.mjs"
    start.write_text(start.read_text(encoding="utf-8") + "// tuned\n", encoding="utf-8")
    repo.commit("perf(web): tune the cold start")

    # The cache skipped, so a cached api budget would show if it were selected.
    result = repo.run("just", "budgets", NX_BASE=before, NX_SKIP_NX_CACHE="true")
    assert result.returncode == 0, result.stdout + result.stderr
    assert repo.measured() == {"web-cold-start", "workspace-walk"}
    assert "api:" not in result.stdout


def test_an_over_budget_figure_fails_check_and_the_affected_run(generated):
    repo, base = generated
    before = repo.run("git", "rev-parse", "HEAD").stdout.strip()
    repo.set_threshold("services/api/budgets.yaml", "api-requests-per-sync", 1)
    repo.commit("test(api): tighten the request budget")
    verdict = (
        "budget api-requests-per-sync: actual 3 requests, budget 1 requests, "
        "headroom -2 requests"
    )

    for argv, nx_base in ((("just", "check"), base), (("just", "budgets"), before)):
        result = repo.run(*argv, NX_BASE=nx_base)
        output = result.stdout + result.stderr
        assert result.returncode != 0, output
        assert verdict in output, output
        assert "— over" in output.split(verdict, 1)[1].splitlines()[0]


def test_a_second_run_serves_the_deterministic_budget_from_cache(generated):
    repo, base = generated
    first = repo.run("just", "check", NX_BASE=base)
    assert first.returncode == 0, first.stdout + first.stderr
    assert repo.measured() == ALL_BUDGETS

    second = repo.run("just", "check", NX_BASE=base)
    assert second.returncode == 0, second.stdout + second.stderr
    # The reported budgets came back from Nx's cache without running; the
    # elapsed ones, host-labelled or in the root file, ran again.
    assert repo.measured() == {"web-cold-start", "workspace-walk"}
    # Nx says so too, on the line for the cached target (not `budgets-host`).
    [cached] = [
        line for line in second.stdout.splitlines() if "nx run api:budgets " in line
    ]
    assert "cache" in cached, second.stdout
