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

echo "Installing project Agent Skills"
echo "  repo: ${SKILLS_REPO}"
echo "  scope: project"
echo

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh is required." >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "ERROR: gh is not authenticated. Run: gh auth login." >&2
  exit 1
fi

for agent in "${AGENTS[@]}"; do
  echo "Installing skills for ${agent}..."
  for skill in "${SKILLS[@]}"; do
    echo "  - ${skill}"
    gh skill install "${SKILLS_REPO}" "${skill}" \
      --agent "${agent}" \
      --scope project
  done
done

echo
echo "Project Agent Skills installed."
