#!/usr/bin/env bash
# Build a system docpack's PDFs: markdown -> print HTML -> PDF (headless Chromium).
# usage: make_pdfs.sh <system-id> [file-glob]   (default glob: [0-9][0-9]-*.md)
# <system-id> is a registry id or a discovered personal repo path (relative to a
# repo_discovery root); both resolve through registries/docpack-coverage.yaml in SOLO_REGISTRY_DIR.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$D/../../.." && pwd)
SYS=${1:?usage: make_pdfs.sh <system-id> [file-glob]}
read -r PACK BUILDER < <(python3 - "$ROOT" "$SYS" <<'PY'
import os, sys, yaml, pathlib
root, sid = pathlib.Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(root / "infra" / "scripts" / "utils"))
from _env import repo_file  # maps legacy repository-relative paths to the owning clone
registry_dir = os.environ.get("SOLO_REGISTRY_DIR")
if not registry_dir:
    sys.exit("SOLO_REGISTRY_DIR is not set (point it at the repository-catalog folder of the registry repository)")
reg = yaml.safe_load((pathlib.Path(registry_dir) / "registries/docpack-coverage.yaml").read_text(encoding="utf-8"))
for s in reg["systems"]:
    if s["id"] == sid:
        print(repo_file(s["pack"]), s.get("pdf_builder", "standard")); break
else:
    for base in (reg.get("repo_discovery") or {}).get("roots", []):
        repo = repo_file(base) / sid
        if (repo / ".git").is_dir():
            print(repo / "DOCPACK", "standard"); break
    else:
        sys.exit(f"unknown system id: {sid}")
PY
)
if [ "$BUILDER" = "legacy" ]; then
  echo "$SYS uses its own client builder (pdf_builder: legacy); see the pack's automation folder." >&2
  exit 0
fi
FD=$(cd "${FONT_DIR:-$ROOT/.runtime/fonts}" && pwd)
[ -f "$FD/Sarabun-Regular.ttf" ] || { echo "Sarabun fonts missing in $FD (set FONT_DIR)" >&2; exit 2; }
mkdir -p "$PACK/pdf"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
n=0
for md in "$PACK"/${2:-[0-9][0-9]-*.md}; do
  [ -f "$md" ] || continue
  id=$(sed -n '1,15s/^doc_id: *//p' "$md" | head -1)
  [ -n "$id" ] || { echo "skip $(basename "$md") (no doc_id)"; continue; }
  ver=$(sed -n '1,15s/^version: *v\{0,1\}//p' "$md" | head -1 | awk '{print $1}')
  name=$(basename "$md" .md); name=${name#[0-9][0-9]-}
  html="$TMP/$id.html"
  uv run --quiet --no-project --with markdown --with pyyaml python "$D/build_html.py" "$md" "$html" "$FD" >/dev/null
  rm -f "$PACK/pdf/${id}_"*.pdf
  out="$PACK/pdf/${id}_${name}_v${ver:-0.1}.pdf"
  "$D/chrome.sh" --no-pdf-header-footer --print-to-pdf="$out" "file://$html" >/dev/null 2>&1
  [ -s "$out" ] || { echo "FAILED: $out" >&2; exit 1; }
  echo "pdf: $out"; n=$((n+1))
done
echo "$SYS: $n PDF(s) built"
