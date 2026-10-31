"""Application configuration. All secrets come from environment variables / .env."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """Accept a plain Neon/Postgres URL: add the asyncpg driver and translate libpq SSL options."""
    url = (url or "").strip().strip("\"'")
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
    if not url.startswith("postgresql+asyncpg://"):
        return url
    parts = urlsplit(url)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "sslmode":
            if value != "disable":
                query.append(("ssl", "require" if value in ("require", "prefer", "allow") else value))
        elif key != "channel_binding":
            query.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(query)))


@dataclass(frozen=True)
class NodeSpec:
    code: str
    name: str
    country_code: str
    api_url: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field("development", description="development | production | test")
    log_level: str = "INFO"

    # Telegram
    bot_token: SecretStr = SecretStr("")
    admin_ids: str = ""
    # Main administrator (numeric Telegram ID, never a username). Cannot be revoked from the panel.
    owner_telegram_id: int | None = None
    # Extra full owners, comma separated Telegram IDs (in addition to OWNER_TELEGRAM_ID).
    owner_ids: str = ""
    # Mandatory documents. Bump TERMS_VERSION to ask every user to accept again.
    terms_version: str = "2025-08-12"
    privacy_url: str = "https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99"
    terms_url: str = "https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15"
    # Mandatory channel subscription (bot must be an administrator of the channel).
    required_channel: str = "@spirit_vpn_news"
    required_channel_url: str = "https://t.me/spirit_vpn_news"
    channel_check_enabled: bool = True
    channel_cache_seconds: int = 300
    log_file: str = ""
    display_timezone: str = "Europe/Moscow"
    webapp_url: str = ""  # public HTTPS URL of the Mini App (frontend)
    webapp_tunnel: str = ""  # "cloudflared": temporary HTTPS address when there is no domain (backend/tunnel.py)
    support_url: str = ""  # e.g. https://t.me/your_support

    # Database
    database_url: str = "postgresql+asyncpg://spiritt:spiritt@localhost:5432/spiritt"
    db_pool_size: int = 10

    # HTTP API
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    public_base_url: str = ""  # public HTTPS URL of the backend (webhooks)
    cors_origins: str = ""
    session_secret: SecretStr = SecretStr("")
    session_ttl_minutes: int = 60
    admin_session_ttl_minutes: int = 120
    initdata_max_age_seconds: int = 86400
    serve_frontend: bool = True
    frontend_dist: str = "frontend/dist"
    run_worker: bool = True
    worker_interval_seconds: int = 60

    # Payments
    payment_provider: str = "none"  # yookassa | none
    yookassa_shop_id: str = ""
    yookassa_secret_key: SecretStr = SecretStr("")
    yookassa_return_url: str = ""
    yookassa_verify_ip: bool = True
    yookassa_receipt_enabled: bool = False
    yookassa_receipt_email: str = ""
    yookassa_vat_code: int = 1
    trust_forwarded_for: bool = False

    # VPN (H1//VLESS nodes). Format: code|Name|CC|https://host:port/api;code2|...
    vpn_nodes: str = ""
    vpn_verify_tls: bool = True
    vpn_timeout_seconds: float = 20.0
    vpn_block_on_dead_endpoints: bool = False
    # Virtual location "all": one subscription over the hub's federation (see integrations/vpn/h1federated.py).
    vpn_all_enabled: bool = True
    vpn_all_hub: str = "de"

    @field_validator("app_env")
    @classmethod
    def _env(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"development", "production", "test"}:
            raise ValueError("APP_ENV must be development, production or test")
        return v

    @field_validator("database_url", mode="before")
    @classmethod
    def _db_url(cls, v):
        return normalize_database_url(v) if isinstance(v, str) else v

    @field_validator("owner_telegram_id", mode="before")
    @classmethod
    def _owner(cls, v):
        if v in (None, ""):
            return None
        return int(v)

    @property
    def owner_id_set(self) -> frozenset[int]:
        result: set[int] = {self.owner_telegram_id} if self.owner_telegram_id else set()
        for part in self.owner_ids.replace(";", ",").split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                result.add(int(part))
        return frozenset(result)

    @property
    def admin_id_set(self) -> frozenset[int]:
        """Environment-defined admins (ADMIN_IDS + OWNER_TELEGRAM_ID). DB admins: services/roles.py."""
        result: set[int] = set(self.owner_id_set)
        for part in self.admin_ids.replace(";", ",").split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                result.add(int(part))
        return frozenset(result)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def node_specs(self) -> list[NodeSpec]:
        specs: list[NodeSpec] = []
        for chunk in self.vpn_nodes.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = [p.strip() for p in chunk.split("|")]
            if len(parts) != 4:
                raise ValueError(f"VPN_NODES: ожидается code|Название|CC|URL, получено: {chunk!r}")
            code, name, cc, url = parts
            if not code.isidentifier():
                raise ValueError(f"VPN_NODES: недопустимый код ноды {code!r}")
            specs.append(NodeSpec(code=code.lower(), name=name, country_code=cc.upper(), api_url=url.rstrip("/")))
        hub = self.vpn_all_hub.strip().lower()
        if self.vpn_all_enabled and any(sp.code == hub for sp in specs) and not any(sp.code == "all" for sp in specs):
            specs.append(NodeSpec(code="all", name="Все локации (автовыбор)", country_code="ZZ", api_url=f"federation://{hub}"))
        return specs

    @staticmethod
    def node_token(code: str) -> str:
        """Per-node API token: VPN_NODE_<CODE>_TOKEN (read straight from the environment)."""
        import os

        key = f"VPN_NODE_{code.upper()}_TOKEN"
        if os.environ.get(key):
            return os.environ[key]
        # pydantic-settings does not export .env into os.environ, so read the file as well.
        from dotenv import dotenv_values

        return str(dotenv_values(".env").get(key) or "")


def missing_required(settings: Settings, component: str) -> list[str]:
    """Human-readable list of missing/invalid required variables for 'bot' | 'api' | 'all'."""
    problems: list[str] = []
    if component in ("bot", "all") and not settings.bot_token.get_secret_value():
        problems.append("BOT_TOKEN — токен бота от @BotFather")
    if component == "api" and not settings.bot_token.get_secret_value():
        problems.append("BOT_TOKEN — токен бота от @BotFather")
    if not settings.database_url or "CHANGE_ME" in settings.database_url:
        problems.append("DATABASE_URL — строка подключения PostgreSQL (postgresql+asyncpg://...)")
    if component in ("api", "all") and len(settings.session_secret.get_secret_value()) < 32:
        problems.append("SESSION_SECRET — случайная строка не короче 32 символов")
    return list(dict.fromkeys(problems))


def config_warnings(settings: Settings) -> list[str]:
    warnings: list[str] = []
    if not settings.owner_telegram_id and not settings.admin_id_set:
        warnings.append("OWNER_TELEGRAM_ID не задан — владельца можно назначить командой /setup (код будет в логе бота)")
    if not settings.webapp_url.startswith("https://"):
        warnings.append("WEBAPP_URL не задан или не HTTPS — кнопка Mini App в боте скрыта")
    if settings.payment_provider in ("", "none"):
        warnings.append("PAYMENT_PROVIDER=none — покупка тарифов отключена")
    if not settings.vpn_nodes:
        warnings.append("VPN_NODES пуст — выдача VPN-ключей невозможна")
    return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
