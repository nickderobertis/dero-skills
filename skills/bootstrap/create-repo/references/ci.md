# Cross-cutting: GitHub Actions / CI

Applies on top of whichever product shape and language you chose. CI's job is to
prove the artifact the way a future maintainer or user would encounter it — not
to re-run a developer's warm local environment.

- **Required, and it must run the gate.** A CI workflow is non-negotiable, and a
  workflow that doesn't invoke `just check` proves nothing. Start from
  [`assets/ci.yml.template`](../assets/ci.yml.template).
- **Clean checkout -> bootstrap -> full gate.** Every run starts from a clean
  checkout, runs `just bootstrap`, then `just check` (which includes e2e). If
  bootstrap can't produce a working repo from scratch, that is the bug. The
  baseline checker fails CI that never references `just check`.
- **Realistic platform matrix.** Use an OS matrix when the artifact is
  cross-platform (CLIs, plugins, binaries). Test the versions you actually
  support.
- **Prove the end-user install path, on the real platforms.** Bootstrapping the
  *dev* toolchain (`just bootstrap`) is not the same as installing the shipped
  artifact. When the repo produces something users install — a CLI, a binary, a
  published package, a plugin — add a CI job, separate from the dev gate, that
  installs it via the **recommended end-user method** (the README install
  one-liner, `uvx`/`pipx`, `npm i -g`, `cargo install`, `brew install`, asdf
  `plugin add`, ...) on the supported OS matrix, then runs the installed entry
  point (e.g. `tool --version`) as a smoke test. Run it against the artifact the
  release will ship — the built wheel / binary / tarball — so the path is proven
  on every PR, not only after a release. Prefer a single cross-platform install
  script or command so the docs, CI, and what users actually run never drift; if
  CI installs differently than the docs tell users to, CI is proving the wrong
  thing. This drift is invisible until a user hits it.
- **Coverage is a default gate.** The gate measures test coverage and fails
  below the threshold; 95% line coverage is the default bar. Because CI runs the
  same `just check`, the threshold is proven on every PR, not tracked as a
  vanity badge. Measure it with the language's tool (`pytest --cov-fail-under`,
  Vitest `coverage.thresholds`, `cargo llvm-cov --fail-under-lines`, ...). Lower
  the bar — or drop it for a stack where coverage tooling genuinely doesn't fit
  — only with a documented reason in `AGENTS.md`, never by silently leaving
  coverage unmeasured.
- **Validate generated files.** Fail if committed generated files (lockfiles,
  schemas, formatted code) are out of date.
- **Cache for speed, never for correctness.** Cache dependencies, but never let
  a cache hide a broken clean build.
- **Artifacts after gates.** Upload build artifacts only once gates pass.
  Publish checksums for binaries; sign where appropriate.
- **Logs are context.** Keep logs minimal on success; emit detailed diagnostics
  only on failure, so a failed run points straight at the cause.

## Repository settings: merge model & branch protection

CI only proves the artifact if the platform actually *blocks* a merge until the
gate is green. Configure the GitHub repo so the protected default branch can only
take changes that passed the same checks CI runs. These settings are
deterministic, so script them with `gh` rather than clicking through the UI; the
exact commands below are the ones this skill's own repo uses.

- **Squash-merge only.** Disable merge commits and rebase-merging so history on
  the default branch stays linear and one PR is one commit. Set the squash
  commit subject to the **PR title** and the body to the **PR description**, so
  the PR title is what lands (and what a Conventional-Commits / release pipeline
  reads).
- **Auto-merge enabled.** Turn on auto-merge at the repo level so a PR can be
  queued with `gh pr merge --auto --squash` and merges itself the moment every
  required check goes green — no babysitting, and nothing merges early.
- **Delete head branches after merge.** Keep the branch list to live work only.
- **Branch protection on the default branch, requiring *all* CI checks.** Every
  status check that gates correctness must be required — including the
  **full-e2e gate job** (the one that runs `just check`), not just a fast lint
  job. List every required context by name; a check that is not *required* is
  advisory and a red one can still be merged past. Add the standard protections:
  require a PR before merging, linear history, no force-pushes, no branch
  deletion, and conversation resolution.
- **Admins can still override.** Leave admin enforcement off
  (`enforce_admins: false`) so a maintainer can break the glass in an emergency;
  the protection is the default path, not a cage. (Bump the required approval
  count above zero for a team; zero is reasonable for a solo maintainer who
  still wants every check enforced.)

Apply it (replace the contexts with *your* required job names):

```bash
# Merge model: squash only, auto-merge on, delete head branches.
gh repo edit --enable-squash-merge --enable-auto-merge \
  --enable-merge-commit=false --enable-rebase-merge=false \
  --delete-branch-on-merge
# PR title -> squash subject, PR body -> squash body (no gh flag; use the API).
gh api -X PATCH repos/{owner}/{repo} \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY

# Branch protection: require every gating check (the e2e-inclusive gate + any
# others), standard protections, admins can override.
gh api -X PUT repos/{owner}/{repo}/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["check", "commitlint"] },
  "enforce_admins": false,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "required_linear_history": true,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "restrictions": null
}
JSON
```

The skill bundles this as a runnable, idempotent script — pass the required
check contexts and preview with `--dry-run` first:

```bash
uv run --script scripts/setup_github_governance.py check commitlint --dry-run
uv run --script scripts/setup_github_governance.py check commitlint
```

These are repo-side settings, so the filesystem baseline checker cannot verify
them — record the intended model in `AGENTS.md` ("Commits, releases, and
merging") so the decision is auditable and the next maintainer can re-apply it.
