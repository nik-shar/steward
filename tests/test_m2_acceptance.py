"""M2 acceptance: the scenario this milestone was defined by.

    "Am I free Thursday afternoon? If so, block two hours for the job application."

That request needs a read, a decision, and a **write that changes the real world** —
so it exercises the whole spine through the real harness: tool dispatch, the consent
gate, the grid arithmetic, the store, and the audit log. It runs on the *default*
configuration, so nothing here is a special test rig.

Scripted streams are consumed in call order, one per provider request: the
availability read, the booking, then the answer.
"""

from __future__ import annotations

from tau_ai import FakeProvider

from jarvis.brain import Jarvis

READ = {"day": "thursday", "window": "afternoon"}
BOOK = {"day": "thursday", "start": "14:00", "minutes": 120, "label": "job application"}
ANSWER = "Thursday afternoon is clear. Booked 14:00–16:00 for the job application."


async def _allow(question: str, arguments: object) -> bool:
    return True


async def _deny(question: str, arguments: object) -> bool:
    return False


def _script(streams) -> FakeProvider:
    return FakeProvider(
        [
            streams.tool("find_availability", READ),
            streams.tool("place_block", BOOK),
            streams.text(ANSWER),
        ]
    )


def _context(provider: FakeProvider) -> str:
    _model, _system, messages, _tools = provider.calls[-1]
    return " ".join(getattr(message, "text", "") or "" for message in messages)


async def test_the_scenario_books_the_time(settings_factory, streams) -> None:
    provider = _script(streams)
    jarvis = Jarvis(settings_factory(), provider=provider, prompter=_allow, resume=False)
    try:
        async for _event in jarvis.ask("am I free Thursday afternoon? If so block two hours."):
            pass
        rows = jarvis.store.query("SELECT day, slot, label FROM day_slots ORDER BY slot")
        records = jarvis.audit.read()
    finally:
        await jarvis.aclose()

    # Two hours on a half-hour grid is four slots.
    assert len(rows) == 4
    assert {row["label"] for row in rows} == {"job application"}
    assert min(row["slot"] for row in rows) == 28  # 14:00

    verdicts = [r for r in records if r.kind == "consent" and r.tool == "place_block"]
    assert verdicts and verdicts[0].allowed is True
    assert any(r.kind == "tool_call" and r.tool == "place_block" for r in records)


async def test_the_availability_read_reaches_the_model(settings_factory, streams) -> None:
    """The read has to inform the decision, or the model is booking blind."""
    provider = _script(streams)
    jarvis = Jarvis(settings_factory(), provider=provider, prompter=_allow, resume=False)
    try:
        async for _event in jarvis.ask("am I free Thursday afternoon?"):
            pass
    finally:
        await jarvis.aclose()

    context = _context(provider)
    assert "afternoon" in context
    assert "14:00" in context or "12:00" in context  # the free spans it was offered


async def test_a_declined_booking_writes_nothing(settings_factory, streams) -> None:
    """Declining must leave the calendar exactly as it was, and say so."""
    provider = _script(streams)
    jarvis = Jarvis(settings_factory(), provider=provider, prompter=_deny, resume=False)
    try:
        async for _event in jarvis.ask("am I free Thursday afternoon? If so block two hours."):
            pass
        rows = jarvis.store.query("SELECT 1 FROM day_slots")
        context = _context(provider)
        records = jarvis.audit.read()
    finally:
        await jarvis.aclose()

    assert rows == []
    assert "declined by you" in context
    verdicts = [r for r in records if r.kind == "consent" and r.tool == "place_block"]
    assert verdicts and verdicts[0].allowed is False


async def test_the_read_still_works_when_the_write_is_declined(settings_factory, streams) -> None:
    """Refusing a write must fail only the write, not the turn around it."""
    provider = _script(streams)
    jarvis = Jarvis(settings_factory(), provider=provider, prompter=_deny, resume=False)
    try:
        events = [event async for event in jarvis.ask("am I free Thursday afternoon?")]
    finally:
        await jarvis.aclose()

    finished = [event for event in events if event.type == "tool_execution_end"]
    failed = {event.tool_name for event in finished if event.is_error}
    succeeded = {event.tool_name for event in finished if not event.is_error}

    assert events, "the turn should have completed"
    assert "find_availability" in succeeded, "an unrelated read was blocked"
    assert failed == {"place_block"}, "the declined write should be the only failure"
