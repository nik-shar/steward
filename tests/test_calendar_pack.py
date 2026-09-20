"""The calendar pack, through its handlers.

Pure handler tests — no provider, no tokens. The harness-level proof that the consent
gate fires for `place_block` lives in `test_m2_acceptance.py`.

A calendar that quietly adjusts what you asked for is worse than one that refuses it:
off-grid times, midnight-crossing blocks and collisions all come back as errors naming
the alternative.
"""

from __future__ import annotations

from jarvis.packs.calendar.tools import get_day, place_block, remove_block

DAY = "2026-09-24"  # a Thursday


# -- reading a day -------------------------------------------------------------


async def test_an_empty_day_reads_as_free(ctx) -> None:
    outcome = await get_day(ctx, {"day": DAY})

    assert not outcome.is_error
    assert "booked: nothing" in outcome.text
    assert "24h free of 24h" in outcome.text
    assert outcome.details["booked_slots"] == 0


async def test_a_day_is_summarised_not_dumped(ctx) -> None:
    """48 rows would be the most expensive thing this system could put in a context."""
    await place_block(ctx, {"day": DAY, "start": "09:00", "minutes": 30, "label": "standup"})
    outcome = await get_day(ctx, {"day": DAY})

    assert len(outcome.text.splitlines()) <= 4
    assert "09:00–09:30 standup" in outcome.text


async def test_an_unreadable_day_is_refused_rather_than_guessed(ctx) -> None:
    outcome = await get_day(ctx, {"day": "the day after the fair"})

    assert outcome.is_error
    assert "cannot read the day" in outcome.text


# -- booking -------------------------------------------------------------------


async def test_booking_then_reading_it_back(ctx) -> None:
    booked = await place_block(ctx, {"day": DAY, "start": "14:00", "minutes": 120, "label": "deep work"})

    assert not booked.is_error
    assert booked.text == "booked 2026-09-24 14:00–16:00 (2h): deep work"

    day = await get_day(ctx, {"day": DAY})
    assert "14:00–16:00 deep work" in day.text
    assert "22h free of 24h" in day.text


async def test_adjacent_blocks_are_not_a_collision(ctx) -> None:
    first = await place_block(ctx, {"day": DAY, "start": "14:00", "minutes": 60, "label": "a"})
    second = await place_block(ctx, {"day": DAY, "start": "15:00", "minutes": 60, "label": "b"})

    assert not first.is_error
    assert not second.is_error


async def test_an_overlap_is_refused_and_names_what_it_hit(ctx) -> None:
    await place_block(ctx, {"day": DAY, "start": "14:00", "minutes": 120, "label": "deep work"})

    clash = await place_block(ctx, {"day": DAY, "start": "15:00", "minutes": 60, "label": "call"})

    assert clash.is_error
    assert "overlaps" in clash.text
    assert "14:00–16:00 deep work" in clash.text  # so the model knows what to move
    assert "remove_block" in clash.text  # and what to do about it


async def test_an_off_grid_time_is_refused_with_the_neighbours(ctx) -> None:
    outcome = await place_block(ctx, {"day": DAY, "start": "14:15", "minutes": 60, "label": "x"})

    assert outcome.is_error
    assert "14:00" in outcome.text and "14:30" in outcome.text
    assert ctx.store.query("SELECT 1 FROM day_slots") == []  # nothing was written


async def test_a_block_crossing_midnight_is_refused(ctx) -> None:
    outcome = await place_block(ctx, {"day": DAY, "start": "23:00", "minutes": 180, "label": "overrun"})

    assert outcome.is_error
    assert "after midnight" in outcome.text
    assert ctx.store.query("SELECT 1 FROM day_slots") == []


async def test_a_booking_is_audited(ctx) -> None:
    await place_block(ctx, {"day": DAY, "start": "09:00", "minutes": 30, "label": "standup"})

    records = [record for record in ctx.audit.read() if record.kind == "tool_call"]
    assert len(records) == 1
    assert records[0].tool == "place_block"
    assert records[0].detail["label"] == "standup"
    assert records[0].detail["slots"] == 1


# -- freeing -------------------------------------------------------------------


async def test_removing_frees_only_that_block(ctx) -> None:
    await place_block(ctx, {"day": DAY, "start": "09:00", "minutes": 60, "label": "first"})
    await place_block(ctx, {"day": DAY, "start": "10:00", "minutes": 60, "label": "second"})

    freed = await remove_block(ctx, {"day": DAY, "start": "09:00"})

    assert not freed.is_error
    assert freed.text == "freed 2026-09-24 09:00–10:00: first"

    day = await get_day(ctx, {"day": DAY})
    assert "10:00–11:00 second" in day.text
    assert "09:00" not in day.text


async def test_a_same_label_run_does_not_swallow_the_neighbour(ctx) -> None:
    """Adjacent blocks with different labels stay independently removable."""
    await place_block(ctx, {"day": DAY, "start": "13:00", "minutes": 60, "label": "focus"})
    await place_block(ctx, {"day": DAY, "start": "14:00", "minutes": 60, "label": "call"})

    freed = await remove_block(ctx, {"day": DAY, "start": "13:00"})

    assert freed.text == "freed 2026-09-24 13:00–14:00: focus"
    day = await get_day(ctx, {"day": DAY})
    assert "14:00–15:00 call" in day.text


async def test_removing_nothing_says_so(ctx) -> None:
    outcome = await remove_block(ctx, {"day": DAY, "start": "09:00"})

    assert outcome.is_error
    assert "nothing is booked" in outcome.text


async def test_removing_an_off_grid_time_is_refused(ctx) -> None:
    outcome = await remove_block(ctx, {"day": DAY, "start": "09:15"})

    assert outcome.is_error
    assert "boundary" in outcome.text


async def test_a_removal_is_audited(ctx) -> None:
    await place_block(ctx, {"day": DAY, "start": "09:00", "minutes": 30, "label": "standup"})
    await remove_block(ctx, {"day": DAY, "start": "09:00"})

    tools = [record.tool for record in ctx.audit.read() if record.kind == "tool_call"]
    assert tools == ["place_block", "remove_block"]
