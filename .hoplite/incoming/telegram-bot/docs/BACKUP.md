# Резервное копирование и восстановление

## Создание копии

**Linux:**

```bash
./scripts/backup_db.sh                # создаёт backups/spiritt_YYYYmmdd_HHMMSS.dump (формат pg_dump -Fc)
```

Ежедневно через cron (`crontab -e` от пользователя `spiritt`):

```
15 3 * * * cd /opt/spirittvpn/SpirittVPN && ./scripts/backup_db.sh >> backups/backup.log 2>&1
```

Скрипт удаляет копии старше 14 дней (`KEEP_DAYS`). Храните копии **вне сервера**: скачивайте их или отправляйте в своё объектное хранилище. В дампах персональные данные пользователей, поэтому храните их зашифрованными и никому не передавайте.

**Windows:** `scripts\backup_db.bat`. Нужен `pg_dump` в `PATH`; он ставится вместе с PostgreSQL, обычно в `C:\Program Files\PostgreSQL\16\bin`.

## Восстановление

```bash
sudo systemctl stop spirittvpn-bot spirittvpn-api
./scripts/restore_db.sh backups/spiritt_20261027_031500.dump   # скрипт попросит подтверждение — БД будет перезаписана
.venv/bin/alembic upgrade head
sudo systemctl start spirittvpn-api spirittvpn-bot
```

После восстановления воркер сверит устройства с нодами (`reconcile`). Ключ, который есть в БД, но пропал с ноды, при активной подписке будет пересоздан.

**Важно:** отозванные после даты копии ключи сверка не удалит повторно. Проверьте раздел «VPN-инфраструктура → Журнал операций».
