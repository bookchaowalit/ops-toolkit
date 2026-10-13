#!/usr/bin/env bash
# Solo Empire toolchain preflight — Task, node, npm, python3, go
# Exit 0 if required tools look healthy; non-zero with actionable errors.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

TASK_VERSION_FILE="$ROOT/.task-version"
TASK_REQUIRED_VERSION=""
if [[ -f "$TASK_VERSION_FILE" ]]; then
  TASK_REQUIRED_VERSION="$(tr -d '[:space:]' < "$TASK_VERSION_FILE")"
fi

STRICT=0
ONLY_PYTHON=0
QUIET=0
PRODUCTION=0
DATA_LAKE=0

for arg in "$@"; do
  case "$arg" in
    --strict) STRICT=1 ;;
    --python|--python-only) ONLY_PYTHON=1 ;;
    --quiet|-q) QUIET=1 ;;
    --production) PRODUCTION=1; DATA_LAKE=1 ;;
    --data-lake) DATA_LAKE=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: bash infra/scripts/setup/toolchain-preflight.sh [options]

Options:
  --python       Only validate python3 resolution
  --strict       Treat missing optional tools (go) as failure
  --production   Validate production API, service, and worker configuration
  --data-lake    (moved to bookchaowalit/data-lake-pipelines; prints a pointer only)
  --quiet        Print only failures
  -h, --help     Show this help

Environment:
  SOLO_EMPIRE_PYTHON3   Force a python3 binary path
  SOLO_EMPIRE_TASK      Force a Task binary path
EOF
      exit 0
      ;;
  esac
done

PASS=0
FAIL=0
WARN=0

log() { [[ "$QUIET" -eq 1 ]] || echo "$*"; }
pass() { PASS=$((PASS + 1)); log "  ✅ $*"; }
fail() { FAIL=$((FAIL + 1)); echo "  ❌ $*" >&2; }
warn() { WARN=$((WARN + 1)); log "  ⚠️  $*"; }

# ── helpers ──────────────────────────────────────────────────────────

version_ge() {
  # version_ge 20.10.0 20  -> true if $1 >= $2 (major[.minor[.patch]])
  local IFS=.
  local a=($1) b=($2)
  local i
  for i in 0 1 2; do
    local ai="${a[$i]:-0}" bi="${b[$i]:-0}"
    ai="${ai%%[^0-9]*}"
    bi="${bi%%[^0-9]*}"
    [[ -z "$ai" ]] && ai=0
    [[ -z "$bi" ]] && bi=0
    if (( ai > bi )); then return 0; fi
    if (( ai < bi )); then return 1; fi
  done
  return 0
}

is_store_python() {
  local path="$1"
  case "$path" in
    *WindowsApps*|*windowsapps*) return 0 ;;
  esac
  local out
  out="$("$path" -c "import sys; print(sys.executable)" 2>&1)" || {
    if printf '%s' "$out" | grep -qi 'Microsoft Store\|App execution aliases\|Python was not found'; then
      return 0
    fi
    return 1
  }
  case "$out" in
    *WindowsApps*|*windowsapps*) return 0 ;;
  esac
  return 1
}

find_node() {
  local c
  for c in \
    "${SOLO_EMPIRE_NODE:-}" \
    "$(command -v node 2>/dev/null || true)" \
    /usr/bin/node \
    /usr/local/bin/node \
    "$HOME/.nvm/versions/node/"*/bin/node \
    "/mnt/c/Program Files/nodejs/node.exe"
  do
    [[ -n "$c" && -x "$c" ]] || continue
    # expand globs that didn't match
    [[ "$c" == *'*'* ]] && continue
    if "$c" -e "process.exit(0)" >/dev/null 2>&1; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

find_npm() {
  local c
  for c in \
    "$(command -v npm 2>/dev/null || true)" \
    /usr/bin/npm \
    /usr/local/bin/npm \
    "/mnt/c/Program Files/nodejs/npm"
  do
    [[ -n "$c" && -e "$c" ]] || continue
    printf '%s\n' "$c"
    return 0
  done
  return 1
}

find_task() {
  local c
  for c in \
    "${SOLO_EMPIRE_TASK:-}" \
    "$(command -v task 2>/dev/null || true)" \
    "$HOME/.local/bin/task" \
    "$HOME/go/bin/task" \
    /usr/local/bin/task \
    /usr/bin/task \
    /snap/bin/task
  do
    [[ -n "$c" && -x "$c" ]] || continue
    if "$c" --version >/dev/null 2>&1; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

task_version() {
  "$1" --version 2>/dev/null \
    | sed -nE 's/.*v?([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' \
    | head -n 1
}

find_python3() {
  # Prefer the shared resolver
  if [[ -x "$ROOT/infra/scripts/setup/run-python3.sh" ]]; then
    # Probe by running a tiny -c via the wrapper
    if out="$("$ROOT/infra/scripts/setup/run-python3.sh" -c 'import sys; print(sys.executable)' 2>/dev/null)"; then
      printf '%s\n' "$out"
      return 0
    fi
  fi
  local c
  for c in /usr/bin/python3 /usr/local/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
    [[ -n "$c" && -x "$c" ]] || continue
    if is_store_python "$c"; then
      continue
    fi
    if "$c" -c "import sys; assert sys.version_info >= (3, 10)" >/dev/null 2>&1; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

print_wsl_python_help() {
  cat >&2 <<'EOF'

── Microsoft Store / Windows Python alias detected ──────────────────
npm scripts that call `python3` are resolving a Windows stub, not Linux
Python. That commonly happens when npm comes from:

  /mnt/c/Program Files/nodejs/

Fix PATH on WSL (recommended):

  1. Disable Windows App Execution Aliases for python.exe / python3.exe
     Settings → Apps → Advanced app settings → App execution aliases

  2. Install Linux Python:
     sudo apt update && sudo apt install -y python3

  3. Prefer Linux Node.js inside WSL (nvm or nodesource), not Windows Node:
     nvm install 22 && nvm use 22

  4. Keep Linux bins ahead of /mnt/c in ~/.bashrc:
     export PATH="/usr/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

  5. Optional override for this repo:
     export SOLO_EMPIRE_PYTHON3=/usr/bin/python3

  Verification without npm:
     /usr/bin/python3 infra/tests/quality/smoke_test.py
     bash infra/scripts/setup/toolchain-preflight.sh

Task commands in this repo use infra/scripts/setup/run-python3.sh so
docs:check / test should work once a real /usr/bin/python3 exists.

EOF
}

# ── checks ───────────────────────────────────────────────────────────

log ""
log "Solo Empire toolchain preflight"
log "================================"
log "Repo: $ROOT"
log ""

if [[ "$ONLY_PYTHON" -eq 0 ]]; then
  log "[task]"
  if [[ -z "$TASK_REQUIRED_VERSION" ]]; then
    fail "missing .task-version (pin the repository Task version)"
  elif TASK_BIN="$(find_task)"; then
    TASK_VER="$(task_version "$TASK_BIN")"
    if [[ "$TASK_VER" == "$TASK_REQUIRED_VERSION" ]]; then
      pass "task $TASK_VER ($TASK_BIN)"
    elif [[ -z "$TASK_VER" ]]; then
      fail "task found at $TASK_BIN but --version could not be parsed"
    else
      fail "task $TASK_VER found at $TASK_BIN; require Task $TASK_REQUIRED_VERSION"
    fi
  else
    fail "task not found (install Go Task $TASK_REQUIRED_VERSION; see .task-version)"
  fi

  log "[node]"
  if NODE_BIN="$(find_node)"; then
    NODE_VER="$("$NODE_BIN" -p "process.versions.node" 2>/dev/null || true)"
    if [[ -z "$NODE_VER" ]]; then
      fail "node found at $NODE_BIN but could not read version"
    elif version_ge "$NODE_VER" "20"; then
      pass "node $NODE_VER ($NODE_BIN)"
      case "$NODE_BIN" in
        /mnt/c/*|*\.exe)
          warn "Using Windows Node from WSL. Prefer a native Linux Node install for stable npm scripts."
          ;;
      esac
    else
      fail "node $NODE_VER is too old (need >= 20). Binary: $NODE_BIN"
    fi
  else
    fail "node not found (need Node.js >= 20). Install via nvm or apt in WSL."
  fi

  log "[npm]"
  if NPM_BIN="$(find_npm)"; then
    NPM_VER="$("$NPM_BIN" -v 2>/dev/null || true)"
    if [[ -n "$NPM_VER" ]]; then
      pass "npm $NPM_VER ($NPM_BIN)"
      case "$NPM_BIN" in
        /mnt/c/*)
          warn "npm is the Windows binary. Pair with infra/scripts/setup/run-python3.sh (already wired) or install npm in WSL."
          ;;
      esac
    else
      fail "npm found at $NPM_BIN but -v failed"
    fi
  else
    fail "npm not found"
  fi
fi

log "[python3]"
# Detect broken `python3` on PATH even if we can fall back
PATH_PY="$(command -v python3 2>/dev/null || true)"
PY_BIN=""
if [[ -n "$PATH_PY" ]] && is_store_python "$PATH_PY"; then
  fail "PATH python3 is a Microsoft Store alias: $PATH_PY"
  print_wsl_python_help
elif PY_BIN="$(find_python3)"; then
  PY_VER="$("$PY_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
  pass "python3 $PY_VER ($PY_BIN)"
  if [[ -n "$PATH_PY" && "$PATH_PY" != "$PY_BIN" ]]; then
    warn "command -v python3 → $PATH_PY (wrapper will use $PY_BIN instead)"
  fi
else
  fail "No usable python3 >= 3.10 found"
  print_wsl_python_help
fi

if [[ "$ONLY_PYTHON" -eq 0 ]]; then
  log "[go]"
  GO_BIN=""
  for c in \
    "$(command -v go 2>/dev/null || true)" \
    "$HOME/go/bin/go" \
    "$HOME/.local/go/bin/go" \
    /usr/local/go/bin/go \
    /usr/bin/go
  do
    if [[ -n "$c" && -x "$c" ]]; then
      GO_BIN="$c"
      break
    fi
  done
  if [[ -n "$GO_BIN" ]]; then
    GO_VER="$("$GO_BIN" env GOVERSION 2>/dev/null || "$GO_BIN" version | awk '{print $3}')"
    pass "go $GO_VER ($GO_BIN)"
  else
    if [[ "$STRICT" -eq 1 ]]; then
      fail "go not found (required in --strict for test:go)"
    else
      warn "go not found (optional unless you run task test:go)"
    fi
  fi
fi

if [[ "$PRODUCTION" -eq 1 ]]; then
  log ""
  log "Production configuration"

  ENV_FILE="$ROOT/infra/.env"

  read_env_value() {
    local key="$1"
    local line
    if [[ -n "${!key:-}" ]]; then
      printf '%s' "${!key}"
      return 0
    fi
    [[ -f "$ENV_FILE" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" == "$key="* ]] || continue
      printf '%s' "${line#*=}"
      return 0
    done < "$ENV_FILE"
  }

  is_placeholder() {
    local value="${1:-}"
    case "$value" in
      ""|your-*|replace-*|change-me*|*-placeholder*|"<"*|...)
        return 0
        ;;
    esac
    return 1
  }

  validate_scoped_token_config() {
    local raw="${1:-}"
    [[ -n "$raw" && -n "${PY_BIN:-}" ]] || return 1
    SCOPED_TOKEN_CONFIG="$raw" "$PY_BIN" - <<'PY'
import json
import os
import re
from datetime import datetime

value = os.environ.get("SCOPED_TOKEN_CONFIG", "")
try:
    entries = json.loads(value)
except (TypeError, json.JSONDecodeError):
    raise SystemExit(1)

if not isinstance(entries, list) or not entries or len(entries) > 100:
    raise SystemExit(1)

subject_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
hash_pattern = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)
scope_pattern = re.compile(r"^[a-z][a-z0-9:_-]{1,63}$")

for entry in entries:
    if not isinstance(entry, dict) or "token" in entry or "secret" in entry:
        raise SystemExit(1)
    subject = entry.get("subject")
    token_hash = entry.get("token_hash")
    scopes = entry.get("scopes")
    expires_at = entry.get("expires_at")
    if not isinstance(subject, str) or not subject_pattern.fullmatch(subject.strip()):
        raise SystemExit(1)
    if not isinstance(token_hash, str) or not hash_pattern.fullmatch(token_hash.strip()):
        raise SystemExit(1)
    if not isinstance(scopes, list) or not (1 <= len(scopes) <= 20):
        raise SystemExit(1)
    if any(not isinstance(scope, str) or not scope_pattern.fullmatch(scope.strip()) for scope in scopes):
        raise SystemExit(1)
    if expires_at is not None:
        if not isinstance(expires_at, str) or not expires_at.strip():
            raise SystemExit(1)
        try:
            datetime.fromisoformat(expires_at.strip().replace("Z", "+00:00"))
        except ValueError:
            raise SystemExit(1)
PY
  }

  if [[ ! -f "$ENV_FILE" ]]; then
    fail "production requires infra/.env or environment-injected secrets"
  else
    pass "infra/.env exists (values not displayed)"
  fi

  API_TOKEN_VALUE="$(read_env_value API_TOKEN)"
  SCOPED_TOKEN_VALUE="$(read_env_value API_TOKENS_JSON)"
  LEGACY_TOKEN_VALID=0
  SCOPED_TOKEN_VALID=0
  if ! is_placeholder "$API_TOKEN_VALUE" && [[ "${#API_TOKEN_VALUE}" -ge 32 ]]; then
    LEGACY_TOKEN_VALID=1
  fi
  if [[ -n "$SCOPED_TOKEN_VALUE" ]] && validate_scoped_token_config "$SCOPED_TOKEN_VALUE"; then
    SCOPED_TOKEN_VALID=1
    pass "API_TOKENS_JSON is valid hash-only scoped configuration"
  elif [[ -n "$SCOPED_TOKEN_VALUE" ]]; then
    fail "API_TOKENS_JSON is invalid; require hash-only subjects, scopes, and optional ISO expiry"
  fi
  if [[ "$SCOPED_TOKEN_VALID" -eq 1 ]]; then
    : # The scoped registry is the preferred production authentication boundary.
  elif [[ "$LEGACY_TOKEN_VALID" -eq 1 ]]; then
    pass "API_TOKEN is configured with production length (legacy compatibility)"
  else
    fail "API_TOKENS_JSON or API_TOKEN must contain a production authentication configuration"
  fi

  if [[ "$(read_env_value ALLOW_UNAUTH_DEV)" == "true" ]]; then
    fail "ALLOW_UNAUTH_DEV=true is not allowed in production"
  else
    pass "Unauthenticated write mode is disabled"
  fi

  FINANCE_RATE_LIMIT_ENABLED="$(read_env_value FINANCE_API_RATE_LIMIT_ENABLED)"
  if [[ -z "$FINANCE_RATE_LIMIT_ENABLED" || "$FINANCE_RATE_LIMIT_ENABLED" == "true" ]]; then
    pass "Finance API rate limiting is enabled (local guard; use a distributed edge for production)"
  else
    fail "FINANCE_API_RATE_LIMIT_ENABLED must not be false in production"
  fi

  CORS_VALUE="$(read_env_value CORS_ORIGINS)"
  if [[ -z "$CORS_VALUE" || "$CORS_VALUE" == *'*'* ]]; then
    fail "CORS_ORIGINS must be an explicit production allowlist"
  elif [[ "$CORS_VALUE" == *"localhost"* ]]; then
    warn "CORS_ORIGINS includes localhost; verify this is intentional"
  else
    pass "CORS_ORIGINS is not wildcard"
  fi
fi

if [[ "$DATA_LAKE" -eq 1 ]]; then
  log ""
  log "Data Lake runtime"
  warn "The data-lake runtime moved to bookchaowalit/data-lake-pipelines; run its preflight there"
fi

log ""
log "Summary: $PASS passed, $WARN warning(s), $FAIL failed"
if [[ "$FAIL" -gt 0 ]]; then
  log "Preflight FAILED"
  exit 1
fi
log "Preflight OK"
exit 0
