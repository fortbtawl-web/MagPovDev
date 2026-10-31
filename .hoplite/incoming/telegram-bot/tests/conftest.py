from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.config import Settings
from backend.database import make_engine, make_sessionmaker
from backend.integrations.vpn import VpnRegistry
from backend.services import devices as dev_svc
from backend.services import referrals as ref_svc
from backend.services.context import AppContext
from tests.fakes import FakeChannelChecker, FakePayments, FakeVpnNode, RecordingNotifier

ROOT = Path(__file__).resolve().parents[1]
TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://spiritt:spiritt@localhost:5432/spiritt_test")
ADMIN_ID = 1000

PLANS_SQL = """
INSERT INTO plans (code, title, description, price_minor, currency, duration_days, is_lifetime, device_limit, sort_order) VALUES
('dev1_30','1 устройство','',10000,'RUB',30,false,1,10),
('dev2_30','2 устройства','',20000,'RUB',30,false,2,20),
('m3','3 месяца','',25000,'RUB',90,false,1,30),
('y1','1 год','',115000,'RUB',365,false,1,40),
('lifetime','Lifetime','',500000,'RUB',NULL,true,1,50)
"""


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "database" / "migrations"))
    cfg.attributes["database_url"] = TEST_DB
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
async def ctx():
    engine = make_engine(TEST_DB, 20)
    async with engine.begin() as conn:
        tables = (await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))).scalars().all()
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        await conn.execute(text(PLANS_SQL))
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=TEST_DB,
        bot_token="123456:TEST_TOKEN",
        admin_ids=str(ADMIN_ID),
        session_secret="t" * 40,
        vpn_nodes="fi|Финляндия|FI|https://fi.node.test/api;de|Германия|DE|https://de.node.test/api",
        # The fake registry has no federated "all" node; tests target the concrete nodes.
        vpn_all_enabled=False,
        payment_provider="none",
    )
    nodes = {"fi": FakeVpnNode("fi"), "de": FakeVpnNode("de")}
    c = AppContext(
        settings=settings,
        sessionmaker=make_sessionmaker(engine),
        vpn=VpnRegistry(nodes),
        payments=FakePayments(),
        notifier=RecordingNotifier(),
    )
    c.extra["nodes"] = nodes
    c.extra["channel_checker"] = FakeChannelChecker()
    c.extra["bot_username"] = "SpirittTestBot"
    await dev_svc.ensure_nodes(c)
    yield c
    await engine.dispose()


async def make_user(ctx: AppContext, uid: int, start: str | None = None, name: str = "Иван", *, accept_terms: bool = True):
    async with ctx.session() as session, session.begin():
        reg = await ref_svc.register_user(session, ref_svc.TgProfile(uid, f"user{uid}", name), start)
        if accept_terms and reg.user.terms_version is None:
            reg.user.terms_version = ctx.settings.terms_version
    return reg
