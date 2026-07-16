#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL}"
: "${BACKUP_AGE_IDENTITY:?set BACKUP_AGE_IDENTITY}"

BACKUP_FILE="${1:-}"
if [[ -z "$BACKUP_FILE" || ! -f "$BACKUP_FILE" ]]; then
  echo "usage: $0 /path/to/crm-postgres-TIMESTAMP.dump.age" >&2
  exit 1
fi

for command_name in age pg_restore psql python3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "missing required command: $command_name" >&2
    exit 1
  fi
done

verify_checksum() {
  local checksum_file="${BACKUP_FILE}.sha256"
  if [[ ! -f "$checksum_file" ]]; then
    echo "missing checksum file: $checksum_file" >&2
    return 1
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$(dirname "$BACKUP_FILE")" && sha256sum -c "$(basename "$checksum_file")")
  elif command -v shasum >/dev/null 2>&1; then
    (cd "$(dirname "$BACKUP_FILE")" && shasum -a 256 -c "$(basename "$checksum_file")")
  else
    echo "missing required command: sha256sum or shasum" >&2
    return 1
  fi
}

verify_checksum

umask 077
restore_database="crm_restore_$(date -u +%Y%m%d%H%M%S)_$$"
dump_file="$(mktemp "${TMPDIR:-/tmp}/crm-restore.XXXXXX.dump")"
restore_database_url="$({
  python3 - "$DATABASE_URL" "$restore_database" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit

parts = urlsplit(sys.argv[1])
print(urlunsplit((parts.scheme, parts.netloc, "/" + sys.argv[2], parts.query, parts.fragment)))
PY
})"

cleanup() {
  rm -f "$dump_file"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${restore_database}' AND pid <> pg_backend_pid();" \
    >/dev/null 2>&1 || true
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c \
    "DROP DATABASE IF EXISTS \"${restore_database}\";" >/dev/null 2>&1 || true
}
trap cleanup EXIT

age --decrypt --identity "$BACKUP_AGE_IDENTITY" \
  --output "$dump_file" "$BACKUP_FILE"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c \
  "CREATE DATABASE \"${restore_database}\";" >/dev/null
pg_restore --exit-on-error --no-owner --no-privileges \
  --dbname="$restore_database_url" "$dump_file"

required_tables=(
  schema_migrations
  barcode_reports
  app_entities
  operation_logs
  sync_events
  sync_cursors
  sync_tombstones
)

echo "restore verification row counts:"
for table in "${required_tables[@]}"; do
  exists="$(psql "$restore_database_url" -At -v ON_ERROR_STOP=1 -c \
    "SELECT to_regclass('public.${table}') IS NOT NULL;")"
  if [[ "$exists" != "t" ]]; then
    echo "missing required table: $table" >&2
    exit 1
  fi
  count="$(psql "$restore_database_url" -At -v ON_ERROR_STOP=1 -c \
    "SELECT count(*) FROM \"${table}\";")"
  if [[ ! "$count" =~ ^[0-9]+$ ]]; then
    echo "invalid row count for $table: $count" >&2
    exit 1
  fi
  printf '  %s: %s\n' "$table" "$count"
done

echo "restore verification passed"
