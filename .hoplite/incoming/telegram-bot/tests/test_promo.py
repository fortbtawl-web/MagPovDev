"""Promo codes: activation rules, concurrency, discounts in orders, free days, admin management."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from backend.api.app import create_app
from backend.models import AuditLog, Order, PromoActivation, PromoCode
from backend.services import payments as pay_svc
from backend.services import promo as promo_svc
from backend.services import subscriptions as subs_svc
from backend.services.auth import issue_token
from backend.services.context import ServiceError
from backend.utils import utcnow
from tests.conftest import ADMIN_ID, make_user


async def new_promo(ctx, code="SALE20", kind="percent", value=20, **kw):
    return await promo_svc.create(ctx, ADMIN_ID, "web", code=code, kind=kind, value=value, reason="тестовый промокод", **kw)


def h(ctx, uid, role="user"):
    return {"Authorization": f"Bearer {issue_token(ctx.settings.session_secret.get_secret_value(), user_id=uid, role=role, ttl_minutes=5)}"}


async def test_percent_discount_applied_and_marked_used(ctx):
    await make_user(ctx, 1)
    await new_promo(ctx)
    res = await promo_svc.activate(ctx, 1, " sale20 ")
    assert res.kind == "percent" and res.discount_label == "−20%"
    order, pay = await pay_svc.create_order(ctx, 1, "m3")
    assert order.amount_minor == 20000 and order.discount_minor == 5000 and pay.amount_minor == 20000
    ctx.payments.settle(pay.provider_payment_id)
    assert await pay_svc.handle_state(ctx, "fake", await ctx.payments.get_payment(pay.provider_payment_id), source="webhook") == "paid"
    async with ctx.session() as s:
        act = await s.scalar(select(PromoActivation))
    assert act.status == "used" and act.order_id == order.id
    # The discount is gone for the next order.
    order2, _ = await pay_svc.create_order(ctx, 1, "dev1_30")
    assert order2.discount_minor == 0 and order2.amount_minor == 10000


async def test_fixed_discount_keeps_minimum(ctx):
    await make_user(ctx, 2)
    await new_promo(ctx, code="BIG", kind="fixed", value=50000)  # 500 ₽ off a 100 ₽ plan
    await promo_svc.activate(ctx, 2, "BIG")
    order, _ = await pay_svc.create_order(ctx, 2, "dev1_30")
    assert order.amount_minor == promo_svc.MIN_ORDER_MINOR and order.discount_minor == 9900


async def test_days_promo_creates_and_extends(ctx):
    await make_user(ctx, 3)
    await new_promo(ctx, code="FREE7", kind="days", value=7)
    res = await promo_svc.activate(ctx, 3, "FREE7")
    assert res.days == 7 and res.subscription.status == "active"
    assert abs((res.subscription.expires_at - utcnow()) - timedelta(days=7)) < timedelta(minutes=1)
    first = res.subscription.expires_at
    await new_promo(ctx, code="FREE3", kind="days", value=3)
    res = await promo_svc.activate(ctx, 3, "FREE3")
    assert res.subscription.expires_at - first == timedelta(days=3)
    async with ctx.session() as s:
        from backend.services import devices as dev_svc

        devices = await dev_svc.list_devices(s, 3)
    assert len(devices) == 1 and devices[0].status == "active"  # VPN key created and confirmed by the node


async def test_error_messages(ctx):
    await make_user(ctx, 4)
    with pytest.raises(ServiceError) as e:
        await promo_svc.activate(ctx, 4, "NOPE")
    assert e.value.code == "promo_not_found"
    with pytest.raises(ServiceError):
        await promo_svc.activate(ctx, 4, "'; DROP TABLE users;--")
    p = await new_promo(ctx, code="OLD", expires_at=utcnow() + timedelta(seconds=1))
    async with ctx.session() as s, s.begin():
        await s.execute(update(PromoCode).where(PromoCode.id == p.id).values(expires_at=utcnow() - timedelta(minutes=1)))
    with pytest.raises(ServiceError) as e:
        await promo_svc.activate(ctx, 4, "OLD")
    assert "истёк" in e.value.message
    p2 = await new_promo(ctx, code="OFF")
    await promo_svc.set_active(ctx, ADMIN_ID, "web", p2.id, False, "отключаем")
    with pytest.raises(ServiceError) as e:
        await promo_svc.activate(ctx, 4, "OFF")
    assert "отключён" in e.value.message
    await new_promo(ctx, code="ONCE", kind="days", value=1)
    await promo_svc.activate(ctx, 4, "ONCE")
    with pytest.raises(ServiceError) as e:
        await promo_svc.activate(ctx, 4, "ONCE")
    assert e.value.code == "promo_used"


async def test_activation_limit_under_concurrency(ctx):
    await new_promo(ctx, code="LIMIT3", kind="days", value=2, max_activations=3)
    for uid in range(100, 110):
        await make_user(ctx, uid)
    results = await asyncio.gather(*[promo_svc.activate(ctx, uid, "LIMIT3") for uid in range(100, 110)], return_exceptions=True)
    ok = [r for r in results if not isinstance(r, Exception)]
    assert len(ok) == 3
    assert all(isinstance(r, ServiceError) and r.code == "promo_exhausted" for r in results if isinstance(r, Exception))
    async with ctx.session() as s:
        promo = await s.scalar(select(PromoCode).where(PromoCode.code == "LIMIT3"))
        assert promo.activations == 3
        assert await s.scalar(select(func.count(PromoActivation.id))) == 3


async def test_same_user_parallel_activation_once(ctx):
    await make_user(ctx, 5)
    await new_promo(ctx, code="RACE", kind="days", value=5)
    results = await asyncio.gather(*[promo_svc.activate(ctx, 5, "RACE") for _ in range(5)], return_exceptions=True)
    assert sum(1 for r in results if not isinstance(r, Exception)) == 1
    async with ctx.session() as s:
        sub = await subs_svc.get_current(s, 5)
    assert abs((sub.expires_at - utcnow()) - timedelta(days=5)) < timedelta(minutes=1)


async def test_one_pending_discount_and_lifetime_rules(ctx):
    await make_user(ctx, 6)
    await new_promo(ctx, code="A10", value=10)
    await new_promo(ctx, code="B20", value=20)
    await promo_svc.activate(ctx, 6, "A10")
    with pytest.raises(ServiceError) as e:
        await promo_svc.activate(ctx, 6, "B20")
    assert e.value.code == "promo_has_discount"
    from backend.services import grants as grants_svc

    ctx.settings.owner_telegram_id = ADMIN_ID
    await grants_svc.grant(ctx, ADMIN_ID, "web", 6, mode="lifetime", reason="тест", notify=False)
    await new_promo(ctx, code="DAYS", kind="days", value=3)
    with pytest.raises(ServiceError) as e:
        await promo_svc.activate(ctx, 6, "DAYS")
    assert e.value.code == "lifetime"


async def test_admin_validation_and_audit(ctx):
    with pytest.raises(ServiceError):
        await new_promo(ctx, code="X1", value=95)
    with pytest.raises(ServiceError):
        await new_promo(ctx, code="bad code!")
    with pytest.raises(ServiceError):
        await promo_svc.create(ctx, ADMIN_ID, "web", code="NOREASON", kind="days", value=1, reason="")
    p = await new_promo(ctx, code=None, kind="days", value=1)
    assert p.code.startswith("SV") and len(p.code) == 10
    with pytest.raises(ServiceError):
        await new_promo(ctx, code=p.code.lower(), kind="days", value=1)
    async with ctx.session() as s:
        assert await s.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "promo.create")) == 1


async def test_promo_api(ctx):
    app = create_app(ctx, run_worker=False)
    await make_user(ctx, 7)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/admin/promos", headers=h(ctx, 7, "admin"), json={"kind": "days", "value": 3, "reason": "попытка"})
        assert r.status_code == 403
        r = await c.post("/api/admin/promos", headers=h(ctx, ADMIN_ID, "admin"), json={"code": "web15", "kind": "percent", "value": 15, "max_activations": 10, "reason": "акция"})
        assert r.status_code == 200 and r.json()["promo"]["code"] == "WEB15"
        pid = r.json()["promo"]["id"]
        r = await c.post("/api/promo/activate", headers=h(ctx, 7), json={"code": "web15"})
        assert r.status_code == 200 and "−15%" in r.json()["result"]["message"]
        assert (await c.get("/api/promo/active", headers=h(ctx, 7))).json()["discount"]["code"] == "WEB15"
        r = await c.post("/api/orders", headers=h(ctx, 7), json={"plan_code": "y1"})
        assert r.json()["discount_minor"] == 17250 and r.json()["amount_minor"] == 97750 and r.json()["promo_code"] == "WEB15"
        acts = (await c.get(f"/api/admin/promos/{pid}/activations", headers=h(ctx, ADMIN_ID, "admin"))).json()["activations"]
        assert acts[0]["user_id"] == 7 and acts[0]["status"] == "reserved"
        r = await c.post(f"/api/admin/promos/{pid}/disable", headers=h(ctx, ADMIN_ID, "admin"), json={"reason": "конец акции"})
        assert r.json()["promo"]["is_active"] is False
        # A disabled promo's reserved discount no longer applies.
        assert (await c.get("/api/promo/active", headers=h(ctx, 7))).json()["discount"] is None
    async with ctx.session() as s:
        o = await s.scalar(select(Order).where(Order.user_id == 7))
    assert o.promo_activation_id is not None
