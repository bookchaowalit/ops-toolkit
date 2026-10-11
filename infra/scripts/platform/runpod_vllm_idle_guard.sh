#!/usr/bin/env bash
set -Eeuo pipefail

# Stop the local vLLM process after a quiet period or a hard runtime limit.
# Provider-level billing is stopped only through an explicitly configured,
# executable hook (for example a reviewed RunPod stop script). No credentials
# or provider payloads are read or logged here.

VLLM_BASE_URL="${VLLM_IDLE_BASE_URL:-http://127.0.0.1:8000}"
METRICS_URL="${VLLM_IDLE_METRICS_URL:-$VLLM_BASE_URL/metrics}"
IDLE_TIMEOUT_SEC="${VLLM_IDLE_TIMEOUT_SEC:-0}"
MAX_RUNTIME_SEC="${VLLM_MAX_RUNTIME_SEC:-0}"
POLL_SEC="${VLLM_IDLE_POLL_SEC:-30}"
LAUNCHER="${VLLM_LAUNCHER:-}"
STOP_HOOK="${VLLM_IDLE_STOP_HOOK:-}"
SESSION_ID="${LLM_LAB_SESSION_ID:-}"

die() {
  echo "runpod-vllm-idle-guard: $*" >&2
  exit 1
}

is_non_negative_int() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

is_positive_int() {
  is_non_negative_int "$1" && (( $1 > 0 ))
}

is_idle() {
  local metrics running waiting
  metrics="$(curl --fail --silent --show-error --max-time 5 "$METRICS_URL" 2>/dev/null)" || return 1
  running="$(awk '/^vllm:num_requests_running(\{|[[:space:]])/ { if ($NF !~ /^[0-9]+([.][0-9]+)?$/) bad=1; sum+=$NF; found=1 } END { if (found && !bad) print sum }' <<<"$metrics")"
  waiting="$(awk '/^vllm:num_requests_waiting(\{|[[:space:]])/ { if ($NF !~ /^[0-9]+([.][0-9]+)?$/) bad=1; sum+=$NF; found=1 } END { if (found && !bad) print sum }' <<<"$metrics")"
  [[ "$running" =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1
  [[ "$waiting" =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1
  awk -v running="$running" -v waiting="$waiting" 'BEGIN { exit !((running + 0) == 0 && (waiting + 0) == 0) }'
}

stop_everything() {
  local reason="$1"
  local failed=0
  echo "autostop reason=$reason"
  if [[ -n "$LAUNCHER" ]]; then
    [[ -x "$LAUNCHER" ]] || die "VLLM_LAUNCHER is not executable: $LAUNCHER"
    timeout 45 "$LAUNCHER" stop || failed=1
  else
    echo "local_process_stop=not_configured"
  fi
  if [[ -n "$STOP_HOOK" ]]; then
    [[ "$STOP_HOOK" = /* && -x "$STOP_HOOK" ]] || die "VLLM_IDLE_STOP_HOOK must be an executable absolute path"
    timeout 30 "$STOP_HOOK" || failed=1
  else
    echo "provider_stop_hook=not_configured"
  fi
  # An HTTP acknowledgement is not proof that billing has stopped. Keep the
  # reservation active until the operator verifies provider state externally.
  if [[ -n "$SESSION_ID" ]]; then
    echo "session_closeout=provider_verification_required"
  fi
  return "$failed"
}

is_positive_int "$IDLE_TIMEOUT_SEC" || die "VLLM_IDLE_TIMEOUT_SEC must be a positive integer"
is_non_negative_int "$MAX_RUNTIME_SEC" || die "VLLM_MAX_RUNTIME_SEC must be a non-negative integer"
is_positive_int "$POLL_SEC" || die "VLLM_IDLE_POLL_SEC must be a positive integer"
command -v curl >/dev/null 2>&1 || die "missing required command: curl"
command -v awk >/dev/null 2>&1 || die "missing required command: awk"
command -v timeout >/dev/null 2>&1 || die "missing required command: timeout"
[[ -n "$LAUNCHER" || -n "$STOP_HOOK" ]] || die "configure VLLM_LAUNCHER or VLLM_IDLE_STOP_HOOK before starting the guard"
[[ -z "$LAUNCHER" || -x "$LAUNCHER" ]] || die "VLLM_LAUNCHER must be executable"
[[ -z "$STOP_HOOK" || ( "$STOP_HOOK" = /* && -x "$STOP_HOOK" ) ]] || die "VLLM_IDLE_STOP_HOOK must be an executable absolute path"
[[ -z "$SESSION_ID" ]] || (( MAX_RUNTIME_SEC > 0 )) || die "tracked GPU sessions require VLLM_MAX_RUNTIME_SEC"

started_at="$(date +%s)"
idle_since=0
once=false
if [[ "${1:-}" == "--once" ]]; then
  once=true
elif [[ -n "${1:-}" ]]; then
  die "Usage: $0 [--once]"
fi

while true; do
  now="$(date +%s)"
  if (( MAX_RUNTIME_SEC > 0 && now - started_at >= MAX_RUNTIME_SEC )); then
    $once || stop_everything "max_runtime_reached"
    exit $?
  fi

  if is_idle; then
    (( idle_since == 0 )) && idle_since="$now"
    idle_for=$((now - idle_since))
    echo "vllm_idle_seconds=$idle_for"
    if (( idle_for >= IDLE_TIMEOUT_SEC )); then
      $once && exit 0
      stop_everything "idle_timeout"
      exit $?
    fi
  else
    idle_since=0
    echo "vllm_idle_seconds=0"
  fi

  $once && exit 0
  sleep "$POLL_SEC"
done
