#!/usr/bin/env bash
# Validate and smoke-check every skill in the repo.
#
# Discovers all skills under skills/<scope>/<skill-name>/ (a directory is a
# skill iff it contains a SKILL.md) and runs the shared tooling against each.
#
#   ./scripts/validate-skills.sh             # validate + smoke all skills
#   ./scripts/validate-skills.sh --no-smoke  # validate only
#
# Runs the Python tooling through uv (this repo runs all Python through uv).
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

run_smoke=1
if [[ "${1:-}" == "--no-smoke" ]]; then
  run_smoke=0
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "validate-skills.sh requires uv (this repo runs Python through uv)." >&2
  exit 1
fi
PY=(uv run python)

mapfile -t skill_files < <(find skills -type f -name SKILL.md | sort)
if [[ "${#skill_files[@]}" -eq 0 ]]; then
  echo "No skills found under skills/." >&2
  exit 1
fi

# Quiet on success: validate_skill.py / smoke_skill_scripts.py each name the skill
# they check, so no per-skill header here; a clean run prints one final line.
failures=0
for skill_md in "${skill_files[@]}"; do
  skill_dir="$(dirname "$skill_md")"
  if ! "${PY[@]}" tools/validate_skill.py "$skill_dir"; then
    failures=$((failures + 1))
  fi
  if [[ "$run_smoke" -eq 1 ]]; then
    if ! "${PY[@]}" tools/smoke_skill_scripts.py "$skill_dir"; then
      failures=$((failures + 1))
    fi
  fi
done

if [[ "$failures" -ne 0 ]]; then
  echo "Skill validation FAILED ($failures check(s) failed)." >&2
  exit 1
fi
echo "All skills passed validation."
