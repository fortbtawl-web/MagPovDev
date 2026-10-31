# SpirCoin, задания, роли — контракт API

Все суммы — целые SpirCoin. Ошибки: HTTP 400/403/404/409/429 с телом `{"detail": "текст", "code": "машинный_код"}`
(как у остальных эндпоинтов проекта, см. `frontend/src/shared/api.ts`).

## Mini App (Bearer токен пользователя)

### `GET /api/me` — добавлены поля
```json
{
  "coins": 120,
  "coins_enabled": true,
  "payments_enabled": false,
  "subscription_url": "https://s-…/sub/…" | null,
  "subscription_location": "Все локации (автовыбор)" | null
}
```
`subscription_url` — персональная ссылка подписки с основного (первого активного) устройства, `null` если ключа ещё нет.
Остальные поля `/api/me` не менялись (`trial`, `subscription`, `access_state`, `user`, …).

### `GET /api/plans` — у каждого тарифа добавлено
```json
{ "price_coins": 1000 | null }
```
`null` — тариф нельзя купить за SpirCoin. Ответ также содержит `"payments_enabled": bool` (реальная оплата подключена).

### `GET /api/coins`
```json
{ "balance": 120, "enabled": true,
  "history": [{ "id": 1, "amount": 50, "kind": "task_reward", "kind_label": "Задание", "reason": "BowieRP", "created_at": "ISO" }] }
```
`kind`: `task_reward` | `purchase` | `admin_grant` | `admin_debit` | `refund`.

### `POST /api/coins/purchase`
Тело: `{ "plan_code": "dev1_30", "idempotency_key": "<uuid v4, генерируется клиентом один раз на попытку>" }`
Ответ 200:
```json
{ "ok": true, "replayed": false, "order_id": 12, "balance": 0,
  "subscription": { …как в /api/me… }, "vpn": { "confirmed": true, "error": null } }
```
Повтор с тем же ключом возвращает тот же результат с `"replayed": true` и **не** списывает монеты повторно.
Коды ошибок: `insufficient_coins` (в `detail` — сколько не хватает), `plan_unavailable`, `lifetime_active`, `coins_disabled`, `banned`.

### `GET /api/tasks`
```json
{ "tasks": [{ "id": 1, "title": "Канал SpiritVPN", "description": "…", "url": "https://t.me/spirit_vpn_news",
  "reward": 1, "verify": "telegram" | "manual",
  "status": "available" | "pending" | "rewarded" | "rejected" | "unavailable",
  "note": "текст для пользователя или null" }] }
```

### `POST /api/tasks/{id}/claim`
Ответ: `{ "status": "rewarded" | "pending" | "rejected" | "not_subscribed", "message": "…", "balance": 121 }`
- `verify=telegram`: сервер проверяет членство через Telegram. Нет подписки — `not_subscribed`, ничего не начисляется.
- `verify=manual`: создаётся заявка `pending`, награду выдаёт модератор/админ после проверки.

## Админ-панель (Bearer админ-токен)

`GET /api/admin/me` → `{ "admin_id", "role": "owner"|"admin"|"moderator", "is_owner", "permissions": ["users.view", …] }`

Права (проверяются на сервере для каждого запроса):

| permission | owner | admin | moderator |
|---|:-:|:-:|:-:|
| `users.view`, `support`, `tasks.review` | ✓ | ✓ | ✓ |
| `users.manage`, `coins.manage`, `tasks.manage`, `plans.manage`, `nodes`, `settings` | ✓ | ✓ | — |
| `roles.manage`, `economy.limits` | ✓ | — | — |

Модератору доступны только эндпоинты с его правами; остальные возвращают 403.

- `GET /api/admin/stats` — добавлено поле `economy`:
  `{ "trials": n, "paid_orders": n, "coin_orders": n, "tasks_rewarded": n, "coins_issued": n, "coins_spent": n, "coins_in_circulation": n, "claims_pending": n }`
- `GET /api/admin/coins/transactions?user_id=&limit=100` → `{ "items": [{ id, user_id, user_name, amount, balance_after, kind, kind_label, reason, actor, created_at }] }`
- `POST /api/admin/users/{id}/coins` `{ "amount": -50|100, "reason": "…" }` → `{ "balance": n }` (`coins.manage`; отрицательный баланс запрещён)
- `GET /api/admin/users/{id}` — добавлены `coins` (баланс), `coin_history` (последние 50), `task_claims`.
- `GET /api/admin/tasks` → `{ "tasks": [{ id, code, title, description, url, reward, verify, chat_id, is_active, archived, budget_total, issued_total, max_completions, completions, pending, sort_order }] }`
- `POST /api/admin/tasks` / `PATCH /api/admin/tasks/{id}` — поля: `title, description, url, reward, verify ("telegram"|"manual"), chat_id ("@channel", нужен для telegram), is_active, budget_total (null=без лимита), max_completions (null), sort_order, reason`
- `POST /api/admin/tasks/{id}/archive` `{ reason }` — задание скрывается, история сохраняется.
- `GET /api/admin/task-claims?status=pending` → `{ "items": [{ id, task_id, task_title, user_id, user_name, status, reason, created_at }] }`
- `POST /api/admin/task-claims/{id}/approve` `{ reason }` / `POST …/reject` `{ reason }` (`tasks.review`)
- `PATCH /api/admin/plans/{id}` — принимает также `price_coins` (целое > 0 или `null`).
- `GET /api/admin/settings` / `PUT /api/admin/settings/{key}` — новые ключи экономики (только владелец):
  `coins_enabled`, `coins_daily_emission_limit`, `coins_monthly_emission_limit`, `coins_admin_max_per_operation` (0 = без лимита).
- `POST /api/admin/admins` — `role` теперь `"owner" | "admin" | "moderator"` (только владелец).
