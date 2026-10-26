import os
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_REAL")

from app.config import Settings  # noqa: E402
from app.database import Base  # noqa: E402
from app.models import Plan, User  # noqa: E402
from app.services.users import accept_terms, register_user  # noqa: E402
from app.vpn.mock import MockVPNService  # noqa: E402

ADMIN_ID = 1000
BOT_TOKEN = "123456:TEST-TOKEN"
NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
LEGAL = "test-v1"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None, bot_token="123456:TEST", admin_ids=str(ADMIN_ID), app_env="test",
        database_url="sqlite+aiosqlite://", payment_provider="test", payment_test_secret="s3cret",
        vpn_provider="test", legal_docs_version=LEGAL, payment_currency="RUB",
    )


@pytest_asyncio.fixture
async def session_factory():
    # TEST_DATABASE_URL=postgresql+asyncpg://... runs the suite against PostgreSQL.
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        engine = create_async_engine(url)
    else:
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture
def vpn() -> MockVPNService:
    return MockVPNService()


async def make_user(session, tg_id: int = 42, accepted: bool = True, referrer: int | None = None) -> User:
    user, _ = await register_user(session, tg_id, "tester", "Test", NOW, referrer)
    if accepted:
        await accept_terms(session, user, LEGAL, NOW)
    return user


async def make_plan(session, code="m1", price=29900, days=30, currency="RUB", devices=1, lifetime=False) -> Plan:
    plan = Plan(code=code, title="1 месяц", description="", duration_days=days, price=price, currency=currency,
                is_active=True, device_limit=devices, is_lifetime=lifetime)
    session.add(plan)
    await session.commit()
    return plan


async def trial(session, user, vpn, now=NOW):
    from app.services.trial import activate_trial

    return await activate_trial(session, user, vpn, LEGAL, now)


def client_of(vpn: MockVPNService, tg_id: int):
    return next((c for c in vpn.clients.values() if c.username.startswith(f"sp_{tg_id}_")), None)
