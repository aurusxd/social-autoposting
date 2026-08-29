from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.scheduling import (
    MOSCOW,
    ScheduleError,
    format_moscow,
    moscow_input_value,
    normalize_schedule,
    utc_now,
)


def test_a_moment_without_an_offset_is_read_as_moscow_time() -> None:
    stored = normalize_schedule(datetime(2027, 3, 1, 12, 0))

    assert stored == datetime(2027, 3, 1, 9, 0)


def test_an_offset_aware_moment_keeps_the_instant() -> None:
    berlin = timezone(timedelta(hours=2))

    stored = normalize_schedule(datetime(2027, 3, 1, 11, 0, tzinfo=berlin))

    assert stored == datetime(2027, 3, 1, 9, 0)


def test_no_time_means_publish_at_once() -> None:
    assert normalize_schedule(None) is None


def test_a_time_in_the_past_is_refused() -> None:
    past = (utc_now() - timedelta(minutes=1)).replace(tzinfo=UTC).astimezone(MOSCOW)

    with pytest.raises(ScheduleError, match="в будущем"):
        normalize_schedule(past.replace(tzinfo=None))


def test_a_time_beyond_a_year_is_refused() -> None:
    far = (utc_now() + timedelta(days=366)).replace(tzinfo=UTC).astimezone(MOSCOW)

    with pytest.raises(ScheduleError, match="год"):
        normalize_schedule(far.replace(tzinfo=None))


def test_stored_moments_are_shown_in_moscow_time() -> None:
    stored = datetime(2027, 3, 1, 9, 0)

    assert format_moscow(stored) == "01.03.2027 12:00 МСК"
    assert moscow_input_value(stored) == "2027-03-01T12:00"


def test_a_missing_moment_renders_as_nothing() -> None:
    assert format_moscow(None) == ""
    assert moscow_input_value(None) == ""
