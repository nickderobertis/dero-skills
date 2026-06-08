#!/usr/bin/env bash
set -euo pipefail

echo "Checking skill runtime tools..."

missing=0

if ! command -v uv >/dev/null 2>&1; then
  echo "MISSING: uv"
  missing=1
else
  uv --version
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "WARNING: python3 not found. uv may still manage Python, but some stdlib scripts may expect python3."
else
  python3 --version
fi

if ! command -v node >/dev/null 2>&1; then
  echo "WARNING: node not found. Required only for Node-based skill scripts."
else
  node --version
fi

if ! command -v pnpm >/dev/null 2>&1; then
  echo "WARNING: pnpm not found. Required only for skills with package.json."
else
  pnpm --version
fi

if [[ "${missing}" -ne 0 ]]; then
  echo "One or more required tools are missing." >&2
  exit 1
fi

echo "Skill runtime check passed."
