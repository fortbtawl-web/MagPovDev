"""Личный кабинет в чате с ботом (те же данные, что в Mini App)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy import select

from backend import webapp_url
from backend.api.serializers import EVENT_LABELS
from backend.models import SubscriptionEvent
from backend.services import account as account_svc
from backend.services import referrals as ref_svc
from backend.services import roles
from backend.services.context import AppContext
from backend.utils import fmt_dt, utcnow
from bot import ui
from bot.common import PLAN_NAMES, show
from bot.keyboards import Nav, back_row, copy_button, is_https

router = Router(name="cabinet")


def remaining(sub) -> str:
    if sub.is_lifetime:
        return "бессрочно"
    if not sub.expires_at:
        return "—"
    secs = int((sub.expires_at - utcnow()).total_seconds())
    if secs <= 0:
        return "истёк"
    days, hours = secs // 86400, secs % 86400 // 3600
    return f"{days} дн. {hours} ч." if days or hours else "меньше часа"


async def cabinet_view(ctx: AppContext, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    data = await account_svc.overview(ctx, user_id)
    user, sub = data["user"], data["subscription"]
    used = sum(1 for d in data["devices"] if d.status in ("pending", "active", "expired", "suspended", "revoking"))
    active_keys = sum(1 for d in data["devices"] if d.status == "active")
    async with ctx.session() as session:
        refs = await ref_svc.referral_summary(session, user_id)
    role = await roles.get_role(ctx, user_id)
    role_tag = {"owner": "главный администратор", "admin": "администратор"}.get(role or "")
    account = ui.kv("Аккаунт", "🔴 заблокирован" if user.is_banned else "🟢 активен") + (f" · {role_tag}" if role_tag else "")
    blocks = [ui.title("Профиль"), ui.card(ui.kv("SpirittVPN ID", f"<code>{user.public_id}</code>"), account)]
    if sub is None:
        blocks.append(ui.no_subscription())
    else:
        status = "♾ Бессрочная подписка" if sub.is_lifetime and sub.status == "active" else ui.STATUS.get(sub.status, sub.status)
        blocks.append(
            ui.card(
                ui.kv("Тариф", f"<b>{PLAN_NAMES.get(sub.plan_code, sub.plan_code)}</b>"),
                ui.kv("Статус", status),
                ui.kv("Активирована", fmt_dt(sub.starts_at)),
                ui.kv("Действует до", "бессрочно" if sub.is_lifetime else fmt_dt(sub.expires_at)),
                ui.kv("Осталось", remaining(sub)),
                ui.kv("Устройства", f"{used} из {sub.device_limit} · активных ключей {active_keys}"),
            )
        )
    blocks.append(
        ui.card(
            ui.kv("Бонусных дней", data["bonus_balance"]),
            ui.kv("Приглашено", f"{refs['invited']} · оплатили {refs['with_payment']}"),
        )
    )
    if user.is_banned:
        blocks.append(ui.warn("Аккаунт заблокирован. Обратитесь в поддержку."))
    blocks.append(ui.note("Статус подписки не означает, что VPN сейчас включён на устройстве."))
    lines = ["\n\n".join(blocks)]

    rows = [[copy_button("Скопировать ID", user.public_id)]]
    if sub is not None:
        rows.append([InlineKeyboardButton(text="⚡ Ссылка подписки и QR", callback_data=Nav(to="devices").pack())])
    rows.append([
        InlineKeyboardButton(text="🕘 История", callback_data="cab:hist"),
        InlineKeyboardButton(text="💳 Платежи", callback_data=Nav(to="history").pack()),
    ])
    rows.append([
        InlineKeyboardButton(text="👥 Друзья", callback_data=Nav(to="ref").pack()),
        InlineKeyboardButton(text="💬 Поддержка", callback_data=Nav(to="support").pack()),
    ])
    if role:
        rows.append([InlineKeyboardButton(text="⚙ Админ-панель", callback_data="adm:home")])
    app_url = webapp_url.current(ctx.settings)
    if is_https(app_url):
        rows.append([InlineKeyboardButton(text="◆ Открыть в приложении", web_app=WebAppInfo(url=app_url))])
    rows.append(ui.nav_row())
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("cabinet", "me"))
async def cmd_cabinet(message: Message, ctx: AppContext) -> None:
    text, markup = await cabinet_view(ctx, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(Nav.filter(F.to == "cabinet"))
async def nav_cabinet(cb: CallbackQuery, ctx: AppContext) -> None:
    await show(cb, *await cabinet_view(ctx, cb.from_user.id))


@router.callback_query(F.data == "cab:hist")
async def cabinet_history(cb: CallbackQuery, ctx: AppContext) -> None:
    async with ctx.session() as session:
        events = (
            await session.execute(
                select(SubscriptionEvent).where(SubscriptionEvent.user_id == cb.from_user.id).order_by(SubscriptionEvent.created_at.desc()).limit(15)
            )
        ).scalars().all()
    lines = []
    for e in events:
        line = f"◦ {fmt_dt(e.created_at)} — {EVENT_LABELS.get(e.action, e.action)}"
        if e.new_expires_at:
            line += f" (до {fmt_dt(e.new_expires_at)})"
        if e.public_note:  # internal admin reason (comment) is never shown
            line += f"\n    <i>{ui.safe(e.public_note)}</i>"
        lines.append(line)
    text = ui.screen(ui.title("История подписки"), "\n".join(lines) if lines else "Изменений пока нет.")
    kb = InlineKeyboardMarkup(inline_keyboard=[ui.nav_row("cabinet", "‹ Профиль")])
    await show(cb, text, kb)


__all__ = ["router", "cabinet_view"]
