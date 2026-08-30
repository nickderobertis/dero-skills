"""Deterministic checks on the llmlint tier's own configuration.

The judged (model) half of this tier cannot run in the gate — it is
non-deterministic and needs a harness credential, so `references/ci.md` promotes
it out unconditionally. What *can* be proven offline is that the tier is wired to
something real: `llmlint.yml` composes this repo's rule fragments by local
in-tree path, and a renamed fragment breaks that silently — llmlint would still
exit 0 having judged the repo against fewer rules than it claims to.

So this project holds the tier's deterministic half: its `validate` target runs
the model-free `llmlint validate` in the gate, and the case below reads the real
committed config and resolves every local plugin path against the working tree.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "llmlint.yml"

# A `plugins:` list entry: `  - "path/or/url"`, single- or double-quoted.
_PLUGIN_ENTRY_RE = re.compile(r"""^\s*-\s*["']?([^"'\s]+)["']?\s*$""")


def local_plugin_paths() -> list[str]:
    """The non-URL entries of `llmlint.yml`'s `plugins:` list, in file order."""
    entries: list[str] = []
    in_plugins = False
    for line in CONFIG.read_text(encoding="utf-8").splitlines():
        if re.match(r"^plugins:\s*$", line):
            in_plugins = True
            continue
        if in_plugins:
            match = _PLUGIN_ENTRY_RE.match(line)
            if match is None:
                # A non-comment, non-entry line ends the block.
                if line.strip() and not line.lstrip().startswith("#"):
                    break
                continue
            if not match.group(1).startswith(("http://", "https://")):
                entries.append(match.group(1))
    return entries


def test_the_config_composes_local_fragments() -> None:
    """A tier composing no local fragment would judge nothing this repo owns."""
    assert local_plugin_paths(), (
        f"{CONFIG} lists no local plugin fragments — the judged tier would run "
        "with only its bundled rules"
    )


def test_every_local_plugin_path_resolves() -> None:
    missing = [p for p in local_plugin_paths() if not (REPO_ROOT / p).is_file()]
    assert missing == [], (
        "llmlint.yml names local plugin fragments that do not exist: "
        + ", ".join(missing)
    )
