#!/usr/bin/env bash
set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh (GitHub CLI) is required. Install it: https://cli.github.com" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "ERROR: gh is not authenticated. Run: gh auth login" >&2
  exit 1
fi

gh skill update --all
