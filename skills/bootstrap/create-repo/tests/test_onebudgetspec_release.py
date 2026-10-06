"""The onebudgetspec surface the skill restates, held to the sources it copies.

Offline drift gates, so they run in the fast tier on every change that can
reach them:

* The release's README ships as the `onebudgetspec-cli` wheel's METADATA, and
  this repo's dev group pins that wheel to the release the composer pins (see
  test_compose_repo_plan.py's lockstep test). Every name the reference gives for
  the release's surface, and the target defaults the composer writes, are read
  against that README.
* The composer's table of Nx input objects is read against Nx's own schema, and
  the checker's reading of `bun.lock` against this repo's own bun-written lock —
  both from the `node_modules` the gate already runs Nx from.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from importlib import metadata
from pathlib import Path

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


def test_the_nx_input_objects_the_composer_accepts_are_nxs_own():
    schema = json.loads(NX_SCHEMA.read_text(encoding="utf-8"))
    variants = schema["definitions"]["inputs"]["items"]["oneOf"]
    from_schema = {
        frozenset(variant["properties"])
        for variant in variants
        if variant.get("type") == "object"
    }
    from_composer = {
        frozenset({kind, *extras}) for kind, extras in crp.NX_INPUT_OBJECT_KEYS.items()
    }
    assert from_composer == from_schema


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
