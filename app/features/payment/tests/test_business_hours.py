"""Tests for app.features.payment.business_hours.

Covers:
- is_business_time: weekday in/out, lunch hole, end-exclusive boundary
- next_business_window_after: during block (no-op), lunch → same-day 14:00,
  Friday end → Monday 08:00, weekend → Monday 08:00
"""

from __future__ import annotations

import datetime
import zoneinfo

import pytest  # type: ignore[import-not-found]

TZ_CO = zoneinfo.ZoneInfo("America/Bogota")
UTC = datetime.UTC


def _bogota(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime.datetime:
    """Construct a tz-aware datetime in America/Bogota."""
    return datetime.datetime(year, month, day, hour, minute, tzinfo=TZ_CO)


def _to_utc(dt: datetime.datetime) -> datetime.datetime:
    """Convert a Bogota datetime to UTC for next_business_window_after."""
    return dt.astimezone(UTC)


class TestIsBusinessTime:
    def test_monday_morning_is_business(self) -> None:
        """Monday 10:00 Bogota is within a business block."""
        from app.features.payment.business_hours import is_business_time

        dt = _bogota(2026, 6, 29, 10, 0)  # 2026-06-29 is Monday
        assert is_business_time(dt) is True

    def test_monday_lunch_is_not_business(self) -> None:
        """Monday 13:00 Bogota (lunch) is NOT in any business block."""
        from app.features.payment.business_hours import is_business_time

        dt = _bogota(2026, 6, 29, 13, 0)
        assert is_business_time(dt) is False

    def test_saturday_is_not_business(self) -> None:
        """Saturday 10:00 Bogota is NOT a business day."""
        from app.features.payment.business_hours import is_business_time

        dt = _bogota(2026, 7, 4, 10, 0)  # 2026-07-04 is Saturday
        assert is_business_time(dt) is False

    def test_friday_16_is_not_business(self) -> None:
        """Friday 16:00 is end-exclusive — NOT in business block."""
        from app.features.payment.business_hours import is_business_time

        dt = _bogota(2026, 7, 3, 16, 0)  # 2026-07-03 is Friday
        assert is_business_time(dt) is False

    def test_friday_1559_is_business(self) -> None:
        """Friday 15:59 is still inside the afternoon block."""
        from app.features.payment.business_hours import is_business_time

        dt = _bogota(2026, 7, 3, 15, 59)
        assert is_business_time(dt) is True

    def test_monday_after_lunch_is_business(self) -> None:
        """Monday 14:00 (first minute of afternoon block) is business time."""
        from app.features.payment.business_hours import is_business_time

        dt = _bogota(2026, 6, 29, 14, 0)
        assert is_business_time(dt) is True

    def test_monday_before_open_is_not_business(self) -> None:
        """Monday 07:59 is before the first block opens."""
        from app.features.payment.business_hours import is_business_time

        dt = _bogota(2026, 6, 29, 7, 59)
        assert is_business_time(dt) is False


class TestNextBusinessWindowAfter:
    def test_during_block_returns_unchanged(self) -> None:
        """If already in a business window, return the input unchanged."""
        from app.features.payment.business_hours import next_business_window_after

        dt_co = _bogota(2026, 6, 29, 10, 30)  # Monday 10:30
        dt_utc = _to_utc(dt_co)
        result = next_business_window_after(dt_utc)
        assert result == dt_utc

    def test_lunch_returns_same_day_1400(self) -> None:
        """Tuesday 13:00 Bogota → returns Tuesday 14:00 Bogota as UTC."""
        from app.features.payment.business_hours import next_business_window_after

        dt_co = _bogota(2026, 6, 30, 13, 0)  # Tuesday 13:00
        dt_utc = _to_utc(dt_co)
        result = next_business_window_after(dt_utc)

        expected_co = _bogota(2026, 6, 30, 14, 0)
        expected_utc = _to_utc(expected_co)
        assert result == expected_utc

    def test_friday_end_returns_monday_0800(self) -> None:
        """Friday 15:59 + 1 minute (after close) → Monday 08:00 Bogota as UTC."""
        from app.features.payment.business_hours import next_business_window_after

        # Friday after close
        dt_co = _bogota(2026, 7, 3, 16, 5)  # Friday 16:05 — past end
        dt_utc = _to_utc(dt_co)
        result = next_business_window_after(dt_utc)

        expected_co = _bogota(2026, 7, 6, 8, 0)  # Monday 08:00
        expected_utc = _to_utc(expected_co)
        assert result == expected_utc

    def test_saturday_returns_monday_0800(self) -> None:
        """Saturday morning → Monday 08:00 Bogota as UTC."""
        from app.features.payment.business_hours import next_business_window_after

        dt_co = _bogota(2026, 7, 4, 10, 0)  # Saturday 10:00
        dt_utc = _to_utc(dt_co)
        result = next_business_window_after(dt_utc)

        expected_co = _bogota(2026, 7, 6, 8, 0)  # Monday 08:00
        expected_utc = _to_utc(expected_co)
        assert result == expected_utc

    def test_sunday_returns_monday_0800(self) -> None:
        """Sunday → Monday 08:00 Bogota as UTC."""
        from app.features.payment.business_hours import next_business_window_after

        dt_co = _bogota(2026, 7, 5, 9, 0)  # Sunday 09:00
        dt_utc = _to_utc(dt_co)
        result = next_business_window_after(dt_utc)

        expected_co = _bogota(2026, 7, 6, 8, 0)  # Monday 08:00
        expected_utc = _to_utc(expected_co)
        assert result == expected_utc

    def test_raises_on_naive_datetime(self) -> None:
        """Naive datetime must raise ValueError."""
        from app.features.payment.business_hours import next_business_window_after

        naive = datetime.datetime(2026, 6, 29, 10, 0)
        with pytest.raises((ValueError, AssertionError)):
            next_business_window_after(naive)
