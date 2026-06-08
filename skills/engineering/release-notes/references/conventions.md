# Commit conventions for release notes

The formatter (`scripts/format_release_notes.mjs`) reads
[Conventional Commits](https://www.conventionalcommits.org/) subject lines.

## Subject format

```
type(scope)!: short summary
```

- `type` — one of `feat`, `fix`, `perf`, plus the usual non-release types
  (`docs`, `chore`, `refactor`, `test`, `build`, `ci`).
- `scope` — optional area in parentheses, e.g. `feat(uploads):`.
- `!` — optional marker that the change is breaking.
- `summary` — imperative, lowercase, no trailing period.

## How types map to sections

| Commit type | Release notes section |
| --- | --- |
| `feat` | Features |
| `fix` | Fixes |
| `perf` | Performance |
| any type with `!` or a `BREAKING CHANGE` footer | Breaking changes |
| anything else | Other |

## Examples

```
feat(uploads): resume transfers after a network drop
fix(auth): reject expired refresh tokens
perf(search): cache tokenized queries
refactor!: drop the deprecated v1 client
```

The formatter produces a first draft only. Always rewrite terse subjects into
user-facing outcomes and add migration guidance under Breaking changes.
