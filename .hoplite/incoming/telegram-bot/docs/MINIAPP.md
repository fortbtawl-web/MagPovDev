# Telegram Mini App: настройка

Mini App открывается **только по HTTPS**. Пока у backend нет публичного HTTPS-адреса, кнопка «🚀 Открыть SpirittVPN» в боте не показывается (бот проверяет, что `WEBAPP_URL` начинается с `https://`).

## 1. Публикация

Backend сам раздаёт собранный frontend:

- `https://ВАШ_ДОМЕН/` — Mini App;
- `https://ВАШ_ДОМЕН/admin` — админ-панель;
- `https://ВАШ_ДОМЕН/api/...` — API.

Соберите frontend (`cd frontend && pnpm install && pnpm build`) и настройте nginx и certbot по `docs/DEPLOY_LINUX.md`.

Если frontend нужно разместить на отдельном статическом хостинге (Netlify, Cloudflare Pages и т. п.):

1. Соберите его с `VITE_API_BASE=https://АДРЕС_BACKEND`.
2. На backend добавьте домен фронтенда в `CORS_ORIGINS`.

## 2. BotFather

1. `/mybots` → ваш бот → **Bot Settings → Menu Button → Configure menu button** → URL `https://ВАШ_ДОМЕН/` и название `SpirittVPN`. Бот при запуске сам вызывает `setChatMenuButton` с `WEBAPP_URL`, поэтому этот шаг можно пропустить.
2. Необязательно: **Bot Settings → Configure Mini App → Enable Mini App** и тот же URL. Тогда появится прямая ссылка `https://t.me/ВАШ_БОТ?startapp` и кнопка «Открыть» в профиле бота. Параметр `startapp=ref_КОД` обрабатывается как реферальный.
3. Необязательно: `/setdomain` → ваш домен.

## 3. `.env`

```
WEBAPP_URL=https://ВАШ_ДОМЕН
PUBLIC_BASE_URL=https://ВАШ_ДОМЕН
```

После изменения перезапустите бота и API.

## 4. Безопасность авторизации

- Frontend отправляет **сырой** `Telegram.WebApp.initData` на `POST /api/auth/telegram`.
- Backend проверяет HMAC-SHA256 по официальному алгоритму (ключ `HMAC("WebAppData", BOT_TOKEN)`) и возраст `auth_date` (`INITDATA_MAX_AGE_SECONDS`). Затем выдаёт короткоживущий JWT (`SESSION_TTL_MINUTES`).
- Пользователь определяется только по проверенному `initData`. `initDataUnsafe` для идентификации не используется.
- Токен бота и секреты во frontend не попадают.
- Админ-операции требуют отдельного токена с ролью `admin`. Telegram ID проверяется по `ADMIN_IDS` **при каждом запросе**.

## 5. Проверка

- **Телефон:** откройте бота → `/start` → «🚀 Открыть SpirittVPN» или кнопка меню слева от поля ввода.
- **Telegram Desktop:** то же. Для отладки включите `Settings → Advanced → Experimental settings → Enable webview inspecting`, после чего доступен правый клик → Inspect.
- **Обычный браузер:** приложение покажет «Откройте SpirittVPN через кнопку в Telegram-боте». Это ожидаемо: без `initData` авторизации нет, демо-режима тоже нет.

## 6. Админ-панель в браузере

1. Откройте `https://ВАШ_ДОМЕН/admin` и введите свой Telegram ID.
2. Бот пришлёт одноразовый код (действует 5 минут, 5 попыток, не больше 3 кодов за 10 минут).
3. Предварительно нажмите `/start` в боте, иначе Telegram не даст боту написать вам.

Администратор может открыть панель и из Mini App: «Профиль → Открыть админ-панель».

## Включение Mini App на хостинге бота

Mini App отдаётся самим API (порт 8080) вместе с админкой; в архив обновления уже входит собранный `frontend/dist`.

1. **Со своим доменом (постоянный адрес):** укажите `WEBAPP_URL=https://ваш-домен` в `.env`, домен должен проксироваться на порт API.
2. **Без домена:** задайте `WEBAPP_TUNNEL=cloudflared`. При запуске `run.py` скачает закреплённую версию cloudflared (проверка SHA-256), поднимет временный HTTPS-адрес `*.trycloudflare.com`, передаст его боту (кнопка Mini App в меню) и выведет в консоль строку «Mini App доступен по временному HTTPS-адресу». Адрес меняется при каждом запуске и не имеет гарантии работы, для постоянной работы нужен свой домен. Журнал: `logs/cloudflared.log`.

Платежи в Mini App не подключены (`PAYMENT_PROVIDER=none`).

## Лимит обращений в поддержку

Не больше `support_daily_limit` (по умолчанию 2) новых обращений за скользящие 24 часа на пользователя; закрытые тоже считаются. Изменить число: админка → «Настройки». Сбросить пользователю: бот `/admin` → карточка пользователя → «🎫 Сбросить лимит обращений» (или `POST /api/admin/users/{id}/support-limit-reset`). Сброс пишется в журнал аудита.
