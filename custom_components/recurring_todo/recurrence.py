"""Recurrence calculation for recurring tasks (no Home Assistant imports)."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, tzinfo

from .const import FREQ_DAILY, FREQ_MONTHLY, FREQ_WEEKLY, WEEKDAYS


def _matches(task: dict, day: date, start: date) -> bool:
    """Return True if the task is scheduled on the given day."""
    freq = task["frequency"]
    interval = max(1, int(task.get("interval", 1)))
    if day < start:
        return False

    if freq == FREQ_DAILY:
        return (day - start).days % interval == 0

    if freq == FREQ_WEEKLY:
        weekdays = task.get("weekdays") or [WEEKDAYS[start.weekday()]]
        if WEEKDAYS[day.weekday()] not in weekdays:
            return False
        start_week = start - timedelta(days=start.weekday())
        week_index = (day - start_week).days // 7
        return week_index % interval == 0

    if freq == FREQ_MONTHLY:
        month_index = (day.year - start.year) * 12 + (day.month - start.month)
        if month_index % interval != 0:
            return False
        wanted = int(task.get("day_of_month") or start.day)
        last_day = calendar.monthrange(day.year, day.month)[1]
        return day.day == min(wanted, last_day)

    raise ValueError(f"Unknown frequency: {freq}")


def next_occurrence(task: dict, after: datetime, tz: tzinfo) -> datetime | None:
    """Return the first scheduled datetime strictly after `after`."""
    start = date.fromisoformat(task["start_date"])
    at = time.fromisoformat(task.get("time") or "09:00:00")
    after_local = after.astimezone(tz)
    day = max(start, after_local.date())
    interval = max(1, int(task.get("interval", 1)))
    # Monthly with interval N can need up to ~N*31 days; weekly N*7.
    limit = 400 * interval
    for _ in range(limit):
        if _matches(task, day, start):
            candidate = datetime.combine(day, at, tzinfo=tz)
            if candidate > after_local:
                return candidate
        day += timedelta(days=1)
    return None
