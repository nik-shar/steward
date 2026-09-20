"""The day grid: 48 half-hour slots, and the arithmetic over them.

Pure — no store, no model, no clock. Everything here works in *minutes past
midnight* and converts to slot indices only where needed.

Why a fixed grid rather than datetimes: a personal calendar is *asked about* in
wall-clock terms ("Thursday afternoon", "free for two hours"), and slots make
"afternoon" and "two hours" exact, cheap to compare, and impossible to get subtly
wrong across a daylight-saving boundary.

The price is granularity: a block must start on a 30-minute boundary and last a
positive multiple of 30 minutes. That is a real limitation, so it is enforced with
an error naming the nearest valid options rather than being guessed around.
"""

from __future__ import annotations

SLOTS_PER_DAY = 48
SLOT_MINUTES = 30
MINUTES_PER_DAY = SLOTS_PER_DAY * SLOT_MINUTES
END_OF_DAY = MINUTES_PER_DAY  # 1440, rendered as "24:00"


class GridError(ValueError):
    """Raised for input that cannot be interpreted. Never guessed around."""


def format_time(minutes: int) -> str:
    """Render minutes past midnight as ``14:30``, with midnight's end as ``24:00``."""
    if not 0 <= minutes <= MINUTES_PER_DAY:
        raise GridError(f"{minutes} is outside the day")
    if minutes == END_OF_DAY:
        return "24:00"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def format_duration(minutes: int) -> str:
    if minutes < 0:
        raise GridError("a duration cannot be negative")
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours}h {rest:02d}m"
    return f"{hours}h" if hours else f"{rest}m"


def require_aligned(minutes: int, what: str) -> None:
    """Refuse a time that does not land on the grid, naming the neighbours."""
    if minutes % SLOT_MINUTES:
        lower = minutes - (minutes % SLOT_MINUTES)
        raise GridError(
            f"{what} {format_time(minutes)} is not on a {SLOT_MINUTES}-minute boundary; "
            f"use {format_time(lower)} or {format_time(lower + SLOT_MINUTES)}"
        )


def slot_of(minutes: int) -> int:
    if not 0 <= minutes <= MINUTES_PER_DAY:
        raise GridError(f"{minutes} is outside the day")
    return SLOTS_PER_DAY if minutes == END_OF_DAY else minutes // SLOT_MINUTES


def parse_block(start: int, minutes: int) -> tuple[int, int]:
    """Validate a proposed block against the grid, returning (start, end) minutes."""
    require_aligned(start, "start")
    if minutes < SLOT_MINUTES:
        raise GridError(f"a block must be at least {SLOT_MINUTES} minutes; {minutes} is not")
    if minutes % SLOT_MINUTES:
        raise GridError(f"a block must be a multiple of {SLOT_MINUTES} minutes; {minutes} is not")
    end = start + minutes
    if end > END_OF_DAY:
        raise GridError(
            f"{format_time(start)} for {format_duration(minutes)} would end after midnight. "
            "A block must fit inside one day — split it into two."
        )
    return (start, end)


def free_runs(busy: set[int]) -> list[tuple[int, int]]:
    """Maximal runs of free slots, as (start_slot, end_slot) with end exclusive."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for slot in range(SLOTS_PER_DAY):
        if slot not in busy:
            if start is None:
                start = slot
        elif start is not None:
            runs.append((start, slot))
            start = None
    if start is not None:
        runs.append((start, SLOTS_PER_DAY))
    return runs


def covered_slots(start: int, end: int) -> range:
    return range(slot_of(start), slot_of(end))


def available_windows(busy: set[int], window: tuple[int, int], min_minutes: int) -> list[tuple[int, int]]:
    """Free spans inside `window` lasting at least `min_minutes`, in minutes."""
    require_aligned(min_minutes, "minimum duration")
    lo, hi = window
    found: list[tuple[int, int]] = []
    for start_slot, end_slot in free_runs(busy):
        start = max(start_slot * SLOT_MINUTES, lo)
        end = min(end_slot * SLOT_MINUTES, hi)
        if end - start >= min_minutes:
            found.append((start, end))
    return found


def overlaps(busy: set[int], start: int, end: int) -> list[int]:
    """The busy slots a proposed block would collide with, if any."""
    return sorted(slot for slot in covered_slots(start, end) if slot in busy)
