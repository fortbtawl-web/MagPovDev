#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:?Укажите файл: scripts/restore_db.sh backups/xxx.dump}"
URL=$(grep -E '^DATABASE_URL=' .env | cut -d= -f2- | sed 's#postgresql+asyncpg://#postgresql://#')
read -r -p "Текущие данные БД будут ЗАМЕНЕНЫ содержимым $FILE. Продолжить? (yes/no) " ans
[ "$ans" = "yes" ] || { echo "Отменено"; exit 1; }
pg_restore --clean --if-exists --no-owner --dbname="$URL" "$FILE"
echo "Восстановлено. Выполните: .venv/bin/alembic upgrade head"
