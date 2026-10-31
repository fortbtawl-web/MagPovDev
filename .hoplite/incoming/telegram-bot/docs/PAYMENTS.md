# Платежи

## Выбранный провайдер: ЮKassa

Документация: <https://yookassa.ru/developers/api>, уведомления: <https://yookassa.ru/developers/using-api/webhooks>.

| Операция | Реализация (`backend/integrations/payments/yookassa.py`) |
|---|---|
| Создание платежа | `POST https://api.yookassa.ru/v3/payments`, Basic-авторизация `shopId:secretKey`, заголовок `Idempotence-Key` = UUID заказа, `capture: true`, `confirmation.type = redirect`, `metadata.order_id` |
| Проверка статуса | `GET /v3/payments/{id}` |
| Подлинность webhook | см. ниже |
| Успешный платёж | `payment.succeeded` → повторное чтение платежа через API → проверка суммы, валюты и `metadata.order_id` → заказ `paid` → подписка → выдача VPN |
| Отмена | `payment.canceled` → заказ `canceled` |
| Возврат | `POST /v3/refunds` (метод адаптера). Событие `refund.succeeded` переводит заказ в `refunded` и уведомляет админов. Подписка не отзывается автоматически, решение за администратором |

### Проверка подлинности уведомлений

**ЮKassa не подписывает уведомления.** В документации нет HMAC-подписи, поэтому подлинность проверяется двумя способами, как рекомендует ЮKassa:

1. IP отправителя должен входить в официальный список: `185.71.76.0/27`, `185.71.77.0/27`, `77.75.153.0/25`, `77.75.156.11`, `77.75.156.35`, `77.75.154.128/25`, `2a02:5180::/32`. Если backend стоит за nginx, включите `TRUST_FORWARDED_FOR=true` и передавайте `X-Forwarded-For`.
2. **Тело уведомления не используется как доказательство оплаты.** По ID из уведомления backend сам запрашивает платёж в API ЮKassa со своими ключами и применяет только этот ответ.

### Идемпотентность

- `payments(provider, provider_payment_id)` уникален.
- Заказ блокируется (`SELECT … FOR UPDATE`) на всё время обработки.
- Повторный `succeeded` для уже оплаченного заказа возвращает `duplicate`.
- `subscription_events.order_id` уникален: один заказ физически нельзя применить к подписке дважды.
- `payment_events(provider, payment_id, event)` хранит журнал обработанных событий.
- Есть резервный путь: если webhook потерялся, воркер раз в минуту опрашивает ожидающие платежи.

### Тестовые платежи

В тестовом магазине ЮKassa платёж приходит с `test: true`. При `APP_ENV=production` такие платежи **не дают доступа**: заказ помечается `test_rejected`, админы получают уведомление.

## Что нужно от владельца

1. Магазин в ЮKassa (договор с самозанятым, ИП или ООО). **Сам бот ничего не регистрирует и ничего не покупает.**
2. `YOOKASSA_SHOP_ID` и `YOOKASSA_SECRET_KEY` из личного кабинета (раздел «Интеграция → Ключи API»).
3. В кабинете ЮKassa, «Интеграция → HTTP-уведомления»: URL `https://ВАШ_ДОМЕН/api/payments/webhook/yookassa`, события `payment.succeeded`, `payment.canceled`, `refund.succeeded`.
4. Если включена фискализация (54-ФЗ): `YOOKASSA_RECEIPT_ENABLED=true`, `YOOKASSA_RECEIPT_EMAIL`, `YOOKASSA_VAT_CODE`.
5. `PAYMENT_PROVIDER=yookassa`.

Пока провайдер не настроен (`PAYMENT_PROVIDER=none`), бот и Mini App честно пишут «Оплата временно недоступна», и заказы не создаются.

## Добавление другого провайдера

Реализуйте протокол `PaymentProvider` (`backend/integrations/payments/base.py`): `create_payment`, `get_payment`, `parse_webhook`, `refund`. Затем зарегистрируйте его в `build_payment_provider`. Бизнес-логика в `backend/services/payments.py` не зависит от провайдера.
