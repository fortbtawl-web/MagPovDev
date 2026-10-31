# Архитектура SpirittVPN

```
Telegram ──► bot/ (aiogram 3, long polling) ─┐
                                              ├─► backend/services/*  ──► PostgreSQL (SQLAlchemy 2 async, Alembic)
Mini App (frontend/, React) ─► backend/api ──┤                       ──► H1//VLESS ноды (httpx)
Админ-панель (frontend/admin) ─► backend/api ┘                       ──► ЮKassa (httpx)
ЮKassa webhook ─► /api/payments/webhook/yookassa
Воркер (в процессе API) ─► истечение подписок, опрос платежей, повтор VPN-операций, сверка, health-check
```

Бизнес-логика живёт **только** в `backend/services/`. Бот, Mini App API и админ-панель вызывают одни и те же функции, поэтому двух разных реализаций правил не бывает.

## Модули

| Путь | Назначение |
|---|---|
| `backend/config.py` | Настройки из окружения (pydantic-settings) |
| `backend/models/` | Все таблицы, ограничения и индексы |
| `backend/services/referrals.py` | Регистрация, deep-link, приглашения, бонусы |
| `backend/services/trial.py` | Пробный период: засчитывается только после подтверждения нодой |
| `backend/services/payments.py` | Заказы, обработка статусов платежей, выдача доступа после оплаты |
| `backend/services/subscriptions.py` | Сроки, продление, истечение, история |
| `backend/services/devices.py` | Ключи на нодах: создание, отзыв, замена, синхронизация, очередь повторов |
| `backend/services/admin.py` | Админ-операции и аудит |
| `backend/services/auth.py` | Проверка `initData`, JWT-сессии, одноразовые коды входа админа |
| `backend/integrations/vpn/` | Интерфейс VPN-провайдера и адаптер H1//VLESS |
| `backend/integrations/payments/` | Интерфейс платёжного провайдера и адаптер ЮKassa |
| `backend/api/` | FastAPI: Mini App API, админ API, webhook, health, раздача frontend |
| `backend/tasks/worker.py` | Фоновые задачи с `pg_try_advisory_lock` |
| `bot/` | Обработчики, клавиатуры и middleware Telegram-бота |
| `database/migrations/` | Миграции Alembic (план тарифов добавляется миграцией) |
| `frontend/` | Mini App (`index.html`) и админ-панель (`admin.html`) |

## Ключевые гарантии на уровне БД

- `users.id` = Telegram ID (PK). Реферальный код уникален.
- `referrals.referred_id` уникален: реферер устанавливается один раз. `CHECK referrer_id <> referred_id`.
- `referral_rewards (referral_id, event_type)` уникален: каждый бонус начисляется один раз.
- `bonus_transactions (referral_reward_id, kind)` уникален. Баланс равен сумме записей журнала.
- `trial_activations.user_id` уникален: пробный период один на пользователя навсегда.
- `subscriptions`: частичный уникальный индекс, у пользователя не больше одной текущей подписки. `CHECK`: у Lifetime нет даты окончания.
- `subscription_events.order_id` уникален: оплату нельзя применить повторно.
- `payments (provider, provider_payment_id)` и `payment_events (provider, id, event)` уникальны.
- `vpn_operations`: на устройство не больше одной ожидающей операции каждого типа.
- `audit_logs.reason`: `CHECK length >= 3`, без причины записи нет.
- Деньги хранятся в копейках (`integer`). Все временные метки хранятся как `timestamptz` (UTC).

## Конкурентность

- **Лимит устройств:** строка подписки блокируется `SELECT … FOR UPDATE` на время подсчёта и резервирования. Тест `test_parallel_adds_respect_limit`: 6 параллельных запросов при лимите 2 дают ровно 2 устройства.
- **Платёж:** строки платежа и заказа блокируются. Тест `test_concurrent_events_single_reward`: 6 одновременных webhook дают одно применение и один бонус.
- **Пробный период:** строка пользователя блокируется, плюс уникальный индекс.
- **Бот:** троттлинг повторных нажатий (0,7 с) и пользовательская блокировка на чувствительных операциях.

## Ограничения текущей реализации

- FSM бота и rate-limit API хранятся в памяти процесса. Запускайте **один** процесс бота и один процесс API, либо добавьте Redis и общий лимитер на прокси.
- Защиты от мультиаккаунтов нет и абсолютной быть не может. Есть только разумные ограничения: один пробный период на Telegram ID, бонусы лишь за подтверждённые сервером события, ручная отмена начислений администратором.
