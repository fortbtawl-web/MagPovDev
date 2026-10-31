from __future__ import annotations

from backend.config import Settings
from backend.integrations.payments.base import (
    CreatedPayment,
    PaymentError,
    PaymentProvider,
    PaymentState,
    PaymentStatus,
    WebhookEvent,
    WebhookRejected,
)


def build_payment_provider(settings: Settings) -> PaymentProvider | None:
    """Returns None when no provider is configured: purchases are then honestly unavailable."""
    name = settings.payment_provider.strip().lower()
    if name in ("", "none"):
        return None
    if name == "yookassa":
        from backend.integrations.payments.yookassa import YooKassaProvider

        return YooKassaProvider(
            settings.yookassa_shop_id,
            settings.yookassa_secret_key.get_secret_value(),
            return_url=settings.yookassa_return_url or settings.webapp_url or "https://t.me",
            verify_ip=settings.yookassa_verify_ip,
            receipt_email=settings.yookassa_receipt_email if settings.yookassa_receipt_enabled else None,
            vat_code=settings.yookassa_vat_code,
        )
    raise PaymentError(f"Неизвестный PAYMENT_PROVIDER={name!r}")


__all__ = [
    "build_payment_provider",
    "PaymentProvider",
    "PaymentError",
    "PaymentState",
    "PaymentStatus",
    "CreatedPayment",
    "WebhookEvent",
    "WebhookRejected",
]
