#!/usr/bin/env bash
# Copied from dero-skills' create-repo skill and edited only there, never in a copy.
# source: github.com/nickderobertis/dero-skills/skills/bootstrap/create-repo/assets/budgets/actions-budget-figures.sh
# version: 1
# sha256: e41b4f7509258f220fca89ce1eab55781c23c390e045c176d31606d5a9fa90ee
#
# The GitHub Actions budget figures, measured from onevcs's read-only host answers
# and reported to `onebudgetspec check` (a `measure: reported` budget's command).
# onevcs selects the runs, jobs and pull requests; this script only applies the
# counting rules and the arithmetic. The contract is the "GitHub Actions budget
# figures" section of create-repo's references/tools/onebudgetspec.md.
#
#   actions-budget-figures.sh longest-job --stage pr|cd [--release TAG] [--repo OWNER/NAME] [--now TIMESTAMP]
#   actions-budget-figures.sh job-count   --stage pr|cd [--release TAG] [--repo OWNER/NAME] [--now TIMESTAMP]
#   actions-budget-figures.sh release-cycle-time        [--release TAG] [--repo OWNER/NAME] [--now TIMESTAMP]
#   actions-budget-figures.sh release-read-requests     [--release TAG] [--repo OWNER/NAME]
#   actions-budget-figures.sh pr-read-requests          [--release TAG] [--repo OWNER/NAME]
#
# Needs bash, jq and `onevcs` (or the binary `ONEVCS` names). Exit 0 wrote the
# figure to ONEBUDGETSPEC_RESULT; 1 is missing data or a failed read, with the
# reason on stderr and nothing written; 2 is an invalid invocation.
set -euo pipefail

me=actions-budget-figures

usage() {
  [ -z "${1:-}" ] || printf '%s: %s\n' "$me" "$1" >&2
  sed -n 's/^#   \(actions-budget-figures.sh \)/usage: \1/p' "$0" >&2
  exit 2
}
die() {
  printf '%s: %s\n' "$me" "$1" >&2
  exit 1
}
missing() {
  printf 'missing: %s\n' "$1" >&2
  exit 1
}

case "${1:-}" in
  -h | --help) sed -n '7,/^set -euo/{/^#/s/^# \{0,1\}//p;}' "$0"; exit 0 ;;
esac
if [ -z "${ONEBUDGETSPEC_RESULT:-}" ]; then
  printf "%s: ONEBUDGETSPEC_RESULT is unset; run this as a budget's command under 'onebudgetspec check', which names the file the figure is written to\n" "$me" >&2
  exit 2
fi

figure="${1:-}"
case "$figure" in
  longest-job | job-count) takes="stage release repo now" ;;
  release-cycle-time) takes="release repo now" ;;
  release-read-requests | pr-read-requests) takes="release repo" ;;
  "") usage "name a figure" ;;
  *) usage "unknown figure: $figure" ;;
esac
shift
stage="" release="" repo="" now=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stage | --release | --repo | --now) ;;
    *) usage "unknown argument: $1" ;;
  esac
  case " $takes " in
    *" ${1#--} "*) ;;
    *) usage "$figure takes no $1" ;;
  esac
  [ $# -ge 2 ] && [ -n "$2" ] || usage "$1 needs a value"
  case "$1" in
    --stage) stage=$2 ;;
    --release) release=$2 ;;
    --repo) repo=$2 ;;
    --now) now=$2 ;;
  esac
  shift 2
done
case "$takes" in
  stage*) case "$stage" in pr | cd) ;; *) usage "$figure needs --stage pr or --stage cd" ;; esac ;;
esac

command -v jq >/dev/null 2>&1 || die "jq is not on PATH"
onevcs=${ONEVCS:-onevcs}
command -v "$onevcs" >/dev/null 2>&1 ||
  die "onevcs is not on PATH (install a release carrying \`onevcs host\`, or name the binary in ONEVCS)"

[ -n "$now" ] || now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
[[ "$now" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$ ]] ||
  usage "--now must be an RFC 3339 UTC timestamp such as 2026-10-15T07:20:03Z, got: $now"

repo=${repo:-${GITHUB_REPOSITORY:-}}
[ -n "$repo" ] || die "no repository: pass --repo OWNER/NAME (GITHUB_REPOSITORY is unset)"
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || usage "--repo must be OWNER/NAME, got: $repo"

if [ -z "$release" ] && [ -n "${GITHUB_EVENT_PATH:-}" ] && [ -f "$GITHUB_EVENT_PATH" ]; then
  release=$(jq -r '.release.tag_name // empty' "$GITHUB_EVENT_PATH") ||
    die "the event payload $GITHUB_EVENT_PATH is not JSON"
fi
if [ -z "$release" ] && [ "${GITHUB_REF_TYPE:-}" = tag ]; then
  release=${GITHUB_REF_NAME:-}
fi
[ -n "$release" ] ||
  die "no release to measure: pass --release TAG (no release event payload and no tag ref in the environment)"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Saved answers: one file per query and argument under RUNNER_TEMP, so every figure
# a job takes for one release asks onevcs each question once. Nothing is saved off
# a runner, and nothing is shared across jobs.
cache=""
if [ -n "${RUNNER_TEMP:-}" ]; then
  cache="$RUNNER_TEMP/actions-budget-figures/${repo%%/*}__${repo#*/}/$(printf '%s' "$release" | tr -c 'A-Za-z0-9._+-' '_')"
  mkdir -p "$cache"
fi

# fetch KEY QUERY ARG... prints the path of onevcs's answer to `host QUERY ARG...`,
# reading the saved answer when there is one and saving a fresh one whole before it
# is used. A failed read saves nothing and ends the script with onevcs's reason.
fetch() {
  local key=$1 query=$2 answer
  shift 2
  if [ -n "$cache" ] && [ -f "$cache/$key.json" ]; then
    printf '%s\n' "$cache/$key.json"
    return
  fi
  answer="$work/$key.json"
  if ! "$onevcs" host "$query" --repo "$repo" "$@" >"$work/answer" 2>"$work/stderr"; then
    die "onevcs host $query $* failed: $(tr -s '\n' ' ' <"$work/stderr")"
  fi
  jq -e '.schema_version == 1 and (.cost.requests | type) == "array"' "$work/answer" >/dev/null 2>&1 ||
    die "onevcs host $query $* answered no schema_version 1 document"
  mv "$work/answer" "$answer"
  if [ -n "$cache" ]; then
    cp "$answer" "$cache/.$key.$$" && mv "$cache/.$key.$$" "$cache/$key.json"
  fi
  printf '%s\n' "$answer"
}

safe() { printf '%s' "$1" | tr -c 'A-Za-z0-9._+-' '-'; }

# The selections every figure reads, as one JSON document on stdout:
#   {"blocks": [{"label", "number", "merged_at", "runs": [run + jobs]}],
#    "answers": [{"query", "cost"}]}
# with one unlabelled block for --stage cd and one per pull request for --stage pr.
release_changes() {
  fetch release-changes release-changes --tag "$release"
}

cd_selection() {
  local rc cr commit ids id payload_run seen=" " answers=() extra=()
  rc=$(release_changes) || exit 1
  commit=$(jq -r .commit "$rc")
  cr=$(fetch "commit-runs-$commit-push-release" commit-runs --commit "$commit" --event push --event release) || exit 1
  [ "$(jq '.runs | length' "$cr")" -gt 0 ] ||
    missing "no push or release run at $release's commit $commit"
  if [ -n "${GITHUB_RUN_ID:-}" ]; then
    extra+=("$GITHUB_RUN_ID")
  fi
  if [ -n "${GITHUB_EVENT_PATH:-}" ] && [ -f "$GITHUB_EVENT_PATH" ]; then
    payload_run=$(jq -r '.workflow_run.id // empty' "$GITHUB_EVENT_PATH") ||
      die "the event payload $GITHUB_EVENT_PATH is not JSON"
    [ -z "$payload_run" ] || extra+=("$payload_run")
  fi
  ids=$(jq -r '.runs[].id' "$cr") || die "onevcs host commit-runs answered no run ids"
  for id in $ids ${extra[@]+"${extra[@]}"}; do
    [[ "$id" =~ ^[0-9]+$ ]] || die "a run id must be a number, got: $id"
    case "$seen" in *" $id "*) continue ;; esac
    seen="$seen$id "
    answers+=("$(fetch "run-jobs-$id" run-jobs --run "$id")") || exit 1
  done
  jq -n --slurpfile rc "$rc" --slurpfile cr "$cr" '
    [inputs] as $jobs
    | ($cr[0].runs | map({key: (.id | tostring), value: .}) | from_entries) as $at
    | {blocks: [{label: null, runs: [$jobs[] | ($at[.run_id | tostring] // {id: .run_id, workflow_name: null, event: null}) + {jobs}]}],
       answers: ([{query: "release-changes --tag \($rc[0].tag)", cost: $rc[0].cost},
                  {query: "commit-runs --commit \($cr[0].commit) --event push --event release", cost: $cr[0].cost}]
                 + [$jobs[] | {query: "run-jobs --run \(.run_id)", cost}])}' "${answers[@]}"
}

pr_selection() {
  local rc unmapped number head merged answers=()
  rc=$(release_changes) || exit 1
  unmapped=$(jq -r '[.commits[] | select(.change == null) | .sha] | join(", ")' "$rc")
  [ -z "$unmapped" ] ||
    missing "commit $unmapped of $release maps to no merged pull request (a rebase merge, or a push with no pull request)"
  [ "$(jq '.changes | length' "$rc")" -gt 0 ] || missing "$release carries no pull request"
  jq -r '.changes[] | "\(.number) \(.head_sha) \(.merged_at)"' "$rc" >"$work/changes" ||
    die "onevcs host release-changes answered no changes"
  while read -r number head merged; do
    [ "$merged" != null ] || missing "#$number has no merged_at"
    answers+=("$(fetch "change-checks-$number-$head-$(safe "$merged")" change-checks \
      --change "$number" --head "$head" --merged-at "$merged")") || exit 1
  done <"$work/changes"
  jq -n '[inputs] | {blocks: map({label: "#\(.change.number)", number: .change.number,
                                   head: .change.head_sha, merged_at: .change.merged_at, runs, cost}),
                     answers: map({query: "#\(.change.number)", cost})}' "${answers[@]}"
}

# What is missing from a selection, if anything: a pull request with no run, or a
# selected run whose job listing came back empty.
check_selection() {
  local gap
  gap=$(jq -r 'first(.blocks[] | . as $b
      | if .runs == [] then "\(.label) has no pull_request run at its head \(.head) created by its merge"
        else (.runs[] | select(.jobs == [])
              | "run \(.id)\(if .workflow_name then " (\(.workflow_name), \(.event))" else "" end)"
                + "\(if $b.label then " at \($b.label)\u0027s head" else "" end) lists no job")
        end) // empty' "$1")
  [ -z "$gap" ] || missing "$gap"
}

# shellcheck disable=SC2016 # a jq program: every $name in it is jq's, never the shell's
figures='
def ts: sub("\\.[0-9]+Z$"; "Z") | fromdate;
# A counted job: completed, not skipped, and taken by a runner; or the job running
# this script, still in progress, timed to --now. Its seconds are never below 0.
def counted:
  if .status == "completed" and .conclusion != "skipped" and .runner_name != null then
    . + {secs: ([(.completed_at | ts) - (.started_at | ts), 0] | max), end: (.completed_at | ts)}
  elif $self != "" and $runner != "" and (.run_id | tostring) == $self
       and .status == "in_progress" and .runner_name == $runner then
    . + {secs: ([$now - (.started_at | ts), 0] | max), end: $now}
  else empty end;
def os: .runner_os // "no runner OS";
def line: "\(.workflow_name) / \(.name): \(.secs)s";
def head: if .label then ["\(.label):"] else [] end;
def indent: if .label then "  " else "" end;
def blocks: [.blocks[] | . + {all: [.runs[].jobs[]], jobs: ([.runs[].jobs[] | counted] | sort_by(-.secs))}];

def longest_job:
  blocks as $b
  | ([$b[].jobs[].secs] | max) as $value
  | if $value == null then {missing: "no counted job: every job the selection lists was skipped, or never taken by a runner"}
    else {value: $value, detail: ([$b[] | indent as $i | head + if .jobs == [] then ["\($i)no counted job"]
          else [.jobs[] | $i + line] + ["\($i)longest per runner OS:"]
               + [.jobs | group_by(os)[] | max_by(.secs) | "\($i)  \(os): \(line)"] end] | add | join("\n"))} end;

def job_count:
  blocks as $b
  | {value: ([$b[].jobs | length] | max),
     detail: ([$b[] | indent as $i | .jobs as $jobs
       | ["\(if .label then "\(.label): " else "" end)\($jobs | length) jobs", "\($i)per workflow:"]
         + [.all | group_by(.workflow_name)[] | .[0].workflow_name as $w
            | "\($i)  \($w): \([$jobs[] | select(.workflow_name == $w)] | length)"]
         + ["\($i)per runner OS:"] + [$jobs | group_by(os)[] | "\($i)  \(.[0] | os): \(length)"]] | add | join("\n"))};

def release_cycle_time:
  [blocks[] | . as $b
   | ([.runs[].created_at | ts] | min) as $first
   | ([.jobs[].end] | max) as $last
   | if $last == null then {missing: "\(.label): no counted job ends its checks"}
     else {label, checks: ($last - $first), tail: ($now - (.merged_at | ts))} end] as $rows
  | (first($rows[] | select(.missing)) // null) as $gap
  | if $gap then $gap
    else {value: ([$rows[] | .checks + .tail] | max),
          detail: ([$rows[] | "\(.label): checks \(.checks)s + merge to release \(.tail)s"] | join("\n"))} end;

def requests: [.cost.requests[] | "  \(.)"];
def release_read_requests:
  {value: ([.answers[].cost.rest_requests] | add),
   detail: ([.answers[] | ["\(.query): \(.cost.rest_requests) requests"] + requests] | add | join("\n"))};
def pr_read_requests:
  {value: ([.blocks[].cost.rest_requests] | max),
   detail: ([.blocks[] | ["\(.label): \(.cost.rest_requests) requests"] + requests] | add | join("\n"))};
'

case "$figure" in
  release-read-requests) cd_selection >"$work/selection" ;;
  pr-read-requests | release-cycle-time) pr_selection >"$work/selection" ;;
  *) "${stage}_selection" >"$work/selection" ;;
esac
check_selection "$work/selection"

now_epoch=$(jq -n --arg t "$now" '$t | sub("\\.[0-9]+Z$"; "Z") | fromdate')
jq --arg self "${GITHUB_RUN_ID:-}" --arg runner "${RUNNER_NAME:-}" --argjson now "$now_epoch" \
  "$figures ${figure//-/_}" "$work/selection" >"$work/figure"
gap=$(jq -r '.missing // empty' "$work/figure")
[ -z "$gap" ] || missing "$gap"

jq '{value, detail}' "$work/figure" >"$ONEBUDGETSPEC_RESULT"
jq -r .detail "$work/figure" >&2
