#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL}"
: "${BACKUP_AGE_RECIPIENT:?set BACKUP_AGE_RECIPIENT}"

BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

case "$BACKUP_RETENTION_DAYS" in
  ''|*[!0-9]*)
    echo "BACKUP_RETENTION_DAYS must be a non-negative integer" >&2
    exit 1
    ;;
esac

for command_name in pg_dump age; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "missing required command: $command_name" >&2
    exit 1
  fi
done

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1"
  else
    echo "missing required command: sha256sum or shasum" >&2
    return 1
  fi
}

umask 077
mkdir -p "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
base_name="crm-postgres-${timestamp}.dump.age"
encrypted_file="${BACKUP_DIR}/${base_name}"
encrypted_tmp="${encrypted_file}.tmp"
dump_file="$(mktemp "${BACKUP_DIR}/.crm-postgres-${timestamp}.XXXXXX.dump")"

cleanup() {
  rm -f "$dump_file" "$encrypted_tmp"
}
trap cleanup EXIT

pg_dump --format=custom --no-owner --no-privileges \
  --file="$dump_file" "$DATABASE_URL"
age --recipient "$BACKUP_AGE_RECIPIENT" \
  --output "$encrypted_tmp" "$dump_file"
mv "$encrypted_tmp" "$encrypted_file"

(
  cd "$BACKUP_DIR"
  sha256_file "$base_name" > "${base_name}.sha256"
)

find "$BACKUP_DIR" -type f \
  \( -name 'crm-postgres-*.dump.age' -o -name 'crm-postgres-*.dump.age.sha256' \) \
  -mtime "+${BACKUP_RETENTION_DAYS}" -delete

echo "backup created: $encrypted_file"
echo "checksum: ${encrypted_file}.sha256"
