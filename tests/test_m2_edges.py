"""M2 edges: the paths where the safe answer is "no", driven through the harness.

Each of these is a case where a naive implementation would have done the wrong thing:
booked without a consent channel, obeyed an instruction that collides with reality,
or let a vault write land wherever the path pointed.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tau_ai import FakeProvider

from steward.brain import Steward
from steward.memory.store import MemoryStore
from steward.packs.calendar.store import DaySlots


async def _allow(question: str, arguments: object) -> bool:
    return True


def _context(provider: FakeProvider) -> str:
    _model, _system, messages, _tools = provider.calls[-1]
    return " ".join(getattr(message, "text", "") or "" for message in messages)


async def test_a_one_shot_run_refuses_to_book_without_asking(settings_factory, streams) -> None:
    """No TTY means no consent channel, which means no write. Fail closed."""
    provider = FakeProvider(
        [
            streams.tool("place_block", {"day": "thursday", "start": "14:00", "minutes": 120}),
            streams.text("I need your approval for that."),
        ]
    )
    steward = Steward(settings_factory(), provider=provider, resume=False)
    try:
        async for _event in steward.ask("block two hours on Thursday"):
            pass
        rows = steward.store.query("SELECT 1 FROM day_slots")
        records = steward.audit.read()
    finally:
        await steward.aclose()

    assert rows == []
    verdicts = [r for r in records if r.kind == "consent" and r.tool == "place_block"]
    assert verdicts[0].reason and "no way to ask" in verdicts[0].reason


async def test_a_collision_is_reported_rather_than_worked_around(settings_factory, streams) -> None:
    """The model books over an existing block and must be told, not obeyed."""
    settings = settings_factory()

    # Seed an existing booking through the store, as another turn would have.
    seed = MemoryStore(settings.paths.db)
    DaySlots(seed).place(date(2026, 9, 24), 840, 960, "deep work")
    seed.close()

    provider = FakeProvider(
        [
            streams.tool(
                "place_block",
                {"day": "2026-09-24", "start": "15:00", "minutes": 60, "label": "call"},
            ),
            streams.text("I could not book that one."),
        ]
    )
    steward = Steward(settings, provider=provider, prompter=_allow, resume=False)
    try:
        async for _event in steward.ask("book a call at 3"):
            pass
        context = _context(provider)
        labels = {row["label"] for row in steward.store.query("SELECT DISTINCT label FROM day_slots")}
    finally:
        await steward.aclose()

    assert "overlaps" in context, "the model was not told why it failed"
    assert labels == {"deep work"}, "the declined booking partially wrote"


async def test_the_notes_pack_lands_a_thought_in_the_vault(settings_factory, streams) -> None:
    """The second half of M2: a vault write, gated the same way."""
    settings = settings_factory()
    provider = FakeProvider(
        [
            streams.tool(
                "append_note",
                {"path": "Daily Notes/today.md", "text": "Booked 14:00–16:00 for the job app."},
            ),
            streams.text("Noted in your daily note."),
        ]
    )
    steward = Steward(settings, provider=provider, prompter=_allow, resume=False)
    try:
        async for _event in steward.ask("note that I booked it"):
            pass
    finally:
        await steward.aclose()

    written = Path(settings.paths.vault) / "Daily Notes" / "today.md"
    assert written.is_file()
    assert "job app" in written.read_text(encoding="utf-8")


async def test_a_declined_vault_write_creates_no_file(settings_factory, streams) -> None:
    settings = settings_factory()

    async def deny(question: str, arguments: object) -> bool:
        return False

    provider = FakeProvider(
        [
            streams.tool("append_note", {"path": "Daily Notes/today.md", "text": "nope"}),
            streams.text("I left it out of the vault."),
        ]
    )
    steward = Steward(settings, provider=provider, prompter=deny, resume=False)
    try:
        async for _event in steward.ask("note that"):
            pass
    finally:
        await steward.aclose()

    assert not (Path(settings.paths.vault) / "Daily Notes" / "today.md").exists()
