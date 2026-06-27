#!/usr/bin/env bash
set -euo pipefail

# Verify the skill runtime tools are present. Quiet on success (no output); on a
# missing required tool, fail with the exact gap and how to close it. Optional
# tools only warn (to stderr) when absent.

missing=0
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required but not found. Install it (https://docs.astral.sh/uv/getting-started/installation/) or run setup-agent-skills-runtime.sh." >&2
  missing=1
fi

command -v python3 >/dev/null 2>&1 ||
  echo "WARNING: python3 not found; uv can manage Python, but some stdlib scripts expect python3." >&2
command -v node >/dev/null 2>&1 ||
  echo "WARNING: node not found; needed only for Node-based skill scripts." >&2
command -v bun >/dev/null 2>&1 ||
  echo "WARNING: bun not found; needed only for skills with a package.json." >&2

[ "${missing}" -eq 0 ] || exit 1
