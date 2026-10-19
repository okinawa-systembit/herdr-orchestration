#!/usr/bin/env bash
# Install central herdr-orchestrator Skill to a user-global skills directory (§17.3).
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${HERDR_ORCHESTRATOR_SKILL_DEST:-${HOME}/.cursor/skills/herdr-orchestrator}"
DRY_RUN=0

CLEANUP_STAGING_DIR=""
CLEANUP_BACKUP_DIR=""
CLEANUP_DEST=""
CLEANUP_SWAPPED=0
CLEANUP_BACKUP_CREATED=0

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM HUP
  if [[ (${CLEANUP_BACKUP_CREATED:-0} -eq 1 || ${CLEANUP_SWAPPED:-0} -eq 1) && $exit_code -ne 0 ]]; then
    if [[ -n "${CLEANUP_BACKUP_DIR:-}" && -d "$CLEANUP_BACKUP_DIR" ]]; then
      echo "setup.sh: Installation failed. Rolling back to previous installation..." >&2
      rm -rf "$CLEANUP_DEST" 2>/dev/null || true
      if mv "$CLEANUP_BACKUP_DIR" "$CLEANUP_DEST" 2>/dev/null; then
        echo "setup.sh: Rolled back successfully to previous installation." >&2
      else
        echo "setup.sh: Error: Rollback to previous installation failed!" >&2
      fi
    fi
  fi
  if [[ -n "${CLEANUP_STAGING_DIR:-}" && -d "$CLEANUP_STAGING_DIR" ]]; then
    rm -rf "$CLEANUP_STAGING_DIR" 2>/dev/null || true
  fi
  if [[ $exit_code -eq 0 && -n "${CLEANUP_BACKUP_DIR:-}" && -d "$CLEANUP_BACKUP_DIR" ]]; then
    rm -rf "$CLEANUP_BACKUP_DIR" 2>/dev/null || true
  fi
  exit "$exit_code"
}
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
trap cleanup EXIT

validate_dest_path() {
  local target=$1
  if [[ -z "${target// /}" ]]; then
    echo "setup.sh: destination path cannot be empty" >&2
    exit 1
  fi

  local resolved
  if command -v realpath > /dev/null 2>&1; then
    resolved=$(realpath -m "$target")
  else
    resolved=$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$target")
  fi

  if [[ "$resolved" == "/" ]]; then
    echo "setup.sh: destination cannot be root directory ('/')" >&2
    exit 1
  fi

  local home_resolved
  if command -v realpath > /dev/null 2>&1; then
    home_resolved=$(realpath -m "$HOME")
  else
    home_resolved=$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$HOME")
  fi
  if [[ "$resolved" == "$home_resolved" ]]; then
    echo "setup.sh: destination cannot be home directory ('$HOME')" >&2
    exit 1
  fi

  case "$resolved" in
    /bin | /sbin | /usr | /usr/bin | /usr/sbin | /usr/local/bin | /etc | /var | /tmp | /dev | /proc | /sys | /boot)
      echo "setup.sh: destination cannot be system directory: $resolved" >&2
      exit 1
      ;;
  esac
}

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

validate_dest_path "$DEST"

echo "source: ${SKILL_ROOT}"
echo "dest:   ${DEST}"

if [[ $DRY_RUN -eq 1 ]]; then
  echo "would mkdir -p $(dirname "$DEST")"
  echo "would cp -a ${SKILL_ROOT}/SKILL.md ${DEST}/SKILL.md"
  echo "would cp -a ${SKILL_ROOT}/scripts ${DEST}/scripts"
  echo "would cp -a ${SKILL_ROOT}/references ${DEST}/references"
  echo "would chmod +x ${DEST}/scripts/herdr-orchestrator ${DEST}/scripts/task-registry.py"
  exit 0
fi

for item in SKILL.md scripts references; do
  if [[ ! -e "${SKILL_ROOT}/${item}" ]]; then
    echo "setup.sh: missing ${SKILL_ROOT}/${item}" >&2
    exit 1
  fi
done

DEST_PARENT="$(dirname "$DEST")"
mkdir -p "$DEST_PARENT"

# Staging directory for atomic swap and anti-nesting protection
STAGING_DIR=$(mktemp -d "${DEST_PARENT}/.herdr-orchestrator.staging.XXXXXX")
CLEANUP_STAGING_DIR="$STAGING_DIR"
BACKUP_DIR="${DEST_PARENT}/.herdr-orchestrator.backup.$$"
CLEANUP_BACKUP_DIR="$BACKUP_DIR"
CLEANUP_DEST="$DEST"

for item in SKILL.md scripts references; do
  cp -a "${SKILL_ROOT}/${item}" "${STAGING_DIR}/${item}"
done

chmod +x "${STAGING_DIR}/scripts/herdr-orchestrator" "${STAGING_DIR}/scripts/task-registry.py" 2>/dev/null || true

# Atomic swap into place
if [[ -e "$DEST" ]]; then
  mv "$DEST" "$BACKUP_DIR"
  CLEANUP_BACKUP_CREATED=1
  CLEANUP_SWAPPED=1
fi

mv "$STAGING_DIR" "$DEST"
CLEANUP_STAGING_DIR="" # Successfully moved into place

# Clean up backup on success
CLEANUP_BACKUP_CREATED=0
CLEANUP_SWAPPED=0
rm -rf "$BACKUP_DIR" 2>/dev/null || true

echo "installed herdr-orchestrator Skill to ${DEST}"
