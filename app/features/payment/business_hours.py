"""Business hours helper for DPG cartera schedule (D-10, Phase 4).

Pure module — no imports from app.*, no I/O, no mutable module state.
Timers for the payment flow use these functions to:
  1. Decide whether cartera is available right now.
  2. Compute the next window start for scheduling reminder and escalation jobs.

Schedule (D-10): Monday-Friday, 08:00-12:00 + 14:00-16:00 America/Bogota (UTC-5).
Colombia does NOT observe DST, so UTC-5 is fixed year-round.

Exports:
  TZ_CO                     — ZoneInfo("America/Bogota")
  WORKDAY_BLOCKS             — tuple of (start_h, start_m, end_h, end_m)
  is_business_time(dt)       — True iff dt falls within a work block
  next_business_window_after(dt_utc) — UTC datetime of next block open
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


__all__ = ["WORKDAY_BLOCKS", "TZ_CO", "is_business_time", "next_business_window_after"]
