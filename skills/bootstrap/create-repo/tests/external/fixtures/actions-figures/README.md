# Recorded GitHub responses for the actions budget figures

`test_actions_budget_figures_e2e.py` replays these through `ONEVCS_GH`
(`actions_figures_replay.py`). Every file under `recordings/` is a real GitHub
REST response in onevcs's envelope, `{"request", "status", "headers", "body"}`,
and its `<name>.source.json` sidecar says where it came from:

- `copied_from` — copied unchanged from onevcs's own journeys, naming the commit
  and path (`crates/onevcs/tests/fixtures/host-queries/recordings/`).
- `recorded` — recorded for these journeys, read-only, with
  `gh api --include --method GET` from a public repository, keeping only the
  headers onevcs keeps, with `why` it was needed and any scrubbing done.

`scenarios/*.json` name what one replay serves, in onevcs's sidecar format: each
entry is served for the request it recorded unless `for` names another, and
`keep_runs` leaves every other run out of a runs listing, and `rename_tags` renames
tags in a releases listing (no public repository records two tags that differ only
by `/` and `_`). Each is a departure from what GitHub answered, and every entry
using one says `why`.

A response's release notes, commit messages and author addresses can name a
private organisation. Before committing a new recording, run it through
`onevcs boundary check --destination public` and replace any refused name with
`example`, in the text and nowhere else, noting it under `scrubbed`;
`test_every_committed_fixture_passes_the_public_boundary` holds every file here
to that check.
