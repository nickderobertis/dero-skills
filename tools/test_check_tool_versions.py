"""Tests for the .tool-versions / CI version-consistency checker."""

from __future__ import annotations

from pathlib import Path

import check_tool_versions as ctv

# A workflow that pins Node (coarse major) and Python, leaving uv/just to latest.
CI_NODE_22 = """\
name: ci
on: [push]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - uses: extractions/setup-just@v2
"""

# A workflow that also pins bun via setup-bun's `bun-version` input.
CI_NODE_22_BUN = """\
name: ci
on: [push]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - uses: oven-sh/setup-bun@v2
        with:
          bun-version: "1.3.11"
"""


def make_repo(tmp_path: Path, *, tool_versions: str | None, ci: str | None) -> Path:
    if tool_versions is not None:
        (tmp_path / ".tool-versions").write_text(tool_versions, encoding="utf-8")
    if ci is not None:
        wf = tmp_path / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text(ci, encoding="utf-8")
    return tmp_path


def errors(findings):
    return [f.message for f in findings if f.level == "ERROR"]


# --- parsing ---------------------------------------------------------------


def test_parse_tool_versions_ignores_comments_and_blanks():
    text = "# header\n\njust 1.51.0\nuv 0.11.19\nnodejs 22.12.0\n"
    assert ctv.parse_tool_versions(text) == {
        "just": "1.51.0",
        "uv": "0.11.19",
        "nodejs": "22.12.0",
    }


def test_parse_ci_pins_maps_keys_and_attributes_uv_version():
    # setup-uv's input is the generic `version:`, attributed via the step's
    # `uses:`; the other tools use unambiguous tool-specific keys.
    ci = """\
jobs:
  check:
    steps:
      - uses: astral-sh/setup-uv@v5
        with:
          version: "0.11.19"
          python-version: "3.12"
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - uses: extractions/setup-just@v2
        with:
          just-version: "1.51.0"
"""
    pins = {p.tool: p.version for p in ctv.parse_ci_pins(ci, "ci.yml")}
    assert pins["uv"] == "0.11.19"
    assert pins["python"] == "3.12"
    assert pins["node"] == "22"
    assert pins["just"] == "1.51.0"


def test_parse_ci_pins_maps_bun_version():
    pins = {p.tool: p.version for p in ctv.parse_ci_pins(CI_NODE_22_BUN, "ci.yml")}
    assert pins["bun"] == "1.3.11"


def test_versions_consistent_prefix_semantics():
    assert ctv.versions_consistent("22", "22.12.0")
    assert ctv.versions_consistent("22.12.0", "22")
    assert ctv.versions_consistent("3.12", "3.12.7")
    assert not ctv.versions_consistent("3.13", "3.12.0")
    assert not ctv.versions_consistent("20", "22.12.0")


# --- audit -----------------------------------------------------------------


def test_coarse_ci_major_matches_exact_pin(tmp_path):
    repo = make_repo(tmp_path, tool_versions="nodejs 22.12.0\n", ci=CI_NODE_22)
    assert not ctv.has_errors(ctv.audit(repo))


def test_conflicting_node_version_is_error(tmp_path):
    repo = make_repo(tmp_path, tool_versions="nodejs 24.3.0\n", ci=CI_NODE_22)
    findings = ctv.audit(repo)
    assert ctv.has_errors(findings)
    assert any("node" in m for m in errors(findings))


def test_python_pin_matches_ci(tmp_path):
    repo = make_repo(
        tmp_path, tool_versions="python 3.12\nnodejs 22.12.0\n", ci=CI_NODE_22
    )
    assert not ctv.has_errors(ctv.audit(repo))


def test_conflicting_python_version_is_error(tmp_path):
    repo = make_repo(tmp_path, tool_versions="python 3.13\n", ci=CI_NODE_22)
    findings = ctv.audit(repo)
    assert ctv.has_errors(findings)
    assert any("python" in m for m in errors(findings))


def test_bun_pin_matches_ci(tmp_path):
    repo = make_repo(
        tmp_path, tool_versions="bun 1.3.11\nnodejs 22.12.0\n", ci=CI_NODE_22_BUN
    )
    assert not ctv.has_errors(ctv.audit(repo))


def test_conflicting_bun_version_is_error(tmp_path):
    repo = make_repo(tmp_path, tool_versions="bun 1.2.0\n", ci=CI_NODE_22_BUN)
    findings = ctv.audit(repo)
    assert ctv.has_errors(findings)
    assert any("bun" in m for m in errors(findings))


def test_tool_pinned_only_locally_is_ok(tmp_path):
    # uv/just are in .tool-versions but CI installs them unpinned -> no conflict.
    repo = make_repo(
        tmp_path,
        tool_versions="just 1.51.0\nuv 0.11.19\nnodejs 22.12.0\n",
        ci=CI_NODE_22,
    )
    assert not ctv.has_errors(ctv.audit(repo))


def test_missing_tool_versions_is_error(tmp_path):
    repo = make_repo(tmp_path, tool_versions=None, ci=CI_NODE_22)
    findings = ctv.audit(repo)
    assert ctv.has_errors(findings)
    assert any(".tool-versions" in m for m in errors(findings))


def test_no_workflows_means_nothing_to_check(tmp_path):
    repo = make_repo(tmp_path, tool_versions="nodejs 22.12.0\n", ci=None)
    assert not ctv.has_errors(ctv.audit(repo))


def test_every_error_carries_a_fix(tmp_path):
    repo = make_repo(tmp_path, tool_versions="nodejs 24.0.0\n", ci=CI_NODE_22)
    errs = [f for f in ctv.audit(repo) if f.level == "ERROR"]
    assert errs and all(f.fix for f in errs)


# --- main / output discipline ----------------------------------------------


def test_main_quiet_and_zero_on_success(tmp_path, capsys):
    repo = make_repo(tmp_path, tool_versions="nodejs 22.12.0\n", ci=CI_NODE_22)
    assert ctv.main([str(repo)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len([ln for ln in captured.out.splitlines() if ln.strip()]) == 1


def test_main_one_and_reports_fix_on_conflict(tmp_path, capsys):
    repo = make_repo(tmp_path, tool_versions="nodejs 24.0.0\n", ci=CI_NODE_22)
    assert ctv.main([str(repo)]) == 1
    captured = capsys.readouterr()
    assert "fix:" in captured.err
    assert "FAIL" in captured.err


def test_main_two_on_missing_directory(tmp_path):
    assert ctv.main([str(tmp_path / "nope")]) == 2
