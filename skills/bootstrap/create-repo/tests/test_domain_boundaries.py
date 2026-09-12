"""Static checks holding the domain-boundaries instruction and its llmlint rules.

``references/project-graph.md`` says the units of one product follow domains,
never kinds of code, and decides when a shared package is a genuine contract
with a two-sentence **contract test**. The ongoing fragment's
``code_lands_in_the_domain_that_owns_it`` and the buildout fragment's
``graph_splits_by_domain`` are what make that instruction bite in a produced
repo, and each restates the contract test word for word. Nothing here can be
driven locally (the judge is a model call, the reference is prose), so these
read the three files for the structure the rule rests on:

  * the reference carries the section and its verification item;
  * both rules are declared, with a true and a false case;
  * the contract test reads identically in all three places — the reference is
    the one source, the two descriptions are checked against it;
  * the ongoing rule's globs equal ``new_code_lands_in_a_project``'s, so it judges
    every language the base fragment does and not one fewer;
  * both fragments stay on major ``1``, which a produced repo pinned at ``@1``
    picks up with no edit.

The fragments are read with the same line-level parsing the neighbouring
structural tests use rather than a YAML dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
REFERENCE = SKILL_DIR / "references" / "project-graph.md"
LLMLINT_ASSETS = SKILL_DIR / "assets" / "llmlint"
ONGOING = LLMLINT_ASSETS / "project-graph.llmlint.yml"
BUILDOUT = LLMLINT_ASSETS / "buildout" / "project-graph.llmlint.yml"
BASE = LLMLINT_ASSETS / "base.llmlint.yml"

SECTION_HEADING = "## Boundaries follow domains, not kinds of code"
CHECKLIST_ITEM = "- [ ] **Boundaries follow domains.**"
ONGOING_RULE = "code_lands_in_the_domain_that_owns_it"
BUILDOUT_RULE = "graph_splits_by_domain"
BASE_RULE = "new_code_lands_in_a_project"

_RULE_START_RE = re.compile(r"^  - name:\s*(\S+)\s*$")
_VERSION_RE = re.compile(r"^version:\s*(\d+)\.(\d+)\.(\d+)\s*$", re.MULTILINE)
_QUOTED_RE = re.compile(r'"([^"]*)"')


def _squash(text: str) -> str:
    """Collapse runs of whitespace so a rewrap is not a rewording."""
    return " ".join(text.split())


def _rules(fragment: Path) -> dict[str, list[str]]:
    """Each rule's name -> its lines (indented under the ``- name:`` entry)."""
    rules: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in fragment.read_text(encoding="utf-8").splitlines():
        if m := _RULE_START_RE.match(line):
            current = rules.setdefault(m.group(1), [])
        elif current is not None and (line.startswith("    ") or not line.strip()):
            current.append(line)
        else:
            current = None
    return rules


def _rule(fragment: Path, name: str) -> list[str]:
    rules = _rules(fragment)
    assert name in rules, (
        f"{fragment.relative_to(SKILL_DIR)} declares no rule {name!r}; "
        f"found {sorted(rules)}"
    )
    return rules[name]


def _field_block(rule_lines: list[str], field: str) -> list[str]:
    """The lines under ``    <field>:`` (its own line included) up to the next
    field at the same indent."""
    block: list[str] = []
    inside = False
    for line in rule_lines:
        if re.match(rf"^    {re.escape(field)}:", line):
            inside = True
            block.append(line)
        elif inside and re.match(r"^    \S", line):
            break
        elif inside:
            block.append(line)
    return block


def _description(rule_lines: list[str]) -> str:
    block = _field_block(rule_lines, "description")
    assert block and block[0].rstrip().endswith("|"), block[:1]
    return _squash("\n".join(block[1:]))


def _include_globs(rule_lines: list[str]) -> set[str]:
    """``files.include`` as a set — inline (``["a", "b"]``) or block-list form."""
    files = _field_block(rule_lines, "files")
    include = "\n".join(files)
    assert "include:" in include, files
    return set(_QUOTED_RE.findall(include.split("include:", 1)[1]))


def _version(fragment: Path) -> tuple[int, int, int]:
    m = _VERSION_RE.search(fragment.read_text(encoding="utf-8"))
    assert m is not None, f"{fragment.name}: no MAJOR.MINOR.PATCH version"
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


@pytest.fixture(scope="module")
def reference() -> str:
    return REFERENCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def contract_test(reference: str) -> str:
    """The two sentences, read off the reference's blockquote: the one source."""
    assert f"\n{SECTION_HEADING}\n" in reference, "reference lost its section"
    section = reference.split(f"\n{SECTION_HEADING}\n", 1)[1].split("\n## ", 1)[0]
    quoted = [line[2:] for line in section.splitlines() if line.startswith("> ")]
    text = _squash(" ".join(quoted))
    assert text.startswith("A shared package is a contract only if"), text
    assert text.endswith("however it is persisted or served."), text
    return text


def test_the_reference_carries_the_section_and_its_checklist_item(reference):
    assert f"\n{SECTION_HEADING}\n" in reference
    verification = reference.split("\n## Verification\n", 1)[1]
    assert CHECKLIST_ITEM in verification


def test_the_ongoing_rule_states_a_true_and_a_false_case():
    rule = _rule(ONGOING, ONGOING_RULE)
    description = _description(rule)
    assert "true when" in description
    assert "false when" in description


def test_the_ongoing_rule_judges_every_language_the_base_rule_does():
    # A drift between the two lists is a rule that judges one language and not
    # another: the ongoing rule's globs are the base rule's, as a set.
    ours = _include_globs(_rule(ONGOING, ONGOING_RULE))
    base = _include_globs(_rule(BASE, BASE_RULE))
    assert ours == base, {"missing": base - ours, "extra": ours - base}


def test_the_buildout_rule_is_declared():
    rule = _rule(BUILDOUT, BUILDOUT_RULE)
    description = _description(rule)
    assert "true when" in description
    assert "false when" in description


@pytest.mark.parametrize(
    ("fragment", "rule_name"),
    [(ONGOING, ONGOING_RULE), (BUILDOUT, BUILDOUT_RULE)],
    ids=["ongoing", "buildout"],
)
def test_the_contract_test_reads_identically_in_the_rule(
    contract_test, fragment, rule_name
):
    description = _description(_rule(fragment, rule_name))
    assert contract_test in description, (
        f"{fragment.relative_to(SKILL_DIR)}: {rule_name} restates the contract "
        f"test in other words than references/project-graph.md; expected:\n"
        f"{contract_test}"
    )


@pytest.mark.parametrize("fragment", [ONGOING, BUILDOUT], ids=["ongoing", "buildout"])
def test_the_fragment_stays_on_major_one_past_the_rule_that_added_this(fragment):
    # A produced repo pins `@1`, so major 1 reaches it with no edit; the rules
    # landed in 1.3.0, so anything below is a tree that lost the bump.
    major, minor, patch = _version(fragment)
    assert major == 1, (fragment.name, major)
    assert (minor, patch) >= (3, 0), (fragment.name, minor, patch)
