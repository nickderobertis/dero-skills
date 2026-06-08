---
name: release-notes
description: Use when drafting release notes or a changelog from merged commits or pull requests, to produce categorized, user-facing notes grouped by impact.
compatibility: Requires Node.js 18+ only if the bundled JavaScript scripts are used.
---

# Release notes

Use this when turning merged commits or pull requests into release notes or a
changelog entry. Notes are for *users*, so describe outcomes, not internals.

## Principles

- **Group by impact**, not by author or file: Breaking changes, Features,
  Fixes, Performance, Other.
- **Lead with breaking changes** and say what the reader must do to migrate.
- **One outcome per entry.** Describe what changed for the user.
  - Good: "Uploads resume automatically after a network drop."
  - Bad: "Refactored UploadManager."
- **Link the PR or issue** at the end of each entry where it helps.
- Omit purely internal churn (lockfile bumps, formatting) unless it affects
  users.

See `references/conventions.md` for the commit conventions the formatter reads.

## Workflow

1. Collect the commit subjects for the range, for example:

   ```bash
   git log --pretty=%s origin/main..HEAD > /tmp/commits.txt
   ```

2. Group them into draft sections with the bundled formatter:

   ```bash
   node scripts/format_release_notes.mjs /tmp/commits.txt
   ```

   It reads conventional-commit subjects (`type(scope)!: summary`) and emits
   grouped markdown. `feat` → Features, `fix` → Fixes, `perf` → Performance,
   a `!` or `BREAKING CHANGE` → Breaking changes, everything else → Other.

3. Edit the draft: rewrite terse subjects into user-facing outcomes, drop noise,
   and add migration notes under Breaking changes.
4. Fill in `assets/release-notes-template.md` with the final, edited sections.
