#!/usr/bin/env bash
# Development install: copy this repo's Skill tree to ~/.agents/skills/herdr-orchestration
# (Cursor / Codex / Agy — same global path; distinct from official "herdr" Skill name).
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=link-cli.sh
source "${INSTALL_DIR}/link-cli.sh"
SKILL_ROOT="$(cd "${INSTALL_DIR}/.." && pwd)"
DEFAULT_DEST="${HOME}/.agents/skills/herdr-orchestration"
DEST="${HERDR_ORCHESTRATION_SKILL_DEST:-${DEFAULT_DEST}}"
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: dev-local-install.sh [--dry-run] [--dest PATH]

  --dry-run   Print actions without copying
  --dest PATH Skill directory (default: $HERDR_ORCHESTRATION_SKILL_DEST or ~/.agents/skills/herdr-orchestration)

Copies SKILL.md, scripts/, and references/ from the checkout you run this from.
Re-run after editing skills/herdr-orchestrator/ so user-global Skill stays in sync.
Also links ~/.local/bin/herdr-orchestrator -> DEST/scripts/herdr-orchestrator (same as future distribution install).

Official Herdr Skill (e.g. "herdr") is separate. Installed Skill name/dir: herdr-orchestration (SKILL.md name:). CLI on PATH: herdr-orchestrator.
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
      echo "dev-local-install.sh: unknown argument: $1" >&2
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
    echo "dev-local-install.sh: missing ${SKILL_ROOT}/${item}" >&2
    exit 1
  fi
  copy_tree "${SKILL_ROOT}/${item}" "${DEST}/${item}"
done

CLI="${DEST}/scripts/herdr-orchestrator"
SHOULD_LINK=0
if [[ "$DEST" == "$DEFAULT_DEST" ]] || [[ -n "${HERDR_ORCHESTRATOR_BIN_LINK:-}" ]]; then
  SHOULD_LINK=1
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "would chmod +x ${CLI} ${DEST}/scripts/task-registry.py"
  if [[ $SHOULD_LINK -eq 1 ]]; then
    link_herdr_orchestrator_cli "$CLI" 1
  else
    echo "would skip ~/.local/bin link (non-default --dest; set HERDR_ORCHESTRATOR_BIN_LINK to override)"
  fi
  echo "hint: use herdr-orchestrator on PATH (~/.local/bin); re-run this script after edits"
  exit 0
fi

chmod +x "${CLI}" "${DEST}/scripts/task-registry.py" 2>/dev/null || true
if [[ $SHOULD_LINK -eq 1 ]]; then
  link_herdr_orchestrator_cli "$CLI" 0
else
  echo "skipped ~/.local/bin link (non-default --dest)"
fi
echo "installed Skill copy to ${DEST}"
echo "hint: herdr-orchestrator on PATH (see: which herdr-orchestrator)"
echo "hint: Codex — append install/codex-execpolicy-snippet.rules to ~/.codex/rules/default.rules (see README.md)"
