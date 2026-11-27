run-41674998643.json is the unaltered response body from
GET https://api.github.com/repos/nickderobertis/onepipeline-ui/actions/runs/41674998643
fetched on 2026-11-04 with gh api. Run PR repository objects contain only id, name, and url.

v1.mjs through v8.mjs, v10.mjs and v11.mjs preserve earlier shipped scripts for baseline upgrade checks.

run-41771574673.json and run-41771574673-jobs.json are unaltered response bodies from
GET https://api.github.com/repos/nickderobertis/onepipeline-ui/actions/runs/41771574673
and GET https://api.github.com/repos/nickderobertis/onepipeline-ui/actions/runs/41771574673/attempts/1/jobs,
respectively, fetched read-only on 2026-11-04 with gh api. The rejected workflow
recorded a failed push run and an empty jobs listing.

run-45640449664-attempt-2.json, run-45640449664-attempt-2-jobs.json and
run-45640449664-artifacts.json are unaltered response bodies from
GET https://api.github.com/repos/nickderobertis/onetaskgraph/actions/runs/45640449664/attempts/2,
its `/attempts/2/jobs?per_page=100` and the run's `/artifacts?per_page=100`, fetched
read-only on 2026-11-26 with gh api. Attempt 2 re-ran only the cancelled test-macos job: the
listing carries test-linux, test-windows and lint under attempt 2 with their attempt-1
start, and their artifacts were uploaded in attempt 1.

run-43793315868-attempt-1.json is the unaltered response body from
GET https://api.github.com/repos/nickderobertis/onefollowup/actions/runs/43793315868/attempts/1,
fetched read-only on 2026-11-26 with gh api. The run is completed now; a release read it
while its test-macos job was still queued, and the tests report that state by setting
`status` short of `completed` with a null `conclusion`.

run-45627092822-attempt-1.json, run-45627092822-attempt-1-jobs.json and
run-45627092822-artifacts.json are unaltered response bodies from
GET https://api.github.com/repos/nickderobertis/onetaskgraph/actions/runs/45627092822/attempts/1,
its `/attempts/1/jobs?per_page=100` and the run's `/artifacts?per_page=100`, fetched
read-only on 2026-11-27 with gh api. The job timeout cancelled test-macos, which still
uploaded artifact 15032072669; artifact-15032072669-executions.json and
artifact-15032072669-manifest.json are that archive's two files, unaltered. Its telemetry
names an unfinished invocation and its manifest replays hashes no recorded task replayed.

run-45913208966-attempt-1.json, run-45931323488-attempt-1.json, pull-4098.json and
commit-f97ded21-pulls.json are unaltered response bodies from
GET https://api.github.com/repos/nickderobertis/onetaskgraph/actions/runs/45913208966/attempts/1,
`/actions/runs/45931323488/attempts/1`, `/pulls/4098` and
`/commits/f97ded21243992b4ba1feecb18558e3af6e61cb6/pulls?per_page=100`, fetched read-only on
2026-11-27 with gh api. Run 45913208966 is release PR 4098's first CI run, on head f97ded21,
which release-plz replaced with 605ea11c (run 45931323488) before the PR merged; GitHub ties
f97ded21 to no pull request.
