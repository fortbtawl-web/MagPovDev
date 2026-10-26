"""Application configuration loaded from environment variables (.env)."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    admin_ids: Annotated[frozenset[int], NoDecode] = frozenset()
    app_env: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite+aiosqlite:///./data/spirittvpn.db"
    redis_url: str | None = None
    display_timezone: str = "Europe/Moscow"

    # Legal documents: bump the version when the texts change to require re-acceptance.
    legal_docs_version: str = "2024-08-12"

    # Payments
    payment_provider: Literal["none", "telegram", "test"] = "none"
    payment_currency: str = "XTR"
    payment_provider_token: SecretStr | None = None
    payment_test_secret: SecretStr | None = None
    order_ttl_hours: int = Field(default=24, ge=1)

    # Plan prices in minor units (kopecks / cents; for XTR — whole stars). Empty = plan disabled.
    plan_1m_price: int | None = Field(default=None, ge=1)
    plan_3m_price: int | None = Field(default=None, ge=1)
    plan_6m_price: int | None = Field(default=None, ge=1)
    plan_12m_price: int | None = Field(default=None, ge=1)

    # VPN
    vpn_provider: Literal["none", "test", "marzban"] = "none"
    vpn_timeout_seconds: float = 10.0
    vpn_retry_attempts: int = Field(default=3, ge=1, le=10)
    vpn_max_sync_attempts: int = Field(default=20, ge=1)
    marzban_url: str | None = None
    marzban_username: str | None = None
    marzban_password: SecretStr | None = None
    marzban_proxies: str = "vless"
    marzban_username_prefix: str = "spiritt_"

    # Built-in HTTP server (payment webhooks, health check)
    web_enabled: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    public_base_url: str | None = None

    # Background jobs
    job_interval_seconds: int = Field(default=300, ge=10)
    reconcile_interval_seconds: int = Field(default=3600, ge=60)
    final_notice_hours: int = Field(default=3, ge=1, le=23)

    # Anti-flood: minimal interval between user actions
    throttle_seconds: float = 0.4
    broadcast_rate_per_second: float = Field(default=20.0, gt=0, le=30)

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return frozenset(int(x) for x in v.replace(";", ",").split(",") if x.strip())
        if isinstance(v, int):
            return frozenset({v})
        return v

    @field_validator("payment_currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _check_production(self) -> "Settings":
        if self.app_env == "production":
            if self.payment_provider == "test":
                raise ValueError("PAYMENT_PROVIDER=test is forbidden in production")
            if self.vpn_provider == "test":
                raise ValueError("VPN_PROVIDER=test is forbidden in production")
        if self.payment_provider == "test" and not self.payment_test_secret:
            raise ValueError("PAYMENT_TEST_SECRET is required for PAYMENT_PROVIDER=test")
        if self.payment_provider == "telegram" and self.payment_currency != "XTR" and not self.payment_provider_token:
            raise ValueError("PAYMENT_PROVIDER_TOKEN is required for fiat Telegram payments")
        if self.vpn_provider == "marzban" and not (self.marzban_url and self.marzban_username and self.marzban_password):
            raise ValueError("MARZBAN_URL, MARZBAN_USERNAME, MARZBAN_PASSWORD are required for VPN_PROVIDER=marzban")
        return self

    def is_admin(self, telegram_id: int | None) -> bool:
        return telegram_id is not None and telegram_id in self.admin_ids

    @property
    def plan_prices(self) -> dict[str, int | None]:
        return {"m1": self.plan_1m_price, "m3": self.plan_3m_price, "m6": self.plan_6m_price, "m12": self.plan_12m_price}


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
