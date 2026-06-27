#!/usr/bin/env bash
# Central, idempotent setup for the optional `llmlint` LLM-as-judge lint.
#
# Wired into Claude Code's SessionStart hook (.claude/settings.json) so web/cloud
# sessions can run `just lint-llm` with no manual steps; also safe to run by hand
# (`just setup-llmlint`) or from a terminal. Every step tolerates failure and the
# script always exits 0 — a flaky install must never break session startup.
#
# What it does, and why:
#   1. Installs the pinned `oneharness` and `llmlint` versions (pins live in
#      scripts/llmlint-versions.sh, the single source of truth — bump them
#      there), reinstalling whenever the present version differs from the pin.
#   2. Inside a Claude Code session, exports env that flips oneharness to the only
#      harness authenticated here — claude-code + Opus — overriding the committed
#      codex + gpt-5.5 default in oneharness.toml. `IS_SANDBOX` for that harness
#      comes from oneharness.toml, not here.
#
# (Workspace trust is intentionally not touched: under the harness's bypass mode
# the permission allowlist is moot, so an untrusted workspace runs fine — verified.)
set -uo pipefail

# Pinned tool versions live in one centralized, sourceable file — bump them
# there. The installs below fetch exactly these tags.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/llmlint-versions.sh
. "$SCRIPT_DIR/llmlint-versions.sh"
readonly ONEHARNESS_VERSION LLMLINT_VERSION
readonly BIN_DIR="$HOME/.local/bin"

log() { printf 'setup-llmlint: %s\n' "$*" >&2; }

# Install $1 at exactly v$2 from its upstream install.sh unless already pinned.
# $3 is the raw install.sh URL.
ensure_pinned() {
  local bin="$1" want="$2" url="$3" have
  have="$("$bin" --version 2>/dev/null | awk '{print $2}')"
  [ "$have" = "$want" ] && return 0
  log "installing $bin v$want (have: ${have:-none})"
  curl -fsSL "$url" | sh -s -- --version "v$want" >&2 \
    || log "$bin install failed (continuing)"
}

ensure_oneharness() {
  ensure_pinned oneharness "$ONEHARNESS_VERSION" \
    https://raw.githubusercontent.com/nickderobertis/oneharness/main/scripts/install.sh
}

ensure_llmlint() {
  ensure_pinned llmlint "$LLMLINT_VERSION" \
    https://raw.githubusercontent.com/nickderobertis/llmlint/main/scripts/install.sh
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
