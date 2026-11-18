from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.recurring_todo.recurrence import next_occurrence

TZ = ZoneInfo("Europe/Helsinki")


def dt(*a):
    return datetime(*a, tzinfo=TZ)


def test_weekly_saturday():
    t = {"frequency": "weekly", "weekdays": ["sat"], "time": "09:00:00", "start_date": "2026-11-18"}
    assert next_occurrence(t, dt(2026, 11, 18, 10), TZ) == dt(2026, 11, 21, 9)
    assert next_occurrence(t, dt(2026, 11, 21, 9), TZ) == dt(2026, 11, 28, 9)


def test_every_second_week():
    t = {"frequency": "weekly", "interval": 2, "weekdays": ["mon", "thu"], "time": "08:00:00", "start_date": "2026-11-16"}
    assert next_occurrence(t, dt(2026, 11, 16, 9), TZ) == dt(2026, 11, 19, 8)
    assert next_occurrence(t, dt(2026, 11, 19, 9), TZ) == dt(2026, 11, 30, 8)


def test_monthly_31_clamps():
    t = {"frequency": "monthly", "day_of_month": 31, "time": "09:00:00", "start_date": "2026-01-01"}
    assert next_occurrence(t, dt(2026, 2, 1), TZ) == dt(2026, 2, 28, 9)
    assert next_occurrence(t, dt(2026, 2, 28, 10), TZ) == dt(2026, 3, 31, 9)


def test_quarterly():
    t = {"frequency": "monthly", "interval": 3, "day_of_month": 1, "time": "09:00:00", "start_date": "2026-01-01"}
    assert next_occurrence(t, dt(2026, 1, 1, 10), TZ) == dt(2026, 4, 1, 9)


def test_daily_dst():
    t = {"frequency": "daily", "time": "09:00:00", "start_date": "2026-03-28"}
    n = next_occurrence(t, dt(2026, 3, 28, 10), TZ)
    assert n == dt(2026, 3, 29, 9) and n.utcoffset().total_seconds() == 3 * 3600
