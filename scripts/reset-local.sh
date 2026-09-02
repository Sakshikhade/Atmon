#!/usr/bin/env bash
#
# Reset the local development database and data artifacts in one command.
#
# Usage:
#   ./scripts/reset-local.sh          # wipe the local SQLite database (data/outbox.db)
#   ./scripts/reset-local.sh --all    # wipe local database and behavior logs (data/behavior_log.csv)
#

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTBOX="$ROOT/data/outbox.db"
LOG_FILE="$ROOT/data/behavior_log.csv"

DO_ALL=false
if [[ "${1:-}" == "--all" ]]; then
  DO_ALL=true
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: $0 [--all]"
  echo ""
  echo "Options:"
  echo "  --all    Remove both data/outbox.db and data/behavior_log.csv"
  echo "  -h, --help Show this help message"
  exit 0
elif [[ -n "${1:-}" ]]; then
  echo "usage: $0 [--all]" >&2
  exit 2
fi

echo "🟢 Resetting local development database state"

# ---------------------------------------------------------------------------
# 1. SQLite database (always).
# ---------------------------------------------------------------------------
if [[ -f "$OUTBOX" ]]; then
  rm -f "$OUTBOX"
  echo "  ✓ removed local database: data/outbox.db"
else
  echo "  • no local database to remove (data/outbox.db absent)"
fi

# ---------------------------------------------------------------------------
# 2. Behavior logs (only with --all).
# ---------------------------------------------------------------------------
if [[ "$DO_ALL" == true ]]; then
  if [[ -f "$LOG_FILE" ]]; then
    rm -f "$LOG_FILE"
    echo "  ✓ removed local behavior log: data/behavior_log.csv"
  else
    echo "  • no local behavior log to remove (data/behavior_log.csv absent)"
  fi
else
  echo "  • kept behavior log (pass --all to remove data/behavior_log.csv)"
fi

echo "✅ Local reset complete."
