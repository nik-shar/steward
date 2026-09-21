#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Live check — the ONLY thing in this repo that spends model budget.
#
# Everything else is free:
#     uv run pytest           # 318 tests, no network, no tokens
#     uv run steward --doctor  # what is wired, no tokens
#
# This script exists so the token-spending checks live in exactly one place, are
# opt-in, and can never touch your real data: it runs against a throwaway
# STEWARD_HOME and deletes it on exit.
#
# Usage:
#     STEWARD_LIVE=1 scripts/live_check.sh      # ~6 model calls
#
# It refuses to run without STEWARD_LIVE=1, because the default must be free.
# ---------------------------------------------------------------------------
set -euo pipefail

if [[ "${STEWARD_LIVE:-}" != "1" ]]; then
  cat >&2 <<'EOF'
refusing to run: this spends model budget.

    STEWARD_LIVE=1 scripts/live_check.sh

The offline checks are free and cover the same code paths:
    uv run pytest           # 318 tests, driven by Tau's FakeProvider
    uv run steward --doctor  # wiring report, no tokens
EOF
  exit 2
fi

cd "$(dirname "$0")/.."

LIVE_HOME="$(mktemp -d)"
cleanup() { rm -rf "$LIVE_HOME"; }
trap cleanup EXIT

echo "=== sandbox: $LIVE_HOME (deleted on exit; your ~/.steward is untouched) ==="
STEWARD_HOME="$LIVE_HOME" uv run steward --doctor | sed -n '1,6p'

echo
echo "=== 1/2  memory turn — expect 3 model calls ==="
STEWARD_HOME="$LIVE_HOME" uv run steward \
  "Use the remember tool to store that I prefer to work in the mornings. Then tell me in one sentence what you know about me."

echo
echo "=== 2/2  booking turn with consent answered 'y' — expect 3 model calls ==="
printf 'y\n' | STEWARD_HOME="$LIVE_HOME" uv run steward \
  "Call find_availability for tomorrow with window afternoon. If there is a gap of 60 minutes or more, call place_block for tomorrow at 15:00 for 60 minutes labelled 'live check'. Then say in one short line what you did."

echo
echo "=== what was written ==="
STEWARD_HOME="$LIVE_HOME" uv run python - <<'PY'
import os, pathlib, sqlite3

home = pathlib.Path(os.environ["STEWARD_HOME"])
conn = sqlite3.connect(home / "steward.db")
conn.row_factory = sqlite3.Row
memories = [r["text"] for r in conn.execute("select text from dna_memory where superseded_by is null")]
slots = [dict(r) for r in conn.execute("select day, slot, label from day_slots order by slot")]
print("memories  :", memories or "(none)")
print("day_slots :", slots or "(nothing booked)")
PY

echo
echo "=== audit tail ==="
STEWARD_HOME="$LIVE_HOME" uv run steward --audit --audit-limit 12

echo
echo "=== done; sandbox removed ==="
