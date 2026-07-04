"""Business hours helper for DPG cartera schedule (D-10, Phase 4).

Pure module — no imports from app.*, no I/O, no mutable module state.
Timers for the payment flow use these functions to:
  1. Decide whether cartera is available right now.
  2. Compute the next window start for scheduling reminder and escalation jobs.
  3. Count business minutes elapsed between two UTC datetimes (D-11/D-12 timers).

Schedule (D-10): Monday-Friday, 08:00-12:00 + 14:00-16:00 America/Bogota (UTC-5).
Colombia does NOT observe DST, so UTC-5 is fixed year-round.

Exports:
  TZ_CO                     — ZoneInfo("America/Bogota")
  WORKDAY_BLOCKS             — tuple of (start_h, start_m, end_h, end_m)
  is_business_time(dt)       — True iff dt falls within a work block
  next_business_window_after(dt_utc) — UTC datetime of next block open
  business_minutes_between(start_utc, end_utc) — business minutes in [start, end)
"""

from __future__ import annotations

import datetime
import zoneinfo

TZ_CO = zoneinfo.ZoneInfo("America/Bogota")

# Schedule per D-10: each entry is (start_hour, start_min, end_hour, end_min).
# Blocks are start-inclusive, end-exclusive.
WORKDAY_BLOCKS: tuple[tuple[int, int, int, int], ...] = (
    (8, 0, 12, 0),
    (14, 0, 16, 0),
)


def is_business_time(dt: datetime.datetime) -> bool:
    """Return True iff ``dt`` falls within a cartera business block.

    Args:
        dt: A tz-aware datetime. Converted to America/Bogota internally.

    Returns:
        True if weekday (Mon-Fri) AND dt is inside one of ``WORKDAY_BLOCKS``
        (start inclusive, end exclusive). False otherwise.
    """
    dt_co = dt.astimezone(TZ_CO)
    if dt_co.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    for sh, sm, eh, em in WORKDAY_BLOCKS:
        block_start = dt_co.replace(hour=sh, minute=sm, second=0, microsecond=0)
        block_end = dt_co.replace(hour=eh, minute=em, second=0, microsecond=0)
        if block_start <= dt_co < block_end:
            return True
    return False


def next_business_window_after(dt_utc: datetime.datetime) -> datetime.datetime:
    """Return the UTC datetime when the next cartera business block opens.

    If ``dt_utc`` is already inside a business block, return ``dt_utc``
    unchanged. Otherwise, walk forward (up to 14 days) to find the next
    block start.

    Args:
        dt_utc: A tz-aware datetime. Must have tzinfo (naive raises ValueError).

    Returns:
        A tz-aware UTC datetime representing the start of the next block.

    Raises:
        ValueError: if ``dt_utc`` is naive (no tzinfo).
    """
    if dt_utc.tzinfo is None:
        raise ValueError("next_business_window_after requires a tz-aware datetime; got naive.")

    if is_business_time(dt_utc):
        return dt_utc

    dt_co = dt_utc.astimezone(TZ_CO)
    candidate = dt_co

    for _ in range(14):  # safety bound: max 2 weeks
        if candidate.weekday() < 5:  # weekday — check each block start
            for sh, sm, _eh, _em in WORKDAY_BLOCKS:
                block_start = candidate.replace(hour=sh, minute=sm, second=0, microsecond=0)
                if block_start > candidate:
                    return block_start.astimezone(datetime.UTC)

        # No block found on candidate's day — move to next day at midnight
        candidate = (candidate + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    raise RuntimeError(f"Could not find next business window within 14 days of {dt_utc!r}")


def business_minutes_between(start_utc: datetime.datetime, end_utc: datetime.datetime) -> int:
    """Count business minutes in [start_utc, end_utc).

    Converts both datetimes to America/Bogota, then walks minute by minute
    from start to end counting only minutes that fall inside a WORKDAY_BLOCK
    on a weekday.

    Implementation uses a day-by-day loop (max 60 days safety bound) that sums
    the overlap of each WORKDAY_BLOCK with the [start, end) interval, working
    entirely in minutes to avoid floating-point rounding. The inner loop over
    WORKDAY_BLOCKS is O(1) per day (constant 2 blocks).

    Args:
        start_utc: tz-aware UTC datetime (start of interval, inclusive).
        end_utc:   tz-aware UTC datetime (end of interval, exclusive).

    Returns:
        Non-negative integer count of business minutes in [start_utc, end_utc).
        Returns 0 when start_utc >= end_utc.
    """
    if start_utc >= end_utc:
        return 0

    # Convert to Bogota timezone for day-level comparisons.
    start_co = start_utc.astimezone(TZ_CO)
    end_co = end_utc.astimezone(TZ_CO)

    total_minutes = 0
    # Candidate day starts at the calendar day of start_co.
    current_day = start_co.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end_co.replace(hour=0, minute=0, second=0, microsecond=0)

    # Safety bound: max 60 calendar days to avoid infinite loop on bad input.
    for _ in range(60):
        if current_day > end_day:
            break

        # Skip weekends.
        if current_day.weekday() < 5:
            for sh, sm, eh, em in WORKDAY_BLOCKS:
                # Block boundaries on this calendar day (in Bogota).
                block_start = current_day.replace(hour=sh, minute=sm, second=0, microsecond=0)
                block_end = current_day.replace(hour=eh, minute=em, second=0, microsecond=0)

                # Clamp to [start_co, end_co).
                overlap_start = max(block_start, start_co)
                overlap_end = min(block_end, end_co)

                if overlap_end > overlap_start:
                    delta = overlap_end - overlap_start
                    total_minutes += int(delta.total_seconds()) // 60

        current_day += datetime.timedelta(days=1)

    return total_minutes


__all__ = [
    "WORKDAY_BLOCKS",
    "TZ_CO",
    "business_minutes_between",
    "is_business_time",
    "next_business_window_after",
]
