#!/usr/bin/env bash
# Symlink installed CLI to ~/.local/bin/herdr-orchestrator (shared by dev + future distribution install).
set -euo pipefail

link_herdr_orchestrator_cli() {
  local cli_path=$1
  local dry_run=${2:-0}
  local link_path="${HERDR_ORCHESTRATOR_BIN_LINK:-${HOME}/.local/bin/herdr-orchestrator}"

  [[ -f "$cli_path" ]] || {
    echo "link-cli.sh: missing CLI: $cli_path" >&2
    return 1
  }

  if [[ $dry_run -eq 1 ]]; then
    echo "would mkdir -p $(dirname "$link_path")"
    echo "would ln -sf $cli_path $link_path"
    return 0
  fi

  mkdir -p "$(dirname "$link_path")"
  ln -sf "$cli_path" "$link_path"
  echo "linked CLI: ${link_path} -> ${cli_path}"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  link_herdr_orchestrator_cli "${1:?usage: link-cli.sh CLI_PATH [--dry-run]}" "${2:-0}"
fi
