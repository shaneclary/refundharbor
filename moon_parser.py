# moon_parser.py — Parse NASA moon rise/set/transit data
#
# Extracts transit times from USNO data for full moon harvesting.

import re
from pathlib import Path

# Path to the NASA HTML file
NASA_DATA_PATH = Path(__file__).parent / "moon" / "Rise_Set_Transit Times for Major Solar System Bodies and Bright Stars.html"


def parse_nasa_moon_data() -> dict[str, dict]:
    """
    Parse NASA moon data and extract transit times for each date.

    Returns dict mapping date string (YYYY-MM-DD) to:
    {
        "rise": "HH:MM" or None,
        "transit": "HH:MM" or None,
        "set": "HH:MM" or None,
        "transit_alt": "61S" (altitude in degrees, S=south)
    }
    """
    if not NASA_DATA_PATH.exists():
        return {}

    content = NASA_DATA_PATH.read_text(encoding="utf-8", errors="ignore")

    # Pattern to match data lines like:
    # 2026 Mar 03 (Tue)        18:34  87        00:08 61S        06:35 277
    pattern = re.compile(
        r"(\d{4})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})"  # Date
        r"\s+\([A-Za-z]+\)"  # Day of week
        r"\s+(\d{2}:\d{2})?\s*(\d+)?"  # Rise time and azimuth (optional)
        r"\s+(\d{2}:\d{2})?\s*(\d+[SN])?"  # Transit time and altitude (optional)
        r"\s+(\d{2}:\d{2})?\s*(\d+)?"  # Set time and azimuth (optional)
    )

    month_map = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
        "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
        "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"
    }

    results = {}

    for line in content.split("\n"):
        # Quick filter - must start with a year
        if not line.strip().startswith("202"):
            continue

        match = pattern.search(line)
        if match:
            year, month_str, day = match.group(1), match.group(2), match.group(3)
            month = month_map.get(month_str, "01")
            date_key = f"{year}-{month}-{day}"

            rise_time = match.group(4)
            transit_time = match.group(6)
            transit_alt = match.group(7)
            set_time = match.group(8)

            results[date_key] = {
                "rise": rise_time,
                "transit": transit_time,
                "transit_alt": transit_alt,
                "set": set_time,
            }

    return results


def get_transit_time(date_str: str) -> str | None:
    """
    Get the moon transit time for a specific date.

    Args:
        date_str: Date in YYYY-MM-DD format

    Returns:
        Transit time as "HH:MM" or None if not found
    """
    data = parse_nasa_moon_data()
    entry = data.get(date_str)
    if entry:
        return entry.get("transit")
    return None


def get_moon_data_for_date(date_str: str) -> dict | None:
    """Get all moon data for a specific date."""
    data = parse_nasa_moon_data()
    return data.get(date_str)


if __name__ == "__main__":
    # Test parsing
    data = parse_nasa_moon_data()
    print(f"Parsed {len(data)} dates")

    # Show some full moon dates
    full_moon_dates = [
        "2026-03-03", "2026-04-02", "2026-05-01", "2026-05-31",
        "2026-06-29", "2026-07-29", "2026-08-28", "2026-09-26",
    ]

    print("\nFull moon transit times (PST):")
    for date in full_moon_dates:
        entry = data.get(date)
        if entry:
            print(f"  {date}: Transit at {entry['transit']} (alt: {entry['transit_alt']})")
        else:
            print(f"  {date}: No data found")
