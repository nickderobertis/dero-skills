"""The onebudgetspec surface the skill restates, held to the sources it copies.

Offline drift gates, so they run in the fast tier on every change that can
reach them:

* The release's README ships as the `onebudgetspec-cli` wheel's METADATA, and
  this repo's dev group pins that wheel to the release the composer pins (see
  test_compose_repo_plan.py's lockstep test). Every name the reference gives for
  the release's surface, and the target defaults the composer writes, are read
  against that README.
* The composer's table of Nx input objects is read against Nx's own schema, the
  checker's reading of `bun.lock` against this repo's own bun-written lock, and
  its SemVer pattern against node's `semver` — all from the `node_modules` the
  gate already runs Nx from — and its PEP 440 pattern against `packaging`.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from packaging.version import InvalidVersion, Version

SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR.parents[2]
REFERENCE = SKILL_DIR / "references" / "tools" / "onebudgetspec.md"
NX_SCHEMA = REPO_ROOT / "node_modules" / "nx" / "schemas" / "nx-schema.json"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{name}_release", SKILL_DIR / "scripts" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


crp = _load("compose_repo_plan")
crb = _load("check_repo_baseline")


def release_readme() -> str:
    """The README of the onebudgetspec release the composer pins."""
    assert metadata.version("onebudgetspec-cli") == crp.ONEBUDGETSPEC_VERSION, (
        "the installed onebudgetspec-cli is not the release the composer pins; "
        "run `uv sync --locked`"
    )
    readme = metadata.metadata("onebudgetspec-cli").get_payload()
    assert isinstance(readme, str) and readme.strip(), "the wheel ships no README"
    return readme


def test_the_reference_names_the_releases_surface():
    readme = release_readme()
    reference = REFERENCE.read_text(encoding="utf-8")
    signatures = re.findall(r"^\| [^|]+ \| `([^`]+)`", reference, re.MULTILINE)
    assert len(signatures) == 3, signatures
    for signature in signatures:
        assert f"`{signature}`" in readme, signature
    for name in (
        "ONEBUDGETSPEC_BUDGET_ID",
        "ONEBUDGETSPEC_RESULT",
        "--exclude-label host",
        "--label host",
        "nx affected -t budgets budgets-host",
        "onebudgetspec check budgets.yaml",
    ):
        assert name in reference, name
        assert name in readme, name


def test_the_wired_target_defaults_are_the_readmes_nx_example():
    readme = release_readme()
    [example] = [
        json.loads(block)
        for block in re.findall(r"```json\n(.*?)```", readme, re.DOTALL)
        if '"budgets-host"' in block
    ]
    budgets = example["targets"]["budgets"]
    host = example["targets"]["budgets-host"]
    assert crp.ONEBUDGETSPEC_TARGET_DEFAULTS == {
        "budgets": {"cache": budgets["cache"], "inputs": budgets["inputs"]},
        "budgets-host": {"cache": host["cache"]},
    }
    # The reference's caching section names each of those inputs.
    reference = REFERENCE.read_text(encoding="utf-8")
    for cache_input in budgets["inputs"]:
        written = (
            cache_input if isinstance(cache_input, str) else json.dumps(cache_input)
        )
        assert written.strip("{}").strip() in reference, written


def _schema_types(prop: dict) -> frozenset[str]:
    """The JSON types one Nx input property takes; every array is of strings."""
    shapes = prop.get("oneOf", [prop])
    for shape in shapes:
        if shape["type"] == "array":
            assert shape["items"] == {"type": "string"}, shape
    return frozenset(shape["type"] for shape in shapes)


def test_the_nx_input_objects_the_composer_accepts_are_nxs_own():
    schema = json.loads(NX_SCHEMA.read_text(encoding="utf-8"))
    variants = [
        variant
        for variant in schema["definitions"]["inputs"]["items"]["oneOf"]
        if variant.get("type") == "object"
    ]
    from_schema = {
        frozenset(
            (name, _schema_types(prop)) for name, prop in variant["properties"].items()
        )
        for variant in variants
    }
    from_composer = {
        frozenset(properties.items()) for properties in crp.NX_INPUT_OBJECTS.values()
    }
    assert from_composer == from_schema
    # The one kind with alternatives: `input` with `projects`, with
    # `dependencies`, or alone — never with both.
    [input_variant] = [v for v in variants if "input" in v["properties"]]
    alternatives = {frozenset(o["required"]) for o in input_variant["oneOf"]}
    assert alternatives == {
        frozenset({"input"}),
        *(frozenset({"input", other}) for other in crp.NX_INPUT_EXCLUSIVE),
    }


def test_the_checker_reads_a_bun_written_lock():
    # This repo's bun.lock is written by the real `bun install`; what the checker
    # reads from it must be what bun installed.
    for name in ("nx", "@commitlint/cli"):
        installed = json.loads(
            (REPO_ROOT / "node_modules" / name / "package.json").read_text(
                encoding="utf-8"
            )
        )["version"]
        assert crb.bun_lock_resolution(REPO_ROOT / "bun.lock", name) == installed


# Release versions and near misses, each read by both version patterns.
VERSIONS = [
    *("0.1.3", "1.0.0", "10.20.30", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-0.3.7"),
    *("1.0.0-x.7.z.92", "1.0.0-a.b-c", "1.0.0+20130313144700", "1.0.0-beta+exp.5"),
    *("1.0.0-01", "01.0.0", "1.0.0-", "1.0.0+", "1.0.0-alpha..1", "v1.0.0"),
    *("^0.1.3", "~1.2.3", "", "latest", "1", "1.0", "01.0", "1.2.3.4"),
    *("2.0.0a1", "1.0rc1", "1.0.0rc1", "1.0c1", "1.0a", "1.0-1", "1.0.post1"),
    *("1.0.dev0", "1!2.0", "1.0+local.7", "1.0+Local", "1.0.0.dev1+abc"),
]


def test_the_checkers_semver_pattern_is_node_semvers():
    # `semver.valid` returns the canonical version or null, and canonical drops
    # build metadata (and a leading `v` or `=`), so a version is valid as written
    # when it is canonical apart from its build metadata.
    script = (
        "const semver = require('semver');"
        "const versions = JSON.parse(process.argv[1]);"
        "process.stdout.write(JSON.stringify(versions.map("
        "(v) => semver.valid(v) !== null && semver.valid(v) === v.split('+')[0])));"
    )
    done = subprocess.run(
        ["node", "-e", script, json.dumps(VERSIONS)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    expected = dict(zip(VERSIONS, json.loads(done.stdout), strict=True))
    actual = {v: bool(crb.NPM_VERSION_RE.fullmatch(v)) for v in VERSIONS}
    assert actual == expected


def _normalized(version: str) -> bool:
    try:
        return str(Version(version)) == version
    except InvalidVersion:
        return False


def test_the_checkers_pep440_pattern_is_packagings_normalized_form():
    actual = {v: bool(crb.PEP440_NORMALIZED_RE.fullmatch(v)) for v in VERSIONS}
    assert actual == {v: _normalized(v) for v in VERSIONS}
