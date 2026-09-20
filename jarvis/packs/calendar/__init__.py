"""The calendar pack: a 48-slot day, and the two tools that change it.

Mounted inline (topology A) like every pack so far, and promotable to its own
subagent later without changing anything in here.

This is the first pack with **write** tools. `place_block` and `remove_block`
declare ``Scope.WRITE``, so the consent gate fires before they run and the verdict
lands in the audit log either way. That is the point of the scope system: a
capability that can touch the world should show up in ``--doctor`` and be gated at
runtime, rather than depend on anyone remembering to ask.
"""

from __future__ import annotations

from jarvis.packs.calendar.tools import find_availability, get_day, place_block, remove_block
from jarvis.policy.scopes import Scope
from jarvis.tools.registry import CapabilityPack
from jarvis.tools.spec import ToolSpec

GUIDELINES = """\
- Call `get_day` before proposing a booking. Never assume a day is free.
- Blocks sit on a 30-minute grid: start on :00 or :30 and last a multiple of 30
  minutes. An off-grid request is refused, with the nearest valid times named.
- A block cannot cross midnight. Split it in two.
- `place_block` and `remove_block` change the real calendar and the person is asked
  first. If they decline, say so and stop — do not retry the same call and do not
  look for another way to write it.
- After booking or freeing something, state it in one line. Do not read the whole
  day back at them.
"""

_DAY = {
    "type": "string",
    "description": "YYYY-MM-DD, 'today', 'tomorrow', 'next friday', or a weekday name.",
}

_HALF_HOUR = {"type": "integer", "minimum": 30, "multipleOf": 30}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_day",
        label="Read a day",
        description=(
            "Read one day of the calendar as booked blocks and free gaps, plus total free "
            "time. Call this before proposing any booking."
        ),
        parameters={"type": "object", "properties": {"day": _DAY}, "additionalProperties": False},
        handler=get_day,
        scope=Scope.READ,
        pack="calendar",
    ),
    ToolSpec(
        name="find_availability",
        label="Find free time",
        description=(
            "List the free spans on a day inside an optional window, longest first. Use it "
            "to answer 'am I free on Thursday afternoon?'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "day": _DAY,
                "window": {
                    "type": "string",
                    "description": (
                        "'morning', 'afternoon', 'evening', 'day', or '13:00-17:00'. Default: the whole day."
                    ),
                },
                "min_minutes": {
                    **_HALF_HOUR,
                    "description": "Only spans at least this long. Default 30.",
                },
            },
            "additionalProperties": False,
        },
        handler=find_availability,
        scope=Scope.READ,
        pack="calendar",
    ),
    ToolSpec(
        name="place_block",
        label="Book a block",
        description=(
            "Book a block into the calendar. Start on a 30-minute boundary, use a length "
            "that is a multiple of 30 minutes, and keep it inside one day. Refuses if it "
            "would overlap an existing block. This writes to the real calendar and the "
            "person is asked first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "day": _DAY,
                "start": {"type": "string", "description": "Start time, for example '14:00' or '2pm'."},
                "minutes": {**_HALF_HOUR, "description": "Length in minutes."},
                "label": {"type": "string", "description": "What the block is for. Keep it short."},
            },
            "required": ["start", "minutes"],
            "additionalProperties": False,
        },
        handler=place_block,
        scope=Scope.WRITE,
        pack="calendar",
        guidelines=("Book only inside a gap you have read from get_day or find_availability.",),
    ),
    ToolSpec(
        name="remove_block",
        label="Free a block",
        description=(
            "Remove the booking that starts at a given time, freeing it. This writes to the "
            "real calendar and the person is asked first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "day": _DAY,
                "start": {"type": "string", "description": "Start time of the block to free."},
            },
            "required": ["start"],
            "additionalProperties": False,
        },
        handler=remove_block,
        scope=Scope.WRITE,
        pack="calendar",
    ),
)


def calendar_pack() -> CapabilityPack:
    """The calendar as a mountable pack."""
    return CapabilityPack(
        name="calendar",
        summary="A 48-slot day: read it, find room, book and free blocks.",
        tools=TOOLS,
        guidelines=GUIDELINES,
    )
