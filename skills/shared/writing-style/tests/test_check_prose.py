"""Tests for the writing-style prose checker.

Loads the bundled script by path so the test stays self-contained and does not
depend on the skills repo source tree being importable.
"""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_prose.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_prose", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses can resolve the module by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_prose = _load()


def test_clean_text_has_no_findings():
    text = "The server loads the config at startup.\nIt then opens a port.\n"
    assert check_prose.check_text(text, max_sentence_words=30) == []


def test_flags_weasel_word():
    findings = check_prose.check_text("We added various improvements.", 30)
    assert any(f.kind == "weasel" and "various" in f.detail for f in findings)


def test_flags_wordy_phrase():
    findings = check_prose.check_text("We did it in order to ship.", 30)
    assert any(f.kind == "wordy" for f in findings)


def test_flags_long_sentence():
    sentence = " ".join(["word"] * 40) + "."
    findings = check_prose.check_text(sentence, max_sentence_words=30)
    assert any(f.kind == "long-sentence" for f in findings)


def test_flags_passive_voice():
    findings = check_prose.check_text("The config is loaded at startup.", 30)
    assert any(f.kind == "passive" for f in findings)


def test_flags_trailing_whitespace():
    findings = check_prose.check_text("A normal line.   ", 30)
    assert any(f.kind == "trailing-whitespace" for f in findings)


def test_skips_headings_and_code():
    text = "# Heading with several words that would otherwise trip weasel\n"
    assert check_prose.check_text(text, 30) == []
