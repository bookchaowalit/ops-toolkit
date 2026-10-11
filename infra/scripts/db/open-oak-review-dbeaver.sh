#!/usr/bin/env bash
set -euo pipefail

# Prepare local, isolated PostgreSQL review databases from Oak delivery dumps
# and open them in DBeaver. This script never writes credentials to the repo.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
GITHUB_ROOT="${SOLO_BOOK_DEV_GITHUB_ROOT:-$REPO_ROOT/../../..}"

DOCKER_BIN="${DOCKER_BIN:-docker}"
DOCKER_SOCKET="${DOCKER_SOCKET:-unix:///var/run/docker.sock}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-scannpop-backend-db-1}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DBEAVER_BIN="${DBEAVER_BIN:-dbeaver}"

TARGET="all"
REFRESH=0
OPEN=1
SOURCE_OVERRIDE=""

usage() {
  cat <<'EOF'
Usage:
  bash infra/scripts/db/open-oak-review-dbeaver.sh [target] [options]

Targets:
  games       Oak / Aura Nua Games review database
  wellness    Oak / LiveWell Wellness review database
  inful       Oak / Inful review database (requires an SQL dump)
  all         Prepare and open every available target (default)

Options:
  --refresh       Recreate only the named oak_*_review database(s) from dump
  --no-open       Prepare/verify the database without launching DBeaver
  --source PATH   Override the SQL dump path (single target only)
  -h, --help      Show this help

Examples:
  task db:oak-review -- games
  task db:oak-review -- wellness --refresh
  task db:oak-review -- inful --source /path/to/full_database.sql
EOF
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

while (($# > 0)); do
  case "$1" in
    games|wellness|inful|all)
      TARGET="$1"
      shift
      ;;
    --refresh)
      REFRESH=1
      shift
      ;;
    --no-open)
      OPEN=0
      shift
      ;;
    --source)
      (($# >= 2)) || fail "--source requires a path"
      SOURCE_OVERRIDE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1 (use --help)"
      ;;
  esac
done

if [[ -n "$SOURCE_OVERRIDE" && "$TARGET" == "all" ]]; then
  fail "--source can only be used with games, wellness, or inful"
fi

if ! command -v "$DOCKER_BIN" >/dev/null 2>&1; then
  fail "Docker CLI not found: $DOCKER_BIN"
fi

if ! command -v "$DBEAVER_BIN" >/dev/null 2>&1 && ((OPEN == 1)); then
  fail "DBeaver CLI not found: $DBEAVER_BIN (use --no-open to prepare only)"
fi

docker_cmd() {
  "$DOCKER_BIN" -H "$DOCKER_SOCKET" "$@"
}

CONTAINER_ID="$(docker_cmd ps -q --filter "name=${POSTGRES_CONTAINER}" | head -n 1)"
[[ -n "$CONTAINER_ID" ]] || fail "PostgreSQL container is not running: $POSTGRES_CONTAINER"

container_env_value() {
  local key="$1"
  docker_cmd inspect "$CONTAINER_ID" --format '{{range .Config.Env}}{{println .}}{{end}}' |
    awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }'
}

PGUSER="$(container_env_value POSTGRES_USER)"
PGPASSWORD="$(container_env_value POSTGRES_PASSWORD)"
[[ -n "$PGUSER" ]] || fail "POSTGRES_USER is missing from $POSTGRES_CONTAINER"
[[ -n "$PGPASSWORD" ]] || fail "POSTGRES_PASSWORD is missing from $POSTGRES_CONTAINER"

psql_exec() {
  docker_cmd exec -i "$CONTAINER_ID" sh -c \
    'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$@"' \
    oak-review-psql "$@"
}

database_exists() {
  local db_name="$1"
  [[ "$(psql_exec -d postgres -Atc "select 1 from pg_database where datname='$db_name'")" == "1" ]]
}

database_is_review_name() {
  [[ "$1" =~ ^oak_(games|wellness|inful)_review$ ]]
}

recreate_database() {
  local db_name="$1"
  database_is_review_name "$db_name" || fail "refusing to recreate unexpected database: $db_name"
  psql_exec -d postgres -c "drop database if exists \"$db_name\" with (force)"
  psql_exec -d postgres -c "create database \"$db_name\""
}

ensure_database() {
  local db_name="$1"
  if database_exists "$db_name"; then
    if ((REFRESH == 1)); then
      printf 'Recreating isolated review database: %s\n' "$db_name"
      recreate_database "$db_name"
    else
      printf 'Keeping existing review database: %s (use --refresh to reload)\n' "$db_name"
    fi
  else
    printf 'Creating isolated review database: %s\n' "$db_name"
    psql_exec -d postgres -c "create database \"$db_name\""
  fi
}

dump_filter() {
  # PostgreSQL 17 dumps contain metadata commands that PostgreSQL 16 does not
  # understand. These lines are not schema/data and are safe to omit for a
  # local visualization copy. CRLF is normalized for UTF-16LE dumps as well.
  sed -E 's/\r$//; /^\\restrict /d; /^\\unrestrict/d; /^SET transaction_timeout = 0;$/d; /^CREATE SCHEMA public;$/d'
}

inful_review_filter() {
  # The Inful delivery is a Supabase export. Its managed auth/storage data and
  # Supabase-only extensions cannot be restored into the generic local
  # PostgreSQL container used for DBeaver review. Keep the application-owned
  # public schema/data and omit only those managed objects.
  dump_filter |
    awk '
      /^-- Data for Name: .*; Schema: (auth|storage);/ { managed=1; next }
      /^-- Data for Name: / { managed=0 }
      /^-- Name: / { managed=0 }
      managed { next }
      { print }
    ' |
    sed -E '/^CREATE EXTENSION IF NOT EXISTS "(pg_stat_statements|pgcrypto|supabase_vault|uuid-ossp)" WITH SCHEMA "(extensions|vault)";$/d; /^ALTER PUBLICATION "supabase_realtime"/d; /^INSERT INTO "(auth|storage)"\./d; /^SELECT pg_catalog\.setval\('\''"auth"/d; /^GRANT .* TO "(anon|authenticated|service_role)";$/d; /^ALTER DEFAULT PRIVILEGES .* TO "(anon|authenticated|service_role)";$/d'
}

target_config() {
  case "$1" in
    games)
      TARGET_LABEL="Oak Games Review"
      TARGET_DB="oak_games_review"
      TARGET_DUMP="${GITHUB_ROOT}/bookchaowalit-freelance/oak/games/01-round-1/00-incoming/06-database-dump/aura_nua.sql"
      TARGET_ENCODING="UTF-16LE"
      ;;
    wellness)
      TARGET_LABEL="Oak Wellness Review"
      TARGET_DB="oak_wellness_review"
      TARGET_DUMP="${GITHUB_ROOT}/bookchaowalit-freelance/oak/wellness/02-follow-up/00-incoming/06-database-dump/livewell_dump.sql"
      TARGET_ENCODING="UTF-8"
      ;;
    inful)
      TARGET_LABEL="Oak Inful Review"
      TARGET_DB="oak_inful_review"
      TARGET_DUMP="${GITHUB_ROOT}/bookchaowalit-freelance/oak/inful/01-round-1/00-incoming/06-database-dump/full_database.sql"
      TARGET_ENCODING="UTF-8"
      ;;
    *)
      fail "unsupported target: $1"
      ;;
  esac

  if [[ -n "$SOURCE_OVERRIDE" ]]; then
    TARGET_DUMP="$(cd "$(dirname "$SOURCE_OVERRIDE")" && pwd)/$(basename "$SOURCE_OVERRIDE")"
  fi
}

import_dump() {
  local db_name="$1"
  local dump_path="$2"
  local encoding="$3"
  local target="$4"

  [[ -f "$dump_path" ]] || fail "SQL dump not found: $dump_path"
  printf 'Importing %s into %s...\n' "$dump_path" "$db_name"

  if [[ "$encoding" == "UTF-16LE" ]]; then
    source_stream=(iconv -f UTF-16LE -t UTF-8 "$dump_path")
  else
    source_stream=(sed -e 's/$//' "$dump_path")
  fi

  if [[ "$target" == "inful" ]]; then
    "${source_stream[@]}" | inful_review_filter | docker_cmd exec -i "$CONTAINER_ID" sh -c \
      'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1"' \
      oak-review-import "$db_name" >/dev/null
  else
    "${source_stream[@]}" | dump_filter | docker_cmd exec -i "$CONTAINER_ID" sh -c \
      'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1"' \
      oak-review-import "$db_name" >/dev/null
  fi
}

verify_database() {
  local db_name="$1"
  local tables
  tables="$(psql_exec -d "$db_name" -Atc "select count(*) from pg_tables where schemaname='public'")"
  printf 'Verified %s: %s public tables\n' "$db_name" "$tables"
}

open_dbeaver() {
  local label="$1"
  local db_name="$2"
  local spec="driver=postgresql|host=${DB_HOST}|port=${DB_PORT}|database=${db_name}|user=\${PGUSER}|password=\${PGPASSWORD}|name=${label}"

  # Credentials are passed through environment-variable expansion. The
  # password is never placed in the command-line spec or repository files.
  PGUSER="$PGUSER" PGPASSWORD="$PGPASSWORD" "$DBEAVER_BIN" -con "$spec" \
    >/dev/null 2>&1 &
  printf 'Opened DBeaver connection: %s (%s:%s/%s)\n' "$label" "$DB_HOST" "$DB_PORT" "$db_name"
}

prepare_target() {
  local target="$1"
  target_config "$target"

  if [[ ! -f "$TARGET_DUMP" ]]; then
    printf 'SKIP %s: SQL dump not found at %s\n' "$target" "$TARGET_DUMP" >&2
    return 2
  fi

  if ! ensure_database "$TARGET_DB"; then
    printf 'FAILED %s: could not prepare database %s\n' "$target" "$TARGET_DB" >&2
    return 1
  fi
  local existing_tables
  if ! existing_tables="$(psql_exec -d "$TARGET_DB" -Atc "select count(*) from pg_tables where schemaname='public'")"; then
    printf 'FAILED %s: could not inspect %s\n' "$target" "$TARGET_DB" >&2
    return 1
  fi
  if ((REFRESH == 1)) || [[ "$existing_tables" == "0" ]]; then
    if ! import_dump "$TARGET_DB" "$TARGET_DUMP" "$TARGET_ENCODING" "$target"; then
      printf 'FAILED %s: import failed for %s\n' "$target" "$TARGET_DB" >&2
      return 1
    fi
  else
    printf 'Skipping import for %s because the review database already has data\n' "$TARGET_DB"
  fi
  if ! verify_database "$TARGET_DB"; then
    printf 'FAILED %s: verification failed for %s\n' "$target" "$TARGET_DB" >&2
    return 1
  fi

  if ((OPEN == 1)); then
    open_dbeaver "$TARGET_LABEL" "$TARGET_DB"
  fi
  return 0
}

targets=("$TARGET")
if [[ "$TARGET" == "all" ]]; then
  targets=(games wellness inful)
fi

failures=0
for target in "${targets[@]}"; do
  if ! prepare_target "$target"; then
    failures=$((failures + 1))
  fi
done

if ((failures > 0)); then
  printf '%s target(s) were unavailable; existing targets were prepared.\n' "$failures" >&2
  exit 2
fi

printf 'Oak review database preparation complete.\n'
