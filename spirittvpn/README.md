# SpirittVPN — Telegram-бот VPN-сервиса

<p align="center"><img src="app/assets/spirittvpn_logo.png" width="220" alt="SpirittVPN"></p>

Бот на aiogram 3 для VPN-сервиса SpirittVPN. В нём есть пробный период, тарифы, заказы с идемпотентной обработкой платежей, промокоды, личный кабинет, поддержка с тикетами, админ-панель, фоновые задачи по подпискам и интеграция с VPN через сменные адаптеры.

> **Принцип:** бот никогда не выдаёт фиктивный доступ. Пока VPN-сервер не подключён (`VPN_PROVIDER=none`), пробный период и продажи закрыты сообщением «Сервис готовится к запуску». Пока не подключена оплата (`PAYMENT_PROVIDER=none`), оплата недоступна.

---

## 1. Архитектура

```
spirittvpn/
├── app/
│   ├── __main__.py          # python -m app
│   ├── main.py              # сборка Bot/Dispatcher, HTTP-сервер, планировщик, логирование с редактированием секретов
│   ├── config.py            # все настройки из переменных окружения (pydantic-settings) + защита production
│   ├── branding.py          # ЕДИНОЕ место: логотип, ссылки на документы, контакты, роли команды
│   ├── texts.py             # тексты интерфейса (RU, HTML)
│   ├── keyboards.py         # inline-клавиатуры
│   ├── database.py          # Base, UTC-тип даты, engine/session
│   ├── models.py            # схема БД
│   ├── runtime.py           # Deps, отправка логотипа (с fallback), уведомления, обработка результата оплаты
│   ├── middlewares.py       # сессия БД, регистрация/бан/техработы, антифлуд
│   ├── jobs.py              # фоновый цикл задач
│   ├── web.py               # aiohttp: /health, webhook оплаты, тестовая страница оплаты (dev)
│   ├── assets/spirittvpn_logo.png   # оригинальный логотип (PNG 1254×1254, 1.6 МБ, без изменений)
│   ├── handlers/            # user.py (меню, пробный, аккаунт, промо, поддержка), buy.py (покупка), admin.py
│   ├── services/            # бизнес-логика без Telegram: users, trial, orders, promos, admin, ops, vpn_sync, settings_store
│   ├── payments/providers.py  # PaymentProvider + Telegram Payments + Test + Disabled
│   └── vpn/                 # VPNProvider, retry, Marzban, None, Test
├── migrations/              # Alembic (async), 0001_initial_schema
├── tests/                   # pytest (52 теста)
├── scripts/backup.sh        # ежедневный pg_dump с ротацией
├── Dockerfile, docker-compose.yml (bot + PostgreSQL 16 + Redis 7 + backup)
├── requirements.txt / requirements-dev.txt (версии зафиксированы)
└── .env.example
```

Обработчики Telegram тонкие: каждое действие уходит в `services/*`. Сервисы получают `AsyncSession` и `now`, поэтому их можно тестировать без Telegram. Платежи и VPN подключаются через интерфейсы `PaymentProvider` и `VPNProvider`, так что смена провайдера не затрагивает бизнес-логику.

## 2. Схема базы данных

| Таблица | Назначение | Ключевые ограничения |
|---|---|---|
| `users` | Telegram ID, username, группа, блокировки | `telegram_id UNIQUE` |
| `legal_acceptances` | факт, дата и версия принятия документов | `UNIQUE(user_id, version)` |
| `trial_activations` | выдача пробного периода | `user_id UNIQUE`: второй пробный невозможен на уровне БД |
| `subscriptions` | срок, статус, тариф, флаг синхронизации VPN, маркеры уведомлений | `user_id UNIQUE` |
| `vpn_accounts` | связь пользователя с клиентом на VPN-сервере (без ключей) | `user_id UNIQUE` |
| `plans` | тарифы: цена (в минимальных единицах), срок, валюта, вкл/выкл | `code UNIQUE` |
| `orders` | заказы со снимком тарифа и суммы; статусы `created/pending/paid/cancelled/failed` | `provider_payment_id UNIQUE` |
| `payment_events` | каждое уведомление об оплате | `UNIQUE(provider, event_id)` (идемпотентность) |
| `promo_codes` | `days / percent / fixed`, срок, лимит, группа, «только новым» | `code UNIQUE` |
| `promo_activations` | использование промокодов: `applied / pending / used` | `UNIQUE(promo_id, user_id)` |
| `audit_log` | журнал действий администраторов | — |
| `settings` | настройки, редактируемые из бота | — |
| `support_tickets`, `support_messages` | обращения и ответы | — |
| `broadcasts` | рассылки и их статистика | — |

Время хранится в UTC (`timestamptz` в PostgreSQL), пользователю показывается в `DISPLAY_TIMEZONE`.

## 3. Создание бота в BotFather

1. Откройте [@BotFather](https://t.me/BotFather) → `/newbot` → задайте имя и username.
2. Скопируйте токен в `.env` → `BOT_TOKEN=`. **Токен никому не показывайте и не коммитьте.**
3. Рекомендуется: `/setuserpic` (загрузите `app/assets/spirittvpn_logo.png`), `/setdescription`, `/setabouttext`.
4. Для оплаты в рублях: `/mybots` → бот → **Payments** → подключите провайдера и скопируйте его токен в `PAYMENT_PROVIDER_TOKEN`. Для Telegram Stars провайдер не нужен.
5. Свой Telegram ID можно узнать у [@userinfobot](https://t.me/userinfobot) и вписать в `ADMIN_IDS`.

## 4. Установка на сервер (Docker)

```bash
# Ubuntu 22.04/24.04
curl -fsSL https://get.docker.com | sh
git clone <repo> && cd <repo>/spirittvpn
cp .env.example .env && nano .env        # BOT_TOKEN, ADMIN_IDS, POSTGRES_PASSWORD, ...
docker compose up -d --build
docker compose logs -f bot               # ожидается: "bot @... started"
```

Миграции применяются автоматически при старте контейнера (`alembic upgrade head`). HTTP-порт 8080 открыт только на `127.0.0.1`. Для внешних webhook поставьте перед ним reverse-proxy с TLS (Caddy или nginx).

**Без Docker (разработка):**
```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env    # DATABASE_URL=sqlite+aiosqlite:///./data/spirittvpn.db
mkdir -p data && alembic upgrade head && python -m app
```

При запуске бот снимает ранее установленный webhook (polling и webhook несовместимы).

### Пример `.env` (production)
```env
BOT_TOKEN=1234567890:AA...           # из BotFather
ADMIN_IDS=111111111,222222222
APP_ENV=production
POSTGRES_PASSWORD=<длинный случайный пароль>
PAYMENT_PROVIDER=telegram
PAYMENT_CURRENCY=XTR                 # Telegram Stars; или RUB + PAYMENT_PROVIDER_TOKEN
PLAN_1M_PRICE=150                    # 150 ⭐ (для RUB укажите копейки: 29900 = 299 ₽)
PLAN_3M_PRICE=400
PLAN_6M_PRICE=750
PLAN_12M_PRICE=1400
VPN_PROVIDER=marzban
MARZBAN_URL=https://panel.example.com
MARZBAN_USERNAME=admin
MARZBAN_PASSWORD=<пароль панели>
```
Цены выше — **только пример формата**, окончательные цены задаёт владелец.

## 5. Тарифы и промокоды

При первом запуске создаются тарифы `m1`, `m3`, `m6`, `m12` (30/90/180/365 дней). Тариф без цены (`PLAN_*_PRICE` пусто) создаётся **выключенным** и пользователю не показывается. Дальше всё управляется из бота (`/admin`):

```
/plans                                         список тарифов
/plan_set m1 price=29900 active=on             цена (в минимальных единицах) и включение
/plan_set m3 days=90 title=3_месяца desc=Выгодно   «_» заменяется пробелом
/plan_add m24 730 499900 2 года                новый тариф (создаётся выключенным)

/promo_add WELCOME7 days 7 max=500 until=2026-12-31      +7 дней, лимит 500, до даты
/promo_add SALE20 percent 20 new=on                      −20% только для новых покупателей
/promo_add MINUS100 fixed 10000 group=vip desc=Для_VIP   −100 ₽ только для группы vip
/group 123456789 vip                                     назначить пользователю группу
/promo_set SALE20 max=100 until=2027-01-31
/promo_off SALE20  ·  /promo_on SALE20  ·  /promo_archive SALE20
/promo SALE20                                            статистика
/promos                                                  список
```

Правила: один код на пользователя один раз. Лимит активаций атомарен (условный `UPDATE`). Скидочный код ждёт следующей покупки (одновременно можно держать одну неиспользованную скидку) и списывается только после подтверждённой оплаты. Бонусные дни начисляются сразу; пока VPN не подключён, они не выдаются.

## 6. Подключение VPN-провайдера

Интерфейс `app/vpn/base.py::VPNProvider` включает `ensure_client`, `suspend`, `revoke`, `get_state`, `get_connection`. Временные ошибки (`VPNUnavailableError`) повторяются с экспоненциальной задержкой (`VPN_RETRY_ATTEMPTS`). Если операция так и не удалась, у подписки остаётся флаг `vpn_sync_needed`, и фоновая задача повторяет её до `VPN_MAX_SYNC_ATTEMPTS` раз, а затем уведомляет администраторов.

**Marzban (готовый адаптер, `app/vpn/marzban.py`).** Эндпоинты взяты из официального исходного кода Marzban (`/api/admin/token`, `/api/user`, `/api/user/{username}`):
1. Установите Marzban по официальной документации и создайте inbound (например, VLESS).
2. В `.env`: `VPN_PROVIDER=marzban`, `MARZBAN_URL`, `MARZBAN_USERNAME`, `MARZBAN_PASSWORD`, `MARZBAN_PROXIES=vless`.
3. Каждый пользователь бота становится клиентом `spiritt_<telegram_id>` со сроком `expire`, равным сроку подписки. Пользователь получает subscription-ссылку в «🔑 Мои подключения» (сообщение с `protect_content`, в логи не пишется).
4. **Перед запуском** сверьте API со страницей `/docs` вашей версии панели: адаптер на реальной панели не тестировался.

**Другая панель или WireGuard.** Создайте класс-наследник `VPNProvider` по официальному API вашей панели (например, wg-easy или 3x-ui), добавьте его в `app/vpn/factory.py` и значение в `VPN_PROVIDER` (`config.py`). Приватные ключи не храните в БД и не логируйте: `get_connection` должен запрашивать конфигурацию у сервера в момент выдачи.

`VPN_PROVIDER=test` подходит только для разработки: выдаёт файл с пометкой «ТЕСТОВАЯ КОНФИГУРАЦИЯ — НЕ РАБОЧИЙ VPN» и запрещён при `APP_ENV=production`.

## 7. Подключение оплаты

**Telegram Payments (готово, `PAYMENT_PROVIDER=telegram`).**
- Stars: `PAYMENT_CURRENCY=XTR`, токен не нужен, цены указываются в звёздах.
- Рубли и другие валюты: `PAYMENT_CURRENCY=RUB` + `PAYMENT_PROVIDER_TOKEN` из BotFather → Payments.
- Поток: заказ → `sendInvoice` → `pre_checkout_query` (проверка заказа, пользователя, суммы, валюты и срока; иначе отказ) → `successful_payment` по аутентифицированному каналу Bot API → идемпотентная обработка по `telegram_payment_charge_id`.
- Чеки по 54-ФЗ (для фиатных провайдеров) передаются через `provider_data` вашего провайдера; это поле пока не заполняется и требует настройки под конкретного провайдера.

**Внешний провайдер с webhook.** Реализуйте `PaymentProvider.create_payment` (возвращает `PaymentSession(kind="url", url=...)`) и `parse_webhook` (проверка подписи по документации провайдера → `PaymentEventData`). Webhook принимается на `POST /payments/<name>/webhook`. Невалидная подпись даёт 401, недоступность БД — 503 (провайдер повторит).

**Тестовый режим (`PAYMENT_PROVIDER=test`, только разработка).** События подписаны HMAC-SHA256 (`PAYMENT_TEST_SECRET`) и помечены как TEST: в сообщениях, в БД и в истории. В выручку они не входят. Подтвердить можно на тестовой странице (`PUBLIC_BASE_URL/payments/test/checkout/...`) или админ-командой `/testpay <order_id> success|fail`. При `APP_ENV=production` режим запрещён.

Гарантии: цена и срок берутся только из БД. Подписка продлевается исключительно по подтверждённому событию провайдера (кнопка «Проверить статус» лишь читает статус). Повторный webhook ничего не продлевает. Несовпадение суммы, валюты или режима не активирует подписку, а уведомляет админов. Деньги, пришедшие на отменённый по TTL заказ, засчитываются.

## 8. Админ-панель

Доступна только ID из `ADMIN_IDS`. Проверка двойная: фильтр роутера и `require_admin()` в каждом сервисе. Действия записываются в `audit_log`. Обычный пользователь на любую админ-команду получает «⛔️ Эта команда недоступна», попытка логируется.

`/admin` открывает меню с разделами: 📊 Статистика · 👥 Пользователи · 💳 Тарифы · 🎟️ Промокоды · ⚙️ Настройки · 📢 Рассылка · 🎫 Обращения · 🧾 Журнал.

| Команда | Действие |
|---|---|
| `/stats` | пользователи (всего, за день, за месяц), активные подписки, пробные, оплаченные заказы, выручка, действующие промокоды |
| `/user ID` | карточка: подписка, заказы, промокоды; кнопки +7/+30 дней и блокировка |
| `/extend ID ДНИ причина` | ручное продление (причина обязательна, пишется в журнал) |
| `/block ID` · `/unblock ID` · `/group ID имя` | доступ к боту и группа для промокодов |
| `/set ключ значение` | `welcome_text`, `trial_enabled`, `trial_days`, `notifications_enabled`, `maintenance`, `maintenance_text`, `support_extra_url` |
| `/broadcast` | текст → предпросмотр → подтверждение → отправка не быстрее `BROADCAST_RATE_PER_SECOND`; заблокировавшие бота помечаются |
| `/tickets` · `/reply ID текст` · `/close ID` | обращения пользователей |
| `/order ID` · `/audit` | заказ и журнал аудита |
| `/confirm_payment ORDER CHARGE_ID` | восстановление платежа Telegram, если БД была недоступна в момент оплаты |

Ссылки на документы и контакты поддержки (@cupsov, @TTwer4) зафиксированы в `app/branding.py`. Из бота можно добавить только дополнительную ссылку (`support_extra_url`), обязательные контакты так не меняются.

## 9. Фоновые задачи (каждые `JOB_INTERVAL_SECONDS`)

- отмена неоплаченных заказов старше `ORDER_TTL_HOURS`;
- уведомление за 24 ч и финальное «заканчивается сегодня» (за `FINAL_NOTICE_HOURS`). Маркер привязан к конкретной дате окончания, поэтому повтор невозможен, а после продления цикл начинается заново;
- истечение подписки: статус `expired`, приостановка доступа на VPN, уведомление (отдельный текст для пробного периода);
- повтор несинхронизированных операций VPN; после продления доступ восстанавливается;
- сверка БД с VPN-сервером раз в `RECONCILE_INTERVAL_SECONDS`.

Все шаги идемпотентны: повторный запуск или перезапуск бота не начисляет дни и не дублирует уведомления.

## 10. Безопасность и данные

- Секреты хранятся только в `.env` (в `.gitignore`). `Settings` хранит их как `SecretStr`. Фильтр логов вырезает bot-токены и Bearer-токены.
- Конфигурации VPN не сохраняются в БД и не логируются, а пользователю отправляются с `protect_content`.
- Webhook оплаты: HMAC или подпись провайдера, лимит тела 64 КБ, 401 на невалидную подпись. Порт открыт только на localhost.
- Антифлуд (`THROTTLE_SECONDS`) и лимит 5 неверных промокодов за 10 минут.
- Ошибки БД показываются пользователю как «Сервис временно недоступен», детали остаются только в логах.
- Резервные копии: сервис `backup` делает `pg_dump` раз в сутки в `./backups`, ротация `BACKUP_RETENTION_DAYS`. Восстановление: `pg_restore -h ... -U ... -d spirittvpn --clean файл.dump`.
- **Политика хранения:** бот хранит Telegram ID, username, имя, данные подписки, заказы (сумма, тариф, ID платежа провайдера), активации промокодов, тексты обращений и факт принятия документов. Платёжные реквизиты (номера карт) бот не получает: их обрабатывает провайдер. Ключи VPN не хранятся. Удаление данных по запросу пользователя администратор пока выполняет вручную в БД (заказы рекомендуется сохранять для бухучёта). Отдельной команды нет.

## 11. Тестирование

```bash
pip install -r requirements-dev.txt
pytest -q                                                                    # SQLite в памяти
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/test pytest -q   # PostgreSQL
```

Покрыто 52 тестами: регистрация и повторный `/start`; пробный период (выдача, повтор, UNIQUE в БД, требование согласия, недоступный VPN не списывает пробный, VPN не подключён); расчёт срока; промокоды (все правила, повтор, лимит под конкурентной нагрузкой); заказы и серверная цена; подтверждение и отклонение оплаты; повторный webhook; несовпадение суммы и режима; оплата после отмены по TTL; pre-checkout; подпись webhook; доступ к админке (сервисы и реальный Dispatcher); ручное продление с аудитом; уведомления; истечение и продление; ошибки и retry VPN; сверка; обязательные ссылки, контакты, роли, логотип и fallback.

## 12. Сценарии из ТЗ

| # | Сценарий | Где обеспечено |
|---|---|---|
| 1–2 | первый и повторный `/start` | `register_user` не перезаписывает данные; приветствие с логотипом и документами |
| 3–4 | пробный период и повтор | `services/trial.py`, `UNIQUE(trial_activations.user_id)` |
| 5–8 | покупка, подтверждение, отказ, двойной webhook | `services/orders.py::process_payment_event` |
| 9–10 | неверный или повторный промокод | `services/promos.py` |
| 11–12 | истечение и продление | `services/ops.py`, `compute_period` |
| 13 | VPN недоступен | retry + `vpn_sync_needed` + честные сообщения |
| 14–15 | админ-доступ и ручное продление | `handlers/admin.py`, `services/admin.py` |
| 16 | БД недоступна | обработчик ошибок; повторы при `successful_payment` + алерт админам; webhook возвращает 503 |
| 17 | перезапуск во время заказа | состояние в БД, идемпотентные события, задачи синхронизации |
