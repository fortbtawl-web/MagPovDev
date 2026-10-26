import asyncio
from datetime import timedelta

from sqlalchemy import func, select

from app.database import Base, create_engine, create_session_factory
from app.models import PromoActivation, PromoCode, PromoKind, User
from app.services.promos import PromoError, activate_promo
from tests.conftest import NOW, make_user


async def add_promo(session, **kw) -> PromoCode:
    promo = PromoCode(**{"code": "BONUS7", "kind": PromoKind.DAYS, "value": 7, **kw})
    session.add(promo)
    await session.commit()
    return promo


async def test_days_promo_extends_subscription(session):
    user = await make_user(session)
    await add_promo(session)
    res = await activate_promo(session, user, " bonus7 ", NOW, vpn_ready=True)
    assert res.ok and res.subscription.expires_at == NOW + timedelta(days=7)
    assert res.subscription.vpn_sync_needed


async def test_invalid_and_unknown_codes(session):
    user = await make_user(session)
    assert (await activate_promo(session, user, "x", NOW, vpn_ready=True)).error == PromoError.INVALID_FORMAT
    assert (await activate_promo(session, user, "NOPE123", NOW, vpn_ready=True)).error == PromoError.NOT_FOUND
    assert (await activate_promo(session, user, "'; DROP TABLE users;--", NOW, vpn_ready=True)).error == PromoError.INVALID_FORMAT


async def test_same_promo_twice_rejected(session):
    user = await make_user(session)
    await add_promo(session)
    assert (await activate_promo(session, user, "BONUS7", NOW, vpn_ready=True)).ok
    res = await activate_promo(session, user, "BONUS7", NOW, vpn_ready=True)
    assert res.error == PromoError.ALREADY_USED
    assert await session.scalar(select(func.count(PromoActivation.id))) == 1
    assert (await session.scalar(select(PromoCode))).activations_count == 1


async def test_promo_rules(session):
    user = await make_user(session)
    await add_promo(session, code="OFF1", is_active=False)
    await add_promo(session, code="OLD1", valid_until=NOW - timedelta(days=1))
    await add_promo(session, code="SOON1", valid_from=NOW + timedelta(days=1))
    await add_promo(session, code="FULL1", max_activations=1, activations_count=1)
    await add_promo(session, code="VIP1", allowed_group="vip")
    await add_promo(session, code="ARCH1", is_archived=True)
    assert (await activate_promo(session, user, "OFF1", NOW, vpn_ready=True)).error == PromoError.INACTIVE
    assert (await activate_promo(session, user, "OLD1", NOW, vpn_ready=True)).error == PromoError.EXPIRED
    assert (await activate_promo(session, user, "SOON1", NOW, vpn_ready=True)).error == PromoError.NOT_STARTED
    assert (await activate_promo(session, user, "FULL1", NOW, vpn_ready=True)).error == PromoError.LIMIT
    assert (await activate_promo(session, user, "VIP1", NOW, vpn_ready=True)).error == PromoError.NOT_ALLOWED
    assert (await activate_promo(session, user, "ARCH1", NOW, vpn_ready=True)).error == PromoError.NOT_FOUND
    user.group = "vip"
    await session.commit()
    assert (await activate_promo(session, user, "VIP1", NOW, vpn_ready=True)).ok


async def test_days_promo_refused_without_vpn(session):
    user = await make_user(session)
    await add_promo(session)
    assert (await activate_promo(session, user, "BONUS7", NOW, vpn_ready=False)).error == PromoError.SERVICE_NOT_READY
    assert (await session.scalar(select(PromoCode))).activations_count == 0


async def test_activation_limit_holds_under_concurrency(tmp_path):
    # Real separate connections (file DB) so transactions are actually isolated.
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/c.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    async with session_factory() as s:
        for i in range(5):
            await make_user(s, tg_id=100 + i)
        s.add(PromoCode(code="ONLY2", kind=PromoKind.PERCENT, value=10, max_activations=2))
        await s.commit()

    async def attempt(tg):
        async with session_factory() as s:
            user = await s.scalar(select(User).where(User.telegram_id == tg))
            return (await activate_promo(s, user, "ONLY2", NOW, vpn_ready=True)).ok

    results = await asyncio.gather(*(attempt(100 + i) for i in range(5)))
    assert sum(results) == 2
    async with session_factory() as s:
        assert (await s.scalar(select(PromoCode))).activations_count == 2
        assert await s.scalar(select(func.count(PromoActivation.id))) == 2
    await engine.dispose()


async def test_second_discount_blocked_while_one_pending(session):
    user = await make_user(session)
    await add_promo(session, code="P10", kind=PromoKind.PERCENT, value=10)
    await add_promo(session, code="F50", kind=PromoKind.FIXED, value=5000)
    assert (await activate_promo(session, user, "P10", NOW, vpn_ready=True)).ok
    assert (await activate_promo(session, user, "F50", NOW, vpn_ready=True)).error == PromoError.DISCOUNT_PENDING
