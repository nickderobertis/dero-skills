# Pinned versions for the llmlint LLM-as-judge harness stack — the single source
# of truth. This file is sourced, not executed: bump a pin here to upgrade, and
# every installer (scripts/setup-llmlint.sh for oneharness + llmlint; the CI
# llmlint job for the Codex harness) picks it up, so each version lives in exactly
# one place.
#
# oneharness must stay >= 0.2.531 — the release that added the `ONEHARNESS_<FIELD>`
# env overrides the Claude Code session harness flip relies on; the pin is well
# past that floor.
ONEHARNESS_VERSION="0.3.0"
LLMLINT_VERSION="0.2.17"

# Codex CLI (@openai/codex) — the committed default harness, installed via bun in
# CI. It reads its key from ~/.codex/auth.json (login --with-api-key), not the env.
CODEX_VERSION="0.142.3"
