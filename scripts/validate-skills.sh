#!/usr/bin/env bash
# Validate and smoke-check every skill in the repo.
#
# Discovers all skills under skills/<scope>/<skill-name>/ (a directory is a
# skill iff it contains a SKILL.md) and runs the shared tooling against each.
#
#   ./scripts/validate-skills.sh             # validate + smoke all skills
#   ./scripts/validate-skills.sh --no-smoke  # validate only
#
# Uses `uv run python` when uv is available, otherwise falls back to python3.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

run_smoke=1
if [[ "${1:-}" == "--no-smoke" ]]; then
  run_smoke=0
fi

if command -v uv >/dev/null 2>&1; then
  PY=(uv run python)
else
  PY=(python3)
fi

mapfile -t skill_files < <(find skills -type f -name SKILL.md | sort)
if [[ "${#skill_files[@]}" -eq 0 ]]; then
  echo "No skills found under skills/." >&2
  exit 1
fi

failures=0
for skill_md in "${skill_files[@]}"; do
  skill_dir="$(dirname "$skill_md")"
  echo "==> $skill_dir"
  if ! "${PY[@]}" tools/validate_skill.py "$skill_dir"; then
    failures=$((failures + 1))
  fi
  if [[ "$run_smoke" -eq 1 ]]; then
    if ! "${PY[@]}" tools/smoke_skill_scripts.py "$skill_dir"; then
      failures=$((failures + 1))
    fi
  fi
done

echo
if [[ "$failures" -ne 0 ]]; then
  echo "Skill validation FAILED ($failures check(s) failed)." >&2
  exit 1
fi
echo "All skills passed validation."
