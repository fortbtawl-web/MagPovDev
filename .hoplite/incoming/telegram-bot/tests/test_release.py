"""Release checks: mandatory channel gate, channel tasks without membership checks, retired review task,
broadcasts with flood control, maintenance notices, connection wizard."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage
from sqlalchemy import select, text

from backend.models import AuditLog, Broadcast, Task, TaskClaim, User
from backend.services import access
from backend.services import broadcast as bc
from backend.services import coins as coins_svc
from backend.services import tasks as tasks_svc
from backend.services.access import ChannelCheckError
from backend.services.context import ServiceError
from tests.conftest import ADMIN_ID, make_user
from tests.test_v2 import client, h, owner_ctx  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[1]


def gate_ctx(ctx):
    ctx.settings.channel_cache_seconds = 0  # every action re-asks Telegram
    return ctx


# ---------------- mandatory channel ----------------

async def test_channel_gate_lifecycle(ctx):
    gate_ctx(ctx)
    checker = ctx.extra["channel_checker"]
    await make_user(ctx, 501)
    checker.statuses[501] = ("left", None)
    st = await access.gate_state(ctx, 501, force_channel=True)
    assert st.terms_accepted and not st.allowed
    with pytest.raises(access.AccessDenied) as e:
        await access.require_access(ctx, 501)
    assert e.value.code == "channel_required"
    checker.statuses[501] = ("member", None)  # subscribed later: re-check opens access, no re-registration
    assert (await access.gate_state(ctx, 501, force_channel=True)).allowed
    checker.statuses[501] = ("left", None)  # unsubscribed: next protected action is blocked again
    assert not (await access.gate_state(ctx, 501)).allowed


async def test_channel_gate_never_fakes_success_on_telegram_errors(ctx):
    gate_ctx(ctx)
    await make_user(ctx, 502)
    ctx.extra["channel_checker"].error = ChannelCheckError("TelegramRetryAfter: flood")
    st = await access.gate_state(ctx, 502, force_channel=True)
    assert not st.allowed and st.channel_error
    ctx.extra.pop("channel_checker")  # no bot client at all
    assert not (await access.gate_state(ctx, 502, force_channel=True)).allowed


async def test_miniapp_reads_are_gated_too(owner_ctx, client):  # noqa: F811
    gate_ctx(owner_ctx)
    await make_user(owner_ctx, 503)
    owner_ctx.extra["channel_checker"].statuses[503] = ("left", None)
    for path in ("/api/devices", "/api/tasks", "/api/coins", "/api/referrals", "/api/orders", "/api/subscription/history"):
        r = await client.get(path, headers=h(owner_ctx, 503))
        assert r.status_code == 403 and r.json()["code"] == "channel_required", path
    assert (await client.get("/api/me", headers=h(owner_ctx, 503))).json()["gate"]["allowed"] is False
    owner_ctx.extra["channel_checker"].statuses[503] = ("member", None)
    assert (await client.get("/api/devices", headers=h(owner_ctx, 503))).status_code == 200


async def test_bot_middleware_blocks_unsubscribed_users(ctx):
    from datetime import datetime

    from aiogram.types import CallbackQuery, Chat, Message
    from aiogram.types import User as TgUser

    from bot.middlewares import GateMiddleware

    gate_ctx(ctx)
    await make_user(ctx, 504)
    ctx.extra["channel_checker"].statuses[504] = ("kicked", None)
    called = []
    shown = []

    async def handler(event, data):
        called.append(event)

    import bot.handlers.gate as gate_mod

    async def fake_show_gate(event, c, st):
        shown.append(st)

    orig = gate_mod.show_gate
    gate_mod.show_gate = fake_show_gate
    try:
        u = TgUser(id=504, is_bot=False, first_name="x")
        cb = CallbackQuery(id="1", from_user=u, chat_instance="c", data="nav:plans")
        msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=504, type="private"), from_user=u, text="/cabinet")
        for ev in (cb, msg):
            await GateMiddleware()(handler, ev, {"ctx": ctx})
        assert called == [] and len(shown) == 2
        ctx.extra["channel_checker"].statuses[504] = ("member", None)
        await GateMiddleware()(handler, cb, {"ctx": ctx})
        assert len(called) == 1
    finally:
        gate_mod.show_gate = orig


# ---------------- channel tasks ----------------

async def _task(ctx, **kw) -> int:
    values = {"title": "Канал партнёра", "url": "https://t.me/partner_channel", "reward": 40, "verify": "telegram", "chat_id": "@partner_channel", "is_active": True}
    values.update(kw)
    return (await tasks_svc.create(ctx, ADMIN_ID, "web", values, "тестовое задание")).id


async def test_channel_task_rewards_without_membership_check_once(ctx):
    await make_user(ctx, 510)

    class Boom:
        async def status(self, chat, user):  # must never be called for task channels
            raise AssertionError("membership must not be checked")

    ctx.extra["member_checker"] = Boom()
    tid = await _task(ctx)
    listed = {t["id"]: t for t in await tasks_svc.list_for_user(ctx, 510)}
    assert listed[tid]["status"] == "available" and listed[tid]["url"] == "https://t.me/partner_channel"
    results = await asyncio.gather(*[tasks_svc.claim(ctx, 510, tid) for _ in range(6)], return_exceptions=True)
    assert all(getattr(r, "status", None) == "rewarded" for r in results)
    async with ctx.session() as s:
        assert await coins_svc.balance(s, 510) == 40
        assert (await s.scalar(select(Task).where(Task.id == tid))).completions == 1


# ---------------- retired review task ----------------

def _migration():
    spec = importlib.util.spec_from_file_location("m0007", ROOT / "database/migrations/versions/0007_broadcasts_and_review_task.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_review_task_archived_without_losing_history(ctx):
    await make_user(ctx, 520)
    await make_user(ctx, 521)
    review = await _task(ctx, title="Оставить отзыв о нашем VPN", verify="manual", reward=300)
    other = await _task(ctx, title="Канал друзей", reward=10)
    await tasks_svc.claim(ctx, 520, review)  # pending
    async with ctx.session() as s, s.begin():  # someone already rewarded earlier: history must stay
        s.add(TaskClaim(task_id=review, user_id=521, status="rewarded"))
    # Same statements as migration 0007 (alembic already ran it on the empty schema).
    m = _migration()
    async with ctx.session() as s, s.begin():
        for sql in (m.REJECT_PENDING_REVIEW_CLAIMS, m.ARCHIVE_REVIEW_TASKS):
            await s.execute(text(sql))
    ids = [t["id"] for t in await tasks_svc.list_for_user(ctx, 520)]
    assert review not in ids and other in ids
    async with ctx.session() as s:
        claims = {c.user_id: c for c in (await s.execute(select(TaskClaim).where(TaskClaim.task_id == review))).scalars()}
        assert claims[520].status == "rejected" and claims[521].status == "rewarded"
    with pytest.raises(ServiceError):
        await tasks_svc.claim(ctx, 520, review)


async def test_no_reward_for_archived_task_review(ctx):
    await make_user(ctx, 522)
    tid = await _task(ctx, title="Ручное", verify="manual")
    await tasks_svc.claim(ctx, 522, tid)
    await tasks_svc.archive(ctx, ADMIN_ID, "web", tid, "снято")
    async with ctx.session() as s:
        cid = (await s.scalar(select(TaskClaim.id).where(TaskClaim.task_id == tid)))
    with pytest.raises(ServiceError):
        await tasks_svc.review(ctx, ADMIN_ID, "web", cid, approve=True, reason="одобрить")
    async with ctx.session() as s:
        assert await coins_svc.balance(s, 522) == 0


# ---------------- broadcasts ----------------

class FakeBot:
    def __init__(self, blocked=(), flood_once=()):
        self.sent: list[tuple[int, str, object]] = []
        self.blocked, self.flood = set(blocked), set(flood_once)

    async def send_message(self, chat_id, text, parse_mode="x", **kw):
        m = SendMessage(chat_id=chat_id, text=text)
        if chat_id in self.flood:
            self.flood.discard(chat_id)
            raise TelegramRetryAfter(method=m, message="Flood control exceeded", retry_after=0)
        if chat_id in self.blocked:
            raise TelegramForbiddenError(method=m, message="Forbidden: bot was blocked by the user")
        self.sent.append((chat_id, text, parse_mode))


@pytest.fixture
def bot_ctx(ctx, monkeypatch):
    monkeypatch.setattr(bc, "SEND_INTERVAL", 0)
    ctx.notifier.bot = FakeBot(blocked={602}, flood_once={603})
    return ctx


async def test_broadcast_all_with_flood_control_and_blocked_users(bot_ctx):
    for uid in (601, 602, 603, 604):
        await make_user(bot_ctx, uid)
    async with bot_ctx.session() as s, s.begin():
        (await s.get(User, 604)).is_banned = True
    body = "Строка 1\nСтрока 2 <b>не HTML</b>"
    b = await bc.create(bot_ctx, admin_id=ADMIN_ID, source="web", kind="all", text_=body, reason="новость")
    assert b.total == 3
    with pytest.raises(ServiceError) as e:  # second mass send while one is queued
        await bc.create(bot_ctx, admin_id=ADMIN_ID, source="web", kind="all", text_="ещё", reason="дубль")
    assert e.value.code == "broadcast_running"
    await bc.run_job(bot_ctx, b.id)
    async with bot_ctx.session() as s:
        b = await s.get(Broadcast, b.id)
        assert (b.status, b.sent, b.blocked, b.failed) == ("done", 2, 1, 0)
        assert await s.scalar(select(AuditLog.id).where(AuditLog.action == "broadcast.all"))
    sent = bot_ctx.notifier.bot.sent
    assert sorted(u for u, _, _ in sent) == [601, 603] and all(t == body and pm is None for _, t, pm in sent)
    await bc.run_job(bot_ctx, b.id)  # finished job is not sent again
    assert len(bot_ctx.notifier.bot.sent) == 2


async def test_broadcast_single_and_validation(bot_ctx):
    await make_user(bot_ctx, 610)
    with pytest.raises(ServiceError):
        await bc.create(bot_ctx, admin_id=ADMIN_ID, source="web", kind="single", text_="привет", reason="тест", target_user_id=999999)
    with pytest.raises(ServiceError):
        await bc.create(bot_ctx, admin_id=ADMIN_ID, source="web", kind="single", text_="   ", reason="тест", target_user_id=610)
    b = await bc.create(bot_ctx, admin_id=ADMIN_ID, source="web", kind="single", text_="Ответ по заявке", reason="тест", target_user_id=610)
    await bc.run_job(bot_ctx, b.id)
    assert bot_ctx.notifier.bot.sent == [(610, "Ответ по заявке", None)]


async def test_broadcast_resumes_after_restart_without_duplicates(bot_ctx):
    for uid in (620, 621, 622):
        await make_user(bot_ctx, uid)
    b = await bc.create(bot_ctx, admin_id=ADMIN_ID, source="web", kind="all", text_="x", reason="тест")
    async with bot_ctx.session() as s, s.begin():  # simulate a crash after the first user
        row = await s.get(Broadcast, b.id)
        row.status, row.cursor_user_id, row.sent = "running", 620, 1
    await bc.run_job(bot_ctx, b.id)
    assert [u for u, _, _ in bot_ctx.notifier.bot.sent] == [621, 622]


async def test_broadcast_api_is_admin_only(owner_ctx, client):  # noqa: F811
    await make_user(owner_ctx, 630)
    body = {"mode": "all", "text": "всем", "reason": "попытка"}
    assert (await client.post("/api/admin/broadcasts", json=body, headers=h(owner_ctx, 630))).status_code in (401, 403)
    assert (await client.post("/api/admin/broadcasts", json=body, headers=h(owner_ctx, 630, role="admin"))).status_code in (401, 403)
    r = await client.post("/api/admin/broadcasts", json=body, headers=h(owner_ctx, ADMIN_ID, role="admin"))
    assert r.status_code == 200 and r.json()["status"] == "queued"


# ---------------- maintenance ----------------

async def test_maintenance_announce_then_auto_restore_once(bot_ctx):
    await make_user(bot_ctx, 640)
    state, b = await bc.announce_maintenance(bot_ctx, ADMIN_ID, "web", bc.TEMPLATE_DOWN, auto_restore=True,
                                             restore_text="Снова работаем", reason="обновление хостинга")
    assert state.active and b.kind == "maintenance_down"
    await bc.run_job(bot_ctx, b.id)
    restored = await bc.on_startup_verified(bot_ctx)
    assert restored is not None and restored.kind == "maintenance_up" and restored.text == "Снова работаем"
    assert await bc.on_startup_verified(bot_ctx) is None  # consumed: a second start doesn't repeat it
    assert not (await bc.get_maintenance(bot_ctx)).active


async def test_maintenance_without_auto_restore_sends_nothing_on_start(bot_ctx):
    await make_user(bot_ctx, 641)
    await bc.announce_maintenance(bot_ctx, ADMIN_ID, "web", "Скоро вернёмся", auto_restore=False, restore_text=None, reason="обновление")
    assert await bc.on_startup_verified(bot_ctx) is None
    assert (await bc.get_maintenance(bot_ctx)).active  # stays announced until an admin restores/cancels


# ---------------- wizard ----------------

def test_wizard_offers_only_supported_apps():
    from bot.handlers.wizard import APPS, apps_for

    assert apps_for("linux") == ["hiddify"]
    assert "happ" in apps_for("windows") and "v2raytun" not in apps_for("windows")
    for code, (_, links) in APPS.items():
        assert all(u.startswith("https://") for u in links.values()), code


async def test_wizard_uses_only_own_link(ctx):
    from backend.services import grants as grants_svc
    from bot.handlers.wizard import _own_link

    await make_user(ctx, 650)
    await make_user(ctx, 651)
    await grants_svc.grant(ctx, ADMIN_ID, "web", 650, mode="extend", amount=30, unit="days", reason="выдача")
    mine = await _own_link(ctx, 650)
    assert mine and await _own_link(ctx, 651) is None


def test_wizard_every_combination_has_six_valid_steps():
    from html.parser import HTMLParser

    from bot.handlers.wizard import APPS, DEVICES, STEPS_TOTAL, _step_lines, apps_for

    class Balance(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack: list[str] = []

        def handle_starttag(self, tag, attrs):
            self.stack.append(tag)

        def handle_endtag(self, tag):
            assert self.stack and self.stack.pop() == tag

    for device in DEVICES:
        for app in apps_for(device):
            assert device in APPS[app][1]
            for link in (None, "https://sub.example/u/abc"):
                for n in range(1, STEPS_TOTAL + 1):
                    text = "\n".join(_step_lines(n, device, app, link))
                    assert text and len(text) < 3500, (device, app, n)
                    p = Balance()
                    p.feed(text)
                    assert not p.stack, (device, app, n)
    # Device-specific content really differs: Hiddify on Windows explains VPN mode, on iOS it does not.
    win = " ".join(_step_lines(4, "windows", "hiddify", None))
    ios = " ".join(_step_lines(4, "ios", "hiddify", None))
    assert "администратора" in win and "администратора" not in ios
    assert "Subscription group setting" in " ".join(_step_lines(3, "android", "v2rayng", None))


def test_welcome_caption_fits_telegram_photo_limit(ctx):
    import html
    import re

    from bot.handlers.gate import brand_header, welcome_text

    ctx.settings.vpn_nodes = "de|Германия|DE|https://a/api;fi|Финляндия|FI|https://b/api;us|США|US|https://c/api;nl|Нидерланды|NL|https://d/api"
    for text in (welcome_text(ctx), brand_header(ctx) + "\n\n" + "x" * 400):
        assert len(html.unescape(re.sub(r"<[^>]+>", "", text))) < 1024
    assert "🇳🇱 Нидерланды" in brand_header(ctx)


# ---------------- link tasks: reward for the click, once ----------------

async def test_task_click_link_rewards_once_and_redirects(owner_ctx, client):  # noqa: F811
    from backend.services.tasks import click_sig

    await make_user(owner_ctx, 700)
    tid = await _task(owner_ctx, title="Канал партнёра", reward=50, chat_id=None)
    secret = owner_ctx.settings.session_secret.get_secret_value()
    url = f"/api/go/{tid}/700/{click_sig(secret, tid, 700)}"
    r = await client.get(url, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://t.me/partner_channel"
    for _ in range(3):  # repeated presses: still redirected, never paid again
        assert (await client.get(url, follow_redirects=False)).status_code == 302
    async with owner_ctx.session() as s:
        assert await coins_svc.balance(s, 700) == 50
        assert (await s.scalar(select(TaskClaim.status).where(TaskClaim.task_id == tid, TaskClaim.user_id == 700))) == "rewarded"
    sent = [t for uid, t in owner_ctx.notifier.sent if uid == 700]
    assert len(sent) == 1 and "Задание выполнено" in sent[0] and "+50 SpirCoin" in sent[0] and "Текущий баланс" in sent[0]


async def test_task_click_link_rejects_forged_signature_and_other_user(owner_ctx, client):  # noqa: F811
    from backend.services.tasks import click_sig

    await make_user(owner_ctx, 701)
    await make_user(owner_ctx, 702)
    tid = await _task(owner_ctx, reward=30)
    secret = owner_ctx.settings.session_secret.get_secret_value()
    assert (await client.get(f"/api/go/{tid}/701/AAAAAAAAAAAAAAAA", follow_redirects=False)).status_code == 403
    # 702's signature can't be used to credit 701
    assert (await client.get(f"/api/go/{tid}/701/{click_sig(secret, tid, 702)}", follow_redirects=False)).status_code == 403
    async with owner_ctx.session() as s:
        assert await coins_svc.balance(s, 701) == 0


async def test_task_click_without_mandatory_channel_gives_no_reward(owner_ctx, client):  # noqa: F811
    from backend.services.tasks import click_sig

    gate_ctx(owner_ctx)
    await make_user(owner_ctx, 703)
    owner_ctx.extra["channel_checker"].statuses[703] = ("left", None)
    tid = await _task(owner_ctx, reward=30)
    secret = owner_ctx.settings.session_secret.get_secret_value()
    r = await client.get(f"/api/go/{tid}/703/{click_sig(secret, tid, 703)}", follow_redirects=False)
    assert r.status_code == 302
    async with owner_ctx.session() as s:
        assert await coins_svc.balance(s, 703) == 0


async def test_manual_task_still_waits_for_moderator(ctx):
    await make_user(ctx, 704)
    tid = await _task(ctx, title="Скриншот отзыва", verify="manual", reward=20)
    r = await tasks_svc.claim(ctx, 704, tid)
    assert r.status == "pending"
    async with ctx.session() as s:
        assert await coins_svc.balance(s, 704) == 0


# ---------------- broadcasts: reason optional ----------------

async def test_broadcast_api_reason_optional(owner_ctx, client):  # noqa: F811
    await make_user(owner_ctx, 710)
    hdr = h(owner_ctx, ADMIN_ID, role="admin")
    r = await client.post("/api/admin/broadcasts", json={"mode": "single", "target_user_id": 710, "text": "Привет"}, headers=hdr)
    assert r.status_code == 200, r.text
    r = await client.post("/api/admin/broadcasts", json={"mode": "all", "text": "Новость", "reason": ""}, headers=hdr)
    assert r.status_code == 200, r.text
    await client.post(f"/api/admin/broadcasts/{r.json()['id']}/cancel", json={}, headers=hdr)
    r = await client.post("/api/admin/broadcasts", json={"mode": "all", "text": "Новость 2", "reason": "плановое объявление"}, headers=hdr)
    assert r.status_code == 200, r.text
    r = await client.post("/api/admin/maintenance/announce", json={"text": "Работы"}, headers=hdr)
    assert r.status_code == 400 and r.json()["code"] == "broadcast_running"  # one mass send at a time
    async with owner_ctx.session() as s, s.begin():
        await s.execute(text("UPDATE broadcasts SET status = 'done'"))
    r = await client.post("/api/admin/maintenance/announce", json={"text": "Работы"}, headers=hdr)  # no reason, default restore text
    assert r.status_code == 200 and r.json()["state"]["restore_text"].startswith("✅ SpirittVPN снова доступен")
    async with owner_ctx.session() as s:
        reasons = (await s.execute(select(AuditLog.reason).where(AuditLog.action.like("broadcast.%")))).scalars().all()
    assert "плановое объявление" in reasons and any(x.startswith("без указания причины") for x in reasons)


# ---------------- owners ----------------

async def test_owner_ids_from_env_have_full_rights(owner_ctx, client):  # noqa: F811
    from backend.services import roles

    await make_user(owner_ctx, 6964704172)
    assert await roles.get_role(owner_ctx, 6964704172) != "owner"
    owner_ctx.settings.owner_ids = "6964704172"
    assert await roles.get_role(owner_ctx, 6964704172) == "owner"
    assert await roles.has_perm(owner_ctx, 6964704172, "coins.manage")
    r = await client.get("/api/admin/me", headers=h(owner_ctx, 6964704172, role="admin"))
    assert r.status_code == 200 and r.json()["role"] == "owner"
    assert any(e["user_id"] == 6964704172 and e["role"] == "owner" for e in await roles.list_admins(owner_ctx))


async def test_owner_migration_grants_only_matching_account(ctx):
    from backend.services import roles

    m = importlib.util.spec_from_file_location("m0008", ROOT / "database/migrations/versions/0008_link_tasks_and_owner.py")
    mod = importlib.util.module_from_spec(m)
    m.loader.exec_module(mod)
    reg = await make_user(ctx, 6964704172)
    async with ctx.session() as s, s.begin():
        await s.execute(text(mod.GRANT_OWNER))
    assert await roles.get_role(ctx, 6964704172) is None  # public id differs -> not promoted
    async with ctx.session() as s, s.begin():
        (await s.get(User, 6964704172)).public_id = "SV-J5MJRH"
        await s.flush()
        await s.execute(text(mod.GRANT_OWNER))
    assert await roles.get_role(ctx, 6964704172) == "owner"
    assert reg.user.id == 6964704172


async def test_terms_version_bump_asks_everyone_again(ctx):
    gate_ctx(ctx)
    for uid in (801, 802):
        await make_user(ctx, uid)
    assert (await access.gate_state(ctx, 801, force_channel=True)).allowed
    ctx.settings.terms_version = "2026-11-01"  # operator edits TERMS_VERSION and restarts
    for uid in (801, 802):
        st = await access.gate_state(ctx, uid, force_channel=True)
        assert not st.terms_accepted and not st.allowed
        with pytest.raises(access.AccessDenied) as e:
            await access.require_access(ctx, uid)
        assert e.value.code == "terms_required"
    await access.accept_terms(ctx, 801, "2026-11-01", "bot")
    assert (await access.gate_state(ctx, 801, force_channel=True)).allowed  # subscribed -> channel re-check passes
    ctx.extra["channel_checker"].statuses[802] = ("left", None)
    await access.accept_terms(ctx, 802, "2026-11-01", "bot")
    st = await access.gate_state(ctx, 802, force_channel=True)
    assert st.terms_accepted and not st.allowed  # accepted documents, but not subscribed -> still locked


def test_updated_terms_text_has_new_design(ctx):
    from bot.handlers.gate import updated_text

    t = updated_text(ctx)
    assert "SPIRITTVPN" in t and "обновил" in t and "━━━" in t and len(t) < 900
