# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Advisory prose linter for the writing-style skill.

Self-contained: run with `uv run --script scripts/check_prose.py FILE`, or pipe
text on stdin. Stdlib only, no third-party dependencies.

It flags, per line:
  * sentences longer than --max-sentence-words words
  * weasel words and wordy phrases
  * passive-voice markers ("is/are/was/were ... <past participle>")
  * trailing whitespace

Exit code is the number of findings (capped at 1), so it can gate CI. Pass
--quiet to suppress the per-finding output and only set the exit code.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

WEASEL_WORDS = {
    "various", "several", "appropriate", "relevant", "utilize", "leverage",
    "facilitate", "robust", "seamless", "simply", "just", "basically",
    "really", "very", "obviously", "clearly",
}
WORDY_PHRASES = {
    "in order to": "to",
    "at this point in time": "now",
    "in the event that": "if",
    "due to the fact that": "because",
    "a number of": "(give the count)",
}
PASSIVE_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being)\b\s+(?:\w+ly\s+)?\w+ed\b",
    re.IGNORECASE,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Finding:
    line: int
    kind: str
    detail: str


def split_sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_SPLIT_RE.split(text.strip()) if s]


def check_text(text: str, max_sentence_words: int) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if raw != raw.rstrip():
            findings.append(Finding(lineno, "trailing-whitespace", "trailing whitespace"))
        line = raw.strip()
        if not line or line.startswith(("#", "```", ">", "|", "-", "*")):
            # Skip headings, code fences, blockquotes, tables, list bullets.
            continue

        lowered = line.lower()
        for phrase, better in WORDY_PHRASES.items():
            if phrase in lowered:
                findings.append(Finding(lineno, "wordy", f"'{phrase}' -> '{better}'"))
        for word in WEASEL_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                findings.append(Finding(lineno, "weasel", f"weasel word '{word}'"))
        if PASSIVE_RE.search(line):
            findings.append(Finding(lineno, "passive", "possible passive voice"))

        for sentence in split_sentences(line):
            words = sentence.split()
            if len(words) > max_sentence_words:
                findings.append(
                    Finding(lineno, "long-sentence",
                            f"{len(words)} words (> {max_sentence_words})")
                )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advisory prose linter.")
    parser.add_argument("path", nargs="?", help="file to check; reads stdin if omitted")
    parser.add_argument("--max-sentence-words", type=int, default=30)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.path:
        with open(args.path, encoding="utf-8") as fh:
            text = fh.read()
        source = args.path
    else:
        text = sys.stdin.read()
        source = "<stdin>"

    findings = check_text(text, args.max_sentence_words)
    if not args.quiet:
        for f in findings:
            print(f"{source}:{f.line}: {f.kind}: {f.detail}")
        print(f"{len(findings)} finding(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
