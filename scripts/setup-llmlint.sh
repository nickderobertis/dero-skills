#!/usr/bin/env bash
# Central, idempotent setup for the optional `llmlint` LLM-as-judge lint.
#
# Run from Claude Code's SessionStart hook via session-setup.sh (which provisions
# `just` first, then hands off here) so web/cloud sessions can run `just lint-llm`
# with no manual steps; also safe to run by hand (`just setup-llmlint`) or from a
# terminal, and still called directly by `just bootstrap` and CI. Every step
# tolerates failure and the script always exits 0 — a flaky install must never
# break session startup.
#
# What it does, and why:
#   1. Installs the `llmlint` binary from PyPI via `uv tool`. `llmlint-cli` wraps
#      the same prebuilt binary the upstream installer ships and depends on
#      `oneharness-cli`, so one dependency resolution fetches both wheels with no
#      Rust toolchain and no github.com reachability (handy in restricted-egress
#      sessions where PyPI is reachable but raw.githubusercontent is not). `uv tool`
#      links only the *requested* package's executable onto PATH, but llmlint >=
#      0.3.7 finds `oneharness` beside its own binary in the tool venv — so this one
#      install is a complete setup; no separate oneharness install / PATH entry.
#      `--upgrade` bumps an older cached tool rather than leaving it.
#   2. Inside a Claude Code session, exports env that flips oneharness to the only
#      harness authenticated here — claude-code + Opus — overriding the committed
#      codex + gpt-5.5 default in oneharness.toml. `IS_SANDBOX` for that harness
#      comes from oneharness.toml, not here.
#
# (Workspace trust is intentionally not touched: under the harness's bypass mode
# the permission allowlist is moot, so an untrusted workspace runs fine — verified.)
# llmlint: ignore-file[robust_shell, tool_output_is_signal, boundary_inputs_validated] deliberate for a session-startup installer (see header): `set -e` is omitted so a flaky install can't abort the hook — the script owns its exit codes and always exits 0; success stays quiet while failures log-and-continue rather than block startup; and the toolchain is installed from PyPI (`uv tool install llmlint-cli`) whose wheels ship with Trusted Publishing + PEP 740 attestations, so no unvalidated external input is executed.
set -uo pipefail

# Version floor, as a PyPI constraint (the `llmlint-cli` package version tracks the
# wrapped binary version). `uv tool install --upgrade` installs the newest release
# satisfying it; oneharness comes along transitively at a compatible version.
#   llmlint >= 0.3.17 — the 0.3.11 baseline (finds `oneharness` beside its own
#     executable, so a lone `uv tool install llmlint-cli` works; the whole-tree
#     default the composed llmlint.yml relies on with no `files.include`; `--diff`
#     scoped to the changed files, skipping empty diffs) plus the latest static
#     checks and diff semantics `just lint-llm-*` now depends on: 0.3.15 makes a
#     plain `--diff-base <ref>` mean three-dot/merge-base (so `lint-llm-diff`
#     passes just `origin/main`, no explicit `...HEAD`), and 0.3.17 adds the
#     `validate` subcommand — the deterministic, model-free gate (config structure
#     + `llmlint: ignore` directives + fragment version bumps) that
#     `just lint-llm-validate` and CI run before ever spending a harness call.
readonly LLMLINT_MIN="0.3.17"
readonly BIN_DIR="$HOME/.local/bin"

log() { printf 'setup-llmlint: %s\n' "$*" >&2; }

# Install llmlint from PyPI via uv (the repo's Python package manager). uv is a
# clean-clone prerequisite; if it is somehow absent, log an actionable pointer and
# leave any already-installed binary in place rather than aborting startup.
ensure_toolchain() {
  if ! command -v uv >/dev/null 2>&1; then
    log "uv not found; cannot install llmlint (install uv: https://docs.astral.sh/uv/)"
    return 0
  fi
  # llmlint-cli pulls oneharness-cli as a dependency into the same tool venv, where
  # llmlint discovers the `oneharness` binary beside its own — no separate install.
  log "installing llmlint-cli >= $LLMLINT_MIN via uv tool"
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
# `llmlint doctor` confirms the sibling `oneharness` is reachable (it is not on
# PATH — llmlint resolves it beside its own binary), so report via doctor.
if command -v llmlint >/dev/null 2>&1; then
  log "ready (llmlint: $(llmlint --version 2>/dev/null || echo unknown))"
  llmlint doctor >&2 2>&1 || log "llmlint doctor reported an issue (see above)"
else
  log "llmlint not installed"
fi
exit 0
