#!/usr/bin/env bash
set -euo pipefail

UV_VERSION="${UV_VERSION:-0.11.16}"

echo "Checking GitHub CLI..."
if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh is required. Install GitHub CLI first." >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "ERROR: gh is installed but not authenticated. Run: gh auth login." >&2
  exit 1
fi

echo "Checking uv..."
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Installing uv ${UV_VERSION}..."
  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv was not found after install. Ensure ~/.local/bin is on PATH." >&2
  exit 1
fi

echo "Checking Python..."
python3 --version 2>/dev/null || echo "WARNING: python3 not found. uv may still manage Python as needed."

echo "Checking Node.js..."
node --version 2>/dev/null || echo "WARNING: node not found. Node is required only for Node-based skill scripts."

echo "Checking pnpm..."
if ! command -v pnpm >/dev/null 2>&1; then
  if command -v corepack >/dev/null 2>&1; then
    corepack enable
    corepack prepare pnpm@10 --activate
  else
    echo "WARNING: pnpm not found and corepack is unavailable. pnpm is required only for skills with package.json."
  fi
fi

echo
echo "Runtime versions:"
gh --version | head -n 1
uv --version
python3 --version 2>/dev/null || true
node --version 2>/dev/null || true
pnpm --version 2>/dev/null || true
