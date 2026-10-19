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
    if [version for version, _ in entries] != list(range(1, len(entries) + 1)):
        problems.append(f"{versions} does not number its versions 1, 2, 3, ...")
    if entries[-1:] != [(header.version, digest)]:
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
                if old is None or (old.version, crb.figures_digest(old.body)) not in (
                    entries
                ):
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
            f"{first[0]} {first[1]}", f"{first[0]} {'0' * 64}"
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


def test_a_repository_without_a_copy_is_not_reported(tmp_path):
    assert crb.check_actions_budget_figures(baseline_repo(tmp_path)) == []


def test_a_copy_without_the_header_is_reported(tmp_path):
    repo = with_copy(tmp_path, b"#!/usr/bin/env bash\necho figures\n")
    [finding] = crb.check_actions_budget_figures(repo)
    assert finding.level == "ERROR"
    assert "carries no `# version:` and `# sha256:` header" in finding.message
