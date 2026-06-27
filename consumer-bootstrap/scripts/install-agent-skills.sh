#!/usr/bin/env bash
set -euo pipefail

SKILLS_REPO="${SKILLS_REPO:-<org>/<skills-repo>}"

# Edit this list in the consuming repo. Keep it hard-coded and project-specific.
SKILLS=(
  "skills/bootstrap/create-repo"
)

# Supported project-local agent targets.
# VS Code is represented by the GitHub Copilot skill target.
AGENTS=(
  "cursor"
  "claude-code"
  "github-copilot"
)

# Quiet on success: gh prints its own per-install output; this script adds no
# banner or narration and fails with a concrete next action.
if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh (GitHub CLI) is required. Install it: https://cli.github.com" >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "ERROR: gh is not authenticated. Run: gh auth login" >&2
  exit 1
fi

for agent in "${AGENTS[@]}"; do
  for skill in "${SKILLS[@]}"; do
    gh skill install "${SKILLS_REPO}" "${skill}" \
      --agent "${agent}" \
      --scope project
  done
done
