"""Tests for the code-review diff summarizer."""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "summarize_diff.py"


def _load():
    spec = importlib.util.spec_from_file_location("summarize_diff", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses can resolve the module by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


summarize_diff = _load()

SAMPLE = """diff --git a/app/main.py b/app/main.py
index 111..222 100644
--- a/app/main.py
+++ b/app/main.py
@@ -1,3 +1,4 @@
 import os
+import sys
-old_line = 1
 keep = 2
diff --git a/logo.png b/logo.png
index 333..444 100644
Binary files a/logo.png and b/logo.png differ
"""


def test_counts_added_and_removed():
    summary = summarize_diff.parse_diff(SAMPLE)
    by_path = {f.path: f for f in summary.files}
    assert by_path["app/main.py"].added == 1
    assert by_path["app/main.py"].removed == 1
    assert summary.total_added == 1
    assert summary.total_removed == 1


def test_detects_binary_file():
    summary = summarize_diff.parse_diff(SAMPLE)
    by_path = {f.path: f for f in summary.files}
    assert by_path["logo.png"].binary is True


def test_does_not_count_header_lines():
    # The +++/--- header lines must not be counted as content changes.
    summary = summarize_diff.parse_diff(SAMPLE)
    main = next(f for f in summary.files if f.path == "app/main.py")
    assert main.added == 1 and main.removed == 1


def test_render_empty_diff():
    assert "No changes" in summarize_diff.render(summarize_diff.parse_diff(""))


def test_render_includes_totals():
    out = summarize_diff.render(summarize_diff.parse_diff(SAMPLE))
    assert "Files changed: 2" in out
    assert "logo.png" in out
