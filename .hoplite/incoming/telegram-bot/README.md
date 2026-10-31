# 🛡️ SpirittVPN — «Твоя свобода в сети»

Сервис продажи VPN-подписок в Telegram:

- **Telegram-бот** на aiogram 3;
- **Telegram Mini App** на React и TypeScript;
- **веб-панель администратора**;
- общий **backend** на FastAPI с PostgreSQL;
- интеграции с нодами **H1//VLESS** (H1Cloud) и платёжной системой **ЮKassa**.

> ⚠️ **Текущий статус.** Код готов, автотесты проходят. **Реальная выдача VPN и приём оплаты ещё не проверены на живой инфраструктуре**: нужны адреса и токены нод H1//VLESS, ключи ЮKassa и HTTPS-домен. Подробнее в [docs/STATUS.md](docs/STATUS.md).

## Возможности

> **Запуск на Linux / H1Cloud:** см. [README_LINUX.md](README_LINUX.md) (`PY_FILE=run.py`).


- **Бот.** Главное меню: 🛡️ Моя подписка · 📱 Мои устройства · 💎 Купить VPN · 🎁 Пробный период · 👥 Пригласить друга · 💳 История платежей · ❓ Поддержка · 🚀 Открыть SpirittVPN. Плюс `/admin` для администраторов.
- **Тарифы** хранятся в БД и редактируются из бота или панели: цена, срок, лимит устройств, включение. Lifetime — отдельный флаг `is_lifetime`, без даты окончания и без продлений.
- **Платежи.** Заказ сохраняет снимок тарифа. Доступ выдаётся **только** после того, как платёж подтверждён API ЮKassa: проверяются сумма, валюта и `order_id`, обработка идемпотентна, есть резервный опрос статусов.
- **Подписки.** Продление добавляет дни к текущему сроку. После истечения новый срок считается от момента оплаты. Ведётся история изменений. Фоновая задача переводит подписки в «истекла».
- **Пробный период** (по умолчанию 3 дня) засчитывается только после того, как нода подтвердила создание ключа. Повторно активировать нельзя.
- **Устройства.** Один ключ на устройство. Лимит соблюдается и при параллельных запросах. Есть ссылка, QR-код, переименование, замена и отзыв через API ноды. Статус «отключено» появляется только после подтверждения сервера.
- **Рефералы.** Ссылка `https://t.me/<бот>?start=ref_<код>` (ссылки вида `ref_<telegram id>` тоже принимаются). Нельзя пригласить себя и нельзя сменить реферера. Бонусы: +3 дня за пробный период друга, +7 дней за его первую оплату. Размеры и включение настраиваются, повторные начисления исключены ограничениями БД.
- **Админка** прямо в Mini App, в браузере (`/admin`) и в боте (`/admin`). Есть статистика и поиск по Telegram ID, @username и SpirittVPN ID. Ручная выдача подписок: продление, новый срок, точная дата, бессрочно, сокращение, досрочное завершение, отзыв; срок в часах, днях, неделях или месяцах. Блокировка пользователей, роли (владелец назначает и снимает админов), тарифы, рефералы, настройки, ноды, журнал аудита. Подробнее в [docs/ADMIN.md](docs/ADMIN.md).
- **Личный кабинет** в Mini App: постоянный **SpirittVPN ID** (`SV-XXXXXX`), статус, тариф, даты, остаток времени, устройства, бонусы, рефералы, история платежей и подписки.
- **Промокоды** (скидка %, скидка ₽, бесплатные дни), **обязательное принятие документов** и **обязательная подписка на канал** `@spirit_vpn_news` (см. [docs/ADMIN.md](docs/ADMIN.md)).
- **Поддержка**: обращения `SUP-XXXXX` с категориями, статусами, перепиской, вложениями через бота, внутренними заметками, назначением ответственного и уведомлениями.
- **Надёжность.** Транзакции и блокировки строк, очередь повторов VPN-операций, сверка БД с нодами, health-check, корректная остановка, резервное копирование.

## Структура

```
SpirittVPN/
├── backend/                 общий backend: модели, сервисы, интеграции, API, воркер
│   ├── models/              таблицы и ограничения БД
│   ├── services/            вся бизнес-логика (используется ботом и API)
│   ├── integrations/vpn/    интерфейс VPN-провайдера + адаптер H1//VLESS
│   ├── integrations/payments/ интерфейс платёжного провайдера + ЮKassa
│   ├── api/                 FastAPI: Mini App API, админ API, webhook
│   └── tasks/worker.py      фоновые задачи
├── bot/                     Telegram-бот: handlers/, keyboards/, middlewares/
├── website/                 публичный сайт-лендинг (статика, `/site/`, см. website/README.md)
├── frontend/                Mini App (index.html) и админ-панель (admin.html), React + TS + Vite
├── database/migrations/     миграции Alembic
├── tests/                   автотесты (pytest, реальный PostgreSQL)
├── docs/                    документация
├── deploy/                  systemd и nginx
├── scripts/                 запуск, тесты, бэкап (Linux) + check_db.py
├── start.bat, check_env.bat, run_tests.bat   — запуск на Windows
├── docker-compose.yml, Dockerfile
└── .env.example
```

## Быстрый старт на Windows 10/11

1. Установите программы:
   - **Python 3.12+** с <https://www.python.org/downloads/> (при установке отметьте «Add python.exe to PATH»);
   - **PostgreSQL 16** с <https://www.postgresql.org/download/windows/> (запомните пароль пользователя `postgres`);
   - **Node.js 22** с <https://nodejs.org/>, затем в терминале `npm i -g pnpm`. Нужен только для пересборки веб-интерфейса: готовая сборка `frontend/dist` уже лежит в архиве.
2. Создайте базу. Откройте «SQL Shell (psql)» и выполните:
   ```sql
   CREATE ROLE spiritt LOGIN PASSWORD 'придумайте_пароль';
   CREATE DATABASE spiritt OWNER spiritt;
   CREATE DATABASE spiritt_test OWNER spiritt;   -- только для автотестов
   ```
3. Распакуйте архив, например в `C:\SpirittVPN`, и дважды щёлкните `start.bat`. Первый запуск создаст `.env` и остановится.
4. Откройте `.env` в Блокноте и заполните минимум:
   - `BOT_TOKEN` — токен бота;
   - `OWNER_TELEGRAM_ID` — ваш числовой Telegram ID (`/whoami` в боте или @userinfobot);
   - `DATABASE_URL=postgresql+asyncpg://spiritt:придумайте_пароль@localhost:5432/spiritt`;
   - `SESSION_SECRET` — случайная строка не короче 32 символов.
5. Снова запустите `start.bat`. Он проверит Python, зависимости и подключение к БД, применит миграции и откроет два окна: **API** и **бота**.
6. Напишите боту `/start`. Админ-панель доступна по адресу <http://localhost:8080/admin>: введите свой Telegram ID, и бот пришлёт код входа.

`check_env.bat` проверяет окружение без запуска, `run_tests.bat` запускает автотесты (см. ниже).

> Локальный запуск предназначен для разработки. Mini App внутри Telegram требует **HTTPS**, а для круглосуточной работы бот и панель должны стоять на сервере ([docs/DEPLOY_LINUX.md](docs/DEPLOY_LINUX.md)). Компьютер владельца для работы сервиса не нужен.

## Linux

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
.venv/bin/alembic upgrade head          # миграции
./scripts/start.sh                      # API + воркер + бот
```

Установка на сервер с systemd, nginx и HTTPS, а также запуск через Docker описаны в [docs/DEPLOY_LINUX.md](docs/DEPLOY_LINUX.md).

## Команды

| Действие | Команда |
|---|---|
| Установить зависимости | `pip install -r requirements.txt` (для тестов `requirements-dev.txt`) |
| Применить миграции | `alembic upgrade head` (обновление существующей БД: миграция 0002 добавляет роли, поддержку и SpirittVPN ID без потери данных) |
| Откатить все миграции | `alembic downgrade base` (⚠️ удаляет данные) |
| Запустить API, Mini App, панель и воркер | `python -m backend` |
| Запустить бота | `python -m bot` |
| Собрать веб-интерфейс | `cd frontend && pnpm install && pnpm build` |
| Тесты | `TEST_DATABASE_URL=postgresql+asyncpg://spiritt:пароль@localhost:5432/spiritt_test pytest -q` |
| Резервная копия БД | `./scripts/backup_db.sh` или `scripts\backup_db.bat` |

**Тесты очищают указанную базу.** Задавайте только отдельную тестовую БД. Тесты не делают реальных оплат и не создают реальных VPN-клиентов: внешние сервисы подменяются фейками из `tests/fakes.py`, которые существуют только в тестах.

## Переменные окружения

Полный список с комментариями — в [.env.example](.env.example).

| Переменная | Обязательно | Назначение |
|---|---|---|
| `BOT_TOKEN` | да | токен @BotFather |
| `OWNER_TELEGRAM_ID` | да* | Telegram ID главного администратора (число). *Без него владельца назначают через `/setup` (код в логе сервера) |
| `ADMIN_IDS` | нет | дополнительные администраторы (числа) |
| `DISPLAY_TIMEZONE` | нет | часовой пояс для дат в админке (по умолчанию `Europe/Moscow`) |
| `DATABASE_URL` | да | PostgreSQL (`postgresql+asyncpg://…`) |
| `SESSION_SECRET` | да | ≥ 32 символов, подпись сессий Mini App и панели |
| `WEBAPP_URL`, `PUBLIC_BASE_URL` | для Mini App и оплаты | публичный HTTPS-адрес |
| `PAYMENT_PROVIDER`, `YOOKASSA_*` | для продаж | ЮKassa ([docs/PAYMENTS.md](docs/PAYMENTS.md)) |
| `VPN_NODES`, `VPN_NODE_<КОД>_TOKEN` | для выдачи VPN | ноды H1//VLESS ([docs/H1VLESS.md](docs/H1VLESS.md)) |
| `SUPPORT_URL` | нет | ссылка на поддержку |

## Настройка Telegram

1. В @BotFather создайте бота (`/newbot`) и скопируйте токен в `BOT_TOKEN`.
2. Mini App (нужен HTTPS): [docs/MINIAPP.md](docs/MINIAPP.md). Бот сам ставит кнопку меню на `WEBAPP_URL`.
3. Реферальные ссылки строятся по реальному имени бота из `getMe`.

## Документация

- [docs/ADMIN.md](docs/ADMIN.md) — админ-панель, роли, SpirittVPN ID, ручная выдача, поддержка
- [docs/STATUS.md](docs/STATUS.md) — что проверено, что заблокировано, что нужно от владельца
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — архитектура и гарантии БД
- [docs/H1VLESS.md](docs/H1VLESS.md) — интеграция с нодами H1//VLESS
- [docs/PAYMENTS.md](docs/PAYMENTS.md) — ЮKassa
- [docs/MINIAPP.md](docs/MINIAPP.md) — Mini App и BotFather
- [docs/DEPLOY_LINUX.md](docs/DEPLOY_LINUX.md) — сервер, HTTPS, Docker
- [docs/BACKUP.md](docs/BACKUP.md) — резервные копии

## Безопасность

- Секреты задаются только через `.env`. В коде, архиве и `.bat`-файлах их нет.
- Права администратора определяются по Telegram ID из `ADMIN_IDS` (не по username) и проверяются на сервере при каждом запросе.
- Все ручные изменения пишутся в `audit_logs`: кто, когда, что изменил, причина, старое и новое значение.
- Токены нод и ключи ЮKassa не логируются и не показываются в интерфейсах.
- SQL-запросы строятся только через SQLAlchemy, без склейки строк.
