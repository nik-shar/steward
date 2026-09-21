"""Turning what a person said into grid numbers — and only that.

Split from `grid.py` deliberately: this module answers "what did they mean?" and
knows nothing about slots, while `grid.py` answers "does it fit?" and knows nothing
about text. Keeping them apart is what makes both testable in isolation, and what
stops tolerant input parsing from quietly weakening the arithmetic.

Parsing is *tolerant where it is unambiguous* — `14:00`, `14`, `1400` and `2pm` are
the same time — and refuses outright where it would have to guess. A calendar that
silently books the wrong hour is worse than one that asks again.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from steward.packs.calendar.grid import (
    END_OF_DAY,
    MINUTES_PER_DAY,
    GridError,
    require_aligned,
)

WINDOWS: dict[str, tuple[int, int]] = {
    "night": (0, 360),  # 00:00-06:00
    "morning": (360, 720),  # 06:00-12:00
    "afternoon": (720, 1020),  # 12:00-17:00
    "evening": (1020, 1320),  # 17:00-22:00
    "day": (360, 1320),  # 06:00-22:00
    "all": (0, MINUTES_PER_DAY),
}

WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}


def parse_time(text: str) -> int:
    """Read a wall-clock time as minutes past midnight."""
    raw = str(text).strip().lower().replace(".", ":").replace(" ", "")
    if not raw:
        raise GridError("empty time")

    meridiem = ""
    for suffix in ("am", "pm"):
        if raw.endswith(suffix):
            meridiem, raw = suffix, raw[: -len(suffix)]
            break

    if ":" in raw:
        hours_text, _, minutes_text = raw.partition(":")
    elif len(raw) == 4 and raw.isdigit():
        hours_text, minutes_text = raw[:2], raw[2:]
    else:
        hours_text, minutes_text = raw, "0"

    try:
        hours = int(hours_text)
        minutes = int(minutes_text or 0)
    except ValueError as exc:
        raise GridError(f"cannot read {text!r} as a time; use HH:MM (for example 14:30)") from exc

    if not 0 <= minutes < 60:
        raise GridError(f"minutes out of range in {text!r}")
    if meridiem:
        if not 1 <= hours <= 12:
            raise GridError(f"{text!r} is not a 12-hour time")
        hours = hours % 12 + (12 if meridiem == "pm" else 0)
    if hours == 24 and minutes == 0:
        return END_OF_DAY
    if not 0 <= hours <= 23:
        raise GridError(f"hours out of range in {text!r}")
    return hours * 60 + minutes


def parse_duration(text: str | int) -> int:
    """Read a duration as minutes. Accepts ``90``, ``90m``, ``2h``, ``1h 30m``."""
    if isinstance(text, int) and not isinstance(text, bool):
        return text
    raw = str(text).strip().lower()
    if raw.isdigit():
        return int(raw)
    match = re.fullmatch(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?", raw)
    if not match or not any(match.groups()):
        raise GridError(f"cannot read {text!r} as a duration; use minutes (90), '90m' or '2h'")
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


def parse_window(text: str | None) -> tuple[int, int]:
    """Resolve a named window (``afternoon``) or an explicit ``12:00-15:00``."""
    if text is None or not str(text).strip():
        return WINDOWS["all"]
    key = str(text).strip().lower()

    if key in WINDOWS:
        return WINDOWS[key]

    if " to " in key:
        left, _, right = key.partition(" to ")
    elif "-" in key:
        left, _, right = key.partition("-")
    else:
        start = parse_time(key)
        require_aligned(start, "window start")
        return (start, min(start + 60, END_OF_DAY))

    start, end = parse_time(left), parse_time(right)
    if end <= start:
        raise GridError(f"window {text!r} ends before it starts")
    require_aligned(start, "window start")
    require_aligned(end, "window end")
    return (start, end)


def parse_day(text: str, today: date) -> date:
    """Resolve a day reference against `today`.

    A bare weekday means the *next* occurrence, counting today — so asking about
    Thursday on a Thursday means today, which is what a person means by it.
    """
    key = str(text).strip().lower().replace(",", "")
    if not key or key == "today":
        return today
    if key == "tomorrow":
        return today + timedelta(days=1)
    if key == "yesterday":
        return today - timedelta(days=1)

    if key.startswith("next "):
        rest = key[5:].strip()
        if rest in WEEKDAYS:
            ahead = (WEEKDAYS[rest] - today.weekday()) % 7
            return today + timedelta(days=ahead or 7)

    if key in WEEKDAYS:
        return today + timedelta(days=(WEEKDAYS[key] - today.weekday()) % 7)

    try:
        return date.fromisoformat(key)
    except ValueError as exc:
        raise GridError(
            f"cannot read {text!r} as a day; use YYYY-MM-DD, 'today', 'tomorrow', 'next friday' or a weekday name"
        ) from exc
