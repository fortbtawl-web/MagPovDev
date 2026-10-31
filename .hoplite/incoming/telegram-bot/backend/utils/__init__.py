from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fmt_money(minor: int, currency: str = "RUB") -> str:
    rub, kop = divmod(int(minor), 100)
    whole = f"{rub:,}".replace(",", " ")
    sign = "₽" if currency == "RUB" else currency
    return f"{whole} {sign}" if kop == 0 else f"{whole},{kop:02d} {sign}"


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    # Displayed in Moscow time for users; storage is always UTC.
    from zoneinfo import ZoneInfo

    return dt.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M МСК")
