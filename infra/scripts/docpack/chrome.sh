#!/usr/bin/env bash
# Headless Chromium wrapper (Playwright headless shell + optional extracted GTK runtime libs).
set -euo pipefail
CH=$(ls "$HOME"/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell 2>/dev/null | tail -1)
[ -n "$CH" ] || { echo "chrome-headless-shell not found; run: npx playwright install chromium-headless-shell" >&2; exit 2; }
LIBS="${CHROME_RUNTIME_LIBS:-/tmp/solo-ai-usage-browser-runtime/root/usr/lib/x86_64-linux-gnu}"
LD_LIBRARY_PATH="$LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" exec "$CH" --no-sandbox --disable-gpu --hide-scrollbars --virtual-time-budget="${VT:-20000}" "$@"
