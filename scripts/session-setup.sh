#!/usr/bin/env bash
# Idempotent, non-blocking session provisioner, wired into Claude Code's
# SessionStart hook (.claude/settings.json). Its job is to make a fresh web/cloud
# session able to run the documented `just` command surface with no manual steps.
#
# What it does, and why:
#   1. Ensures `just` — the entry point to every recipe (`just check`, `just
#      lint-llm`, ...). A cloud session's image ships uv/node/bun but usually not
#      `just`, and there is no asdf here to read `.tool-versions`, so without this
#      the very first `just ...` call fails. `rust-just` packages the prebuilt
#      `just` binary on PyPI, so `uv tool install` fetches it with no Rust
#      toolchain and no github.com reachability — the same egress-friendly path
#      setup-llmlint.sh uses (PyPI-only).
#   2. Verifies the rest of the toolchain (uv/node/bun). These are image- or
#      asdf-provided and cannot be cleanly uv-installed, so a miss is logged with
#      an actionable pointer rather than a failed install.
#   3. Hands off to the optional llmlint-tier installer (setup-llmlint.sh), kept a
#      separate script so `just setup-llmlint` can run it standalone.
#
# CI provisions the toolchain itself (setup-* actions on the `.tool-versions`
# pins; `just bootstrap` calls setup-llmlint.sh directly), so this no-ops there.
# Every step tolerates failure and the script always exits 0 — a flaky install
# must never abort session startup. Also safe to run by hand (`just session-setup`).
# llmlint: ignore-file[robust_shell, tool_output_is_signal, boundary_inputs_validated] deliberate for a session-startup installer (see header): `set -e` is omitted so a flaky install can't abort the hook — the script owns its exit codes and always exits 0; success stays quiet while failures log-and-continue rather than block startup; and `just` is installed from PyPI (`uv tool install rust-just`) whose wheels ship with Trusted Publishing + PEP 740 attestations, so no unvalidated external input is executed.
set -uo pipefail

# `just` floor tracks the `.tool-versions` pin; `uv tool install --upgrade`
# installs the newest release satisfying it. `rust-just` is the PyPI package that
# ships the `just` binary.
readonly JUST_MIN="1.51.0"
readonly BIN_DIR="$HOME/.local/bin"
# Capture the inherited PATH before we prepend BIN_DIR, so persist_session_env can
# tell whether BIN_DIR was already resolvable (it usually is — the image PATH
# includes ~/.local/bin) and only writes an override when it is not.
readonly ORIG_PATH="${PATH}"

log() { printf 'session-setup: %s\n' "$*" >&2; }

# CI has its own provisioning (setup-* actions on `.tool-versions`), so skip there
# rather than racing it. The SessionStart hook does not fire in CI; this is a
# belt-and-suspenders guard for a hand/`just` invocation on a CI runner.
if [ -n "${CI:-}" ]; then
  log "CI detected; skipping (toolchain provisioned by the CI workflow)"
  exit 0
fi

export PATH="${BIN_DIR}:${PATH}"

# Install `just` via uv unless it already resolves. uv is a clean-clone
# prerequisite; if it is somehow absent, log an actionable pointer and leave any
# existing binary in place rather than aborting startup.
ensure_just() {
  if command -v just >/dev/null 2>&1; then
    log "just present ($(just --version 2>/dev/null || echo unknown))"
    return 0
  fi
  if ! command -v uv >/dev/null 2>&1; then
    log "uv not found; cannot install just (install uv: https://docs.astral.sh/uv/)"
    return 0
  fi
  log "installing rust-just >= $JUST_MIN via uv tool"
  uv tool install --upgrade "rust-just>=$JUST_MIN" >&2 \
    || log "rust-just install failed (continuing)"
}

# The rest of the toolchain (uv/node/bun) is image- or asdf-provided; verify and
# log a pointer if missing rather than attempting an install this hook can't do
# reliably in a restricted-egress session.
verify_prereqs() {
  local tool
  for tool in uv node bun; do
    command -v "$tool" >/dev/null 2>&1 \
      || log "$tool not on PATH (normally provided by the cloud image or asdf/.tool-versions)"
  done
}

# Persist PATH so the freshly installed `just` resolves in every later Bash call.
# Only write an override when BIN_DIR was not already on the inherited PATH — the
# common case (image PATH includes ~/.local/bin) needs nothing. No-op off-session.
persist_session_env() {
  [ -n "${CLAUDE_ENV_FILE:-}" ] || { log "no CLAUDE_ENV_FILE (not a session); skipping env"; return 0; }
  case ":${ORIG_PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) printf 'export PATH=%q\n' "${BIN_DIR}:${PATH}" >> "$CLAUDE_ENV_FILE"
       log "prepended ${BIN_DIR} to PATH for the session";;
  esac
}

ensure_just
verify_prereqs
persist_session_env

if command -v just >/dev/null 2>&1; then
  log "ready (just: $(just --version 2>/dev/null || echo unknown))"
else
  log "just not installed (the just recipes will not resolve until it is)"
fi

# Hand off to the optional llmlint-tier installer beside this script. It installs
# oneharness + llmlint and selects the session harness; kept separate so a plain
# terminal can run just its concern via `just setup-llmlint`.
setup_llmlint="$(dirname "$0")/setup-llmlint.sh"
if [ -x "$setup_llmlint" ]; then
  log "running setup-llmlint.sh"
  "$setup_llmlint" || log "setup-llmlint.sh reported an issue (continuing)"
fi

exit 0
