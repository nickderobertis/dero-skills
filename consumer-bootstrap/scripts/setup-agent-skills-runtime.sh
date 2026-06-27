#!/usr/bin/env bash
set -euo pipefail

# Provision the skill runtime (gh, uv, bun). Quiet on success: it announces only
# the installs it actually performs (to stderr) and fails with a concrete next
# action. No success banner or version dump.

UV_VERSION="${UV_VERSION:-0.11.16}"

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh (GitHub CLI) is required. Install it: https://cli.github.com" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "ERROR: gh is installed but not authenticated. Run: gh auth login" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "installing uv ${UV_VERSION}..." >&2
  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found after install. Add ~/.local/bin to PATH, then re-run." >&2
  exit 1
fi

command -v python3 >/dev/null 2>&1 ||
  echo "WARNING: python3 not found; uv can manage Python as needed." >&2
command -v node >/dev/null 2>&1 ||
  echo "WARNING: node not found; needed only for Node-based skill scripts." >&2

if ! command -v bun >/dev/null 2>&1; then
  echo "installing bun..." >&2
  curl -fsSL https://bun.sh/install | bash
  export PATH="$HOME/.bun/bin:$PATH"
fi
command -v bun >/dev/null 2>&1 ||
  echo "WARNING: bun not found after install; needed only for skills with a package.json." >&2
