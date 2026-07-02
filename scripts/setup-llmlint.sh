#!/usr/bin/env bash
# Central, idempotent setup for the optional `llmlint` LLM-as-judge lint.
#
# Wired into Claude Code's SessionStart hook (.claude/settings.json) so web/cloud
# sessions can run `just lint-llm` with no manual steps; also safe to run by hand
# (`just setup-llmlint`) or from a terminal. Every step tolerates failure and the
# script always exits 0 — a flaky install must never break session startup.
#
# What it does, and why:
#   1. Installs `oneharness` (>= the floor that satisfies both the
#      `ONEHARNESS_<FIELD>` env overrides and the read-only mode current `llmlint`
#      requires) and `llmlint` (>= the floor with `--diff`/`--diff-base` and
#      `check-ignores`), upgrading an older cached binary rather than leaving it.
#   2. Inside a Claude Code session, exports env that flips oneharness to the only
#      harness authenticated here — claude-code + Opus — overriding the committed
#      codex + gpt-5.5 default in oneharness.toml. `IS_SANDBOX` for that harness
#      comes from oneharness.toml, not here.
#
# (Workspace trust is intentionally not touched: under the harness's bypass mode
# the permission allowlist is moot, so an untrusted workspace runs fine — verified.)
# llmlint: ignore-file[robust_shell, tool_output_is_signal, boundary_inputs_validated] deliberate for a session-startup installer (see header): `set -e` is omitted so a flaky install can't abort the hook — the script owns its exit codes and always exits 0; success stays quiet while failures log-and-continue rather than block startup; and the upstream `curl | sh` installers are the documented, version-pinned install path (each verifies the binary it fetches).
set -uo pipefail

# Minimum oneharness: >= 0.3.0 is what current `llmlint` needs for read-only mode
# (the judge reads but never edits files); it also has the `ONEHARNESS_<FIELD>`
# env config overrides this setup relies on. Bump as llmlint raises the floor.
readonly ONEHARNESS_MIN="0.3.4"
# Minimum llmlint: >= 0.3.1 — llmlint.yml omits `files.include` and relies on the
# whole-tree default (added in 0.2.31; older binaries scan zero files on a no-args
# run). 0.3.0 was a breaking change (agents scope to their subtree, `agent.files`
# removed) but this repo's config has no `agents` block, so it is unaffected. Bump
# the floor as llmlint raises it; an older cached binary is upgraded.
readonly LLMLINT_MIN="0.3.1"
readonly BIN_DIR="$HOME/.local/bin"

log() { printf 'setup-llmlint: %s\n' "$*" >&2; }

# True when $1 (installed) is older than $2 (minimum). Portable component-wise
# numeric compare — avoids `sort -V` (GNU coreutils only, absent on some `sort`s).
older_than() {
  local IFS=. ; local -a have min ; read -ra have <<<"$1" ; read -ra min <<<"$2"
  local i x y
  for ((i = 0; i < ${#have[@]} || i < ${#min[@]}; i++)); do
    x=${have[i]:-0} ; y=${min[i]:-0} ; x=${x%%[!0-9]*} ; y=${y%%[!0-9]*}
    ((10#${x:-0} < 10#${y:-0})) && return 0
    ((10#${x:-0} > 10#${y:-0})) && return 1
  done
  return 1
}

ensure_oneharness() {
  local have
  if have="$(oneharness --version 2>/dev/null | awk '{print $2}')" && [ -n "$have" ] \
     && ! older_than "$have" "$ONEHARNESS_MIN"; then
    return 0
  fi
  log "installing oneharness >= v$ONEHARNESS_MIN (have: ${have:-none})"
  curl -fsSL https://raw.githubusercontent.com/nickderobertis/oneharness/main/scripts/install.sh \
    | sh -s -- --version "v$ONEHARNESS_MIN" >&2 || log "oneharness install failed (continuing)"
}

ensure_llmlint() {
  local have
  if have="$(llmlint --version 2>/dev/null | awk '{print $2}')" && [ -n "$have" ] \
     && ! older_than "$have" "$LLMLINT_MIN"; then
    return 0
  fi
  log "installing llmlint >= v$LLMLINT_MIN (have: ${have:-none})"
  curl -fsSL https://raw.githubusercontent.com/nickderobertis/llmlint/main/scripts/install.sh \
    | sh -s -- --version "v$LLMLINT_MIN" >&2 || log "llmlint install failed (continuing)"
}

# Persist env for the rest of the session via CLAUDE_ENV_FILE (Claude Code sources
# it into every later Bash call). PATH so the freshly installed binaries resolve;
# ONEHARNESS_* so the run uses claude-code + Opus here. No-op outside a session.
persist_session_env() {
  [ -n "${CLAUDE_ENV_FILE:-}" ] || { log "no CLAUDE_ENV_FILE (not a session); skipping env"; return 0; }
  {
    case ":${PATH}:" in *":${BIN_DIR}:"*) ;; *) printf 'export PATH=%q\n' "${BIN_DIR}:${PATH}";; esac
    printf 'export ONEHARNESS_HARNESSES=%q\n' "claude-code"
    printf 'export ONEHARNESS_MODEL=%q\n' "claude-opus-4-8"
  } >> "$CLAUDE_ENV_FILE"
  log "exported PATH + ONEHARNESS_HARNESSES=claude-code + ONEHARNESS_MODEL=claude-opus-4-8"
}

export PATH="${BIN_DIR}:${PATH}"
ensure_oneharness
ensure_llmlint
persist_session_env
log "ready (oneharness: $(oneharness --version 2>/dev/null || echo missing); llmlint: $(llmlint --version 2>/dev/null || echo missing))"
exit 0
