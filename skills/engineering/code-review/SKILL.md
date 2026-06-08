---
name: code-review
description: Use when reviewing a pull request or code diff to apply a correctness-first review checklist and summarize findings by severity.
compatibility: Requires uv and Python 3.12+ only if bundled Python scripts are used.
---

# Code review

Use this when reviewing a pull request or a diff. The goal is correct, safe,
maintainable code — not style nitpicking that a formatter should handle.

## Order of attention

Review in this priority order. Spend time where the risk is highest.

1. **Correctness.** Does it do what the PR claims? Look for off-by-one errors,
   wrong conditionals, unhandled `None`/null, and broken edge cases.
2. **Safety.** Untrusted input validation, authz checks, secrets in code,
   injection, unsafe deserialization, resource leaks.
3. **Data and migrations.** Backward compatibility, irreversible migrations,
   nullable columns, large backfills run inline.
4. **Tests.** Do tests cover the new behavior and the failure paths? Would they
   actually fail if the change regressed?
5. **Design and reuse.** Duplication, leaky abstractions, simpler alternatives.
6. **Readability.** Naming, comments that explain *why*, dead code.

See `references/checklist.md` for the full checklist.

## Workflow

1. Read the PR description and the linked issue. Know what it claims to do.
2. Get an overview of the diff size and shape:

   ```bash
   git diff origin/main...HEAD | uv run --script scripts/summarize_diff.py
   ```

   This reports files changed, additions/deletions, and flags unusually large
   or binary files so you know where to focus.
3. Review file by file against the checklist, highest-risk files first.
4. Write findings using `assets/review-comment-template.md`. Tag each with a
   severity: **blocking**, **should-fix**, or **nit**.
5. End with a short summary and an explicit verdict: approve, approve with
   comments, or request changes.

## Findings: severity definitions

- **blocking** — must be fixed before merge (correctness, security, data loss).
- **should-fix** — worth fixing now but not merge-blocking on its own.
- **nit** — optional polish; never block a PR on a nit alone.
