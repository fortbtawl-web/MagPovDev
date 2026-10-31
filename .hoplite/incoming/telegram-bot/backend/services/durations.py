"""Custom durations and dates for manual grants. Calendar math is done in the display timezone, storage is UTC."""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from backend.services.context import ServiceError

UNITS = {"hours": "часов", "days": "дней", "weeks": "недель", "months": "месяцев"}
_SHORT = {"h": "hours", "ч": "hours", "d": "days", "д": "days", "w": "weeks", "н": "weeks", "m": "months", "м": "months"}


def add_months(dt: datetime, months: int) -> datetime:
    """Calendar months, clamping the day (31 Jan + 1 month = 28/29 Feb)."""
    total = dt.month - 1 + months
    year, month = dt.year + total // 12, total % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def shift(base: datetime, amount: int, unit: str, tz: str) -> datetime:
    """base (aware) +/- amount units. Returns UTC."""
    if unit not in UNITS:
        raise ServiceError("Единица срока: часы, дни, недели или месяцы.", "bad_unit")
    if base.tzinfo is None:
        raise ValueError("naive datetime")
    local = base.astimezone(ZoneInfo(tz))
    if unit == "hours":
        out = local + timedelta(hours=amount)
    elif unit == "days":
        out = local + timedelta(days=amount)
    elif unit == "weeks":
        out = local + timedelta(weeks=amount)
    else:
        out = add_months(local, amount)
    return out.astimezone(timezone.utc)


def parse_datetime(value: str | datetime, tz: str) -> datetime:
    """ISO 8601 (with or without offset) or DD.MM.YYYY [HH:MM]; naive values are in the display timezone."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip()
        dt = None
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                dt = datetime.strptime(text, fmt)
                if fmt == "%d.%m.%Y":
                    dt = dt.replace(hour=23, minute=59)
                break
            except ValueError:
                pass
        if dt is None:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ServiceError("Дата: ДД.ММ.ГГГГ [ЧЧ:ММ] или ISO 8601.", "bad_date") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc)


_SPEC_RE = re.compile(r"^([+-]?)(\d{1,6})\s*([a-zа-я]+)$", re.IGNORECASE)


def parse_spec(text: str) -> tuple[int, str] | None:
    """'12h', '+7d', '-3д', '2w', '1m' -> (signed amount, unit). None if not a duration."""
    m = _SPEC_RE.match(text.strip())
    if not m:
        return None
    sign, num, unit = m.groups()
    unit = _SHORT.get(unit.lower()[:1]) if unit.lower() not in UNITS else unit.lower()
    if unit is None:
        return None
    amount = int(num) * (-1 if sign == "-" else 1)
    return amount, unit
