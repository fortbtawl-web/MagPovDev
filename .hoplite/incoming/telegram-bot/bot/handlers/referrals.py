"""Друзья и реферальная программа."""
from __future__ import annotations

from urllib.parse import quote

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from backend.models import User
from backend.services import referrals as ref_svc
from backend.services import settings as settings_svc
from backend.services.context import AppContext
from backend.utils import fmt_dt
from bot import ui
from bot.common import show
from bot.keyboards import Nav, copy_button

router = Router(name="referrals")


@router.callback_query(Nav.filter(F.to == "ref"))
async def ref_home(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        user = await session.get(User, cb.from_user.id)
        s = await ref_svc.referral_summary(session, cb.from_user.id)
        st = await ref_svc.referral_stats(session, cb.from_user.id)
        cfg = await settings_svc.get_all(session)
    username = ctx.extra.get("bot_username")
    link = f"https://t.me/{username}?start=ref_{user.referral_code}" if username else None
    rules = []
    if cfg["referral_trial_bonus_enabled"]:
        rules.append(ui.kv(f"+{cfg['referral_trial_bonus_days']} дн.", "когда друг активирует пробный период"))
    if cfg["referral_payment_bonus_enabled"]:
        rules.append(ui.kv(f"+{cfg['referral_payment_bonus_days']} дн.", "после первой оплаты друга"))
    blocks = [ui.title("Друзья", "Приглашайте и получайте бонусные дни")]
    if not cfg["referral_enabled"]:
        blocks.append(ui.warn("Реферальная программа сейчас приостановлена."))
    if rules:
        blocks.append(ui.card(*rules))
    blocks.append(ui.note(f"Другу — пробный период на {cfg['trial_days']} дн., если он ещё его не использовал."))
    stats = [
        ui.kv("Переходов по ссылке", st["visits"]),
        ui.kv("Новых пользователей", st["registered"]),
        ui.kv("Активировали пробный период", st["trial"]),
        ui.kv("Купили подписку", f"{st['buyers']} · оплат {st['purchases']} на {st['paid_label']}"),
    ]
    if st["coin_purchases"]:
        stats.append(ui.kv("Покупок за SpirCoin", st["coin_purchases"]))
    if st["refunds"]:
        stats.append(ui.kv("Возвратов", st["refunds"]))
    stats.append(ui.kv("Бонусных дней", f"получено {s['earned_days']} · на балансе {s['balance_days']}"))
    blocks.append(ui.card(*stats))
    rows = []
    if link:
        blocks.append(ui.kv("Ваша ссылка", f"\n<code>{link}</code>"))
        share = f"https://t.me/share/url?url={quote(link)}&text={quote('Попробуй SpirittVPN — твоя свобода в сети!')}"
        rows.append([InlineKeyboardButton(text="Поделиться ссылкой", url=share)])
        rows.append([copy_button("Скопировать ссылку", link)])
    else:
        blocks.append(ui.warn("Ссылка временно недоступна: не удалось получить имя бота."))
    rows.append([
        InlineKeyboardButton(text="Приглашения", callback_data=Nav(to="ref_invites").pack()),
        InlineKeyboardButton(text="Бонусы", callback_data=Nav(to="ref_bonus").pack()),
    ])
    rows.append([InlineKeyboardButton(text="События", callback_data=Nav(to="ref_events").pack())])
    rows.append(ui.nav_row())
    await show(cb, ui.screen(*blocks), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(Nav.filter(F.to == "ref_invites"))
async def ref_invites(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        items = await ref_svc.list_invites(session, cb.from_user.id, limit=30)
    if not items:
        text = ui.screen(ui.title("Приглашения"), ui.card("○ Пока никого"), ui.note("Поделитесь ссылкой — она на экране «Друзья»."))
    else:
        yes = lambda v: "✓" if v else "—"  # noqa: E731
        lines = [
            f"<b>{ui.safe(i['name'])}</b> · {fmt_dt(i['registered_at'])}\nпробный {yes(i['trial'])} · оплата {yes(i['paid'])} · бонус {i['bonus_days']} дн."
            for i in items
        ]
        text = ui.screen(ui.title("Приглашения"), ui.card(*lines))
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=[ui.nav_row("ref", "‹ Друзья")]))


@router.callback_query(Nav.filter(F.to == "ref_bonus"))
async def ref_bonus(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        items = await ref_svc.bonus_history(session, cb.from_user.id, limit=30)
        balance = await ref_svc.bonus_balance(session, cb.from_user.id)
    lines = [f"{'+' if b.days > 0 else ''}{b.days} дн. · {ref_svc.BONUS_KIND_LABELS.get(b.kind, b.kind)} · {fmt_dt(b.created_at)}" for b in items]
    text = ui.screen(
        ui.title("Бонусы"),
        ui.card(ui.kv("На балансе", f"{balance} дн.")),
        ui.card(*lines) if lines else ui.card("○ Начислений пока нет"),
    )
    rows = []
    if balance > 0:
        rows.append([InlineKeyboardButton(text="Применить к подписке", callback_data=Nav(to="bonus_apply").pack())])
    rows.append(ui.nav_row("ref", "‹ Друзья"))
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(Nav.filter(F.to == "ref_events"))
async def ref_events(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        items = await ref_svc.referral_events(session, cb.from_user.id, limit=25)
    lines = [f"{ui.safe(e['label'])} · {ui.safe(e['who'])}" + (f" · {ui.safe(e['detail'])}" if e["detail"] else "") + f" · {fmt_dt(e['at'])}" for e in items]
    text = ui.screen(ui.title("События"), ui.card(*lines) if lines else ui.card("○ Событий пока нет"))
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=[ui.nav_row("ref", "‹ Друзья")]))
