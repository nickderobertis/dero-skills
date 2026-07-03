#!/usr/bin/env bash
# Central, idempotent setup for the optional `llmlint` LLM-as-judge lint.
#
# Wired into Claude Code's SessionStart hook (.claude/settings.json) so web/cloud
# sessions can run `just lint-llm` with no manual steps; also safe to run by hand
# (`just setup-llmlint`) or from a terminal. Every step tolerates failure and the
# script always exits 0 — a flaky install must never break session startup.
#
# What it does, and why:
#   1. Installs the `llmlint` + `oneharness` binaries from PyPI via `uv tool`.
#      `llmlint-cli` wraps the same prebuilt binary the upstream installer ships
#      and depends on `oneharness-cli`, so one dependency resolution fetches both
#      wheels with no Rust toolchain and no github.com reachability (handy in
#      restricted-egress sessions where PyPI is reachable but raw.githubusercontent
#      is not). `uv tool` only links the *requested* package's executable, so
#      `oneharness-cli` is installed as its own tool to expose the `oneharness`
#      binary `llmlint` needs on PATH. `--upgrade` bumps an older cached tool
#      rather than leaving it, honouring the version floors below.
#   2. Inside a Claude Code session, exports env that flips oneharness to the only
#      harness authenticated here — claude-code + Opus — overriding the committed
#      codex + gpt-5.5 default in oneharness.toml. `IS_SANDBOX` for that harness
#      comes from oneharness.toml, not here.
#
# (Workspace trust is intentionally not touched: under the harness's bypass mode
# the permission allowlist is moot, so an untrusted workspace runs fine — verified.)
# llmlint: ignore-file[robust_shell, tool_output_is_signal, boundary_inputs_validated] deliberate for a session-startup installer (see header): `set -e` is omitted so a flaky install can't abort the hook — the script owns its exit codes and always exits 0; success stays quiet while failures log-and-continue rather than block startup; and the toolchain is installed from PyPI (`uv tool install llmlint-cli`/`oneharness-cli`) whose wheels ship with Trusted Publishing + PEP 740 attestations, so no unvalidated external input is executed.
set -uo pipefail

# Version floors, as PyPI constraints (the `*-cli` package version tracks the
# wrapped binary version). `uv tool install --upgrade` installs the newest release
# satisfying each floor. Bump as the tools raise theirs.
#   oneharness >= 0.3.4 — the read-only mode current `llmlint` requires plus the
#     `ONEHARNESS_<FIELD>` env config overrides this setup relies on.
#   llmlint    >= 0.3.1 — llmlint.yml omits `files.include` and relies on the
#     whole-tree default; also needs `--diff`/`--diff-base` and `check-ignores`.
readonly ONEHARNESS_MIN="0.3.4"
readonly LLMLINT_MIN="0.3.1"
readonly BIN_DIR="$HOME/.local/bin"

log() { printf 'setup-llmlint: %s\n' "$*" >&2; }

# Install both binaries from PyPI via uv (the repo's Python package manager). uv is
# a clean-clone prerequisite; if it is somehow absent, log an actionable pointer and
# leave any already-installed binaries in place rather than aborting startup.
ensure_toolchain() {
  if ! command -v uv >/dev/null 2>&1; then
    log "uv not found; cannot install the llmlint toolchain (install uv: https://docs.astral.sh/uv/)"
    return 0
  fi
  # oneharness-cli exposes the `oneharness` binary; llmlint-cli exposes `llmlint`
  # (and pulls oneharness-cli as a dependency, but uv tool only links the primary
  # package's executable — hence the separate install to put `oneharness` on PATH).
  log "installing oneharness-cli >= $ONEHARNESS_MIN + llmlint-cli >= $LLMLINT_MIN via uv tool"
  uv tool install --upgrade "oneharness-cli>=$ONEHARNESS_MIN" >&2 \
    || log "oneharness-cli install failed (continuing)"
  uv tool install --upgrade "llmlint-cli>=$LLMLINT_MIN" >&2 \
    || log "llmlint-cli install failed (continuing)"
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
ensure_toolchain
persist_session_env
log "ready (oneharness: $(oneharness --version 2>/dev/null || echo missing); llmlint: $(llmlint --version 2>/dev/null || echo missing))"
exit 0
