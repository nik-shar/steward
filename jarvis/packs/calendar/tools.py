"""The calendar pack's four handlers: two reads, two writes.

The reads answer "what does the day look like" and "where is there room". The writes
occupy and free slots, and they are *deterministic and refusing*: an off-grid time, a
booking crossing midnight, or a collision with an existing block all come back as an
error the model can act on — never as a silent adjustment to what was asked for.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, tzinfo
from typing import Any

from jarvis.packs.calendar.grid import (
    SLOT_MINUTES,
    GridError,
    available_windows,
    format_duration,
    format_time,
    overlaps,
    parse_block,
    require_aligned,
    slot_of,
)
from jarvis.packs.calendar.store import DaySlots, blocks, render_day, render_range, render_windows
from jarvis.packs.calendar.when import parse_day, parse_duration, parse_time, parse_window
from jarvis.tools.spec import ToolContext, ToolOutcome

DEFAULT_MIN_MINUTES = 30


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _today(settings: Any) -> date:
    """Today in the configured zone. Defensive: `settings` may be None in a bare test."""
    resolver = getattr(settings, "tz", None)
    zone: tzinfo | None = resolver() if callable(resolver) else None
    return datetime.now(zone).date()


def _resolve_day(ctx: ToolContext, arguments: Mapping[str, Any]) -> date:
    """Resolve ``day``, defaulting to today. Raises `GridError`."""
    return parse_day(_text(arguments.get("day")) or "today", _today(ctx.settings))


def _describe_clash(table: dict[int, str], clashing: list[int]) -> str:
    """Name what a proposed block would collide with."""
    named = [
        f"{render_range(start, end)} {label}"
        for start, end, label in blocks(table)
        if any(start <= slot < end for slot in clashing)
    ]
    return " and ".join(named) or "an existing booking"


async def get_day(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    """Read one day as booked blocks and free gaps."""
    try:
        day = _resolve_day(ctx, arguments)
    except GridError as exc:
        return ToolOutcome.error(f"cannot read the day: {exc}")

    busy = DaySlots(ctx.store).busy(day)
    return ToolOutcome.ok(render_day(day, busy), details={"day": day.isoformat(), "booked_slots": len(busy)})


async def find_availability(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    """Where there is room on a day, inside an optional window."""
    try:
        day = _resolve_day(ctx, arguments)
        span = parse_window(_text(arguments.get("window")) or None)
        minimum = parse_duration(arguments.get("min_minutes") or DEFAULT_MIN_MINUTES)
    except GridError as exc:
        return ToolOutcome.error(str(exc))

    busy = set(DaySlots(ctx.store).busy(day))
    found = available_windows(busy, span, minimum)
    label = _text(arguments.get("window")) or "the whole day"
    stamp = f"{day.isoformat()} ({day.strftime('%A')})"

    if not found:
        return ToolOutcome.ok(
            f"no gap of {format_duration(minimum)} or more in {label} on {stamp}",
            details={"day": day.isoformat(), "gaps": 0},
        )
    return ToolOutcome.ok(
        f"{stamp}, {label}: {render_windows(found)}",
        details={"day": day.isoformat(), "gaps": len(found)},
    )


async def place_block(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    """Book a block. A write — so the consent gate has already run."""
    try:
        day = _resolve_day(ctx, arguments)
        start, end = parse_block(parse_time(_text(arguments.get("start"))), arguments.get("minutes"))
    except GridError as exc:
        return ToolOutcome.error(str(exc))

    label = _text(arguments.get("label")) or "blocked"
    slots = DaySlots(ctx.store)
    table = slots.busy(day)
    clashing = overlaps(set(table), start, end)
    if clashing:
        return ToolOutcome.error(
            f"{format_time(start)}–{format_time(end)} on {day.isoformat()} overlaps "
            f"{_describe_clash(table, clashing)}. Free it first with remove_block, or "
            "choose another time."
        )

    written = slots.place(day, start, end, label)
    detail = {
        "day": day.isoformat(),
        "start": format_time(start),
        "end": format_time(end),
        "label": label,
        "slots": written,
    }
    ctx.audit.record("tool_call", tool="place_block", allowed=True, session_id=ctx.session_id, detail=detail)
    return ToolOutcome.ok(
        f"booked {day.isoformat()} {format_time(start)}–{format_time(end)} ({format_duration(end - start)}): {label}",
        details=detail,
    )


async def remove_block(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    """Free the block starting at a time. Also a write, also gated."""
    try:
        day = _resolve_day(ctx, arguments)
        start = parse_time(_text(arguments.get("start")))
        require_aligned(start, "start")
    except GridError as exc:
        return ToolOutcome.error(str(exc))

    removed = DaySlots(ctx.store).remove(day, slot_of(start))
    if removed is None:
        return ToolOutcome.error(
            f"nothing is booked at {format_time(start)} on {day.isoformat()}. "
            "Call get_day to see what is actually there."
        )

    count, label = removed
    end = start + count * SLOT_MINUTES
    detail = {
        "day": day.isoformat(),
        "start": format_time(start),
        "end": format_time(end),
        "label": label,
        "slots": count,
    }
    ctx.audit.record("tool_call", tool="remove_block", allowed=True, session_id=ctx.session_id, detail=detail)
    return ToolOutcome.ok(
        f"freed {day.isoformat()} {format_time(start)}–{format_time(end)}: {label}",
        details=detail,
    )
