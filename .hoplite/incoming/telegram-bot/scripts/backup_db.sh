#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
URL=$(grep -E '^DATABASE_URL=' .env | cut -d= -f2- | sed 's#postgresql+asyncpg://#postgresql://#')
mkdir -p backups && chmod 700 backups
OUT="backups/spiritt_$(date -u +%Y%m%d_%H%M%S).dump"
pg_dump --format=custom --no-owner --file="$OUT" "$URL"
chmod 600 "$OUT"
find backups -name 'spiritt_*.dump' -mtime +"${KEEP_DAYS:-14}" -delete
echo "Резервная копия: $OUT"
