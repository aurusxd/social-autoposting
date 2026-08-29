from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

# The panel is operated from Moscow, so a delayed post is always planned in
# Moscow time no matter where the browser is. Moscow has kept a fixed UTC+3
# without DST since 2014, so a plain offset is exact and needs no tz database.
MOSCOW = timezone(timedelta(hours=3), "MSK")
MOSCOW_LABEL = "МСК"

# A year is far beyond any realistic content plan, so anything past it is a
# mistyped date rather than an intent.
MAX_LEAD_TIME = timedelta(days=365)


class ScheduleError(ValueError):
    """Raised when a requested publication time cannot be honoured."""


def utc_now() -> datetime:
    """Current UTC moment without a tzinfo, matching the stored timestamps."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_moscow(moment: datetime) -> datetime:
    """Read a stored naive UTC timestamp as Moscow wall clock."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(MOSCOW)


def format_moscow(moment: datetime | None) -> str:
    if moment is None:
        return ""
    return f"{to_moscow(moment).strftime('%d.%m.%Y %H:%M')} {MOSCOW_LABEL}"


def moscow_input_value(moment: datetime | None) -> str:
    """Render a stored moment for an `<input type="datetime-local">`."""
    if moment is None:
        return ""
    return to_moscow(moment).strftime("%Y-%m-%dT%H:%M")


def normalize_schedule(value: datetime | None) -> datetime | None:
    """Turn a requested publication time into the naive UTC value the DB stores.

    A moment without an offset is Moscow time: that is what the panel's date
    field holds, and reading it any other way would silently shift the post.
    """
    if value is None:
        return None

    aware = value if value.tzinfo is not None else value.replace(tzinfo=MOSCOW)
    moment = aware.astimezone(UTC).replace(tzinfo=None, microsecond=0)

    now = utc_now()
    if moment <= now:
        raise ScheduleError("Время публикации должно быть в будущем")
    if moment - now > MAX_LEAD_TIME:
        raise ScheduleError("Публикацию нельзя отложить больше чем на год")
    return moment
