from __future__ import annotations

from datetime import datetime, timedelta, timezone


DISPLAY_TIMEZONE = timezone(timedelta(hours=8))
DISPLAY_TIMEZONE_LABEL = "东八区"
DISPLAY_TIMEZONE_DESCRIPTION = "东八区（UTC+08:00）"


def _display_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Human display timestamps must include a UTC offset")
    return parsed.astimezone(DISPLAY_TIMEZONE)


def format_display_timestamp(value: str) -> str:
    return _display_datetime(value).strftime("%Y-%m-%d %H:%M:%S")


def format_display_minute(value: str) -> str:
    return _display_datetime(value).strftime("%Y-%m-%d %H:%M")


def format_display_date(value: str) -> str:
    return _display_datetime(value).strftime("%Y-%m-%d")


def format_display_month(value: str) -> str:
    return _display_datetime(value).strftime("%Y-%m")
