# Recorded GitHub responses for the composed release tail

`../../test_ci_cd_shape_external.py` replays these through `ONEVCS_GH` while it
runs the last step of a composed repository's `publish` job as release.yml
writes it.

`onetaskgraph-v0.13.0/` is copied byte for byte, sidecars included, from
onetaskgraph's own release-tail test fixtures at its `onetaskgraph-ci-budgets`
landing commit:

- repository: `github.com/nickderobertis/onetaskgraph`
- commit: `a8f736ba4f53e56255ccac8a7be47f71c3672249` (branch `nick/3633/onetaskgraph-ci-budgets`)
- path: `.github/policy/tests/fixtures/release-tail/recordings-v0.13.0/`

Each `.http` file is exactly what `gh api --include --method GET` printed (the
status line, the headers, a blank line and the body), and its
`<name>.source.json` sidecar names the API `path` it answers. The one change a
recording is served with is the one its sidecar names under
`served_in_progress`: the `publish` job the tail runs inside is served still in
progress. Nothing here is edited; to refresh them, copy that directory again
from a later landing commit and say which here.
