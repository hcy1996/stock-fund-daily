#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

./scripts/fetch_market_data.sh
if [ -f "$ROOT/config.json" ]; then
  python3 -m app.cli run-once
else
  echo "config.json not found, fallback to dry-run"
  python3 -m app.cli run-once --dry-run
fi
