"""YooKassa (ЮKassa) adapter — https://yookassa.ru/developers/api

- POST /v3/payments, GET /v3/payments/{id}, POST /v3/refunds; HTTP Basic auth shopId:secretKey;
  `Idempotence-Key` header on POST.
- Notifications are NOT signed by YooKassa. Authenticity is established by (1) the documented sender IP
  allowlist and (2) re-reading the payment via the API with our credentials; only that response is trusted.
"""
from __future__ import annotations

import ipaddress
import json
from decimal import Decimal
from typing import Any, Mapping

import httpx

from backend.integrations.payments.base import (
    CreatedPayment,
    PaymentError,
    PaymentState,
    WebhookEvent,
    WebhookRejected,
)

API_BASE = "https://api.yookassa.ru/v3"

# https://yookassa.ru/developers/using-api/webhooks — "IP-адреса ЮKassa"
YOOKASSA_NETWORKS = [
    ipaddress.ip_network(n)
    for n in (
        "185.71.76.0/27",
        "185.71.77.0/27",
        "77.75.153.0/25",
        "77.75.156.11/32",
        "77.75.156.35/32",
        "77.75.154.128/25",
        "2a02:5180::/32",
    )
]


def to_minor(value: str) -> int:
    return int((Decimal(value) * 100).to_integral_value())


def from_minor(minor: int) -> str:
    return f"{Decimal(minor) / 100:.2f}"


def ip_allowed(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in YOOKASSA_NETWORKS)


class YooKassaProvider:
    name = "yookassa"

    def __init__(
        self,
        shop_id: str,
        secret_key: str,
        *,
        return_url: str,
        verify_ip: bool = True,
        receipt_email: str | None = None,
        vat_code: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not shop_id or not secret_key:
            raise PaymentError("YooKassa: не заданы YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY")
        self._return_url = return_url
        self._verify_ip = verify_ip
        self._receipt_email = receipt_email
        self._vat_code = vat_code
        self._client = httpx.AsyncClient(
            base_url=API_BASE, auth=(shop_id, secret_key), timeout=httpx.Timeout(20.0, connect=10.0), transport=transport
        )

    async def _call(self, method: str, path: str, *, json_body: dict[str, Any] | None = None, idem: str | None = None) -> dict[str, Any]:
        headers = {"Idempotence-Key": idem} if idem else {}
        try:
            resp = await self._client.request(method, path, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise PaymentError(f"YooKassa недоступна: {type(exc).__name__}", retryable=True) from exc
        if resp.status_code >= 500:
            raise PaymentError(f"YooKassa: HTTP {resp.status_code}", retryable=True)
        try:
            data = resp.json()
        except ValueError as exc:
            raise PaymentError(f"YooKassa: некорректный ответ HTTP {resp.status_code}") from exc
        if resp.status_code >= 400:
            raise PaymentError(f"YooKassa: {data.get('code')} {data.get('description', '')}".strip())
        return data

    @staticmethod
    def _state(data: dict[str, Any]) -> PaymentState:
        amount = data.get("amount") or {}
        cancel = data.get("cancellation_details") or {}
        return PaymentState(
            provider_payment_id=str(data["id"]),
            status=str(data.get("status")),
            amount_minor=to_minor(str(amount.get("value", "0"))),
            currency=str(amount.get("currency", "")),
            paid=bool(data.get("paid")),
            metadata=dict(data.get("metadata") or {}),
            is_test=bool(data.get("test")),
            cancel_reason=cancel.get("reason"),
        )

    async def create_payment(
        self, *, amount_minor: int, currency: str, description: str, order_public_id: str, idempotency_key: str
    ) -> CreatedPayment:
        body: dict[str, Any] = {
            "amount": {"value": from_minor(amount_minor), "currency": currency},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": self._return_url},
            "description": description[:128],
            "metadata": {"order_id": order_public_id},
        }
        if self._receipt_email:
            # 54-ФЗ receipt; required if the shop has fiscalization enabled.
            body["receipt"] = {
                "customer": {"email": self._receipt_email},
                "items": [
                    {
                        "description": description[:128],
                        "quantity": "1.00",
                        "amount": {"value": from_minor(amount_minor), "currency": currency},
                        "vat_code": self._vat_code,
                        "payment_mode": "full_payment",
                        "payment_subject": "service",
                    }
                ],
            }
        data = await self._call("POST", "/payments", json_body=body, idem=idempotency_key)
        st = self._state(data)
        return CreatedPayment(
            provider_payment_id=st.provider_payment_id,
            status=st.status,
            confirmation_url=(data.get("confirmation") or {}).get("confirmation_url"),
            amount_minor=st.amount_minor,
            currency=st.currency,
            is_test=st.is_test,
        )

    async def get_payment(self, provider_payment_id: str) -> PaymentState:
        return self._state(await self._call("GET", f"/payments/{provider_payment_id}"))

    def parse_webhook(self, *, body: bytes, headers: Mapping[str, str], client_ip: str | None) -> WebhookEvent:
        if self._verify_ip and not ip_allowed(client_ip):
            raise WebhookRejected(f"IP {client_ip} не входит в список адресов ЮKassa")
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise WebhookRejected("Некорректный JSON") from exc
        if not isinstance(data, dict) or data.get("type") != "notification":
            raise WebhookRejected("Неизвестный формат уведомления")
        event = str(data.get("event") or "")
        obj = data.get("object") or {}
        if event.startswith("refund."):
            pid = obj.get("payment_id")
        else:
            pid = obj.get("id")
        if not event or not pid:
            raise WebhookRejected("В уведомлении нет события или ID платежа")
        return WebhookEvent(event=event, provider_payment_id=str(pid))

    async def refund(self, *, provider_payment_id: str, amount_minor: int, currency: str, idempotency_key: str) -> str:
        data = await self._call(
            "POST",
            "/refunds",
            json_body={"payment_id": provider_payment_id, "amount": {"value": from_minor(amount_minor), "currency": currency}},
            idem=idempotency_key,
        )
        return str(data.get("status"))

    async def close(self) -> None:
        await self._client.aclose()
