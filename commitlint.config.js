// Conventional Commits, enforced both locally (husky commit-msg hook) and in CI
// (the `commitlint` PR job lints the PR title — the subject of the squash
// commit, which is what semantic-release reads to compute the next release).
module.exports = {
  extends: ["@commitlint/config-conventional"],
};
