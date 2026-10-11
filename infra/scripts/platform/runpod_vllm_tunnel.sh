#!/usr/bin/env bash
set -Eeuo pipefail

# Local SSH tunnel to a Pod whose vLLM server remains bound to 127.0.0.1.
# Configure values through environment variables; never put private keys here.

RUNTIME_ROOT="${RUNPOD_TUNNEL_ROOT:-${XDG_RUNTIME_DIR:-/tmp}/solo-empire-runpod-vllm}"
PID_FILE="$RUNTIME_ROOT/tunnel.pid"
LOG_FILE="$RUNTIME_ROOT/tunnel.log"
LOCK_FILE="$RUNTIME_ROOT/tunnel.lock"
SSH_USER="${RUNPOD_SSH_USER:-}"
SSH_HOST="${RUNPOD_SSH_HOST:-}"
SSH_PORT="${RUNPOD_SSH_PORT:-}"
SSH_KEY="${RUNPOD_SSH_KEY:-}"
LOCAL_HOST="${RUNPOD_LOCAL_HOST:-127.0.0.1}"
LOCAL_PORT="${RUNPOD_LOCAL_PORT:-18000}"
REMOTE_PORT="${RUNPOD_REMOTE_PORT:-8000}"

die() {
  echo "runpod-vllm-tunnel: $*" >&2
  exit 1
}

validate_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 )) || die "invalid port: $1"
}

validate_config() {
  command -v ssh >/dev/null 2>&1 || die "missing required command: ssh"
  command -v setsid >/dev/null 2>&1 || die "missing required command: setsid"
  [[ -n "$SSH_USER" ]] || die "set RUNPOD_SSH_USER"
  [[ -n "$SSH_HOST" ]] || die "set RUNPOD_SSH_HOST"
  [[ -n "$SSH_KEY" && -r "$SSH_KEY" ]] || die "set RUNPOD_SSH_KEY to a readable private key"
  validate_port "$LOCAL_PORT"
  validate_port "$REMOTE_PORT"
  case "$LOCAL_HOST" in
    127.0.0.1|localhost) ;;
    *) die "RUNPOD_LOCAL_HOST must remain loopback" ;;
  esac
  if [[ -n "$SSH_PORT" ]]; then validate_port "$SSH_PORT"; fi
}

acquire_lock() {
  mkdir -p "$RUNTIME_ROOT"
  command -v flock >/dev/null 2>&1 || die "missing required command: flock"
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "another tunnel operation is already running"
}

read_pid() {
  [[ -s "$PID_FILE" ]] || return 1
  local pid
  pid="$(<"$PID_FILE")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$pid"
}

running() {
  local pid
  pid="$(read_pid)" || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq "$LOCAL_PORT:127.0.0.1:$REMOTE_PORT" || return 1
  printf '%s\n' "$pid"
}

start() {
  validate_config
  acquire_lock
  local pid
  if pid="$(running)"; then
    echo "SSH tunnel already running (pid $pid) at http://$LOCAL_HOST:$LOCAL_PORT"
    return 0
  fi
  rm -f "$PID_FILE"
  : > "$LOG_FILE"

  local -a args=(-N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3)
  args+=( -i "$SSH_KEY" )
  if [[ -n "$SSH_PORT" ]]; then args+=( -p "$SSH_PORT" ); fi
  args+=( -L "$LOCAL_HOST:$LOCAL_PORT:127.0.0.1:$REMOTE_PORT" "$SSH_USER@$SSH_HOST" )

  # Start in a new session so the tunnel survives the shell that launched this
  # helper (for example, a short-lived CI/SSH command). The subshell's exec
  # keeps the recorded PID attached to the actual ssh process.
  (
    exec setsid ssh "${args[@]}"
  ) >> "$LOG_FILE" 2>&1 < /dev/null &
  pid="$!"
  printf '%s\n' "$pid" > "$PID_FILE"
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    die "SSH tunnel failed to start; inspect $LOG_FILE"
  fi
  echo "SSH tunnel ready at http://$LOCAL_HOST:$LOCAL_PORT"
  echo "vLLM health will become ready after the Pod finishes loading the model."
}

stop() {
  acquire_lock
  local pid
  if ! pid="$(running)"; then
    rm -f "$PID_FILE"
    echo "SSH tunnel already stopped."
    return 0
  fi
  kill -TERM "$pid"
  for _ in $(seq 1 10); do
    kill -0 "$pid" 2>/dev/null || {
      rm -f "$PID_FILE"
      echo "SSH tunnel stopped."
      return 0
    }
    sleep 1
  done
  echo "SSH tunnel did not stop within 10s; inspect $LOG_FILE" >&2
  return 1
}

status() {
  local pid
  if pid="$(running)"; then
    echo "tunnel=up pid=$pid local=http://$LOCAL_HOST:$LOCAL_PORT"
  else
    echo "tunnel=stopped"
    return 1
  fi
  if command -v curl >/dev/null 2>&1 && curl --fail --silent --show-error --max-time 5 "http://$LOCAL_HOST:$LOCAL_PORT/health" >/dev/null 2>&1; then
    echo "vllm_health=ready"
  else
    echo "vllm_health=unavailable_or_loading"
  fi
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "Usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
