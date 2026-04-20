"""Time string utilities for speed skating results."""

import re


def parse_time(time_str: str) -> float | None:
    """
    Convert a race time string to total seconds.

    Handles formats:
      "1:23.456"  → 83.456
      "0:51.630"  → 51.630
      "51.630"    → 51.630
      "2:15.874"  → 135.874
      "14.250"    → 14.250
    Returns None for non-time strings (DNS, DNF, PEN, etc.).
    """
    if not time_str:
        return None
    s = time_str.strip()

    # M:SS.mmm
    m = re.fullmatch(r'(\d+):(\d{2})\.(\d+)', s)
    if m:
        minutes = int(m.group(1))
        seconds = int(m.group(2))
        frac = float('0.' + m.group(3))
        return round(minutes * 60 + seconds + frac, 4)

    # SS.mmm (no minutes)
    m = re.fullmatch(r'(\d+)\.(\d+)', s)
    if m:
        return round(float(s), 4)

    return None


def is_time(token: str) -> bool:
    """Return True if token looks like a race time."""
    return bool(re.fullmatch(r'\d+:\d{2}\.\d+', token.strip()) or
                re.fullmatch(r'\d+\.\d+', token.strip()))


# Matches a standard race time: "1:23.456"
TIME_RE = re.compile(r'^\d+:\d{2}\.\d{3}$')

# Status codes that may appear in place of a time
STATUS_CODES = frozenset({"DNS", "DNF", "DQ", "PEN", "ADV", "DSQ", "EX", "FNT"})

# Qualification/advancement flags (not counted as status)
QUAL_FLAGS = frozenset({"Q", "ADV"})


def is_status(token: str) -> bool:
    return token.strip().upper() in STATUS_CODES
