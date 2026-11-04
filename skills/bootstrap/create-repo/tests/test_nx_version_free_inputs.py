"""Runtime inputs through the portable CLI, over tracked consumer files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "assets/ci/nx-version-free-inputs.mjs"


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    files = {
        "Cargo.toml": '[workspace]\nmembers = ["crates/member"]\n[workspace.package]\nversion = "1.0.0"\n[workspace.dependencies]\nthird = "2.0.0"\n',
        "crates/member/Cargo.toml": '[package]\nname = "member"\nversion.workspace = true\n',
        "Cargo.lock": 'version = 4\n[[package]]\nname = "member"\nversion = "1.0.0"\n[[package]]\nname = "third"\nversion = "2.0.0"\nsource = "registry+https://example.com"\n',
        "package.json": '{"name":"web","version":"1.0.0","dependencies":{"third":"2.0.0"}}',
        "bun.lock": '{"lockfileVersion":1,"workspaces":{"":{"name":"web","version":"1.0.0","dependencies":{"third":"2.0.0"}}},"packages":{"third":["third@2.0.0","",{},"hash"]}}',
        "package-lock.json": '{"name":"web","version":"1.0.0","lockfileVersion":3,"packages":{"":{"name":"web","version":"1.0.0","dependencies":{"third":"2.0.0"}},"node_modules/third":{"version":"2.0.0"}}}',
        "pyproject.toml": '[project]\nname = "wheel"\nversion = "1.0.0"\ndependencies = ["third==2.0.0"]\n',
        "uv.lock": 'version = 1\n[[package]]\nname = "wheel"\nversion = "1.0.0"\nsource = { editable = "." }\n[[package]]\nname = "third"\nversion = "2.0.0"\nsource = { registry = "https://example.com" }\n',
        ".github/nx-version-files": "src/version.py\n",
        "src/version.py": '__version__ = "1.0.0"\n',
        "CHANGELOG.md": "# Release 1.0.0\n",
    }
    for path, text in files.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def digest(repo, ecosystem, keep=False):
    done = subprocess.run(
        [
            "node",
            str(SCRIPT),
            "--ecosystem",
            ecosystem,
            *(["--keep-version"] if keep else []),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert len(done.stdout.strip()) == 64
    return done.stdout.strip(), done.stderr


@pytest.mark.parametrize("ecosystem", ["cargo", "npm", "python", "files"])
def test_release_edits_leave_digest_unchanged_but_keep_version_moves(repo, ecosystem):
    before, stderr = digest(repo, ecosystem)
    assert stderr == ""
    raw, _ = digest(repo, ecosystem, keep=True)
    for path in repo.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(repo).parts:
            path.write_text(path.read_text().replace("1.0.0", "1.0.1"))
    after, stderr = digest(repo, ecosystem)
    assert stderr == ""
    assert after == before
    assert digest(repo, ecosystem, keep=True)[0] != raw


@pytest.mark.parametrize(
    ("ecosystem", "path"),
    [("cargo", "Cargo.toml"), ("npm", "package.json"), ("python", "pyproject.toml")],
)
def test_dependency_edits_move_only_their_ecosystem(repo, ecosystem, path):
    before = {name: digest(repo, name)[0] for name in ["cargo", "npm", "python"]}
    file = repo / path
    file.write_text(file.read_text().replace("2.0.0", "2.0.1"))
    assert digest(repo, ecosystem)[0] != before[ecosystem]
    for other in before.keys() - {ecosystem}:
        assert digest(repo, other)[0] == before[other]


@pytest.mark.parametrize(
    ("ecosystem", "path"),
    [("cargo", "Cargo.toml"), ("npm", "package.json"), ("python", "pyproject.toml")],
)
def test_unparseable_input_falls_back_to_changing_raw_digest(repo, ecosystem, path):
    file = repo / path
    file.write_text("not a manifest")
    first, stderr = digest(repo, ecosystem)
    assert "hashing raw tracked inputs" in stderr
    assert first == digest(repo, ecosystem, keep=True)[0]
    file.write_text("still not a manifest")
    second, stderr = digest(repo, ecosystem)
    assert "hashing raw tracked inputs" in stderr
    assert second != first


def test_untracked_manifests_and_changelogs_are_never_read(repo):
    before = {name: digest(repo, name)[0] for name in ["cargo", "npm", "python"]}
    (repo / "ignored").mkdir()
    (repo / "ignored/package.json").write_text("not json")
    (repo / "CHANGELOG.md").write_text("A release")
    assert before == {name: digest(repo, name)[0] for name in before}


def test_literal_files_use_an_npm_workspace_version_without_cargo(repo):
    (repo / "Cargo.toml").unlink()
    (repo / "crates/member/Cargo.toml").unlink()
    subprocess.run(["git", "add", "-u"], cwd=repo, check=True)
    before, stderr = digest(repo, "files")
    assert stderr == ""
    package = repo / "package.json"
    package.write_text(package.read_text().replace("1.0.0", "1.0.1"))
    literal = repo / "src/version.py"
    literal.write_text(literal.read_text().replace("1.0.0", "1.0.1"))
    after, stderr = digest(repo, "files")
    assert stderr == ""
    assert before == after


def test_deleted_tracked_input_has_a_path_specific_raw_fallback(repo):
    before = digest(repo, "cargo")[0]
    (repo / "Cargo.lock").unlink()
    missing_lock, stderr = digest(repo, "cargo")
    assert "hashing raw tracked inputs" in stderr
    assert missing_lock != before
    (repo / "Cargo.toml").unlink()
    missing_both, stderr = digest(repo, "cargo")
    assert "hashing raw tracked inputs" in stderr
    assert missing_both != missing_lock


@pytest.mark.parametrize(
    "text",
    [
        'package = { name = "inline", version = "1.0.0" }\n',
        '[package]\nname = "multiline"\nversion = """\n1.0.0"""\n',
    ],
)
def test_unusual_valid_toml_uses_raw_digest_instead_of_partial_masking(repo, text):
    path = repo / "Cargo.toml"
    path.write_text(text)
    digest_before, stderr = digest(repo, "cargo")
    assert "unsupported" in stderr
    assert digest_before == digest(repo, "cargo", keep=True)[0]
    path.write_text(text.replace("1.0.0", "1.0.1"))
    assert digest(repo, "cargo")[0] != digest_before


def test_toml_table_structure_and_comments_remain_inputs(repo):
    before = digest(repo, "cargo")[0]
    path = repo / "Cargo.toml"
    path.write_text(
        path.read_text().replace("[workspace]", "[workspace] # build configuration")
    )
    assert digest(repo, "cargo")[0] != before


@pytest.mark.parametrize(
    ("ecosystem", "name", "text"),
    [
        (
            "cargo",
            "Cargo.toml",
            '[package]\nname="external-fixture"\nversion="0.0.1"\n',
        ),
        ("npm", "package.json", '{"name":"external-fixture","version":"0.0.1"}'),
        (
            "python",
            "pyproject.toml",
            '[project]\nname="external-fixture"\nversion="0.0.1"\n',
        ),
    ],
)
def test_fixture_package_versions_are_hashed_literally(repo, ecosystem, name, text):
    file = repo / "tests/fixtures/external" / name
    file.parent.mkdir(parents=True)
    file.write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    before = digest(repo, ecosystem)[0]
    file.write_text(text.replace("0.0.1", "0.0.2"))
    assert digest(repo, ecosystem)[0] != before


def test_package_named_metadata_is_a_real_input_not_a_version_field(repo):
    import json

    path = repo / "package.json"
    doc = json.loads(path.read_text())
    doc["metadata"] = {"web": "1.0.0"}
    path.write_text(json.dumps(doc))
    before = digest(repo, "npm")[0]
    for relative in ("package.json", "bun.lock", "package-lock.json"):
        file = repo / relative
        file.write_text(file.read_text().replace("1.0.0", "1.0.1"))
    assert digest(repo, "npm")[0] != before
