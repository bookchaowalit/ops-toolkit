#!/usr/bin/env bash
# BOOK_DEV_AI_HOOK wrapper for Book Dev engineering-loop execution tickets.
# Placeholders from book_dev_execution_hook.py:
#   {ticket} {markdown} {task_id}
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TICKET="${1:-{ticket}}"
PROVIDERS="${BOOK_DEV_AI_PROVIDERS:-grok,codex}"
MODE="${BOOK_DEV_AI_MODE:-implement}"
MAX_TURNS="${BOOK_DEV_AI_MAX_TURNS:-24}"
TIMEOUT_SEC="${BOOK_DEV_AI_TIMEOUT_SEC:-1800}"

# If the hook is invoked with unsubstituted placeholders, fall back to current ticket.
if [[ "$TICKET" == "{ticket}" || -z "$TICKET" ]]; then
  TICKET="data/runtime/agent-loop/book-dev/execution/current.json"
fi

exec bash "$ROOT/infra/scripts/setup/run-python3.sh" \
  "$ROOT/infra/scripts/workflows/book_dev_ai_dispatch.py" \
  --ticket "$TICKET" \
  --providers "$PROVIDERS" \
  --mode "$MODE" \
  --max-turns "$MAX_TURNS" \
  --timeout-sec "$TIMEOUT_SEC" \
  --json
