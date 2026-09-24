"""End-to-end: the composed ``oneharness.toml`` is accepted by the real
oneharness a repo built from this baseline actually runs.

The baseline emits ``oneharness.toml`` alongside the pinless ``llmlint.yml``, and
a produced repo's whole judged tier loads it before doing any work. So a key that
the bundled oneharness release rejects is not a lint finding — it fails every
``llmlint`` invocation in that repository at startup, before a single rule runs.

The existing guards cannot see that. ``tools/test_oneharness_dogfood.py`` compares
two files with ``tomllib``, ``test_compose_repo_plan_e2e.py`` checks the file is
emitted with the expected lines, and ``check_repo_baseline.py`` regex-scans for
``run_mode`` — none of them knows what the release's config schema accepts. This
module closes that gap by generating the config the way the skill does and handing
it to the real binary's ``config --config`` (the effective-configuration contract),
which fails, naming the key, on anything the schema does not know.

**Which release, and where the bound lives.** The root ``pyproject.toml``'s dev
group declares ``oneharness-cli``, and that entry is the one place the bound is
written: a consumer acquires oneharness through ``llmlint-cli``, which constrains
it only from below, so without that declaration a future llmlint release would
quietly move this suite onto a different config schema. These cases read the
specifier back out of that declaration and drive the ``oneharness-cli``
distribution at it directly — the ``oneharness`` console script in the environment
uv resolved from it (what ``uv run pytest`` provides, and what ``uv.lock`` pins),
else ``uvx --from <that specifier> oneharness``. Neither path names a version
here, so editing the declaration moves the release these cases parse with, and
nothing else does.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

SKILL_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[5]
COMPOSER = SKILL_DIR / "scripts" / "compose_repo_plan.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# The distribution whose declaration this suite is pinned to. Only the NAME lives
# here — the version constraint is read, never restated, so there is one copy of it.
ONEHARNESS_DIST = "oneharness-cli"

# A key no oneharness release knows, standing in for one a drifting template
# could emit — e.g. a fallback spelling that an older line accepted.
REJECTED_KEY = "fallback_mode"


class ReportedField(NamedTuple):
    """One field of the effective config: its value, and the file it came from."""

    value: object
    source: object


def requirement_name(entry: str) -> str | None:
    """The canonical distribution name a dev-group entry names, if it names one.

    Parsed with ``packaging`` rather than matched on a prefix, so the PEP 508
    grammar is not restated here and a neighbouring distribution that merely
    starts the same (``oneharness-cli-extra``) cannot stand in for the
    declaration this suite resolves.
    """
    try:
        return canonicalize_name(Requirement(entry).name)
    except InvalidRequirement:
        return None


def dev_dependency_group() -> list[str]:
    """The root manifest's dev group, validated before it is read."""
    manifest = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    groups = manifest.get("dependency-groups")
    assert isinstance(groups, dict), (
        f"{PYPROJECT} declares no `[dependency-groups]` table"
    )
    dev = groups.get("dev")
    assert isinstance(dev, list) and all(isinstance(entry, str) for entry in dev), (
        f"{PYPROJECT}'s `dependency-groups.dev` must be a list of requirement "
        f"strings; got {dev!r}"
    )
    return dev


def oneharness_requirement() -> Requirement:
    """The ``oneharness-cli`` requirement, read from its one declaration."""
    declared = [
        entry
        for entry in dev_dependency_group()
        if requirement_name(entry) == ONEHARNESS_DIST
    ]
    assert len(declared) == 1, (
        f"{PYPROJECT}'s dev group must declare `{ONEHARNESS_DIST}` exactly once — "
        "it is the single source of the oneharness line this suite parses the "
        f"baseline's config with; found {declared}"
    )
    requirement = Requirement(declared[0])
    assert str(requirement.specifier), (
        f"`{requirement}` pins no version, so it cannot decide which oneharness "
        "release the baseline's config is verified against"
    )
    return requirement


def installed_version(argv: list[str], requirement: Requirement) -> Version:
    """The release ``argv`` runs, checked against the declared specifier."""
    result = subprocess.run([*argv, "--version"], capture_output=True, text=True)
    assert result.returncode == 0, (
        f"`{' '.join(argv)} --version` failed:\n{result.stderr}"
    )
    reported = result.stdout.split()
    assert len(reported) == 2 and reported[0] == "oneharness", (
        f"`{' '.join(argv)} --version` did not report `oneharness <version>`: "
        f"{result.stdout!r}"
    )
    try:
        version = Version(reported[1])
    except InvalidVersion as exc:  # pragma: no cover - a release would have to break
        raise AssertionError(f"unparseable oneharness version {reported[1]!r}") from exc
    assert requirement.specifier.contains(version, prereleases=True), (
        f"the resolved oneharness {version} does not satisfy `{requirement}` — this "
        "suite would verify the baseline's config against the wrong release; run "
        "`uv sync`"
    )
    return version


@lru_cache(maxsize=None)
def oneharness_argv() -> tuple[str, ...]:
    """Argv prefix for the ``oneharness-cli`` distribution at the declared bound.

    Prefer the console script in the environment running these tests: ``uv run
    pytest`` syncs it from ``uv.lock``, which uv resolved from that same
    declaration. Resolved beside ``sys.executable`` rather than off ``PATH``, so
    an unrelated ``oneharness`` on the developer's ``PATH`` cannot stand in for
    it — and checked against the specifier either way.
    """
    requirement = oneharness_requirement()
    in_env = Path(sys.executable).parent / "oneharness"
    if os.access(in_env, os.X_OK):
        argv = [str(in_env)]
    else:
        # The environment has not been synced against the declaration; resolve the
        # same requirement on the fly. uv is a clean-clone prerequisite here.
        assert shutil.which("uvx") is not None, (
            f"no `oneharness` beside {sys.executable} and no `uvx` on PATH, so "
            f"`{requirement}` cannot be resolved — run `uv sync`"
        )
        argv = ["uvx", "--from", str(requirement), "oneharness"]
    installed_version(argv, requirement)
    return tuple(argv)


def compose_oneharness_config(target_dir: Path) -> Path:
    """Generate the baseline's ``oneharness.toml`` the way the skill tells an
    agent to: the PEP 723 composer as a real subprocess, emitting the config
    beside the ongoing llmlint config."""
    result = subprocess.run(
        [
            "uv",
            "run",
            "--script",
            str(COMPOSER),
            "--shape",
            "cli",
            "--language",
            "python",
            "--llmlint-config",
            str(target_dir / "llmlint.yml"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"composer failed:\n{result.stderr}"
    config = target_dir / "oneharness.toml"
    assert config.is_file(), "the composer emitted no oneharness.toml"
    return config


def read_effective_config(config: Path) -> subprocess.CompletedProcess[str]:
    """Run the real binary's effective-configuration contract over ``config``."""
    return subprocess.run(
        [*oneharness_argv(), "config", "--config", str(config), "--format", "json"],
        capture_output=True,
        text=True,
    )


def reported_field(entry: object, what: str) -> ReportedField:
    """Unpack one ``{"value": ..., "source": ...}`` field of the effective config.

    The response crosses a process boundary from a third-party CLI, so its shape
    is checked here rather than assumed at each use — a contract change reads as
    itself instead of as a ``KeyError`` from the middle of a case.
    """
    assert isinstance(entry, dict) and {"value", "source"} <= entry.keys(), (
        f"{what} is not a `value`/`source` field of the effective config: {entry!r}"
    )
    return ReportedField(value=entry["value"], source=entry["source"])


def table(holder: dict[str, object], key: str, what: str) -> dict[str, object]:
    """Unpack one nested table — of the effective config, or of the parsed TOML.

    Both sides are parsed data this module did not produce, so neither is walked
    with ``.items()`` until it has been checked to be a table.
    """
    nested = holder.get(key)
    assert isinstance(nested, dict), f"{what} is not a table: {nested!r}"
    return nested


def test_composed_config_is_accepted_by_the_bundled_release(tmp_path):
    config = compose_oneharness_config(tmp_path)
    result = read_effective_config(config)
    assert result.returncode == 0, (
        "the composed oneharness.toml was rejected by the declared oneharness "
        f"release ({oneharness_requirement()}) — every bundled llmlint run in a "
        f"produced repo would fail at startup:\n{result.stderr}"
    )


def test_every_composed_key_survives_into_the_effective_config(tmp_path):
    """A key the schema accepts but ignores is as broken as one it rejects, and
    exits 0 either way — so read every declared key back out of the effective
    config, with the value and the attribution the file should have given it."""
    config = compose_oneharness_config(tmp_path)
    result = read_effective_config(config)
    assert result.returncode == 0, result.stderr
    effective = json.loads(result.stdout)
    assert isinstance(effective, dict), (
        f"`oneharness config --format json` did not emit an object: {effective!r}"
    )
    declared = tomllib.loads(config.read_text(encoding="utf-8"))
    source = str(config)

    top_level = {k: v for k, v in declared.items() if k != "harness"}
    assert top_level, "the composed config declares no top-level keys"
    for key, value in top_level.items():
        assert key in effective, f"`{key}` is absent from the effective config"
        got = reported_field(effective[key], f"`{key}`")
        assert got.value == value, f"`{key}` did not survive: {got.value!r}"
        assert got.source == source, f"`{key}` was not attributed to the file"

    if "harness" not in declared:
        return
    declared_harnesses = table(declared, "harness", "the composed `[harness]` table")
    harnesses = table(effective, "harness", "the effective `harness` table")
    for harness_id in declared_harnesses:
        settings = table(declared_harnesses, harness_id, f"composed `{harness_id}`")
        assert harness_id in harnesses, f"harness `{harness_id}` is absent"
        reported = table(harnesses, harness_id, f"harness `{harness_id}`")
        for field, value in settings.items():
            label = f"harness `{harness_id}`.{field}"
            if field == "env":
                declared_env = table(settings, "env", f"composed {label}")
                env = table(reported, "env", label)
                for name, env_value in declared_env.items():
                    assert name in env, f"{label}.{name} is absent"
                    got = reported_field(env[name], f"{label}.{name}")
                    assert got.value == env_value, (
                        f"{label}.{name} did not survive: {got.value!r}"
                    )
                    assert got.source == source
                continue
            assert field in reported, f"{label} is absent"
            got = reported_field(reported[field], label)
            assert got.value == value, f"{label} did not survive: {got.value!r}"
            assert got.source == source


def test_a_key_the_release_rejects_fails_and_names_it(tmp_path):
    """The guard's failure mode: feed the generated config a key the schema does
    not know and the release must refuse it, naming the key."""
    config = compose_oneharness_config(tmp_path)
    head, sep, tail = config.read_text(encoding="utf-8").partition("[harness.")
    assert sep, "the composed config no longer opens a [harness.*] table"
    config.write_text(
        f'{head}{REJECTED_KEY} = "fallback"\n\n{sep}{tail}', encoding="utf-8"
    )

    result = read_effective_config(config)
    assert result.returncode != 0, (
        f"the release accepted the unknown key `{REJECTED_KEY}` — this suite can "
        "no longer tell a rejected key from an accepted one"
    )
    assert REJECTED_KEY in result.stderr, (
        f"the refusal did not name `{REJECTED_KEY}`:\n{result.stderr}"
    )
