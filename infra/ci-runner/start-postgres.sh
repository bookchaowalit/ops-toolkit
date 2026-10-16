#!/usr/bin/env bash
# Start the runner's local PostgreSQL 17 (idempotent) and wait until it accepts connections.
set -euo pipefail
pg_ctlcluster 17 main status >/dev/null 2>&1 || pg_ctlcluster 17 main start
for _ in $(seq 1 30); do
  pg_isready -h 127.0.0.1 -p 5432 -U postgres >/dev/null 2>&1 && exit 0
  sleep 1
done
echo "PostgreSQL did not become ready" >&2
exit 1
