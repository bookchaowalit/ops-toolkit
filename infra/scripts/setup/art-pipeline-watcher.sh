#!/usr/bin/env bash
# Start/stop the local art pipeline watcher without requiring a service manager.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUNTIME_DIR="$ROOT/data/runtime/art-pipeline"
PID_FILE="$RUNTIME_DIR/watcher.pid"
LOG_FILE="$RUNTIME_DIR/watcher.log"
PYTHON_WRAPPER="$ROOT/infra/scripts/setup/run-python3.sh"
PIPELINE="$ROOT/infra/scripts/utils/art_pipeline.py"

mkdir -p "$RUNTIME_DIR"

read_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(tr -cd '0-9' < "$PID_FILE")"
  [[ -n "$pid" ]] || return 1
  printf '%s\n' "$pid"
}

is_watcher() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null || return 1
  ps -p "$pid" -o args= 2>/dev/null | grep -F -- "$PIPELINE --watch" >/dev/null
}

status() {
  local pid
  if pid="$(read_pid 2>/dev/null)" && is_watcher "$pid"; then
    echo "art pipeline watcher: running (pid $pid)"
    return 0
  fi
  echo "art pipeline watcher: stopped"
  return 0
}

start() {
  local pid
  if pid="$(read_pid 2>/dev/null)" && is_watcher "$pid"; then
    echo "art pipeline watcher already running (pid $pid)"
    return 0
  fi
  rm -f "$PID_FILE"
  if command -v setsid >/dev/null 2>&1; then
    nohup setsid "$PYTHON_WRAPPER" "$PIPELINE" --watch >> "$LOG_FILE" 2>&1 < /dev/null &
  else
    nohup "$PYTHON_WRAPPER" "$PIPELINE" --watch >> "$LOG_FILE" 2>&1 < /dev/null &
  fi
  pid="$!"
  printf '%s\n' "$pid" > "$PID_FILE"
  sleep 0.2
  if ! is_watcher "$pid"; then
    echo "art pipeline watcher failed to start; see $LOG_FILE" >&2
    rm -f "$PID_FILE"
    return 1
  fi
  echo "art pipeline watcher started (pid $pid)"
}

stop() {
  local pid
  if ! pid="$(read_pid 2>/dev/null)" || ! is_watcher "$pid"; then
    rm -f "$PID_FILE"
    echo "art pipeline watcher already stopped"
    return 0
  fi
  kill "$pid"
  for _ in {1..20}; do
    is_watcher "$pid" || break
    sleep 0.1
  done
  if is_watcher "$pid"; then
    echo "art pipeline watcher did not stop cleanly (pid $pid)" >&2
    return 1
  fi
  rm -f "$PID_FILE"
  echo "art pipeline watcher stopped"
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "Usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
