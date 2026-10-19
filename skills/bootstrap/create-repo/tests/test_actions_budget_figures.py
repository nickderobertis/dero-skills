"""The GitHub Actions budget figures script's copy rule: its header, its versions
file, and the baseline checker's report on a repository's copy.

`assets/budgets/actions-budget-figures.sh` is copied into every repository that
measures its CI and CD, so a change to it reaches them only as a new version. Its
header carries `version` and the `sha256` of its body, and
`actions-budget-figures.versions` lists every version it has had. These tests hold
the source to both — a body change that adds no version fails here — and drive
`check_repo_baseline.py` over a repository's copy the way a consumer runs it. The
figures themselves are measured in `external/test_actions_budget_figures_e2e.py`.
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
            ["release-cycle-time", "--now", "2026-10-15 07:20:03"],
            "--now must be an RFC 3339 UTC timestamp",
        ),
        (["release-cycle-time", "--repo", "onepipeline"], "--repo must be OWNER/NAME"),
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
