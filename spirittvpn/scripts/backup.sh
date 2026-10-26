#!/bin/sh
# Periodic pg_dump with retention. Restore: pg_restore -h HOST -U USER -d DB --clean FILE.dump
set -eu
mkdir -p /backups
while true; do
  file="/backups/spirittvpn_$(date -u +%Y%m%d_%H%M%S).dump"
  if pg_dump --format=custom --file="$file.tmp"; then
    mv "$file.tmp" "$file"
    echo "backup ok: $file"
  else
    rm -f "$file.tmp"
    echo "backup FAILED" >&2
  fi
  find /backups -name 'spirittvpn_*.dump' -mtime "+${BACKUP_RETENTION_DAYS:-14}" -delete
  sleep "${BACKUP_INTERVAL_SECONDS:-86400}"
done
