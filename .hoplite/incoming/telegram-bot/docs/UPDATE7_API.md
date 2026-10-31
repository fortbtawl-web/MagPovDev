# Рефералы, статистика промокодов, коды тестовых подписок — контракт API (обновление 7)

Ошибки как и раньше: `{"detail": "текст", "code": "машинный_код"}`. Даты — ISO 8601 (UTC). Деньги — копейки (`*_minor`) + готовая подпись `*_label`.

## Mini App (Bearer токен пользователя)

### `GET /api/referrals` — добавлено поле `stats` (старые поля `link`, `code`, `summary`, `invites`, … без изменений)
```json
"stats": {
  "visits": 12,            // уникальные пользователи, открывшие бота по ссылке
  "registered": 5,         // новые пользователи, закреплённые за вами
  "trial": 3,              // из них активировали пробный период
  "buyers": 2,             // приглашённые с хотя бы одной подтверждённой оплатой
  "purchases": 3,          // подтверждённые оплаты приглашённых (деньги), возвраты не считаются
  "paid_minor": 45000, "paid_label": "450 ₽",
  "coin_purchases": 1,     // покупки приглашённых за SpirCoin (отдельно, не деньги)
  "refunds": 0
},
"events": [ { "kind": "visit"|"registered"|"trial"|"purchase"|"coin_purchase"|"refund",
              "label": "Регистрация", "who": "Ив*** ", "detail": "1 месяц · 100 ₽" | null, "at": "ISO" } ]
```

### Тестовая подписка по коду
`GET /api/me` — добавлено: `"trial_code": { "available": true, "reason": null, "used": false, "expires_at": null }`
(`available=false` + `reason` — почему нельзя; `used=true` — уже активирована, `expires_at` — до какого числа).

`POST /api/trial-code` тело `{ "code": "BOWIE2026" }` →
200 `{ "ok": true, "days": 5, "expires_at": "ISO", "message": "🎉 Код принят! …", "device": {...как раньше, с ссылками} }`
Ошибки: `bad_code` (нейтральное «Код не найден или больше не действует»), `already_used`, `has_subscription`,
`trial_used` (уже был обычный пробный период и правило запрещает), `too_many_attempts`, `vpn_failed`.
Код никогда не возвращается клиенту обратно и не хранится во фронтенде.

## Админ-панель (Bearer админ-токен)

`GET /api/admin/me.permissions` — новое право `trialcodes.manage` (владелец; администратор — только если владелец включил
настройку `trial_codes_admin_access`). Рефералы/промокоды: просмотр — `users.view`, изменение промокодов — `plans.manage`.

### Реферальная система
- `GET /api/admin/referrals` — добавлено `overview`:
  `{ "referrers": n, "visits": n, "registered": n, "buyers": n, "purchases": n, "paid_minor": n, "paid_label": "…", "coin_purchases": n, "refunds": n }`
  и `top`: `[{ user_id, user_name, username, public_id, registered, buyers, purchases, paid_minor, paid_label }]` (до 50).
  Старые поля `stats`, `rewards` сохранены.
- `GET /api/admin/referrals/user?q=<telegram id | @username | SV-ID>` →
  `{ "user": {id, name, username, public_id}, "link": "https://t.me/…?start=ref_…", "stats": {…как в Mini App…},
     "invitees": [{ user_id, name, username, registered_at, trial: bool, purchases, paid_label }],
     "orders": [{ id, user_id, plan_title, amount_label, currency, status, status_label, promo_code, created_at, paid_at }] }`
- `GET /api/admin/referrals/export.csv` — CSV (UTF-8 с BOM, `;`) по всем рефералам: referrer_id, username, invited, buyers, purchases, paid_rub. Скачивание через fetch + Blob (нужен заголовок Authorization).

### Промокоды
- `GET /api/admin/promos` — у каждого промокода добавлены поля
  `starts_at, expires_at, note, per_user_limit (всегда 1), stats: { activations, unique_users, orders, paid_orders, canceled_orders, refunded_orders, paid_minor, paid_label, discount_minor, discount_label, first_used_at, last_used_at }`
- `PATCH /api/admin/promos/{id}` тело `{ value?, max_activations? (null = без лимита), starts_at?, expires_at?, note?, reason }`
  (тип скидки менять нельзя; диапазоны как при создании; лимит не меньше уже использованных).
- `GET /api/admin/promos/{id}/orders` → `{ "orders": [{ id, user_id, plan_title, base_label, discount_label, amount_label, status, status_label, payment_id, created_at, paid_at }] }`
- Существующие `POST /promos`, `/enable`, `/disable`, `/activations` без изменений.

### Коды тестовых подписок (право `trialcodes.manage`)
- `GET /api/admin/trial-codes?q=&status=all|active|disabled|expired|exhausted|archived&sort=new|old` →
  `{ "codes": [{ id, code, name, description, note, days, max_activations, activations, starts_at, ends_at, is_active,
                 archived, status: "active"|"disabled"|"scheduled"|"expired"|"exhausted"|"archived", created_at, updated_at }],
     "settings": { "trial_code_after_trial": false, "trial_codes_admin_access": false } }`
- `POST /api/admin/trial-codes` `{ code? (пусто — сгенерировать), name, description?, note?, days=5, max_activations?, starts_at?, ends_at?, is_active=true, reason }`
- `POST /api/admin/trial-codes/bulk` `{ count (1–200), length (6–16), prefix?, name, days=5, max_activations?, starts_at?, ends_at?, is_active, reason }` → `{ "codes": [ …как в списке… ] }`
- `PATCH /api/admin/trial-codes/{id}` — любые из `name, description, note, days, max_activations, starts_at, ends_at, is_active` + `reason`
  (код менять нельзя; срок уже выданных подписок не меняется).
- `POST /api/admin/trial-codes/{id}/archive` `{reason}`; `DELETE /api/admin/trial-codes/{id}` `{reason}` — только если активаций не было (иначе 400 `has_activations`, предложить архив).
- `GET /api/admin/trial-codes/{id}/activations` → `{ "items": [{ user_id, name, username, status, days, activated_at, expires_at }] }`
- Настройки (владелец): `PUT /api/admin/settings/trial_code_after_trial` и `…/trial_codes_admin_access` `{ value: bool, reason }`.
