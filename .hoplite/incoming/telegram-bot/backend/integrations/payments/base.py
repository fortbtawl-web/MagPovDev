"""Payment provider interface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


class PaymentError(Exception):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class WebhookRejected(Exception):
    """Webhook failed authenticity checks."""


class PaymentStatus:
    PENDING = "pending"
    WAITING_FOR_CAPTURE = "waiting_for_capture"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"
    REFUNDED = "refunded"


@dataclass
class CreatedPayment:
    provider_payment_id: str
    status: str
    confirmation_url: str | None
    amount_minor: int
    currency: str
    is_test: bool = False


@dataclass
class PaymentState:
    provider_payment_id: str
    status: str
    amount_minor: int
    currency: str
    paid: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    is_test: bool = False
    cancel_reason: str | None = None


@dataclass
class WebhookEvent:
    event: str
    provider_payment_id: str


class PaymentProvider(Protocol):
    name: str

    async def create_payment(
        self,
        *,
        amount_minor: int,
        currency: str,
        description: str,
        order_public_id: str,
        idempotency_key: str,
    ) -> CreatedPayment: ...

    async def get_payment(self, provider_payment_id: str) -> PaymentState:
        """Authoritative server-side status; webhooks are only a trigger to call this."""

    def parse_webhook(self, *, body: bytes, headers: Mapping[str, str], client_ip: str | None) -> WebhookEvent:
        """Authenticate and parse a webhook. Raises WebhookRejected."""

    async def refund(self, *, provider_payment_id: str, amount_minor: int, currency: str, idempotency_key: str) -> str: ...

    async def close(self) -> None: ...
