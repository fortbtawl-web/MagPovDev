"""Payment provider abstraction. Business logic only consumes verified PaymentEventData."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from aiogram.types import LabeledPrice

from app.models import Order


class PaymentUnavailableError(Exception):
    pass


class InvalidWebhookError(Exception):
    """Notification failed authenticity checks; must be rejected without side effects."""


@dataclass(frozen=True)
class PaymentSession:
    kind: Literal["invoice", "url"]
    url: str | None = None
    provider_payment_id: str | None = None
    invoice: dict[str, Any] | None = None


@dataclass(frozen=True)
class PaymentEventData:
    provider: str
    event_id: str
    order_id: str
    success: bool
    amount: int | None
    currency: str | None
    is_test: bool = False


class PaymentProvider(ABC):
    name: str = "abstract"
    is_test: bool = False
    is_enabled: bool = True

    @abstractmethod
    async def create_payment(self, order: Order) -> PaymentSession: ...

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentEventData:
        raise InvalidWebhookError("webhooks are not supported by this provider")


class DisabledPaymentProvider(PaymentProvider):
    name = "none"
    is_enabled = False

    async def create_payment(self, order: Order) -> PaymentSession:
        raise PaymentUnavailableError


class TelegramPaymentProvider(PaymentProvider):
    """Official Telegram Payments: Stars (XTR, no token) or fiat via a provider connected in BotFather.

    Confirmation arrives as a `successful_payment` update through the authenticated Bot API channel.
    """

    name = "telegram"

    def __init__(self, currency: str, provider_token: str | None) -> None:
        self.currency = currency
        self.provider_token = provider_token or ""

    async def create_payment(self, order: Order) -> PaymentSession:
        return PaymentSession(
            kind="invoice",
            invoice={
                "title": f"SpirittVPN — {order.plan_title}"[:32],
                "description": f"Подписка SpirittVPN на {order.duration_days} дн. Заказ {order.id[:8]}",
                "payload": order.id,
                "currency": order.currency,
                "prices": [LabeledPrice(label=order.plan_title[:32], amount=order.amount)],
                "provider_token": self.provider_token,
            },
        )

    @staticmethod
    def event_from_successful_payment(sp: Any) -> PaymentEventData:
        return PaymentEventData(
            provider="telegram",
            event_id=sp.telegram_payment_charge_id,
            order_id=sp.invoice_payload,
            success=True,
            amount=sp.total_amount,
            currency=sp.currency,
        )


class TestPaymentProvider(PaymentProvider):
    """DEVELOPMENT ONLY. Simulated, clearly labelled test transactions signed with HMAC-SHA256."""

    name = "test"
    is_test = True
    SIGNATURE_HEADER = "X-Spiritt-Signature"

    def __init__(self, secret: str, public_base_url: str | None) -> None:
        self._secret = secret.encode()
        self._base = (public_base_url or "").rstrip("/")

    def sign(self, body: bytes) -> str:
        return hmac.new(self._secret, body, hashlib.sha256).hexdigest()

    def checkout_token(self, order_id: str) -> str:
        return self.sign(f"checkout:{order_id}".encode())[:32]

    async def create_payment(self, order: Order) -> PaymentSession:
        url = None
        if self._base:
            url = f"{self._base}/payments/test/checkout/{order.id}?token={self.checkout_token(order.id)}"
        return PaymentSession(kind="url", url=url, provider_payment_id=None)

    def build_event(self, order_id: str, success: bool, amount: int, currency: str) -> tuple[bytes, dict[str, str]]:
        body = json.dumps(
            {
                "event_id": f"test-{uuid.uuid4()}",
                "order_id": order_id,
                "status": "succeeded" if success else "failed",
                "amount": amount,
                "currency": currency,
                "test": True,
            }
        ).encode()
        return body, {self.SIGNATURE_HEADER: self.sign(body)}

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> PaymentEventData:
        signature = headers.get(self.SIGNATURE_HEADER, "")
        if not signature or not hmac.compare_digest(signature, self.sign(body)):
            raise InvalidWebhookError("bad signature")
        try:
            data = json.loads(body)
            return PaymentEventData(
                provider=self.name,
                event_id=str(data["event_id"]),
                order_id=str(data["order_id"]),
                success=data["status"] == "succeeded",
                amount=int(data["amount"]),
                currency=str(data["currency"]).upper(),
                is_test=True,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidWebhookError("malformed body") from exc


def build_payment_provider(settings) -> PaymentProvider:
    if settings.payment_provider == "telegram":
        token = settings.payment_provider_token.get_secret_value() if settings.payment_provider_token else None
        return TelegramPaymentProvider(settings.payment_currency, token)
    if settings.payment_provider == "test":
        return TestPaymentProvider(settings.payment_test_secret.get_secret_value(), settings.public_base_url)
    return DisabledPaymentProvider()
