from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def local_to_utc(day: date, clock: time, timezone: ZoneInfo) -> datetime:
    """Convert an unambiguous, existing local wall time to UTC."""
    naive = datetime.combine(day, clock)
    candidates: list[datetime] = []
    for fold in (0, 1):
        local = naive.replace(tzinfo=timezone, fold=fold)
        utc = local.astimezone(UTC)
        if utc.astimezone(timezone).replace(tzinfo=None) == naive and utc not in candidates:
            candidates.append(utc)
    if not candidates:
        raise ValueError("nonexistent_local_time")
    if len(candidates) > 1:
        raise ValueError("ambiguous_local_time")
    return candidates[0]


def utc_to_local(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone)


def day_bounds(day: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, timezone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, timezone).astimezone(UTC)
    return start, end


def week_bounds(day: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    monday = day - timedelta(days=day.weekday())
    start = datetime.combine(monday, time.min, timezone).astimezone(UTC)
    end = datetime.combine(monday + timedelta(days=7), time.min, timezone).astimezone(UTC)
    return start, end


def month_bounds(day: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    first = day.replace(day=1)
    next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (
        datetime.combine(first, time.min, timezone).astimezone(UTC),
        datetime.combine(next_month, time.min, timezone).astimezone(UTC),
    )
