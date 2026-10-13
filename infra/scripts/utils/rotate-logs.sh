#!/bin/bash
# Manual log rotation for residual scraping logs.
# Prefer external project clones for new scrapers (see the registry repository).
# Run daily via cron if residual logs still accumulate.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
GITHUB_ROOT="${SOLO_BOOK_DEV_GITHUB_ROOT:-$REPO_ROOT/../../..}"
DATE=$(date +%Y%m%d)

# Canonical scraping-family project paths.
CANDIDATES=(
  "$GITHUB_ROOT/bookchaowalit/public/book-apps/tools/career-job-scraper/data/logs"
  "$GITHUB_ROOT/bookchaowalit/public/book-apps/tools/commerce-product-scraper/data/logs"
  "$GITHUB_ROOT/bookchaowalit/public/book-apps/tools/content-news-scraper/data/logs"
  "$GITHUB_ROOT/bookchaowalit/public/book-apps/tools/realestate-property-scraper/data/logs"
  "$GITHUB_ROOT/bookchaowalit/public/book-apps/tools/food-restaurant-scraper/data/logs"
  "$GITHUB_ROOT/bookchaowalit/public/book-apps/tools/sales-business-directory-scraper/data/logs"
  "$GITHUB_ROOT/bookchaowalit/public/book-apps/tools/opportunity-listing-scraper/data/logs"
)

LOG_DIR=""
for candidate in "${CANDIDATES[@]}"; do
  if [ -d "$candidate" ]; then
    LOG_DIR="$candidate"
    break
  fi
done

if [ -z "$LOG_DIR" ]; then
  echo "[$(date)] No scraping log directory found under book-dev (canonical or residual). Nothing to rotate."
  exit 0
fi

cd "$LOG_DIR" || exit 1

echo "[$(date)] Starting log rotation in $LOG_DIR ..."

rotate_one() {
  local name="$1"
  if [ -f "$name" ] && [ -s "$name" ]; then
    for i in 7 6 5 4 3 2 1; do
      prev=$((i - 1))
      if [ -f "${name}.${prev}" ]; then
        mv "${name}.${prev}" "${name}.${i}"
      fi
    done

    for i in 1 2 3 4 5 6 7; do
      if [ -f "${name}.${i}" ] && [ ! -f "${name}.${i}.gz" ]; then
        gzip "${name}.${i}"
      fi
    done

    cp "$name" "${name}.0"
    gzip "${name}.0"
    : > "$name"
    echo "  ✓ Rotated $name"
  fi
}

rotate_one cron.log
rotate_one scrape_main.log

find . -name "*.gz" -mtime +7 -delete 2>/dev/null || true

echo "[$(date)] Log rotation completed"
ls -lh ./*.log ./*.gz 2>/dev/null | tail -10 || true
