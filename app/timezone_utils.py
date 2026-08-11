from __future__ import annotations

from datetime import datetime, timedelta, timezone


BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def beijing_now_iso(*, timespec: str = "milliseconds") -> str:
    return beijing_now().isoformat(timespec=timespec)


def parse_datetime(value: str | None, *, naive_timezone=BEIJING_TZ) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=naive_timezone)
    return parsed


def to_beijing(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TZ)
    else:
        parsed = parse_datetime(value)
    return parsed.astimezone(BEIJING_TZ) if parsed is not None else None


def to_beijing_iso(value: str | datetime | None, *, timespec: str = "milliseconds") -> str | None:
    parsed = to_beijing(value)
    return parsed.isoformat(timespec=timespec) if parsed is not None else None
