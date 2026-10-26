"""Application configuration loaded from environment variables (.env)."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_ids(v: object) -> object:
    if isinstance(v, str):
        return frozenset(int(x) for x in v.replace(";", ",").split(",") if x.strip())
    if isinstance(v, int):
        return frozenset({v})
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    # Roles from env: OWNER_IDS → owner, ADMIN_IDS → admin. Other roles are managed in the admin panel.
    owner_ids: Annotated[frozenset[int], NoDecode] = frozenset()
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
    payment_currency: str = "RUB"
    payment_provider_token: SecretStr | None = None
    payment_test_secret: SecretStr | None = None
    order_ttl_hours: int = Field(default=24, ge=1)

    # Default catalog, seeded once (minor units: kopecks; whole stars for XTR). Later edited in the admin panel.
    plan_solo30_price: int = Field(default=10000, ge=1)
    plan_duo30_price: int = Field(default=20000, ge=1)
    plan_q3_price: int = Field(default=25000, ge=1)
    plan_q3_devices: int = Field(default=2, ge=1, le=20)
    plan_y1_price: int = Field(default=115000, ge=1)
    plan_y1_devices: int = Field(default=3, ge=1, le=20)
    plan_life_price: int = Field(default=500000, ge=1)
    plan_life_devices: int = Field(default=3, ge=1, le=20)
    trial_device_limit: int = Field(default=1, ge=1, le=5)

    # VPN: none (not connected) | mock (development only) | remnawave | marzban
    vpn_provider: Literal["none", "mock", "test", "remnawave", "marzban"] = "none"
    vpn_timeout_seconds: float = 10.0
    vpn_retry_attempts: int = Field(default=3, ge=1, le=10)
    vpn_max_sync_attempts: int = Field(default=20, ge=1)
    vpn_username_prefix: str = Field(default="sp", pattern=r"^[a-zA-Z0-9]{1,8}$")
    remnawave_url: str | None = None
    remnawave_token: SecretStr | None = None
    remnawave_squads: str = ""  # comma-separated internal squad UUIDs assigned to new clients
    remnawave_hwid_limit: int = Field(default=1, ge=0)  # per device-client; 0 = don't set
    remnawave_local: bool = False  # add X-Forwarded-* headers when calling the panel over plain HTTP inside a private network
    marzban_url: str | None = None
    marzban_username: str | None = None
    marzban_password: SecretStr | None = None
    marzban_proxies: str = "vless"

    # Built-in HTTP server (Mini App, API, payment webhooks, health check)
    web_enabled: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    public_base_url: str | None = None
    webapp_url: str | None = None  # defaults to PUBLIC_BASE_URL + /app/
    webapp_dist: str = "webapp/dist"
    init_data_ttl_seconds: int = Field(default=86400, ge=60)
    api_rate_per_minute: int = Field(default=120, ge=10)

    # Background jobs
    job_interval_seconds: int = Field(default=300, ge=10)
    reconcile_interval_seconds: int = Field(default=3600, ge=60)
    node_check_interval_seconds: int = Field(default=300, ge=30)
    final_notice_hours: int = Field(default=3, ge=1, le=23)

    throttle_seconds: float = 0.4
    broadcast_rate_per_second: float = Field(default=20.0, gt=0, le=30)

    @field_validator("owner_ids", "admin_ids", mode="before")
    @classmethod
    def _parse_ids(cls, v: object) -> object:
        return _parse_ids(v)

    @field_validator("payment_currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("vpn_provider")
    @classmethod
    def _alias(cls, v: str) -> str:
        return "mock" if v == "test" else v

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        if self.app_env == "production":
            if self.payment_provider == "test":
                raise ValueError("PAYMENT_PROVIDER=test is forbidden in production")
            if self.vpn_provider == "mock":
                raise ValueError("VPN_PROVIDER=mock is forbidden in production")
        if self.payment_provider == "test" and not self.payment_test_secret:
            raise ValueError("PAYMENT_TEST_SECRET is required for PAYMENT_PROVIDER=test")
        if self.payment_provider == "telegram" and self.payment_currency != "XTR" and not self.payment_provider_token:
            raise ValueError("PAYMENT_PROVIDER_TOKEN is required for fiat Telegram payments")
        if self.vpn_provider == "remnawave" and not (self.remnawave_url and self.remnawave_token):
            raise ValueError("REMNAWAVE_URL and REMNAWAVE_TOKEN are required for VPN_PROVIDER=remnawave")
        if self.vpn_provider == "marzban" and not (self.marzban_url and self.marzban_username and self.marzban_password):
            raise ValueError("MARZBAN_URL, MARZBAN_USERNAME, MARZBAN_PASSWORD are required for VPN_PROVIDER=marzban")
        return self

    @property
    def resolved_webapp_url(self) -> str | None:
        """Mini App URL; Telegram only opens HTTPS pages."""
        url = self.webapp_url or (f"{self.public_base_url.rstrip('/')}/app/" if self.public_base_url else None)
        return url if url and url.startswith("https://") else None

    def env_role(self, telegram_id: int | None) -> str | None:
        if telegram_id is None:
            return None
        if telegram_id in self.owner_ids:
            return "owner"
        if telegram_id in self.admin_ids:
            return "admin"
        return None

    def default_catalog(self) -> list[dict]:
        return [
            dict(code="solo30", title="Solo", description="1 устройство · 30 дней", duration_days=30,
                 device_limit=1, price=self.plan_solo30_price, is_lifetime=False, sort_order=1),
            dict(code="duo30", title="Duo", description="2 устройства · 30 дней", duration_days=30,
                 device_limit=2, price=self.plan_duo30_price, is_lifetime=False, sort_order=2),
            dict(code="q3", title="3 месяца", description="Выгоднее помесячной оплаты", duration_days=90,
                 device_limit=self.plan_q3_devices, price=self.plan_q3_price, is_lifetime=False, sort_order=3),
            dict(code="y1", title="1 год", description="Максимальная выгода", duration_days=365,
                 device_limit=self.plan_y1_devices, price=self.plan_y1_price, is_lifetime=False, sort_order=4),
            dict(code="life", title="Lifetime",
                 description="Без регулярных продлений — на всё время работы сервиса по условиям оферты",
                 duration_days=0, device_limit=self.plan_life_devices, price=self.plan_life_price,
                 is_lifetime=True, sort_order=5),
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
