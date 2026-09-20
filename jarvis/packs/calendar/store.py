"""Reading and writing `day_slots`, and rendering a day compactly.

The store access goes through `MemoryStore`, so the single-writer rule still holds
even though the calendar is its own pack.

Rendering matters more than it looks: the model pays for every token it reads, so a
day is summarised as *blocks and gaps*, never as 48 rows. A full grid dump would be
the single most expensive thing this system could put in a context window.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from jarvis.clock import now_ms
from jarvis.packs.calendar.grid import (
    SLOT_MINUTES,
    SLOTS_PER_DAY,
    covered_slots,
    format_duration,
    format_time,
    free_runs,
)


class DaySlots:
    """The day grid for one store. Every write goes through `MemoryStore`."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def busy(self, day: date) -> dict[int, str]:
        """booked slot -> label, for one local calendar day."""
        rows = self._store.query("SELECT slot, label FROM day_slots WHERE day = ? ORDER BY slot", (day.isoformat(),))
        return {int(row["slot"]): str(row["label"]) for row in rows}

    def place(self, day: date, start: int, end: int, label: str, *, source: str = "jarvis") -> int:
        """Occupy every slot the block covers. Returns how many were written."""
        stamp = now_ms()
        slots = [(day.isoformat(), slot, label, source, stamp) for slot in covered_slots(start, end)]
        self._store.connection.executemany(
            """
            INSERT OR REPLACE INTO day_slots (day, slot, label, source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            slots,
        )
        self._store.commit()
        return len(slots)

    def remove(self, day: date, start_slot: int) -> tuple[int, str] | None:
        """Free the contiguous same-label run starting at `start_slot`.

        A run stops where the label changes, so two adjacent bookings for different
        things stay independently removable.
        """
        table = self.busy(day)
        label = table.get(start_slot)
        if label is None:
            return None

        slot = start_slot
        while table.get(slot) == label:
            slot += 1

        self._store.connection.execute(
            "DELETE FROM day_slots WHERE day = ? AND slot >= ? AND slot < ?",
            (day.isoformat(), start_slot, slot),
        )
        self._store.commit()
        return (slot - start_slot, label)


def blocks(busy: dict[int, str]) -> list[tuple[int, int, str]]:
    """Merge adjacent same-label slots into (start_slot, end_slot, label) blocks."""
    merged: list[tuple[int, int, str]] = []
    start: int | None = None
    current: str | None = None

    for slot in range(SLOTS_PER_DAY):
        label = busy.get(slot)
        if label == current:
            continue
        if current is not None and start is not None:
            merged.append((start, slot, current))
        start = slot if label is not None else None
        current = label

    if current is not None and start is not None:
        merged.append((start, SLOTS_PER_DAY, current))
    return merged


def render_range(start_slot: int, end_slot: int) -> str:
    return f"{format_time(start_slot * SLOT_MINUTES)}–{format_time(end_slot * SLOT_MINUTES)}"


def render_day(day: date, busy: dict[int, str]) -> str:
    """A day as booked blocks and free gaps, which is all the model needs."""
    lines = [f"{day.isoformat()} ({day.strftime('%A')})"]
    merged = blocks(busy)

    if merged:
        lines.append("  booked: " + " · ".join(f"{render_range(start, end)} {label}" for start, end, label in merged))
    else:
        lines.append("  booked: nothing")

    gaps = free_runs(set(busy))
    if gaps:
        lines.append("  free: " + " · ".join(render_range(start, end) for start, end in gaps))
    free_minutes = sum(end - start for start, end in gaps) * SLOT_MINUTES
    lines.append(f"  {format_duration(free_minutes)} free of 24h")
    return "\n".join(lines)


def render_windows(windows: list[tuple[int, int]]) -> str:
    """Free spans in minutes, with their length, longest first."""
    ordered = sorted(windows, key=lambda span: span[1] - span[0], reverse=True)
    return " · ".join(
        f"{format_time(start)}–{format_time(end)} ({format_duration(end - start)})" for start, end in ordered
    )
