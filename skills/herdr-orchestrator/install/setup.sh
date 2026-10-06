#!/usr/bin/env bash
# Install central herdr-orchestrator Skill to a user-global skills directory (§17.3).
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${HERDR_ORCHESTRATOR_SKILL_DEST:-${HOME}/.cursor/skills/herdr-orchestrator}"
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: setup.sh [--dry-run] [--dest PATH]

  --dry-run   Print actions without copying
  --dest PATH Install destination (default: $HERDR_ORCHESTRATOR_SKILL_DEST or ~/.cursor/skills/herdr-orchestrator)

Copies SKILL.md, scripts/, and references/ from this Skill package.
Confirm Cursor/Codex global Skill paths against product docs at install time (§17.3).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --dest)
      DEST="${2:?missing path after --dest}"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "setup.sh: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

copy_tree() {
  local src=$1
  local dst=$2
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "would mkdir -p $(dirname "$dst")"
    echo "would cp -a $src $dst"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  cp -a "$src" "$dst"
}

echo "source: ${SKILL_ROOT}"
echo "dest:   ${DEST}"

for item in SKILL.md scripts references; do
  if [[ ! -e "${SKILL_ROOT}/${item}" ]]; then
    echo "setup.sh: missing ${SKILL_ROOT}/${item}" >&2
    exit 1
  fi
  copy_tree "${SKILL_ROOT}/${item}" "${DEST}/${item}"
done

if [[ $DRY_RUN -eq 1 ]]; then
  echo "would chmod +x ${DEST}/scripts/herdr-orchestrator ${DEST}/scripts/task-registry.py"
  exit 0
fi

chmod +x "${DEST}/scripts/herdr-orchestrator" "${DEST}/scripts/task-registry.py" 2>/dev/null || true
echo "installed herdr-orchestrator Skill to ${DEST}"
