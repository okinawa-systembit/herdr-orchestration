#!/usr/bin/env bash
# Pre-adoption check: unit tests, Markdown lint, Skill installer dry-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== npm run test:orchestrator =="
npm run test:orchestrator

echo "== npm run check:md =="
npm run check:md

echo "== install/dev-local-install.sh --dry-run =="
bash skills/herdr-orchestrator/install/dev-local-install.sh --dry-run

echo "== install/setup.sh --dry-run (legacy) =="
bash skills/herdr-orchestrator/install/setup.sh --dry-run

echo "verify-orchestrator: OK"
