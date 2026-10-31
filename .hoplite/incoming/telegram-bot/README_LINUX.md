# SpirittVPN на Linux (H1Cloud) — установка и запуск

Коротко: **`PY_FILE=run.py`**, Python **3.12**, запуск **`python3 run.py`**.

`run.py` делает по порядку:
1. Проверяет Python, зависимости и `.env`.
2. Проверяет подключение к PostgreSQL и состояние миграций.
3. Защищает от второго запуска (файл `run.lock`).
4. Запускает **два отдельных процесса**:
   - API с Mini App, админ-панелью, сайтом, webhook оплаты и фоновыми задачами;
   - Telegram-бот.
5. Если один процесс упал, корректно останавливает второй и выходит с ошибкой, чтобы панель хостинга перезапустила проект.

Ресурсы: два процесса вместе занимают ~450–500 МБ памяти (замер на тестовом сервере). На 1 CPU / 1 ГБ RAM этого хватает. Проект без логов и окружения весит ~1 МБ, зависимости — ~150–200 МБ.

---

## 0. Что нужно заранее

| Что | Зачем | Где взять |
|---|---|---|
| Токен бота | работа бота | @BotFather → `/newbot` или `/token`. **Выпустите новый токен, если старый где-то светился (`/revoke`)** |
| PostgreSQL 14+ | база данных (SQLite не поддерживается: проект использует JSONB и частичные индексы) | см. раздел 2 |
| Ваш Telegram ID | права владельца | команда `/whoami` в боте или @userinfobot |
| Бот — администратор канала `@spirit_vpn_news` | обязательная проверка подписки | см. раздел 9 |
| Домен и HTTPS | Mini App и webhook ЮKassa (бот работает и без них) | см. раздел 9 |

## 1. Распаковка

```bash
unzip SpirittVPN_Linux.zip
cd SpirittVPN
```

В панели H1Cloud загрузите архив во вкладке «Файлы» и распакуйте его. Рабочая папка должна быть той, где лежит `run.py`.

## 2. PostgreSQL

**Вариант А — сервер с доступом к консоли (root или sudo):**

```bash
sudo apt update && sudo apt install -y postgresql
sudo -u postgres psql -c "CREATE ROLE spiritt LOGIN PASSWORD 'СЛОЖНЫЙ_ПАРОЛЬ';"
sudo -u postgres psql -c "CREATE DATABASE spiritt OWNER spiritt;"
```

На 1 ГБ RAM PostgreSQL с настройками по умолчанию помещается рядом с ботом.

**Вариант Б — хостинг-контейнер без root**, когда в панели есть только «консоль, файлы, автозапуск»: установить PostgreSQL внутри нельзя. Используйте внешнюю базу — отдельный сервер H1Cloud с PostgreSQL или управляемую БД у любого провайдера — и укажите её адрес в `DATABASE_URL`.

> Я не знаю, какой у вас тип сервера H1Cloud. Если не уверены, начните с варианта А. Если `sudo` недоступен, переходите к варианту Б.

## 3. Зависимости (Python 3.12)

```bash
python3 --version          # должно быть 3.12.x
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

Если панель хостинга сама ставит пакеты из `requirements.txt` при запуске, этот шаг делает она. Системные пакеты (`apt`) самому проекту не нужны. Готовая сборка Mini App и админки уже лежит в архиве (`frontend/dist`), поэтому Node.js на сервере не требуется.

## 4. Настройка `.env`

```bash
cp .env.example .env
nano .env
chmod 600 .env
```

**Обязательные переменные:**

| Переменная | Значение |
|---|---|
| `BOT_TOKEN` | токен бота |
| `DATABASE_URL` | `postgresql+asyncpg://spiritt:ПАРОЛЬ@localhost:5432/spiritt` |
| `SESSION_SECRET` | случайная строка ≥ 32 символов: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `OWNER_TELEGRAM_ID` | ваш числовой Telegram ID. Если пусто, бот напишет в лог одноразовую команду `/setup <код>` |
| `APP_ENV` | `production` на сервере |

**Для полной работы** (без них соответствующая функция честно отключена):

| Переменные | Функция |
|---|---|
| `WEBAPP_URL`, `PUBLIC_BASE_URL` | HTTPS-адрес, нужен для Mini App и webhook |
| `API_PORT` | порт, который выдал хостинг (по умолчанию 8080) |
| `PAYMENT_PROVIDER=yookassa`, `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `YOOKASSA_RETURN_URL` | приём оплаты |
| `VPN_NODES`, `VPN_NODE_<КОД>_TOKEN` | выдача VPN-ключей через H1//VLESS (`docs/H1VLESS.md`) |
| `REQUIRED_CHANNEL`, `REQUIRED_CHANNEL_URL`, `CHANNEL_CHECK_ENABLED` | обязательная подписка (по умолчанию `@spirit_vpn_news`) |
| `TERMS_VERSION`, `PRIVACY_URL`, `TERMS_URL` | обязательные документы (ссылки уже прописаны) |
| `SUPPORT_URL`, `ADMIN_IDS`, `LOG_FILE`, `RUN_COMPONENTS`, `AUTO_MIGRATE` | дополнительно |

Если обязательной переменной не хватает, `run.py` выведет список и завершится с кодом 2. Токен в коде, логах и архиве не хранится. `.env` внесён в `.gitignore`.

## 5. Проверка и первый запуск

```bash
python3 run.py check       # .env, PostgreSQL, миграции — ничего не запускает
python3 run.py             # запуск (Ctrl+C — корректная остановка)
```

На **пустой** базе таблицы создаются автоматически.

## 6. H1Cloud: автозапуск

1. Во вкладке настроек сервера укажите **`PY_FILE=run.py`**.
2. Включите автозапуск. Панель запускает `python run.py`, и тот поднимает API и бота.
3. Переменные можно задать в `.env` (рекомендуется) или в окружении панели. Значения из окружения панели имеют приоритет над `.env`.
4. Если хостинг не даёт открыть порт для API, запустите только бота: `RUN_COMPONENTS=bot`. Бот и фоновые задачи будут работать, а Mini App, веб-админка и webhook оплаты — нет.

**Сервер с systemd вместо панели:**

```ini
# /etc/systemd/system/spirittvpn.service
[Unit]
Description=SpirittVPN
After=network-online.target postgresql.service

[Service]
WorkingDirectory=/opt/SpirittVPN
ExecStart=/opt/SpirittVPN/.venv/bin/python run.py
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=40
User=spiritt

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now spirittvpn
```

Запускайте проект **одним** способом: панелью **или** systemd. Два одновременных запуска с одним токеном ломают бота (ошибка Telegram 409). `run.lock` защищает от второго запуска только в одной и той же папке.

## 7. Логи и диагностика

| Где | Как смотреть |
|---|---|
| Панель H1Cloud | вкладка «Консоль» — весь вывод `run.py`, API и бота |
| systemd | `journalctl -u spirittvpn -f` |
| Файл | задайте `LOG_FILE=logs/spirittvpn.log`; ротация 5 МБ × 3 файла, диск не переполнится |

**Коды выхода `run.py`:**
- 2 — не заполнен `.env`;
- 3 — уже запущен;
- 4 — проблема с миграциями;
- 1 — упал API или бот.

**Частые проблемы:**

| Сообщение | Решение |
|---|---|
| `нет подключения к PostgreSQL` | проверьте `DATABASE_URL`, что база запущена и порт доступен |
| `база данных устарела` | см. раздел 10 |
| `TelegramConflictError` / 409 | бот запущен где-то ещё (другой сервер, ваш ПК) — остановите второй экземпляр |
| `getChatMember … member list is inaccessible` в логе | бот не администратор канала (раздел 9); пользователи не пройдут проверку подписки |
| Кнопки «🚀 Открыть SpirittVPN» нет | `WEBAPP_URL` не задан или не начинается с `https://` |

Проверка API: `curl http://127.0.0.1:${API_PORT:-8080}/api/health` → `{"ok":true,"db":true}`.

## 8. Локальный тест на Windows

1. Установите Python 3.12 и PostgreSQL 16, создайте базу (`README.md`).
2. Заполните `.env` своим тестовым токеном.
3. Запустите `start.bat`. Он проверит окружение, применит миграции к **локальной** базе (`AUTO_MIGRATE=true` только в `start.bat`) и запустит `run.py`.

Админ-панель: <http://localhost:8080/admin>. Mini App внутри Telegram требует HTTPS.

**Не держите бота одновременно на ПК и на хостинге с одним токеном.** Перед загрузкой на хостинг выпустите новый токен.

## 9. Внешние сервисы

| Сервис | Действие |
|---|---|
| **Канал `@spirit_vpn_news`** | канал → «Администраторы» → добавить бота (достаточно минимальных прав). Без этого Telegram отвечает «member list is inaccessible», и доступ получают только администраторы бота |
| **BotFather** | Menu Button → URL Mini App; можно не делать — бот сам ставит кнопку по `WEBAPP_URL` |
| **Домен и HTTPS** | nginx + certbot (`deploy/nginx.conf`, `docs/DEPLOY_LINUX.md`) или HTTPS, если его даёт хостинг |
| **ЮKassa** | ключи и URL уведомлений `https://ДОМЕН/api/payments/webhook/yookassa` (`docs/PAYMENTS.md`) |
| **H1//VLESS** | URL API и токены нод → `VPN_NODES` (`docs/H1VLESS.md`) |

## 10. Обновление без потери данных

```bash
# 1. остановить (панель: «Стоп» / systemctl stop spirittvpn)
./scripts/backup_db.sh                     # 2. резервная копия базы
# 3. заменить файлы проекта новыми, НЕ трогая .env и backups/
. .venv/bin/activate && pip install -r requirements.txt
python3 run.py migrate                     # 4. применить новые миграции
python3 run.py check                       # 5. проверка
# 6. запустить
```

Существующую базу `run.py` автоматически **не** мигрирует: при устаревшей схеме он останавливается и просит сделать бэкап. Чтобы разрешить автоматические миграции при старте, один раз выставьте `AUTO_MIGRATE=true`.

Миграции 0001–0004 только добавляют таблицы и колонки; 0003 снимает ограничение «один владелец». Существующие данные не удаляются. Восстановление из копии описано в `docs/BACKUP.md`.

---

**Что заработает только с реальными доступами:** выдача VPN (нужны ноды H1//VLESS), оплата (ЮKassa), Mini App (HTTPS) и проверка подписки (бот должен быть администратором канала).
