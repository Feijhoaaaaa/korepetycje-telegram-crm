from datetime import date, time
from zoneinfo import ZoneInfo

import pytest

from app.utils.dates import local_to_utc, month_bounds, week_bounds

WARSAW = ZoneInfo("Europe/Warsaw")


def test_regular_local_time_converts_to_utc() -> None:
    result = local_to_utc(date(2026, 9, 24), time(17, 0), WARSAW)
    assert result.isoformat() == "2026-09-24T15:00:00+00:00"


@pytest.mark.parametrize(
    ("day", "clock", "reason"),
    [
        (date(2026, 3, 29), time(2, 30), "nonexistent_local_time"),
        (date(2026, 10, 25), time(2, 30), "ambiguous_local_time"),
    ],
)
def test_dst_edge_times_are_rejected(day: date, clock: time, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        local_to_utc(day, clock, WARSAW)


def test_week_is_monday_through_sunday() -> None:
    start, end = week_bounds(date(2026, 9, 23), WARSAW)
    assert start.astimezone(WARSAW).date() == date(2026, 9, 21)
    assert end.astimezone(WARSAW).date() == date(2026, 9, 28)


def test_month_bounds_handle_year_change() -> None:
    start, end = month_bounds(date(2026, 12, 20), WARSAW)
    assert start.astimezone(WARSAW).date() == date(2026, 12, 1)
    assert end.astimezone(WARSAW).date() == date(2027, 1, 1)
