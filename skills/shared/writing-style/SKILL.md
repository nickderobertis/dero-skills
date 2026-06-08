---
name: writing-style
description: Use when writing or editing prose — docs, READMEs, comments, commit messages, PR descriptions, or release notes — to apply the team's plain-language writing conventions.
compatibility: Requires uv and Python 3.12+ only if bundled Python scripts are used.
---

# Writing style

Apply these conventions to any user-facing prose: documentation, READMEs, code
comments, commit messages, pull request descriptions, and release notes.

## Core principles

- **Lead with the point.** Put the conclusion or action in the first sentence.
- **Plain language.** Prefer short, common words over jargon. Define a term the
  first time it appears if it is unavoidable.
- **Short sentences.** Aim for one idea per sentence. Split anything past ~30
  words.
- **Active voice.** "The job retries failed uploads," not "Failed uploads are
  retried by the job."
- **Concrete over vague.** Replace weasel words ("various", "several",
  "appropriate") with specifics.
- **Consistent terminology.** Pick one term per concept and reuse it. Do not
  alternate between synonyms.

## Mechanics

- Use sentence case for headings.
- Use the Oxford comma.
- Format code, paths, commands, and identifiers as `inline code`.
- Write commit subjects in the imperative mood, ≤50 characters, no trailing
  period (see `assets/commit-message-template.md`).
- Wrap commit and PR body text at ~72 characters.

## Workflow

1. Draft the text following the principles above.
2. Optionally lint a draft with the bundled checker:

   ```bash
   uv run --script scripts/check_prose.py path/to/draft.md
   ```

   It flags overlong sentences, weasel words, passive-voice markers, and
   trailing whitespace. Treat its output as advisory, not absolute.
3. Revise and re-read aloud once. If a sentence is hard to say, shorten it.

See `references/style-guide.md` for the full house style and worked examples.
