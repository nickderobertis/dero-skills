# Cross-cutting: Releases & versioning

Applies whenever the repo ships a **versioned artifact** — a binary, a published
package, a plugin, a container image. Skip it only for a repo that publishes
nothing (a private app deployed by other means, or a demo/consumer repo that
deploys a site instead of cutting versions). It layers on top of the product
shape, the language(s), and `ci.md`, and it is the other half of the squash-merge
governance in `ci.md`: that section makes the **PR title** the one commit that
lands; this section makes that commit *drive the release*.

The model to converge on: **Conventional Commits are the source of truth; a bot
computes the next version, writes the changelog and every manifest, and tags;
the tag triggers build and publish.** Nobody hand-edits a version number, and
versioning is decoupled from building.

## Conventional Commits drive everything

- **Lint the release input.** Under squash-merge the PR title becomes the sole
  commit, so it is what the release tool parses — lint it in CI as a *required*
  check (`amannn/action-semantic-pull-request`, or `commitlint` over the PR
  title). A malformed title that lands is a release computed from garbage. This
  check belongs in the required set alongside the `just check` gate (see the
  governance section of `ci.md`).
- **Pin the bump policy in `AGENTS.md`.** Decide how commit types map to version
  bumps and write it down; the tool's config (`release-plz.toml`, `.releaserc`,
  `release-please-config.json`) only encodes the decision. The pre-1.0 default
  these repos share: `feat` → minor, `feat!` / `BREAKING CHANGE` → minor (a
  breaking change pre-1.0 is *not* yet a major), `fix` / `perf` / `refactor` /
  `build` → patch, and `chore` / `docs` / `ci` / `test` / `style` → no release.
  Post-1.0, a breaking change is a major. State which regime the repo is in.

## Pick the release driver for the ecosystem

- **Rust:** `release-plz` (opens a release PR from the merged commits; on merge
  it tags `vX.Y.Z` and optionally publishes to crates.io). Set
  `semver_check = false` only if you rely on review + Conventional Commits
  instead of an automated semver check.
- **JS / polyglot / general:** `semantic-release` or `release-please` (compute
  the next version on push to the default branch, write the manifests and
  changelog, commit `chore(release): X.Y.Z`, tag).
- **Binary-only tools** (no library consumers): set `publish = false` / don't
  push to a registry; ship via GitHub Releases plus `cargo install --git`, an
  `install.sh`, or an asdf plugin. crates.io / PyPI / npm publication is a
  separate, optional surface.

## Decouple versioning from building

- **The version bumper's only job** is: compute the version, write the changelog
  and *every* manifest + lockfile, commit `chore(release): X.Y.Z`, and push the
  tag `vX.Y.Z`. It must **not** build or publish artifacts itself.
- **The tag triggers the rest.** A push of `vX.Y.Z` fires a separate `release.yml`
  (compile and upload per-platform archives with checksums) and, when publishing,
  a `publish.yml` (push to crates.io / PyPI / npm in dependency order). Keep
  publish **idempotent** — skip a version that is already live so a re-run after a
  partial failure is safe.

## The PAT gotcha — write this down

A tag or release created by the default `GITHUB_TOKEN` **does not trigger other
workflows** (GitHub suppresses recursive Actions triggers to prevent loops). So a
release bot authenticated with `GITHUB_TOKEN` will bump and tag, but `release.yml`
and `publish.yml` never fire — the release silently ships nothing. Authenticate
the release/version job with a **PAT or GitHub App token** (these repos use
`RELEASE_PLZ_TOKEN` / `RELEASE_PAT` / `RELEASE_PLEASE_TOKEN`), scoped to contents
and pull-requests. This trap is invisible until the first release that produces no
binaries.

## Lockstep across many manifests (polyglot / monorepo)

When one logical version spans several manifests — `Cargo.toml`, `pyproject.toml`,
`package.json`, per-platform carrier packages, and their lockfiles — never
hand-edit one of N. Make a single `scripts/set-version.sh` the source of truth and
have the release tool call it (`semantic-release`'s `exec` step, or `release-plz`).
One script writes every manifest, every lockfile, and any cross-package version
pins. See `monorepo.md`.

## Secrets

Registry tokens and the release PAT live in repo/Org secrets, never in the tree. A
checked-in *declarative secret manifest* (names + sources, with the values
gitignored) is fine and keeps the required-secrets list auditable; the values are
not. Never print a token, and never let CI echo one on failure.

## Record the model

Capture the chosen driver, the bump policy, and the tag → release/publish split in
the "Commits, releases, and merging" section of `AGENTS.md` so it is auditable and
re-appliable, and make sure the PR-title-lint check is in the required-checks set
when you apply branch protection (`ci.md`).
