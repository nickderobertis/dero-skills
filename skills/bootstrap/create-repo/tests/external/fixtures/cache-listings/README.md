# Recorded Actions cache listings

Real answers of `GET /repos/{r}/actions/caches?sort=size_in_bytes&direction=desc&per_page=100`,
every page in order, read with `gh api` on the date each file carries. Local servers replay
them at GitHub's REST boundary for `actions-cache-prune.mjs` and `actions-budget-figures.sh
cache-headroom` (`e2e/test_actions_cache_e2e.py`). A listing holds cache keys, refs and sizes,
none of it secret; a test that needs a state the recording lacks adds entries to its own copy.
