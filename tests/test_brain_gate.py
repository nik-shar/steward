"""The consent gate driven through the real loop — the most important test here.

Tau's ``before_tool_call`` hook returns ``(blocked, reason)`` where **True means
block**, while Steward's gate returns an ``allowed`` flag. Invert that conversion and
every write is silently permitted while every read is refused.

Nothing but an end-to-end run through the actual harness proves the polarity is
right, so this file drives a WRITE tool through the whole loop: denied, approved,
and — the one that matters most — **from inside a subagent**, where the same gate
has to hold.
"""

from __future__ import annotations

import pytest
from tau_ai import FakeProvider

from steward.brain import Steward
from steward.packs.core import core_pack
from steward.policy.scopes import Scope
from steward.tools.delegate import delegate_tool
from steward.tools.registry import CapabilityPack
from steward.tools.spec import SubagentSpec, ToolOutcome, ToolSpec

PLACED: list[dict] = []


async def _place_block(ctx, arguments) -> ToolOutcome:
    PLACED.append(dict(arguments))
    return ToolOutcome.ok("placed 14:00-16:00")


WRITE_PACK = CapabilityPack(
    name="test-calendar",
    summary="a write tool, for exercising the gate",
    tools=(
        ToolSpec(
            name="place_block",
            label="Place a block",
            description="Write a block into the calendar.",
            parameters={
                "type": "object",
                "properties": {"slot": {"type": "integer"}},
                "additionalProperties": False,
            },
            handler=_place_block,
            scope=Scope.WRITE,
            pack="test-calendar",
        ),
    ),
)

SCHEDULER = SubagentSpec(
    name="scheduler",
    purpose="book things in the calendar",
    system="You book the requested block and reply with the outcome.",
    tools=("place_block",),
)


def packs() -> tuple[CapabilityPack, ...]:
    return (core_pack(), WRITE_PACK)


def packs_with_delegation() -> tuple[CapabilityPack, ...]:
    """Core's own `delegate` is replaced, so a promoted subagent is reachable."""
    return (
        CapabilityPack(name="delegation", summary="", tools=(delegate_tool({"scheduler": SCHEDULER}),)),
        WRITE_PACK,
    )


async def _deny(question: str, arguments: object) -> bool:
    return False


async def _allow(question: str, arguments: object) -> bool:
    return True


@pytest.fixture(autouse=True)
def _clear_placed():
    PLACED.clear()
    yield
    PLACED.clear()


def _last_model_context(provider: FakeProvider) -> str:
    """Everything the model saw on its most recent call, as one string."""
    _model, _system, messages, _tools = provider.calls[-1]
    return " ".join(getattr(message, "text", "") or "" for message in messages)


# -- the polarity --------------------------------------------------------------


async def test_a_denied_write_never_reaches_the_handler(settings_factory, streams) -> None:
    provider = FakeProvider([streams.tool("place_block", {"slot": 28}), streams.text("I could not book it.")])
    steward = Steward(settings_factory(), packs=packs(), prompter=_deny, provider=provider, resume=False)
    try:
        async for _event in steward.ask("book two hours"):
            pass
        verdicts = [r for r in steward.audit.read() if r.kind == "consent" and r.tool == "place_block"]
        told = _last_model_context(provider)
    finally:
        await steward.aclose()

    assert PLACED == [], "a denied write ran anyway — the gate polarity is inverted"
    assert len(verdicts) == 1
    assert verdicts[0].allowed is False
    assert "declined by you" in told, "the model must be told it was refused"


async def test_an_approved_write_runs(settings_factory, streams) -> None:
    provider = FakeProvider([streams.tool("place_block", {"slot": 28}), streams.text("Booked 14:00-16:00.")])
    steward = Steward(settings_factory(), packs=packs(), prompter=_allow, provider=provider, resume=False)
    try:
        async for _event in steward.ask("book two hours"):
            pass
        verdicts = [r for r in steward.audit.read() if r.kind == "consent" and r.tool == "place_block"]
    finally:
        await steward.aclose()

    assert PLACED == [{"slot": 28}]
    assert verdicts[0].allowed is True
    assert verdicts[0].reason == "approved by you"


async def test_reads_are_never_gated(settings_factory, streams) -> None:
    """The gate must not block a read — that would invert the mistake the other way."""
    provider = FakeProvider([streams.tool("get_profile", {}), streams.text("Nothing yet.")])
    steward = Steward(settings_factory(), packs=packs(), prompter=_deny, provider=provider, resume=False)
    try:
        async for _event in steward.ask("what do you know about me?"):
            pass
        told = _last_model_context(provider)
    finally:
        await steward.aclose()

    assert "declined" not in told
    assert "cold start" in told


# -- no way to ask -------------------------------------------------------------


async def test_without_a_prompt_channel_the_write_is_denied(settings_factory, streams) -> None:
    """A one-shot run has no TTY. It must refuse rather than assume consent."""
    provider = FakeProvider([streams.tool("place_block", {"slot": 28}), streams.text("I need your approval.")])
    steward = Steward(settings_factory(), packs=packs(), provider=provider, resume=False)
    try:
        async for _event in steward.ask("book two hours"):
            pass
        verdicts = [r for r in steward.audit.read() if r.kind == "consent" and r.tool == "place_block"]
    finally:
        await steward.aclose()

    assert PLACED == []
    assert verdicts[0].allowed is False
    assert "no way to ask" in verdicts[0].reason


async def test_an_allowlisted_write_skips_the_prompt(settings_factory, streams) -> None:
    """Allowlisting is the only way past the prompt, and it is opt-in per tool."""
    provider = FakeProvider([streams.tool("place_block", {"slot": 28}), streams.text("Booked.")])
    steward = Steward(
        settings_factory(allowlist=frozenset({"place_block"})),
        packs=packs(),
        prompter=_deny,
        provider=provider,
        resume=False,
    )
    try:
        async for _event in steward.ask("book two hours"):
            pass
    finally:
        await steward.aclose()

    assert PLACED == [{"slot": 28}]


async def test_allow_all_is_explicit_and_recorded(settings_factory, streams) -> None:
    """`--yes` must leave a trace in the audit log, not just take effect."""
    provider = FakeProvider([streams.tool("place_block", {"slot": 28}), streams.text("Booked.")])
    steward = Steward(settings_factory(), packs=packs(), provider=provider, allow_all_writes=True, resume=False)
    try:
        async for _event in steward.ask("book two hours"):
            pass
        records = steward.audit.read()
    finally:
        await steward.aclose()

    assert PLACED == [{"slot": 28}]
    assert any(record.reason and "--yes" in record.reason for record in records)


# -- delegation is not a loophole ---------------------------------------------


async def test_a_write_inside_a_subagent_still_prompts(settings_factory, streams) -> None:
    """A child harness is built with the same gate, so it cannot be used to bypass it.

    Streams are consumed in call order: the parent asks to delegate, the child
    (running inside that tool call) asks to write, then the parent answers.
    """
    provider = FakeProvider(
        [
            streams.tool("delegate", {"agent": "scheduler", "task": "book two hours"}),
            streams.tool("place_block", {"slot": 28}),
            streams.text("The scheduler could not book it."),
        ]
    )
    steward = Steward(
        settings_factory(),
        packs=packs_with_delegation(),
        prompter=_deny,
        provider=provider,
        resume=False,
    )
    try:
        async for _event in steward.ask("book two hours via the scheduler"):
            pass
        records = steward.audit.read()
    finally:
        await steward.aclose()

    delegations = [record for record in records if record.kind == "delegation"]
    verdicts = [record for record in records if record.kind == "consent" and record.tool == "place_block"]

    assert delegations, "the delegation itself should be audited"
    assert delegations[0].detail["tools"] == ["place_block"]
    assert PLACED == [], "a write inside a subagent bypassed the gate"
    assert verdicts and verdicts[0].allowed is False


async def test_a_subagent_gets_only_its_declared_tools(settings_factory, streams) -> None:
    """The child's tool list is the spec's, not the parent's."""
    provider = FakeProvider(
        [
            streams.tool("delegate", {"agent": "scheduler", "task": "book two hours"}),
            streams.text("Booked by hand instead."),
        ]
    )
    steward = Steward(
        settings_factory(),
        packs=packs_with_delegation(),
        prompter=_allow,
        provider=provider,
        resume=False,
    )
    try:
        async for _event in steward.ask("book two hours via the scheduler"):
            pass
        child_tools = {tool.name for tool in provider.calls[1][3]}
    finally:
        await steward.aclose()

    assert child_tools == {"place_block"}


async def test_status_lists_a_write_as_needing_consent(settings_factory) -> None:
    steward = Steward(settings_factory(), packs=packs(), provider=FakeProvider([]), resume=False)
    try:
        status = steward.status()
    finally:
        await steward.aclose()

    assert status["consent_required"] == ["place_block"]
    scopes = {tool["name"]: tool["scope"] for tool in status["tools"]}
    assert scopes["place_block"] == "write"
