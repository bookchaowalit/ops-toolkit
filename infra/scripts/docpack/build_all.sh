#!/usr/bin/env bash
# Build PDFs for every registry system and discovered personal repo whose pack
# has Markdown newer than its PDFs (or no PDFs yet). usage: build_all.sh [--scope all|systems|repos]
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$D/../../.." && pwd)
SCOPE=${2:-all}; [ "${1:-}" = "--scope" ] || SCOPE=all
python3 "$ROOT/infra/scripts/utils/docpack_coverage.py" --scope "$SCOPE" --json \
  | python3 -c 'import json,sys; [print(r["id"]) for r in json.load(sys.stdin)["systems"] if r["found"] and (not r["pdf"]["built"] or r["pdf"]["stale"])]' \
  | while read -r sid; do bash "$D/make_pdfs.sh" "$sid" | tail -1; done
