"""Connection wizard: device → app → step-by-step guide with the user's own subscription link.

Offered once the documents are accepted and the mandatory channel check has passed; also opened from
«Инструкция». Only apps with an official download link for the chosen platform are listed (links are the
project's existing, verified ones from spircoin.APPS). The current choice travels in the callback data, so
«Назад» returns to the previous step with the choice still marked.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from backend.models import Device
from backend.services import trial as trial_svc
from backend.services.context import AppContext
from bot import ui
from bot.common import show
from bot.keyboards import Nav, copy_button

router = Router(name="wizard")


class WizCb(CallbackData, prefix="wiz"):
    s: str  # dev | app | guide (legacy -> step 1) | st
    d: str = ""
    a: str = ""
    n: int = 1  # guide step 1..6


DEVICES = {"windows": "Windows", "macos": "macOS", "android": "Android", "ios": "iPhone / iPad", "linux": "Linux"}

# app -> {device: official download link}. Same sources as the existing «Как подключиться» screen.
APPS: dict[str, tuple[str, dict[str, str]]] = {
    "happ": ("Happ", {
        "android": "https://play.google.com/store/apps/details?id=com.happproxy",
        "ios": "https://apps.apple.com/app/happ-proxy-utility/id6504287215",
        "macos": "https://apps.apple.com/app/happ-proxy-utility/id6504287215",
        "windows": "https://www.happ.su/main",
    }),
    "hiddify": ("Hiddify", {
        "android": "https://github.com/hiddify/hiddify-app/releases",
        "ios": "https://apps.apple.com/app/hiddify-proxy-vpn/id6596777532",
        "windows": "https://github.com/hiddify/hiddify-app/releases",
        "macos": "https://github.com/hiddify/hiddify-app/releases",
        "linux": "https://github.com/hiddify/hiddify-app/releases",
    }),
    "v2raytun": ("v2RayTun", {
        "android": "https://play.google.com/store/apps/details?id=com.v2raytun.android",
        "ios": "https://apps.apple.com/app/v2raytun/id6476628951",
    }),
    "v2rayng": ("v2rayNG", {"android": "https://github.com/2dust/v2rayNG/releases"}),
}
RECOMMENDED = {"happ"}
AUTOSELECT = {"happ", "hiddify"}  # show the «Автовыбор» group from our subscription


def apps_for(device: str) -> list[str]:
    return [code for code, (_, links) in APPS.items() if device in links]


STEPS_TOTAL = 6
STEP_TITLES = {1: "Установка приложения", 2: "Ваша VPN-подписка", 3: "Добавление подписки", 4: "Подключение",
               5: "Проверка работы", 6: "Если что-то не так"}


def step_install(device: str, app: str) -> list[str]:
    name, links = APPS[app]
    url = links[device]
    if "apps.apple.com" in url:
        lines = [f"Откройте App Store по кнопке «↓ Скачать {name}» ниже и установите приложение."]
        if device == "macos":
            lines.append("На Mac приложение из App Store работает на компьютерах с процессором Apple (M1 и новее).")
        lines.append("Если приложения нет в App Store вашей страны — это ограничение региона Apple ID, а не сервиса.")
        return lines
    if "play.google.com" in url:
        return [f"Откройте Google Play по кнопке «↓ Скачать {name}» ниже и нажмите «Установить»."]
    if "github.com" in url:
        pkg = {
            "windows": "установщик для Windows (файл .exe)",
            "macos": "файл для macOS (.dmg)",
            "linux": "файл AppImage для Linux",
            "android": "APK для Android — обычно подходит вариант arm64-v8a",
        }.get(device, "файл для вашей системы")
        lines = [
            f"Откройте официальную страницу релизов {name} кнопкой «↓ Скачать {name}» ниже.",
            f"В последней версии раскройте раздел Assets и скачайте {pkg}.",
            "Установите и запустите приложение.",
        ]
        if device == "android":
            lines.append("Android может попросить разрешить установку из этого источника — разрешите для браузера.")
        return lines
    return [f"Скачайте {name} с официального сайта кнопкой «↓ Скачать {name}» ниже, установите и запустите."]


def step_add(device: str, app: str) -> list[str]:
    copy = "Скопируйте свою ссылку подписки — кнопка «📋 Скопировать ссылку» ниже."
    page = "Или откройте «Страницу подписки» и нажмите там «Добавить в приложение» — приложение откроется само."
    if app == "happ":
        return [copy, "Откройте Happ и нажмите «+» в правом верхнем углу главного экрана.",
                "Выберите добавление из буфера обмена — Happ сам распознает ссылку.",
                "Подтвердите добавление, если приложение спросит. Появится список серверов SpirittVPN.", page]
    if app == "hiddify":
        return [copy, "Откройте Hiddify и нажмите «+».", "Выберите «Clipboard» (буфер обмена) — профиль добавится.",
                "Если не получилось: «+» → «Manually», в поле «Name» введите SpirittVPN, в «URL» вставьте ссылку и нажмите «Add».",
                page]
    if app == "v2raytun":
        return [copy, "Откройте v2RayTun и нажмите «+».", "Выберите «Импорт из буфера обмена».",
                "Подтвердите добавление — появится список серверов.", page]
    # v2rayNG
    return [copy, "Откройте v2rayNG и нажмите кнопку меню (☰) слева вверху.", "Выберите «Subscription group setting» и нажмите «+».",
            "В поле «remarks» введите SpirittVPN, в «Optional URL» вставьте ссылку и сохраните.",
            "Вернитесь на главный экран, нажмите «⋮» справа вверху и выберите «Update subscription» — появятся серверы."]


def step_connect(device: str, app: str) -> list[str]:
    if app == "hiddify":
        lines = ["Нажмите большую кнопку подключения в центре главного экрана.",
                 "По умолчанию Hiddify сам выбирает самый быстрый сервер. Другую страну можно выбрать во вкладке «Proxies» после подключения."]
        if device == "windows":
            lines.append("На Windows Hiddify стартует в режиме «System proxy». Чтобы через VPN шёл весь трафик, переключите режим на «VPN» — для этого запустите Hiddify от имени администратора (правой кнопкой по значку → «Запуск от имени администратора»).")
        if device == "linux":
            lines.append("Для режима «VPN» на Linux нужны права root — запустите AppImage через sudo.")
    elif app == "v2rayng":
        lines = ["Выберите сервер в списке — нажмите на него.", "Нажмите круглую кнопку «V» внизу экрана."]
    elif app == "happ":
        lines = ["Выберите группу «Автовыбор» — Happ сам подберёт сервер с лучшим пингом. Или выберите конкретную страну.",
                 "Нажмите кнопку подключения на главном экране."]
    else:
        lines = ["Выберите сервер нужной страны в списке.", "Нажмите кнопку подключения."]
    lines.append(_permission_step(device))
    if device in ("android", "ios"):
        lines.append("Признак успешного подключения — значок VPN в строке состояния телефона.")
    else:
        lines.append("Признак успешного подключения — приложение показывает, что соединение установлено, а сайты открываются через VPN.")
    return lines


def step_check() -> list[str]:
    return ["Откройте в браузере ipinfo.io — в строке страны должен быть выбранный сервер.",
            "Откройте сайты и приложения, которыми пользуетесь, — они должны открываться без задержек.",
            "Не совпадает страна или ничего не открывается — переходите к следующему шагу."]


def step_trouble(app: str) -> list[str]:
    update = "«⋮» → «Update subscription»" if app == "v2rayng" else "обновите подписку в приложении"
    return [
        "<b>Ссылка не добавляется</b> — скопируйте её целиком кнопкой «📋 Скопировать ссылку» или используйте «Добавить в приложение» на странице подписки.",
        f"<b>Не подключается</b> — {update}, выберите другой сервер, включите автоматическую дату и время на устройстве и перезапустите приложение.",
        "<b>Соединение нестабильно</b> — выберите другую страну или «Автовыбор», попробуйте другую сеть (Wi-Fi / мобильный интернет).",
        "<b>Подписка закончилась</b> — продлите её в «💎 Тарифы», заново настраивать приложение не нужно.",
        "<b>Пишет «лимит устройств»</b> — освободите место в разделе «📱 Устройства».",
        "<b>Не помогло</b> — напишите в «💬 Поддержка» и приложите скриншот ошибки.",
    ]


def _permission_step(device: str) -> str:
    if device in ("ios", "macos"):
        return "При первом подключении система попросит разрешить добавление конфигурации VPN — разрешите."
    if device == "android":
        return "При первом подключении Android спросит разрешение на VPN-подключение — нажмите «ОК»."
    if device == "windows":
        return "Если Windows спросит разрешение для приложения (контроль учётных записей или брандмауэр) — разрешите."
    return "Если система запросит пароль администратора — введите его."


def guide_text(device: str, app: str, link: str | None) -> str:
    """Whole guide in one text (kept for callers/tests that want the full version)."""
    parts = [ui.title(f"{APPS[app][0]} · {DEVICES[device]}")]
    for n in range(1, STEPS_TOTAL + 1):
        parts.append(f"<b>{n}. {STEP_TITLES[n]}</b>\n" + "\n".join(_step_lines(n, device, app, link)))
    return ui.screen(*parts)


def _step_lines(n: int, device: str, app: str, link: str | None) -> list[str]:
    if n == 1:
        return step_install(device, app)
    if n == 2:
        return (["Ваша личная ссылка подписки — ниже. Она только ваша: не пересылайте её другим людям."] if link else
                ["Для подключения нужна VPN-подписка — ниже показано, как её получить."])
    if n == 3:
        return step_add(device, app)
    if n == 4:
        return step_connect(device, app)
    if n == 5:
        return step_check()
    return step_trouble(app)


async def _own_link(ctx: AppContext, user_id: int) -> str | None:
    """The caller's own active subscription link only (never another user's)."""
    async with ctx.session() as session:
        devices = (await session.execute(
            select(Device).where(Device.user_id == user_id, Device.status == "active", Device.subscription_url.is_not(None))
        )).scalars().all()
    if not devices:
        return None
    devices = sorted(devices, key=lambda d: (d.node.country_code != "ZZ", d.id))  # "all locations" first
    return devices[0].subscription_url


def offer_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧭 Да, помочь с подключением", callback_data=WizCb(s="dev").pack())],
        [InlineKeyboardButton(text="⌂ Нет, перейти в меню", callback_data=Nav(to="menu").pack())],
    ])


OFFER_TEXT = ui.screen(
    "✅ <b>Доступ открыт — добро пожаловать в SpirittVPN!</b>",
    ui.title("Нужна инструкция по подключению SpirittVPN?"),
    "Подскажем, какое приложение поставить на ваше устройство, и проведём по шагам: установка → подписка → подключение → проверка.",
)


async def send_offer(message: Message) -> None:
    await message.answer(OFFER_TEXT, reply_markup=offer_kb(), disable_web_page_preview=True)


def devices_screen(selected: str = "") -> tuple[str, InlineKeyboardMarkup]:
    mark = lambda code: ("✓ " if code == selected else "") + DEVICES[code]  # noqa: E731
    rows = [
        [InlineKeyboardButton(text=mark("windows"), callback_data=WizCb(s="app", d="windows").pack()),
         InlineKeyboardButton(text=mark("macos"), callback_data=WizCb(s="app", d="macos").pack())],
        [InlineKeyboardButton(text=mark("android"), callback_data=WizCb(s="app", d="android").pack()),
         InlineKeyboardButton(text=mark("ios"), callback_data=WizCb(s="app", d="ios").pack())],
        [InlineKeyboardButton(text=mark("linux"), callback_data=WizCb(s="app", d="linux").pack())],
        [InlineKeyboardButton(text="Отмена", callback_data=Nav(to="menu").pack())],
    ]
    text = ui.screen(ui.title("Шаг 1 из 3 · устройство", "На чём будете пользоваться VPN?"))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(WizCb.filter(F.s == "dev"))
async def wiz_devices(cb: CallbackQuery, callback_data: WizCb) -> None:
    await show(cb, *devices_screen(callback_data.d))


@router.callback_query(WizCb.filter(F.s == "app"))
async def wiz_apps(cb: CallbackQuery, callback_data: WizCb) -> None:
    device = callback_data.d if callback_data.d in DEVICES else "windows"
    codes = apps_for(device)
    rows = []
    for code in codes:
        label = APPS[code][0] + (" · рекомендуем" if code in RECOMMENDED else "")
        if code == callback_data.a:
            label = "✓ " + label
        rows.append([InlineKeyboardButton(text=label, callback_data=WizCb(s="st", d=device, a=code, n=1).pack())])
    rows.append([InlineKeyboardButton(text="‹ Устройство", callback_data=WizCb(s="dev", d=device).pack()),
                 InlineKeyboardButton(text="Отмена", callback_data=Nav(to="menu").pack())])
    hint = "Happ и Hiddify показывают группу «Автовыбор» из нашей подписки." if any(c in AUTOSELECT for c in codes) else ""
    text = ui.screen(ui.title("Шаг 2 из 3 · приложение", f"Подходят для {DEVICES[device]}"), ui.note(hint) if hint else "")
    await show(cb, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(WizCb.filter(F.s.in_({"guide", "st"})))
async def wiz_guide(cb: CallbackQuery, callback_data: WizCb, ctx: AppContext) -> None:
    device, app = callback_data.d, callback_data.a
    if device not in DEVICES or app not in APPS or device not in APPS[app][1]:
        await show(cb, *devices_screen())
        return
    n = callback_data.n if callback_data.s == "st" and 1 <= callback_data.n <= STEPS_TOTAL else 1
    name, links = APPS[app]
    link = await _own_link(ctx, cb.from_user.id) if n in (2, 3) else None
    head = ui.title(f"Шаг {n} из {STEPS_TOTAL} · {STEP_TITLES[n]}", f"{name} · {DEVICES[device]}")
    lines = _step_lines(n, device, app, link)
    body = ui.steps(lines) if n in (1, 3, 4, 5) else "\n\n".join(lines)
    blocks = [head, body]
    rows: list[list[InlineKeyboardButton]] = []
    if n == 1:
        rows.append([InlineKeyboardButton(text=f"↓ Скачать {name}", url=links[device])])
    if n in (2, 3):
        if link:
            if n == 2:
                blocks.append(ui.card("🔗 <b>Ваша ссылка подписки</b>", f"<code>{ui.safe(link)}</code>"))
            rows.append([InlineKeyboardButton(text="🌐 Страница подписки", url=link)])
            if len(link) <= 256:
                rows.append([copy_button("📋 Скопировать ссылку", link)])
        elif n == 2:
            trial = await trial_svc.trial_info(ctx, cb.from_user.id)
            if trial.available:
                blocks.append(ui.no_subscription(f"Активируйте бесплатный пробный период на {trial.days} дн. — ссылка появится сразу. Затем вернитесь к инструкции: «🧭 Инструкция» в меню."))
                rows.append([InlineKeyboardButton(text=f"🎁 Пробный период · {trial.days} дн.", callback_data=Nav(to="trial").pack())])
            else:
                blocks.append(ui.no_subscription("Оформите тариф в разделе «💎 Тарифы» — ссылка появится в «⚡ Подключиться». Затем вернитесь к инструкции."))
            rows.append([InlineKeyboardButton(text="💎 Тарифы", callback_data=Nav(to="plans").pack())])
    nav: list[InlineKeyboardButton] = []
    if n > 1:
        nav.append(InlineKeyboardButton(text="‹ Назад", callback_data=WizCb(s="st", d=device, a=app, n=n - 1).pack()))
    else:
        nav.append(InlineKeyboardButton(text="‹ Приложение", callback_data=WizCb(s="app", d=device, a=app).pack()))
    if n < STEPS_TOTAL:
        nav.append(InlineKeyboardButton(text="Следующий шаг ›", callback_data=WizCb(s="st", d=device, a=app, n=n + 1).pack()))
    rows.append(nav)
    if n == STEPS_TOTAL:
        rows.append([InlineKeyboardButton(text="🔁 Повторить инструкцию", callback_data=WizCb(s="st", d=device, a=app, n=1).pack())])
    rows.append([InlineKeyboardButton(text="📱 Другое устройство", callback_data=WizCb(s="dev", d=device).pack()),
                 InlineKeyboardButton(text="🔄 Другое приложение", callback_data=WizCb(s="app", d=device, a=app).pack())])
    rows.append([InlineKeyboardButton(text="⌂ Главное меню", callback_data=Nav(to="menu").pack())])
    await show(cb, ui.screen(*blocks), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.regexp(r"^wiz:(dev|app|guide):[a-z]*:[a-z0-9]*$"))
async def wiz_legacy(cb: CallbackQuery, ctx: AppContext) -> None:
    """Buttons in messages sent before steps were added carry no step number."""
    _, s, d, a = cb.data.split(":")
    data = WizCb(s=s, d=d, a=a)
    if s == "dev":
        await wiz_devices(cb, data)
    elif s == "app":
        await wiz_apps(cb, data)
    else:
        await wiz_guide(cb, data, ctx)
