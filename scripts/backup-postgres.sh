#!/bin/sh
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="${BACKUP_DIR}/nse-dashboard-${timestamp}.dump"

mkdir -p "$BACKUP_DIR"
pg_dump --format=custom --no-owner --no-privileges --file="$target" "$DATABASE_URL"
find "$BACKUP_DIR" -type f -name 'nse-dashboard-*.dump' -mtime "+$BACKUP_RETENTION_DAYS" -delete
echo "Backup written to $target"
