# Развёртывание на Linux-сервере (Ubuntu 22.04/24.04)

Компьютер владельца для работы бота не нужен: всё работает на VPS. Подойдёт любой VPS с 1 ГБ RAM, например один из серверов H1Cloud **отдельно от VPN-нод** или новый. Покупать что-либо — решение владельца.

## 1. Пакеты

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv postgresql nginx git curl
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
sudo npm i -g pnpm
```

## 2. PostgreSQL

```bash
sudo -u postgres psql -c "CREATE ROLE spiritt LOGIN PASSWORD 'СЛОЖНЫЙ_ПАРОЛЬ';"
sudo -u postgres psql -c "CREATE DATABASE spiritt OWNER spiritt;"
```

## 3. Код и зависимости

```bash
sudo useradd -r -m -d /opt/spirittvpn spiritt
sudo -u spiritt -H bash -c '
cd /opt/spirittvpn && unzip /tmp/SpirittVPN.zip -d . && cd SpirittVPN
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
cd frontend && pnpm install && pnpm build
'
sudo -u spiritt nano /opt/spirittvpn/SpirittVPN/.env   # заполнить переменные
sudo -u spiritt -H bash -c 'cd /opt/spirittvpn/SpirittVPN && .venv/bin/alembic upgrade head'
```

## 4. Сервисы systemd

```bash
sudo cp deploy/spirittvpn-api.service deploy/spirittvpn-bot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now spirittvpn-api spirittvpn-bot
journalctl -u spirittvpn-api -f
```

Процессы останавливаются корректно по `SIGTERM`: polling бота завершается, воркер доделывает текущий цикл, соединения с БД закрываются.

## 5. HTTPS (обязателен для Mini App и webhook)

Нужен домен с A-записью на IP сервера.

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/spirittvpn
sudo sed -i 's/vpn.example.com/ВАШ_ДОМЕН/g' /etc/nginx/sites-available/spirittvpn
sudo ln -s /etc/nginx/sites-available/spirittvpn /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx && sudo certbot --nginx -d ВАШ_ДОМЕН
```

В `.env` задайте `PUBLIC_BASE_URL=https://ВАШ_ДОМЕН`, `WEBAPP_URL=https://ВАШ_ДОМЕН` и `TRUST_FORWARDED_FOR=true`, затем перезапустите оба сервиса.

## 6. Docker (альтернатива)

```bash
cp .env.example .env   # заполнить; DATABASE_URL для compose: postgresql+asyncpg://spiritt:${POSTGRES_PASSWORD}@db:5432/spiritt
POSTGRES_PASSWORD=СЛОЖНЫЙ_ПАРОЛЬ docker compose up -d --build
```

Миграции применяются автоматически при старте контейнера `api`.

## 7. Обновление

Обновление до версии с поддержкой и ролями применяет миграцию `0002`: новые таблицы `admin_roles`, `support_tickets`, `support_messages`, колонки `users.public_id` (заполняется для всех существующих пользователей) и `subscription_events.public_note`. Перед обновлением добавьте в `.env` строку `OWNER_TELEGRAM_ID=...`.


```bash
sudo systemctl stop spirittvpn-bot spirittvpn-api
./scripts/backup_db.sh                       # сначала резервная копия
# заменить файлы проекта, затем:
.venv/bin/pip install -r requirements.txt && .venv/bin/alembic upgrade head && (cd frontend && pnpm install && pnpm build)
sudo systemctl start spirittvpn-api spirittvpn-bot
```
