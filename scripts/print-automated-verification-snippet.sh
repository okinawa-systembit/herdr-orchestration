#!/usr/bin/env bash
# Emit a Markdown snippet for docs/herdr-orchestrator-verification-log.md (§18 automated section).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
DATE="$(date -u +"%Y-%m-%d")"

echo "Paste into [verification log](docs/herdr-orchestrator-verification-log.md):"
echo
echo '```text'
echo "Recorded: ${DATE} UTC"
echo "Git commit: ${COMMIT}"
if OUT="$(npm run test:orchestrator 2>&1)"; then
  echo "${OUT}" | tail -3
else
  echo "test:orchestrator FAILED"
  echo "${OUT}"
  exit 1
fi
echo '```'
