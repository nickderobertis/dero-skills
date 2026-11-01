"""The harness the actions-budget-figures journeys drive the script through.

Nothing below the GitHub boundary is stood in for. The script runs as a
repository's copy at `scripts/actions-budget-figures.sh`, named as budgets'
`command` in a `budgets.yaml` and measured by the real `onebudgetspec check`; it
asks the real `onevcs` release from npm, with the real jq. `ONEVCS_GH` — the
seam onevcs's contract names for replaying recorded responses — points at a
POSIX program that answers `gh api --include` from GitHub's own recordings under
`fixtures/actions-figures/`, each served for the request it recorded (or the one
its scenario's entry names in `for`; an entry may also leave runs out of a
listing (`keep_runs`), report runs of a listing still in progress
(`in_progress_runs`) or rename a listing's tags (`rename_tags`), each saying why)
with its recorded status,
headers and body. It is the double onevcs's own journeys replay through, and it
records every request it served: the read-count assertions are held to that
record, never to what the answers claim they cost.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[2]
SCRIPT = SKILL_DIR / "assets" / "budgets" / "actions-budget-figures.sh"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "actions-figures"
RECORDINGS = FIXTURES / "recordings"

# The onevcs release carrying the `onevcs host` queries this script reads. npm's
# `0.0.0-stage` is an empty staging placeholder with no binary, so the pin is the
# first published release carrying them; a harness supplies another through
# ONEVCS_CLI_VERSION.
ONEVCS_CLI_VERSION = os.environ.get("ONEVCS_CLI_VERSION", "0.56.2")
TOKEN = "replay-token"

# The program `ONEVCS_GH` names, as onevcs's journeys write it: it serves the
# response routed for the request it is asked, records the request, and fails
# the way `gh` does on an error status.
DOUBLE = r"""#!/bin/sh
dir="$(dirname "$0")/.."
for arg in "$@"; do last="$arg"; done
case "$last" in
  https://api.github.com/*) request="${last#https://api.github.com/}" ;;
  *) request="$last" ;;
esac
printf '%s\n' "$request" >> "$dir/served"
n=$(awk -F '\t' -v r="$request" '$1 == r { print $2; exit }' "$dir/routes.tsv")
if [ -z "$n" ]; then
  echo "replay: no recording for $request" >&2
  exit 1
fi
cat "$dir/responses/$n"
if [ -f "$dir/responses/$n.refusal" ]; then
  cat "$dir/responses/$n.refusal" >&2
  exit 1
fi
"""

REASONS = {200: "OK", 401: "Unauthorized", 404: "Not Found"}


def require(*tools: str) -> None:
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        pytest.fail(
            f"{', '.join(missing)} not on PATH — these journeys run the real "
            "toolchain; run `just bootstrap`"
        )


def recording(relative: str) -> dict:
    return json.loads((RECORDINGS / relative).read_text(encoding="utf-8"))


def canonical(name: str) -> str:
    """`content-type` as Go — and so `gh` — prints a header name."""
    return "-".join(part[:1].upper() + part[1:] for part in name.split("-"))


class Replay:
    """One scenario's recordings, served from a directory of the case's own."""

    def __init__(self, root: Path, scenario: str) -> None:
        self.root = root
        (root / "bin").mkdir(parents=True)
        program = root / "bin" / "gh"
        program.write_text(DOUBLE, encoding="utf-8")
        program.chmod(0o755)
        self.use(scenario)

    @property
    def program(self) -> Path:
        return self.root / "bin" / "gh"

    def use(self, scenario: str) -> None:
        """Serve `scenario`'s recordings from now on; the served record stays."""
        sidecar = json.loads(
            (FIXTURES / "scenarios" / f"{scenario}.json").read_text(encoding="utf-8")
        )
        responses = self.root / "responses"
        shutil.rmtree(responses, ignore_errors=True)
        responses.mkdir()
        routes = []
        for n, entry in enumerate(sidecar["serve"]):
            envelope = recording(entry["recording"])
            url = envelope["request"]["url"].removeprefix("https://api.github.com/")
            request = entry.get("for", url)
            departs = {"for", "keep_runs", "in_progress_runs", "rename_tags"}
            departs &= entry.keys()
            assert bool(departs) <= ("why" in entry), entry
            if "rename_tags" in entry:
                renames = entry["rename_tags"]
                for release in envelope["body"]:
                    release["tag_name"] = renames.get(
                        release["tag_name"], release["tag_name"]
                    )
                renamed = {release["tag_name"] for release in envelope["body"]}
                assert set(renames.values()) <= renamed, f"every renamed tag: {entry}"
            if "keep_runs" in entry:
                keep = entry["keep_runs"]
                runs = [
                    run
                    for run in envelope["body"]["workflow_runs"]
                    if run["id"] in keep
                ]
                assert len(runs) == len(keep), f"every kept run is recorded: {entry}"
                envelope["body"]["total_count"] = len(runs)
                envelope["body"]["workflow_runs"] = runs
            if "in_progress_runs" in entry:
                # As GitHub lists a run before it completes: no conclusion yet.
                running = entry["in_progress_runs"]
                runs = [
                    run
                    for run in envelope["body"]["workflow_runs"]
                    if run["id"] in running
                ]
                assert len(runs) == len(running), (
                    f"every running run is recorded: {entry}"
                )
                for run in runs:
                    run["status"], run["conclusion"] = "in_progress", None
            status = envelope["status"]
            head = f"HTTP/2.0 {status} {REASONS.get(status, 'Error')}\n"
            head += "".join(
                f"{canonical(name)}: {value}\r\n"
                for name, value in envelope["headers"].items()
            )
            body = json.dumps(envelope["body"], separators=(",", ":"))
            (responses / str(n)).write_text(f"{head}\r\n{body}", encoding="utf-8")
            if status >= 400:
                message = envelope["body"].get("message", "error")
                (responses / f"{n}.refusal").write_text(
                    f"gh: {message} (HTTP {status})\n", encoding="utf-8"
                )
            routes.append(f"{request}\t{n}\n")
        (self.root / "routes.tsv").write_text("".join(routes), encoding="utf-8")

    def served(self) -> list[str]:
        path = self.root / "served"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def install_onevcs(root: Path) -> Path:
    """The real onevcs release from npm, installed the way this repo installs one."""
    require("bun", "node")
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text('{"private": true}\n', encoding="utf-8")
    installed = subprocess.run(
        ["bun", "add", "--exact", f"onevcs-cli@{ONEVCS_CLI_VERSION}"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert installed.returncode == 0, installed.stderr
    binary = root / "node_modules" / ".bin" / "onevcs"
    version = subprocess.run(
        [binary, "--version"], capture_output=True, text=True, check=True
    )
    assert version.stdout.split() == ["onevcs", ONEVCS_CLI_VERSION], version
    return binary.parent


class Repository:
    """A repository holding the copy and a budgets file, measured as its CI does."""

    def __init__(self, root: Path, replay: Replay, onevcs_bin: Path) -> None:
        self.root = root
        self.replay = replay
        self.onevcs_bin = onevcs_bin
        copy = root / "scripts" / "actions-budget-figures.sh"
        copy.parent.mkdir(parents=True)
        shutil.copy(SCRIPT, copy)
        (root / "home").mkdir()

    def env(self, **extra: str) -> dict[str, str]:
        # Only what a runner would hand the job: no GITHUB_* of this suite's own
        # CI leaks in, so a case names every variable the script reads.
        return {
            "PATH": f"{self.onevcs_bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "ONEVCS_HOME": str(self.root / "home" / "onevcs"),
            "ONEVCS_GH": str(self.replay.program),
            "GH_TOKEN": TOKEN,
            **extra,
        }

    def check(self, budgets: list[dict], **env: str) -> tuple[int, list[dict], str]:
        """`onebudgetspec check --json` over `budgets`: exit code, results, stderr."""
        require("onebudgetspec", "jq", "bash")
        lines = ["schema_version: 1", "budgets:"]
        for budget in budgets:
            lines += [
                f"  - id: {budget['id']}",
                "    description: "
                + json.dumps(
                    f"The {budget['args'][0]} figure measured from the replayed"
                    " release's recordings, holding the figures script to the"
                    " value and detail its contract states."
                ),
                "    measure: reported",
                f"    command: {json.dumps(['bash', 'scripts/actions-budget-figures.sh', *budget['args']])}",
                f"    unit: {budget['unit']}",
                "    direction: max",
                f"    threshold: {budget.get('threshold', 10**9)}",
            ]
        (self.root / "budgets.yaml").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        done = subprocess.run(
            ["onebudgetspec", "check", "--json", "budgets.yaml"],
            cwd=self.root,
            capture_output=True,
            text=True,
            env=self.env(**env),
            timeout=600,
        )
        try:
            results = json.loads(done.stdout)["results"]
        except (json.JSONDecodeError, KeyError):
            pytest.fail(f"onebudgetspec answered no results: {done!r}")
        return done.returncode, results, done.stderr

    def figure(self, *args: str, unit: str, **env: str) -> dict:
        """One figure under `check`, its one result."""
        code, [result], stderr = self.check(
            [{"id": "figure", "args": list(args), "unit": unit}], **env
        )
        result["exit"] = code
        result["stderr"] = stderr
        return result
