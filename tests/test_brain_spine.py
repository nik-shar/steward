"""The spine, end to end, with no network and no tokens.

Tau's ``FakeProvider`` replays scripted assistant streams, so the whole path —
harness, tool dispatch, memory, audit, session file — runs deterministically. That
is a direct benefit of embedding the harness as a library instead of driving its
CLI: the same loop is testable offline.

Everything here goes through a real ``AgentHarness``, so these are the tests that
break first when Tau's API moves.
"""

from __future__ import annotations

from tau_ai import FakeProvider

from steward.brain import Steward


async def test_a_turn_streams_and_is_persisted(settings_factory, streams) -> None:
    from steward.session import load_messages

    provider = FakeProvider([streams.text("Morning. Nothing scheduled.")])
    steward = Steward(settings_factory(), provider=provider, resume=False)
    try:
        events = [event async for event in steward.ask("what's on today?")]
        session_file = steward.session_file
    finally:
        await steward.aclose()

    assert events, "the harness should have emitted events"
    assert session_file.is_file()

    # The transcript round-trips through Tau's own session format.
    messages = load_messages(session_file)
    assert any("Morning" in (getattr(message, "text", "") or "") for message in messages)


async def test_the_turn_reaches_episodic_memory(settings_factory, streams) -> None:
    steward = Steward(
        settings_factory(), provider=FakeProvider([streams.text("You are free after 2pm.")]), resume=False
    )
    try:
        async for _event in steward.ask("am I free?"):
            pass
        rows = steward.store.query("SELECT kind, summary FROM episodic_events")
    finally:
        await steward.aclose()

    assert len(rows) == 1
    assert rows[0]["kind"] == "turn"
    assert "free" in rows[0]["summary"]


async def test_the_identity_markdown_reaches_the_model(settings_factory, streams) -> None:
    """Identity is a file you can edit, so it must actually arrive in the prompt."""
    provider = FakeProvider([streams.text("hello")])
    steward = Steward(settings_factory(), provider=provider, resume=False)
    try:
        async for _event in steward.ask("hi"):
            pass
    finally:
        await steward.aclose()

    model, system, _messages, tools = provider.calls[0]
    assert model == "test-model"
    assert "Steward" in system
    assert "Standing orders" in system  # guidelines.md made it in
    assert "Right now" in system  # so did the current date
    assert {tool.name for tool in tools} >= {"remember", "recall_memory", "delegate"}


async def test_a_tool_call_runs_and_mutates_memory(settings_factory, streams) -> None:
    provider = FakeProvider(
        [
            streams.tool("remember", {"text": "Prefers mornings", "kind": "preference"}),
            streams.text("Noted."),
        ]
    )
    steward = Steward(settings_factory(), provider=provider, resume=False)
    try:
        async for _event in steward.ask("remember that I like mornings"):
            pass
        count = steward.dna.count()
        kinds = [record.kind for record in steward.audit.read()]
    finally:
        await steward.aclose()

    assert count == 1
    assert "memory_write" in kinds
    # Two provider calls: the tool turn, then the answer that followed it.
    assert len(provider.calls) == 2


async def test_a_stored_memory_reaches_the_model_on_recall(settings_factory, streams) -> None:
    settings = settings_factory()
    first = Steward(
        settings,
        provider=FakeProvider(
            [streams.tool("remember", {"text": "Training for a half marathon"}), streams.text("Noted.")]
        ),
        resume=False,
    )
    try:
        async for _event in first.ask("remember the marathon"):
            pass
    finally:
        await first.aclose()

    provider = FakeProvider(
        [streams.tool("recall_memory", {"query": "marathon"}), streams.text("You said a half marathon.")]
    )
    second = Steward(settings, provider=provider, session_id=first.session_id)
    try:
        async for _event in second.ask("what am I training for?"):
            pass
    finally:
        await second.aclose()

    # The tool result carried the memory text back into the model's context.
    _model, _system, messages, _tools = provider.calls[1]
    assert any("half marathon" in (getattr(message, "text", "") or "") for message in messages)


async def test_a_session_resumes_with_its_history(settings_factory, streams) -> None:
    settings = settings_factory()
    first = Steward(settings, provider=FakeProvider([streams.text("One.")]), resume=False)
    try:
        async for _event in first.ask("first message"):
            pass
    finally:
        await first.aclose()

    second = Steward(settings, provider=FakeProvider([streams.text("Two.")]), session_id=first.session_id)
    try:
        prior = len(second.harness.messages)
    finally:
        await second.aclose()

    assert prior >= 2, "a resumed session should carry the earlier turn"


async def test_status_reports_the_wiring(settings_factory) -> None:
    steward = Steward(settings_factory(), provider=FakeProvider([]), resume=False)
    try:
        status = steward.status()
    finally:
        await steward.aclose()

    assert status["model"] == "test-model"
    assert status["packs"] == ["core", "calendar", "notes"]

    scopes = {tool["name"]: tool["scope"] for tool in status["tools"]}
    assert scopes["remember"] == "internal"
    assert scopes["recall_memory"] == "read"
    assert scopes["get_day"] == "read"
    assert scopes["place_block"] == "write"

    # Everything that can change the world, and nothing else.
    assert set(status["consent_required"]) == {"place_block", "remove_block", "append_note"}
