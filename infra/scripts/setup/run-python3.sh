#!/usr/bin/env bash
# Resolve a real Linux python3 (never Microsoft Store aliases) and exec the args.
# Used by npm scripts so Windows-hosted npm still runs WSL Python correctly.
set -euo pipefail
export PYTHONUTF8="${PYTHONUTF8:-1}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

is_store_alias() {
  local path="$1"
  case "$path" in
    *WindowsApps*|*windowsapps*) return 0 ;;
  esac
  # Store stub prints this message and fails meaningfully
  local out
  out="$("$path" -c "import sys; print(sys.executable)" 2>&1)" || {
    if printf '%s' "$out" | grep -qi 'Microsoft Store\|App execution aliases'; then
      return 0
    fi
    return 1
  }
  case "$out" in
    *WindowsApps*|*windowsapps*) return 0 ;;
  esac
  return 1
}

find_python3() {
  local candidates=()
  local repo_root
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
  # Prefer the repository-managed virtualenv so data-lake dependencies are
  # available consistently to npm entrypoints and scheduled jobs.
  candidates+=("$repo_root/.venv/bin/python")
  candidates+=("$repo_root/.venv/Scripts/python.exe")
  # Prefer known Linux locations first
  candidates+=("/usr/bin/python3" "/usr/local/bin/python3")
  if command -v python3 >/dev/null 2>&1; then
    candidates+=("$(command -v python3)")
  fi
  if [[ -n "${SOLO_EMPIRE_PYTHON3:-}" ]]; then
    candidates=("${SOLO_EMPIRE_PYTHON3}" "${candidates[@]}")
  fi

  local c real
  for c in "${candidates[@]}"; do
    [[ -n "$c" && -x "$c" ]] || continue
    real="$(readlink -f "$c" 2>/dev/null || echo "$c")"
    if is_store_alias "$c" || is_store_alias "$real"; then
      continue
    fi
    # Must actually import stdlib
    if "$c" -c "import sys; assert sys.version_info >= (3, 10)" >/dev/null 2>&1; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  return 1
}

print_python_help() {
  cat >&2 <<'EOF'

ERROR: Could not find a real python3 (3.10+) for Solo Empire.

If you see "Python was not found ... Microsoft Store", npm is resolving the
Windows App Execution Alias instead of Linux python3.

Fix (WSL recommended):
  1. Disable Windows aliases:
     Settings → Apps → Advanced app settings → App execution aliases
     Turn OFF "python.exe" and "python3.exe"
  2. Install/use Linux Python in WSL:
     sudo apt update && sudo apt install -y python3
  3. Prefer Linux Node/npm inside WSL (not /mnt/c/Program Files/nodejs):
     # example with nvm
     curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
     nvm install 22
  4. Put Linux bins before Windows PATH entries in ~/.bashrc:
     export PATH="/usr/local/bin:/usr/bin:$HOME/.local/bin:$PATH"
  5. Or force an interpreter for this repo:
     export SOLO_EMPIRE_PYTHON3=/usr/bin/python3

Bypass npm scripts when debugging:
  /usr/bin/python3 infra/tests/quality/smoke_test.py
  /usr/bin/python3 infra/scripts/utils/doc_number_guard.py

EOF
}

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <script-or-args...>" >&2
  exit 2
fi

PY="$(find_python3)" || {
  print_python_help
  exit 1
}

exec "$PY" "$@"
