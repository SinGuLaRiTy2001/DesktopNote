"""Due dates of reminders: their state, how they read, and the steps of the time picker."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from .i18n import month_fields, tr
from .models import Item


def reminder_state(item: Item, soon_minutes: int, now: datetime | None = None) -> str:
    """Returns the reminder state of an item.

    Args:
        item: The item to classify.
        soon_minutes: How close to the due time an item counts as "soon".
        now: The reference time; defaults to the current time.

    Returns:
        One of "done", "none", "normal", "soon" or "overdue".
    """
    if item.done:
        return "done"
    due = item.due_dt
    if due is None:
        return "none"
    now = now or datetime.now()
    if now >= due:
        return "overdue"
    if due - now <= timedelta(minutes=soon_minutes):
        return "soon"
    return "normal"


def format_due(dt: datetime, now: datetime | None = None) -> str:
    """Returns a localised, relative label for a due time ("Today 14:00", "Tomorrow ...").

    Args:
        dt: The due time to format.
        now: The reference time; defaults to the current time.
    """
    now = now or datetime.now()
    day, today, hm = dt.date(), now.date(), dt.strftime("%H:%M")
    if day == today:
        return tr("date.today", hm=hm)
    if day == today + timedelta(days=1):
        return tr("date.tomorrow", hm=hm)
    if day == today - timedelta(days=1):
        return tr("date.yesterday", hm=hm)
    key = "date.this_year" if day.year == today.year else "date.other_year"
    return tr(key, hm=hm, **month_fields(dt))


def humanize_delta(delta: timedelta) -> str:
    """Returns a short localised duration such as "5 min", "2 h 10 min" or "3 days".

    The duration is rounded up to whole minutes, with a minimum of one minute.
    """
    mins = max(1, math.ceil(delta.total_seconds() / 60))
    if mins < 60:
        return tr("delta.minutes", n=mins)
    hours, rem = divmod(mins, 60)
    if hours < 24:
        return tr("delta.hours", n=hours) + (" " + tr("delta.minutes", n=rem) if rem else "")
    return tr("delta.days", n=hours // 24)


def half_hour_step(minutes: int, steps: int) -> tuple[int, int]:
    """Moves a time of day to the next or previous :00/:30 boundary, ``|steps|`` times.

    Args:
        minutes: Time of day in minutes since midnight.
        steps: Number of boundaries to move; positive moves forward, negative backward.

    Returns:
        A ``(day_change, minutes)`` tuple, where ``day_change`` is the number of days crossed.
    """
    for _ in range(abs(steps)):
        minutes = (minutes // 30 + 1) * 30 if steps > 0 else (minutes - 1) // 30 * 30
    return divmod(minutes, 24 * 60)


def default_due(now: datetime | None = None) -> datetime:
    """Returns the default due time for a new reminder: two hours after the current full hour."""
    now = now or datetime.now()
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
