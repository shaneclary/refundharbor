# full_moon.py — Full Moon Harvest Calendar (PST/PDT)
#
# Calculates full moon dates for the Pacific timezone.
# Traditional farming calendar approach: harvest on the full moon.
#
# Uses NASA/USNO data for precise moon transit times (when moon is highest).

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)

# Try to use zoneinfo for proper timezone handling, fallback to fixed offset
try:
    from zoneinfo import ZoneInfo
    PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:
    # Fallback: use fixed UTC-8 offset (PST, ignoring DST for simplicity)
    # This is acceptable since we only care about the date, not exact time
    PACIFIC = timezone(timedelta(hours=-8), "PST")


def _get_nasa_transit_time(date_str: str) -> str | None:
    """Get transit time from NASA data if available."""
    try:
        from moon_parser import get_transit_time
        return get_transit_time(date_str)
    except Exception:
        return None

# Full moon dates 2024-2027 (UTC times, converted to PST at runtime)
# Source: astronomical calculations / USNO
# Format: (year, month, day, hour, minute) in UTC
FULL_MOON_DATES_UTC = [
    # 2024
    (2024, 1, 25, 17, 54),
    (2024, 2, 24, 12, 30),
    (2024, 3, 25, 7, 0),
    (2024, 4, 23, 23, 49),
    (2024, 5, 23, 13, 53),
    (2024, 6, 21, 21, 8),
    (2024, 7, 21, 10, 17),
    (2024, 8, 19, 18, 26),
    (2024, 9, 18, 2, 34),
    (2024, 10, 17, 11, 26),
    (2024, 11, 15, 21, 29),
    (2024, 12, 15, 9, 2),
    # 2025
    (2025, 1, 13, 22, 27),
    (2025, 2, 12, 13, 53),
    (2025, 3, 14, 6, 55),
    (2025, 4, 13, 0, 22),
    (2025, 5, 12, 16, 56),
    (2025, 6, 11, 7, 44),
    (2025, 7, 10, 20, 37),
    (2025, 8, 9, 7, 55),
    (2025, 9, 7, 18, 9),
    (2025, 10, 7, 3, 48),
    (2025, 11, 5, 13, 19),
    (2025, 12, 4, 23, 14),
    # 2026
    (2026, 1, 3, 10, 3),
    (2026, 2, 1, 22, 9),
    (2026, 3, 3, 11, 38),
    (2026, 4, 2, 2, 12),
    (2026, 5, 1, 17, 23),
    (2026, 5, 31, 8, 45),
    (2026, 6, 29, 23, 57),
    (2026, 7, 29, 14, 36),
    (2026, 8, 28, 4, 19),
    (2026, 9, 26, 16, 49),
    (2026, 10, 26, 4, 12),
    (2026, 11, 24, 14, 53),
    (2026, 12, 24, 1, 28),
    # 2027
    (2027, 1, 22, 12, 17),
    (2027, 2, 20, 23, 24),
    (2027, 3, 22, 10, 44),
    (2027, 4, 20, 22, 27),
    (2027, 5, 20, 10, 59),
    (2027, 6, 19, 0, 45),
    (2027, 7, 18, 15, 45),
    (2027, 8, 17, 7, 29),
    (2027, 9, 15, 23, 4),
    (2027, 10, 15, 13, 47),
    (2027, 11, 14, 3, 26),
    (2027, 12, 13, 16, 9),
]


def _utc_to_pacific(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """Convert UTC datetime to Pacific time."""
    utc_dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    return utc_dt.astimezone(PACIFIC)


def get_full_moon_dates_pst() -> list[datetime]:
    """Get all full moon dates in Pacific time."""
    return [_utc_to_pacific(*dt) for dt in FULL_MOON_DATES_UTC]


def get_next_full_moon(from_date: Optional[datetime] = None) -> datetime:
    """
    Get the next full moon date/time in Pacific timezone.
    If from_date is None, uses current time in Pacific.
    """
    if from_date is None:
        from_date = datetime.now(PACIFIC)
    elif from_date.tzinfo is None:
        from_date = from_date.replace(tzinfo=PACIFIC)

    full_moons = get_full_moon_dates_pst()
    for moon in full_moons:
        if moon > from_date:
            return moon

    # Fallback: estimate next full moon ~29.5 days after last known
    last_moon = full_moons[-1]
    return last_moon + timedelta(days=29.53)


def get_current_or_recent_full_moon(within_hours: int = 24) -> Optional[datetime]:
    """
    Get the full moon if we're within `within_hours` of one.
    Returns None if no full moon is within range.
    """
    now = datetime.now(PACIFIC)
    full_moons = get_full_moon_dates_pst()

    for moon in full_moons:
        delta = abs((now - moon).total_seconds() / 3600)
        if delta <= within_hours:
            return moon

    return None


def is_full_moon_day(date: Optional[datetime] = None) -> bool:
    """
    Check if the given date (or today) is a full moon day in Pacific time.
    Returns True if the full moon occurs on this calendar day.
    """
    if date is None:
        date = datetime.now(PACIFIC)
    elif date.tzinfo is None:
        date = date.replace(tzinfo=PACIFIC)

    target_date = date.date()
    full_moons = get_full_moon_dates_pst()

    for moon in full_moons:
        if moon.date() == target_date:
            return True

    return False


def get_full_moon_for_date(date: datetime) -> Optional[str]:
    """
    If the given date is a full moon day, return the full moon date string.
    Otherwise return None.
    """
    if date.tzinfo is None:
        date = date.replace(tzinfo=PACIFIC)

    target_date = date.date()
    full_moons = get_full_moon_dates_pst()

    for moon in full_moons:
        if moon.date() == target_date:
            return moon.strftime("%Y-%m-%d")

    return None


def get_days_until_full_moon() -> int:
    """Get the number of days until the next full moon."""
    now = datetime.now(PACIFIC)
    next_moon = get_next_full_moon(now)
    delta = next_moon.date() - now.date()
    return delta.days


def get_harvest_time_for_date(date_str: str) -> str | None:
    """
    Get the precise harvest time (moon transit) for a full moon date.

    Transit is when the moon crosses the meridian - its highest point.
    This is the fullest, most visible moment of the full moon.

    Returns time as "HH:MM" in PST, or None if no data.
    """
    return _get_nasa_transit_time(date_str)


def is_harvest_time(tolerance_minutes: int = 30) -> bool:
    """
    Check if we're within the harvest window (near moon transit).

    Args:
        tolerance_minutes: Minutes before/after transit to consider valid

    Returns:
        True if current time is within tolerance of moon transit on full moon day
    """
    now = datetime.now(PACIFIC)

    if not is_full_moon_day(now):
        return False

    date_str = now.strftime("%Y-%m-%d")
    transit_time = get_harvest_time_for_date(date_str)

    if not transit_time:
        # No NASA data - fall back to any time on full moon day
        return True

    # Parse transit time and compare
    try:
        hour, minute = map(int, transit_time.split(":"))
        transit_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # Handle overnight transits (e.g., 00:08 is technically next day)
        # If transit is past midnight but we're before midnight, adjust
        delta = abs((now - transit_dt).total_seconds() / 60)
        return delta <= tolerance_minutes
    except Exception:
        return True  # Fall back to any time on full moon day


def get_harvest_status() -> dict:
    """
    Get current harvest status including:
    - Whether today is a full moon
    - Days until next full moon
    - Next full moon date
    - Precise transit time from NASA data
    - Whether we're in the harvest window
    """
    now = datetime.now(PACIFIC)
    next_moon = get_next_full_moon(now)
    recent_moon = get_current_or_recent_full_moon(within_hours=24)

    # Get transit time for next/current full moon
    next_date_str = next_moon.strftime("%Y-%m-%d")
    transit_time = get_harvest_time_for_date(next_date_str)

    is_moon_day = is_full_moon_day(now)
    harvest_ready = is_harvest_time() if is_moon_day else False

    return {
        "is_full_moon_day": is_moon_day,
        "days_until_full_moon": get_days_until_full_moon(),
        "next_full_moon": next_moon.strftime("%Y-%m-%d %H:%M PST"),
        "next_full_moon_date": next_date_str,
        "transit_time": transit_time,  # When moon is at peak (highest point)
        "current_time_pst": now.strftime("%Y-%m-%d %H:%M PST"),
        "recent_full_moon": recent_moon.strftime("%Y-%m-%d") if recent_moon else None,
        "harvest_window_open": recent_moon is not None,
        "harvest_ready": harvest_ready,  # True if within 30min of transit
    }


def format_moon_phase_display(days_until: int) -> str:
    """Return a display string for the moon phase based on days until full."""
    if days_until == 0:
        return "🌕 Full Moon - Harvest Day!"
    elif days_until == 1:
        return "🌖 Almost Full - Harvest Tomorrow"
    elif days_until <= 7:
        return f"🌔 Waxing Gibbous - {days_until} days to harvest"
    elif days_until <= 14:
        return f"🌓 First Quarter - {days_until} days to harvest"
    elif days_until <= 21:
        return f"🌒 Waxing Crescent - {days_until} days to harvest"
    else:
        return f"🌑 New Moon Phase - {days_until} days to harvest"
