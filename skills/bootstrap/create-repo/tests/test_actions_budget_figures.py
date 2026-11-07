"""The GitHub Actions budget figures script's copy rule: its header, its versions
file, and the baseline checker's report on a repository's copy.

`assets/budgets/actions-budget-figures.sh` is copied into every repository that
measures its CI and CD, so a change to it reaches them only as a new version. Its
header carries `version` and the `sha256` of its body, and
`actions-budget-figures.versions` lists every version it has had. These tests hold
the source to both — a body change that adds no version fails here — and drive
`check_repo_baseline.py` over a repository's copy the way a consumer runs it. The
release figures are measured in `external/test_actions_budget_figures_e2e.py`; a pull
request's own run needs only `gh`, doubled here with a recorded run's jobs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_check_repo_baseline import SCRIPT as CHECKER
from test_check_repo_baseline import crb, make_repo

SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR.parents[2]
SOURCE = SKILL_DIR / "assets" / "budgets" / "actions-budget-figures.sh"
VERSIONS = SKILL_DIR / "assets" / "budgets" / "actions-budget-figures.versions"
SOURCE_LINE = (
    "# source: github.com/nickderobertis/dero-skills/skills/bootstrap/create-repo/"
    "assets/budgets/actions-budget-figures.sh"
)


def stamped(body: bytes, version: int) -> bytes:
    """A figures script with `body` under a header naming `version` and its digest."""
    header = SOURCE.read_bytes().split(b"\n")[:5]
    header[3] = f"# version: {version}".encode()
    header[4] = f"# sha256: {crb.figures_digest(body)}".encode()
    return b"\n".join([*header, body])


def drift(root: Path, source: str, versions: str, base: str) -> list[str]:
    """Why `source` and `versions` under the git work tree `root` break the copy rule.

    The source's header must match its body and be the versions file's last entry,
    and every revision of either file committed by `base` must still hold: each
    committed versions file a prefix of this one, each committed source's version
    and digest listed. So a body change that adds no version, or an entry that
    changes, is refused however the header was re-stamped.
    """
    problems = []
    header = crb.parse_figures_header((root / source).read_bytes())
    if header is None:
        return [f"{source} carries no version and sha256 header"]
    digest = crb.figures_digest(header.body)
    if digest != header.sha256:
        problems.append(
            f"{source}'s body digest is {digest}, its header says {header.sha256}"
        )
    entries = crb.read_figures_versions(root / versions)
    if [entry.version for entry in entries] != list(range(1, len(entries) + 1)):
        problems.append(f"{versions} does not number its versions 1, 2, 3, ...")
    if entries[-1:] != [crb.FiguresVersion(header.version, digest)]:
        problems.append(
            f"{versions} does not end with {source}'s version {header.version} "
            f"and digest {digest}: a body change adds a version"
        )

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout

    for path in (source, versions):
        for commit in git("log", "--format=%H", base, "--", path).split():
            shown = subprocess.run(
                ["git", "show", f"{commit}:{path}"], cwd=root, capture_output=True
            )
            if shown.returncode != 0:
                continue  # the path did not exist at that commit under this name
            if path == versions:
                committed = root / ".versions-at-commit"
                committed.write_bytes(shown.stdout)
                earlier = crb.read_figures_versions(committed)
                committed.unlink()
                if entries[: len(earlier)] != earlier:
                    problems.append(
                        f"{versions} changed an entry it held at {commit[:12]}"
                    )
            else:
                old = crb.parse_figures_header(shown.stdout)
                listed = old and crb.FiguresVersion(
                    old.version, crb.figures_digest(old.body)
                )
                if listed not in entries:
                    problems.append(
                        f"{source} at {commit[:12]} is no version {versions} lists"
                    )
    return problems


def merge_base() -> str:
    base = os.environ.get("NX_BASE", "origin/main")
    done = subprocess.run(
        ["git", "merge-base", "HEAD", base],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        pytest.fail(
            f"cannot resolve the merge base with {base} ({done.stderr.strip()}); "
            "fetch it, or set NX_BASE to the released commit"
        )
    return done.stdout.strip()


def test_the_source_header_names_its_source_version_and_body_digest():
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert "copied from dero-skills' create-repo" in lines[1].lower()
    assert "edited only there" in lines[1]
    assert lines[2] == SOURCE_LINE
    header = crb.parse_figures_header(SOURCE.read_bytes())
    assert header is not None
    assert lines[3:5] == [f"# version: {header.version}", f"# sha256: {header.sha256}"]
    assert os.access(SOURCE, os.X_OK), "the source is executable"


def test_the_source_and_its_versions_keep_the_copy_rule():
    rel = SOURCE.relative_to(REPO_ROOT).as_posix()
    versions = VERSIONS.relative_to(REPO_ROOT).as_posix()
    assert drift(REPO_ROOT, rel, versions, merge_base()) == []


@pytest.fixture
def history(tmp_path: Path):
    """A git repository holding the released source and versions file, and a
    function committing the next state of both."""
    root = tmp_path / "skill"
    (root / "assets").mkdir(parents=True)
    shutil.copy(SOURCE, root / "assets" / "figures.sh")
    shutil.copy(VERSIONS, root / "assets" / "figures.versions")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "dev",
                "GIT_AUTHOR_EMAIL": "dev@example.com",
                "GIT_COMMITTER_NAME": "dev",
                "GIT_COMMITTER_EMAIL": "dev@example.com",
            },
        )

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "released")

    def problems() -> list[str]:
        return drift(root, "assets/figures.sh", "assets/figures.versions", "HEAD")

    def commit() -> None:
        git("add", "-A")
        git("commit", "-q", "-m", "next")

    return root, problems, commit


def edited_body() -> bytes:
    body = crb.parse_figures_header(SOURCE.read_bytes()).body
    return body.replace(b"set -euo pipefail", b"set -euo pipefail\n: edited", 1)


def released_entries() -> str:
    return VERSIONS.read_text(encoding="utf-8")


def test_the_released_state_keeps_the_copy_rule(history):
    _root, problems, _commit = history
    assert problems() == []


def test_the_reference_states_the_command_lines_the_script_prints():
    """The contract's command lines in the reference are the script's own usage."""
    reference = (SKILL_DIR / "references" / "tools" / "onebudgetspec.md").read_text(
        encoding="utf-8"
    )
    section = reference.split("## GitHub Actions budget figures", 1)[1]
    stated = section.split("```\n", 2)[1].splitlines()
    usage = [
        line.removeprefix("#   ")
        for line in SOURCE.read_text(encoding="utf-8").splitlines()
        if line.startswith("#   scripts/actions-budget-figures.sh ")
    ]
    assert stated == usage
    done = subprocess.run(
        ["bash", str(SOURCE), "no-such-figure"],
        capture_output=True,
        text=True,
        env={**os.environ, "ONEBUDGETSPEC_RESULT": os.devnull},
    )
    assert done.stderr.splitlines()[1:] == [f"usage: {line}" for line in usage]


def test_a_body_change_that_adds_a_version_keeps_the_copy_rule(history):
    root, problems, commit = history
    version = len(crb.read_figures_versions(VERSIONS)) + 1
    body = edited_body()
    (root / "assets" / "figures.sh").write_bytes(stamped(body, version))
    (root / "assets" / "figures.versions").write_text(
        released_entries() + f"{version} {crb.figures_digest(body)}\n", encoding="utf-8"
    )
    assert problems() == []
    commit()
    assert problems() == []


def test_a_body_change_without_a_new_version_fails(history):
    root, problems, commit = history
    script = root / "assets" / "figures.sh"
    header = crb.parse_figures_header(script.read_bytes())

    # The body edited, the header left alone.
    body = edited_body()
    script.write_bytes(script.read_bytes().replace(header.body, body))
    assert any("its header says" in p for p in problems()), problems()

    # The header re-stamped at the same version, the versions file left alone.
    script.write_bytes(stamped(body, header.version))
    assert any("a body change adds a version" in p for p in problems()), problems()

    # The last entry rewritten in place to match: refused against what was released.
    versions = root / "assets" / "figures.versions"
    versions.write_text(
        released_entries().replace(header.sha256, crb.figures_digest(body)),
        encoding="utf-8",
    )
    assert any("changed an entry it held" in p for p in problems()), problems()
    commit()
    assert any("is no version" in p for p in problems()), problems()


def test_an_earlier_entry_that_changes_fails(history):
    root, problems, commit = history
    version = len(crb.read_figures_versions(VERSIONS)) + 1
    body = edited_body()
    (root / "assets" / "figures.sh").write_bytes(stamped(body, version))
    versions = root / "assets" / "figures.versions"
    versions.write_text(
        released_entries() + f"{version} {crb.figures_digest(body)}\n", encoding="utf-8"
    )
    commit()
    assert problems() == []

    first = crb.read_figures_versions(VERSIONS)[0]
    versions.write_text(
        versions.read_text(encoding="utf-8").replace(
            f"{first.version} {first.sha256}", f"{first.version} {'0' * 64}"
        ),
        encoding="utf-8",
    )
    assert any("changed an entry it held" in p for p in problems()), problems()


# --- check_repo_baseline.py over a repository's copy --------------------------


def checked(repo: Path, checker: Path = CHECKER) -> subprocess.CompletedProcess[str]:
    """The checker's CLI, run the way a consuming repository runs it."""
    return subprocess.run(
        ["uv", "run", "--script", str(checker), str(repo)],
        capture_output=True,
        text=True,
        timeout=300,
    )


def baseline_repo(tmp_path: Path) -> Path:
    """A repository conformant to the baseline, beside the case's other files."""
    root = tmp_path / "repo"
    root.mkdir()
    return make_repo(root)


def with_copy(tmp_path: Path, script: bytes) -> Path:
    repo = baseline_repo(tmp_path)
    (repo / crb.FIGURES_COPY).write_bytes(script)
    return repo


def test_an_unmodified_copy_passes(tmp_path):
    repo = with_copy(tmp_path, SOURCE.read_bytes())
    done = checked(repo)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "actions-budget-figures" not in done.stdout + done.stderr
    [finding] = crb.check_actions_budget_figures(repo)
    assert finding.level == "OK", finding


def test_a_copy_with_one_body_line_changed_is_reported_edited(tmp_path):
    source = SOURCE.read_bytes()
    header = crb.parse_figures_header(source)
    repo = with_copy(tmp_path, source.replace(header.body, edited_body()))
    done = checked(repo)
    assert done.returncode == 1, done.stdout + done.stderr
    assert (
        "scripts/actions-budget-figures.sh was edited in place: its body does not "
        "match its own `sha256` line"
    ) in done.stderr


def test_a_copy_at_a_version_the_versions_file_does_not_list_is_reported_unknown(
    tmp_path,
):
    released = len(crb.read_figures_versions(VERSIONS))
    body = edited_body()
    repo = with_copy(tmp_path, stamped(body, released))
    done = checked(repo)
    assert done.returncode == 1, done.stdout + done.stderr
    assert (
        f"scripts/actions-budget-figures.sh is unknown: version {released} with "
        f"sha256 {crb.figures_digest(body)} is no version the source has had"
    ) in done.stderr


def test_a_copy_at_an_older_listed_version_is_reported_outdated(tmp_path):
    # The skill as it is after releasing one more version: the checker beside a
    # versions file whose last entry is newer than the repository's copy.
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "assets" / "budgets").mkdir(parents=True)
    checker = skill / "scripts" / CHECKER.name
    shutil.copy(CHECKER, checker)
    released = len(crb.read_figures_versions(VERSIONS))
    newer = crb.figures_digest(edited_body())
    (skill / "assets" / "budgets" / VERSIONS.name).write_text(
        released_entries() + f"{released + 1} {newer}\n", encoding="utf-8"
    )

    repo = with_copy(tmp_path, SOURCE.read_bytes())
    done = checked(repo, checker)
    assert done.returncode == 0, done.stdout + done.stderr  # advisory
    assert (
        f"WARN  scripts/actions-budget-figures.sh is outdated: it is at version "
        f"{released}, the source at version {released + 1}"
    ) in done.stdout
    assert "fix: copy the create-repo skill's assets/budgets/" in done.stdout


def test_a_checker_without_the_versions_file_says_currency_is_unknown(tmp_path):
    # The checker copied out of the skill alone, without its assets.
    checker = tmp_path / "loose" / CHECKER.name
    checker.parent.mkdir()
    shutil.copy(CHECKER, checker)
    repo = with_copy(tmp_path, SOURCE.read_bytes())
    done = checked(repo, checker)
    assert done.returncode == 0, done.stdout + done.stderr
    released = crb.parse_figures_header(SOURCE.read_bytes()).version
    assert (
        f"WARN  scripts/actions-budget-figures.sh is at version {released}, but "
    ) in done.stdout
    assert "is missing, so whether it is current is unknown" in done.stdout


def test_a_repository_without_a_copy_is_not_reported(tmp_path):
    assert crb.check_actions_budget_figures(baseline_repo(tmp_path)) == []


def test_a_copy_without_the_header_is_reported(tmp_path):
    repo = with_copy(tmp_path, b"#!/usr/bin/env bash\necho figures\n")
    [finding] = crb.check_actions_budget_figures(repo)
    assert finding.level == "ERROR"
    assert "carries no `# version:` and `# sha256:` header" in finding.message


# --- invocation -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        ([], "name a figure"),
        (["no-such-figure"], "unknown figure: no-such-figure"),
        (["job-count", "--stage", "cd", "--bogus", "x"], "unknown argument: --bogus"),
        (["job-count", "--release"], "--release needs a value"),
        (
            ["job-count", "--release", "v1.0.0"],
            "job-count needs --stage pr or --stage cd",
        ),
        (["job-count", "--stage", "ci"], "job-count needs --stage pr or --stage cd"),
        (
            ["release-cycle-time", "--stage", "pr"],
            "release-cycle-time takes no --stage",
        ),
        (
            ["pr-read-requests", "--now", "2026-10-15T07:20:03Z"],
            "pr-read-requests takes no --now",
        ),
        (
            ["longest-job", "--stage", "pr", "--now", "2026-10-15 07:20:03"],
            "--now must be an RFC 3339 UTC timestamp",
        ),
        (["pr-read-requests", "--repo", "onepipeline"], "--repo must be OWNER/NAME"),
    ],
)
def test_an_invalid_invocation_exits_2_naming_what_is_wrong(tmp_path, args, reason):
    """Refused before onevcs is asked anything: `ONEVCS` names a program that
    records any call, and none is made."""
    calls = tmp_path / "calls"
    onevcs = tmp_path / "onevcs"
    onevcs.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n', encoding="utf-8")
    onevcs.chmod(0o755)
    result = tmp_path / "result.json"
    done = subprocess.run(
        ["bash", str(SOURCE), *args],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "ONEVCS": str(onevcs),
            "ONEBUDGETSPEC_RESULT": str(result),
        },
    )
    assert done.returncode == 2, done
    assert reason in done.stderr, done.stderr
    assert "usage: scripts/actions-budget-figures.sh longest-job" in done.stderr
    assert not calls.exists() and not result.exists()


# --- what the script is handed: refused before it can reach a figure -----------

SHA = "d8d674f1f0b46fff580540ab70137ebc91e6b15b"
HEAD = "4e3dee6c0842603388d96db3d4e8a6f64aac99d2"


def release_changes(**change: object) -> dict:
    """A release-changes answer of onevcs's shape carrying one change."""
    return {
        "schema_version": 1,
        "repository": "owner/name",
        "tag": "v1.0.0",
        "commit": SHA,
        "commits": [{"sha": SHA, "change": 1}],
        "changes": [
            {"number": 1, "head_sha": HEAD, "merged_at": "2026-10-14T22:42:57Z"}
            | change
        ],
        "cost": {"graphql_points": 0, "rest_requests": 3, "requests": []},
    }


def handed(tmp_path: Path, answer: object, *args: str, **env: str):
    """The script, with `ONEVCS` naming a program that answers every query with
    `answer`: the shapes a broken or different onevcs could hand it, which the real
    release never answers."""
    if args[0] == "release-cycle-time":
        published = tmp_path / "published-paths"
        published.write_text("src/**\n")
        args = (*args, "--published-paths", str(published))
    answer_file = tmp_path / "answer.json"
    answer_file.write_text(
        answer if isinstance(answer, str) else json.dumps(answer), encoding="utf-8"
    )
    onevcs = tmp_path / "onevcs"
    onevcs.write_text(f"#!/bin/sh\ncat {answer_file}\n", encoding="utf-8")
    onevcs.chmod(0o755)
    result = tmp_path / "result.json"
    done = subprocess.run(
        ["bash", str(SOURCE), *args],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "ONEVCS": str(onevcs),
            "ONEBUDGETSPEC_RESULT": str(result),
            **env,
        },
    )
    assert not result.exists(), "a refused figure writes nothing"
    return done


RELEASE_ARGS = ["--release", "v1.0.0", "--repo", "owner/name"]


@pytest.mark.parametrize(
    ("answer", "args", "reason"),
    [
        (
            {"answer": "of another schema"},
            ["job-count", "--stage", "pr"],
            "onevcs host release-changes --tag v1.0.0 answered no schema_version 1 "
            "document",
        ),
        (
            release_changes() | {"commit": "v1.0.0"},
            ["job-count", "--stage", "cd"],
            "onevcs host release-changes answered no commit SHA: v1.0.0",
        ),
        (
            release_changes(number="one"),
            ["job-count", "--stage", "pr"],
            "answered a change with no number or head SHA: one " + HEAD,
        ),
        (
            release_changes(head_sha="main"),
            ["release-cycle-time"],
            "answered a change with no number or head SHA: 1 main",
        ),
        (
            release_changes(merged_at=None),
            ["pr-read-requests"],
            "missing: #1 has no merged_at",
        ),
    ],
)
def test_an_answer_the_script_cannot_trust_ends_it_writing_nothing(
    tmp_path, answer, args, reason
):
    done = handed(tmp_path, answer, *args, *RELEASE_ARGS)
    assert done.returncode == 1, done
    assert reason in done.stderr, done.stderr


def commit_runs(*workflow_names: object, **run: object) -> dict:
    """One document answering both release-changes and commit-runs, as `handed`
    serves every query the same answer: one push run per workflow name given,
    concluded success and updated with `run`."""
    runs = [
        {"id": index, "workflow_name": name, "event": "push", "conclusion": "success"}
        | run
        for index, name in enumerate(workflow_names, start=1)
    ]
    return release_changes() | {"runs": runs}


NO_WORKFLOW_NAME = (
    "onevcs host commit-runs answered a run with no workflow name, so "
    "--exclude-workflow cannot be applied"
)


@pytest.mark.parametrize("name", [None, ""])
@pytest.mark.parametrize(
    "figure", [["job-count", "--stage", "cd"], ["release-read-requests"]]
)
def test_a_run_with_no_workflow_name_ends_an_exclusion_writing_nothing(
    tmp_path, name, figure
):
    answer = commit_runs("Release", name)
    args = [*figure, *RELEASE_ARGS, "--exclude-workflow", "warmer"]
    done = handed(tmp_path, answer, *args)
    assert done.returncode == 1, done
    assert NO_WORKFLOW_NAME in done.stderr, done.stderr
    assert "install an onevcs release" in done.stderr, done.stderr


def test_without_an_exclusion_a_run_with_no_workflow_name_is_not_refused_for_it(
    tmp_path,
):
    done = handed(tmp_path, commit_runs("Release", None), "job-count", "--stage", "cd")
    assert NO_WORKFLOW_NAME not in done.stderr, done.stderr


NO_CONCLUSION = (
    "onevcs host commit-runs answered a run with no conclusion, so a run "
    "concluded skipped cannot be left out"
)


def no_conclusion() -> dict:
    """A commit-runs answer whose one run carries no conclusion field at all."""
    answer = commit_runs("Release")
    del answer["runs"][0]["conclusion"]
    return answer


@pytest.mark.parametrize(
    "answer",
    [no_conclusion(), commit_runs("Release", conclusion=0)],
    ids=["absent", "not-a-string"],
)
@pytest.mark.parametrize(
    "figure",
    [
        ["job-count", "--stage", "cd"],
        ["longest-job", "--stage", "cd"],
        ["release-read-requests"],
    ],
)
def test_a_run_with_no_conclusion_ends_a_cd_figure_writing_nothing(
    tmp_path, answer, figure
):
    done = handed(tmp_path, answer, *figure, *RELEASE_ARGS)
    assert done.returncode == 1, done
    assert NO_CONCLUSION in done.stderr, done.stderr
    assert "install an onevcs release" in done.stderr, done.stderr


def test_a_run_still_in_progress_is_not_refused_for_its_null_conclusion(tmp_path):
    answer = commit_runs("Release", conclusion=None)
    done = handed(tmp_path, answer, "job-count", "--stage", "cd", *RELEASE_ARGS)
    assert NO_CONCLUSION not in done.stderr, done.stderr


def test_a_release_payload_that_is_not_json_ends_it_naming_the_payload(tmp_path):
    payload = tmp_path / "event.json"
    payload.write_text("not json", encoding="utf-8")
    done = handed(
        tmp_path,
        release_changes(),
        "job-count",
        "--stage",
        "cd",
        "--repo",
        "owner/name",
        GITHUB_EVENT_PATH=str(payload),
    )
    assert done.returncode == 1, done
    assert f"the event payload {payload} is not JSON" in done.stderr


def test_help_prints_the_contract_lines_and_needs_no_check(tmp_path):
    done = subprocess.run(
        ["bash", str(SOURCE), "--help"],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"]},
    )
    assert done.returncode == 0, done
    assert "  scripts/actions-budget-figures.sh pr-read-requests" in done.stdout
    assert "Needs bash, jq and `onevcs`" in done.stdout


def test_without_jq_it_ends_naming_jq(tmp_path):
    # A PATH holding no jq; the script reaches the check on shell builtins alone.
    done = subprocess.run(
        [shutil.which("bash"), str(SOURCE), "job-count", "--stage", "cd"],
        capture_output=True,
        text=True,
        env={"PATH": str(tmp_path), "ONEBUDGETSPEC_RESULT": str(tmp_path / "r")},
    )
    assert done.returncode == 1, done
    assert "jq is not on PATH" in done.stderr


def test_without_onevcs_it_ends_saying_how_to_provide_it(tmp_path):
    done = subprocess.run(
        ["bash", str(SOURCE), "job-count", "--stage", "cd", *RELEASE_ARGS],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "ONEVCS": str(tmp_path / "no-such-onevcs"),
            "ONEBUDGETSPEC_RESULT": str(tmp_path / "r"),
        },
    )
    assert done.returncode == 1, done
    assert "onevcs is not on PATH" in done.stderr
    assert "name the binary in ONEVCS" in done.stderr


def test_without_a_repository_it_ends_naming_repo(tmp_path):
    done = handed(tmp_path, release_changes(), "job-count", "--stage", "cd")
    assert done.returncode == 1, done
    assert "no repository: pass --repo OWNER/NAME" in done.stderr


@pytest.mark.parametrize(
    ("name", "version"), [("nx-remote-cache", 7), ("nx-version-free-inputs", 3)]
)
def test_shared_cache_scripts_are_versioned_and_checked(tmp_path, name, version):
    source = SKILL_DIR / "assets" / "ci" / f"{name}.mjs"
    versions = source.with_suffix(".versions")
    header = crb.parse_figures_header(source.read_bytes())
    assert header is not None
    assert header.version == version
    assert crb.figures_digest(header.body) == header.sha256
    entries = crb.read_figures_versions(versions)
    assert [entry.version for entry in entries] == list(range(1, version + 1))
    assert entries[-1] == crb.FiguresVersion(version, header.sha256)
    target = tmp_path / "scripts" / source.name
    target.parent.mkdir()
    shutil.copyfile(source, target)
    findings = crb.check_actions_budget_figures(
        tmp_path, versions, f"scripts/{source.name}", f"assets/ci/{source.name}"
    )
    assert [finding.level for finding in findings] == ["OK"]
    target.write_bytes(target.read_bytes() + b"\n// edited\n")
    findings = crb.check_actions_budget_figures(
        tmp_path, versions, f"scripts/{source.name}", f"assets/ci/{source.name}"
    )
    assert [finding.level for finding in findings] == ["ERROR"]
    assert "edited in place" in findings[0].message


@pytest.mark.parametrize("version", [10, 11])
def test_an_earlier_released_copy_is_outdated_against_twelve(tmp_path, version):
    old = (
        Path(__file__).parent / f"fixtures/actions-budget-figures/v{version}.sh"
    ).read_bytes()
    assert crb.parse_figures_header(old).version == version
    repo = with_copy(tmp_path, old)
    done = checked(repo)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (
        f"outdated: it is at version {version}, the source at version 12" in done.stdout
    )


IN_PROGRESS_RECORDING = (
    SKILL_DIR
    / "tests/external/fixtures/actions-figures/recordings/recorded-for-actions-figures"
    / "onepipeline-run-39212146687-jobs-in-progress.json"
)
OWN_RUN = "39212146687"
OWN_REPO = "nickderobertis/onepipeline"
OWN_ENV = {
    "GITHUB_EVENT_NAME": "pull_request",
    "GITHUB_RUN_ID": OWN_RUN,
    "GITHUB_REPOSITORY": OWN_REPO,
    "RUNNER_NAME": "GitHub Actions 1000251012",
}


LISTED_JOB = {
    "name": "leg",
    "workflow_name": "ci",
    "status": "completed",
    "conclusion": "success",
    "runner_name": "r",
    "labels": ["ubuntu-latest"],
    "started_at": "2026-10-19T06:00:00Z",
    "completed_at": "2026-10-19T06:00:10Z",
}


class GhDouble:
    """`gh` on PATH, answering each `gh api` with the next listing and recording it."""

    def __init__(self, root: Path, *answers: object, status: int = 0, says: str = ""):
        self.bin = root / "gh-bin"
        self.bin.mkdir()
        self.calls = root / "gh-calls"
        self.answers = root / "gh-answers"
        self.answers.mkdir()
        for at, answer in enumerate(answers, 1):
            (self.answers / str(at)).write_text(json.dumps(answer), encoding="utf-8")
        gh = self.bin / "gh"
        gh.write_text(
            f'''#!/bin/bash
echo "$*" >> "{self.calls}"
[ {status} -eq 0 ] || {{ echo "{says}" >&2; exit {status}; }}
cat "{self.answers}/$(wc -l < "{self.calls}")"
''',
            encoding="utf-8",
        )
        gh.chmod(0o755)

    def read(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []


def recorded_run_jobs() -> dict:
    return json.loads(IN_PROGRESS_RECORDING.read_text(encoding="utf-8"))["body"]


def own_figure(
    tmp_path: Path, gh: GhDouble, *args: str, **env: str
) -> subprocess.CompletedProcess:
    """The source script, with no onevcs to fall back on and only `gh` to read."""
    missing_onevcs = tmp_path / "no-onevcs"
    return subprocess.run(
        ["bash", str(SOURCE), *args],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{gh.bin}{os.pathsep}{os.environ['PATH']}",
            "ONEVCS": str(missing_onevcs),
            "ONEBUDGETSPEC_RESULT": str(tmp_path / "result.json"),
            "RUNNER_TEMP": str(tmp_path),
            **OWN_ENV,
            **env,
        },
    )


def own_result(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))


def test_job_count_on_a_pull_request_counts_its_own_run_and_the_running_jobs(tmp_path):
    gh = GhDouble(tmp_path, recorded_run_jobs())
    done = own_figure(tmp_path, gh, "job-count", "--stage", "pr")
    assert done.returncode == 0, done.stderr
    assert gh.read() == [
        f"api repos/{OWN_REPO}/actions/runs/{OWN_RUN}/jobs?per_page=100&filter=latest"
    ]
    result = own_result(tmp_path)
    # 8 completed that a runner took, and all 3 in progress; 8 skipped jobs leave it out.
    assert result["value"] == 11
    assert result["detail"].splitlines() == [
        "11 jobs",
        "per workflow:",
        "  CI: 11",
        "per runner OS:",
        "  Linux: 9",
        "  Windows: 1",
        "  macOS: 1",
    ]


def test_longest_job_on_a_pull_request_never_times_a_job_still_running(tmp_path):
    # Mac PR proofs is the job this names, so version 11 would time it to --now.
    gh = GhDouble(tmp_path, recorded_run_jobs())
    done = own_figure(
        tmp_path,
        gh,
        "longest-job",
        "--stage",
        "pr",
        "--now",
        "2026-10-19T09:49:46Z",
    )
    assert done.returncode == 0, done.stderr
    assert len(gh.read()) == 1
    result = own_result(tmp_path)
    assert result["value"] == 263  # gate, 06:49:27 to 06:53:50
    lines = result["detail"].splitlines()
    assert lines[0] == "CI / gate: 263s"
    assert "longest per runner OS:" in lines
    assert "  Linux: CI / gate: 263s" in lines
    assert not any("Mac PR proofs" in line or "in_progress" in line for line in lines)


def test_a_run_past_100_jobs_pages_only_as_far_as_its_total(tmp_path):
    first = {"total_count": 103, "jobs": [LISTED_JOB] * 100}
    second = {"total_count": 103, "jobs": [LISTED_JOB] * 3}
    gh = GhDouble(tmp_path, first, second)
    done = own_figure(tmp_path, gh, "job-count", "--stage", "pr")
    assert done.returncode == 0, done.stderr
    assert own_result(tmp_path)["value"] == 103
    base = (
        f"api repos/{OWN_REPO}/actions/runs/{OWN_RUN}/jobs?per_page=100&filter=latest"
    )
    assert gh.read() == [base, f"{base}&page=2"]


def test_a_listing_past_its_total_writes_no_figure(tmp_path):
    gh = GhDouble(tmp_path, {"total_count": 1, "jobs": [LISTED_JOB] * 2})
    done = own_figure(tmp_path, gh, "job-count", "--stage", "pr")
    assert done.returncode == 1, done.stderr
    assert "listed 2 jobs of a run of 1" in done.stderr
    assert not (tmp_path / "result.json").exists()


def test_a_total_that_changes_between_pages_writes_no_figure(tmp_path):
    gh = GhDouble(
        tmp_path,
        {"total_count": 103, "jobs": [LISTED_JOB] * 100},
        {"total_count": 104, "jobs": [LISTED_JOB] * 3},
    )
    done = own_figure(tmp_path, gh, "job-count", "--stage", "pr")
    assert done.returncode == 1, done.stderr
    assert "answered a total of 104 jobs after one of 103" in done.stderr
    assert not (tmp_path / "result.json").exists()


def test_a_listing_short_of_its_total_writes_no_figure(tmp_path):
    gh = GhDouble(
        tmp_path,
        {
            "total_count": 103,
            "jobs": [{"name": "leg", "workflow_name": "ci", "status": "queued"}] * 100,
        },
        {"total_count": 103, "jobs": []},
    )
    done = own_figure(tmp_path, gh, "job-count", "--stage", "pr")
    assert done.returncode == 1, done.stderr
    assert "listed 100 of the run's 103 jobs" in done.stderr
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        {"jobs": []},
        {"total_count": 1.5, "jobs": [{}]},
        {"total_count": -1, "jobs": []},
        {"total_count": 1, "jobs": "none"},
        {"total_count": 1, "jobs": [{"name": "no status", "workflow_name": "ci"}]},
        {"total_count": 1, "jobs": [dict(LISTED_JOB, labels=[1])]},
        {"total_count": 1, "jobs": [dict(LISTED_JOB, conclusion=1)]},
        {"total_count": 1, "jobs": [dict(LISTED_JOB, runner_name=1)]},
        {"total_count": 1, "jobs": [dict(LISTED_JOB, completed_at="yesterday")]},
        {"total_count": 1, "jobs": [dict(LISTED_JOB, started_at=None)]},
    ],
    ids=[
        "not JSON",
        "no count",
        "a part count",
        "a negative count",
        "no list",
        "no status",
        "labels that are no strings",
        "a conclusion that is no string",
        "a runner that is no string",
        "an end that is no timestamp",
        "no start",
    ],
)
def test_an_answer_that_is_no_job_listing_writes_no_figure(tmp_path, answer):
    gh = GhDouble(tmp_path, answer)
    done = own_figure(tmp_path, gh, "job-count", "--stage", "pr")
    assert done.returncode == 1, done.stderr
    assert "answered no job listing" in done.stderr
    assert not (tmp_path / "result.json").exists()


def test_without_gh_a_pull_requests_own_run_says_how_to_provide_it(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    for tool in ("jq", "bash", "date", "mktemp", "rm", "sed", "tr"):
        (tools / tool).symlink_to(shutil.which(tool))
    done = subprocess.run(
        [str(tools / "bash"), str(SOURCE), "job-count", "--stage", "pr"],
        capture_output=True,
        text=True,
        env={
            "PATH": str(tools),
            "ONEBUDGETSPEC_RESULT": str(tmp_path / "result.json"),
            **OWN_ENV,
        },
    )
    assert done.returncode == 1, done.stderr
    assert "gh is not on PATH: install GitHub CLI" in done.stderr
    assert not (tmp_path / "result.json").exists()


def test_a_refused_read_ends_it_with_gh_s_reason_and_no_figure(tmp_path):
    gh = GhDouble(tmp_path, status=4, says="HTTP 403: Resource not accessible")
    done = own_figure(tmp_path, gh, "longest-job", "--stage", "pr")
    assert done.returncode == 1, done.stderr
    assert "HTTP 403: Resource not accessible" in done.stderr
    assert not (tmp_path / "result.json").exists()


def test_a_run_with_only_the_running_job_has_a_count_and_no_longest(tmp_path):
    running = {
        "name": "budgets",
        "workflow_name": "ci",
        "status": "in_progress",
        "conclusion": None,
        "runner_name": "r",
        "labels": ["ubuntu-latest"],
        "started_at": "2026-10-19T06:00:00Z",
        "completed_at": None,
    }
    answer = {"total_count": 1, "jobs": [running]}
    gh = GhDouble(tmp_path, answer, answer)
    count = own_figure(tmp_path, gh, "job-count", "--stage", "pr")
    assert count.returncode == 0, count.stderr
    assert own_result(tmp_path)["value"] == 1
    (tmp_path / "result.json").unlink()
    longest = own_figure(tmp_path, gh, "longest-job", "--stage", "pr")
    assert longest.returncode == 1, longest.stderr
    assert "no counted job" in longest.stderr
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize(
    "env",
    [
        {"GITHUB_EVENT_NAME": "push"},
        {"GITHUB_EVENT_NAME": "release"},
        {"GITHUB_RUN_ID": ""},
        {"GITHUB_REPOSITORY": ""},
    ],
    ids=["a push", "a release", "no run id", "no repository"],
)
def test_without_the_whole_pull_request_environment_gh_is_never_read(tmp_path, env):
    gh = GhDouble(tmp_path, recorded_run_jobs())
    done = own_figure(
        tmp_path,
        gh,
        "job-count",
        "--stage",
        "pr",
        "--repo",
        OWN_REPO,
        "--release",
        "v1.0.0",
        **env,
    )
    assert done.returncode == 1
    assert "onevcs is not on PATH" in done.stderr
    assert gh.read() == []


@pytest.mark.parametrize(
    "args",
    [
        ["longest-job", "--stage", "cd", "--release", "v1.0.0"],
        ["job-count", "--stage", "cd", "--release", "v1.0.0"],
        ["pr-read-requests", "--release", "v1.0.0"],
        ["release-read-requests", "--release", "v1.0.0"],
    ],
    ids=lambda args: " ".join(args[:3]),
)
def test_every_other_figure_on_a_pull_request_still_asks_onevcs(tmp_path, args):
    gh = GhDouble(tmp_path, recorded_run_jobs())
    done = own_figure(tmp_path, gh, *args)
    assert done.returncode == 1
    assert "onevcs is not on PATH" in done.stderr
    assert gh.read() == []


def test_cache_use_missing_node_reports_action_without_writing(tmp_path):
    result = tmp_path / "result.json"
    done = subprocess.run(
        [shutil.which("bash"), str(SOURCE), "cache-use"],
        env={**os.environ, "PATH": str(tmp_path), "ONEBUDGETSPEC_RESULT": str(result)},
        capture_output=True,
        text=True,
    )
    assert done.returncode == 1
    assert "node is not on PATH" in done.stderr
    assert "install Node.js 24 and rerun this budget" in done.stderr
    assert not result.exists()


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6])
def test_remote_cache_previous_copy_is_outdated(tmp_path, version):
    repo = baseline_repo(tmp_path)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    old = Path(__file__).parent / f"fixtures/nx-remote-cache/v{version}.mjs"
    shutil.copyfile(old, repo / "scripts/nx-remote-cache.mjs")
    done = checked(repo)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (
        f"scripts/nx-remote-cache.mjs is outdated: it is at version {version}, the source at version 7"
        in done.stdout
    )
