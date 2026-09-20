"""The renderer must never fail silently.

A model call that fails has to be *visible*. The first live run of this system hit
a 401 on the provider and printed **nothing at all** — which reads as "Jarvis had
nothing to say", the single most misleading impression a tool-using agent can give.
That bug is why this file exists.
"""

from __future__ import annotations

from io import StringIO

from tau_agent import (
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolResultMessage,
)
from tau_ai import TextDeltaEvent

from jarvis.events.render import TerminalRenderer


def _renderer(**kwargs) -> tuple[TerminalRenderer, StringIO]:
    out = StringIO()
    return TerminalRenderer(out=out, colour=False, **kwargs), out


def test_text_deltas_stream_then_the_message_ends() -> None:
    renderer, out = _renderer()
    partial = AssistantMessage(content="Morning.")

    renderer(
        MessageUpdateEvent(
            message=partial,
            assistant_message_event=TextDeltaEvent(content_index=0, delta="Morning.", partial=partial),
        )
    )
    assert out.getvalue() == "Morning."

    renderer(MessageEndEvent(message=partial))
    assert out.getvalue() == "Morning.\n"


def test_a_failed_model_call_is_visible() -> None:
    """The regression test for the silent-401 bug."""
    renderer, out = _renderer()
    failed = AssistantMessage(
        stop_reason="error",
        error_message="request failed with status 401: Couldn't authenticate",
        model="Qwen3-235B",
        provider="nebius",
    )

    renderer(MessageEndEvent(message=failed))

    text = out.getvalue()
    assert "the model call failed" in text
    assert "401" in text
    assert "nebius" in text
    assert "Qwen3-235B" in text


def test_an_aborted_turn_is_also_visible() -> None:
    renderer, out = _renderer()
    renderer(MessageEndEvent(message=AssistantMessage(stop_reason="aborted")))
    assert "failed" in out.getvalue()


def test_a_healthy_message_is_not_reported_as_a_failure() -> None:
    renderer, out = _renderer()
    renderer(MessageEndEvent(message=AssistantMessage(content="All fine.")))
    assert "failed" not in out.getvalue()


def test_a_tool_result_message_end_is_not_a_model_failure() -> None:
    """Tool results also arrive as message_end. They are not failed turns."""
    renderer, out = _renderer()
    message = ToolResultMessage(tool_call_id="c1", tool_name="get_profile", content="ok")
    renderer(MessageEndEvent(message=message))
    assert "failed" not in out.getvalue()


def test_tool_activity_is_rendered_when_enabled() -> None:
    renderer, out = _renderer()
    renderer(ToolExecutionStartEvent(tool_call_id="c1", tool_name="recall_memory", args={"query": "x"}))
    renderer(
        ToolExecutionEndEvent(
            tool_call_id="c1",
            tool_name="recall_memory",
            result=AgentToolResult(content="found two memories"),
            is_error=False,
        )
    )

    text = out.getvalue()
    assert "recall_memory" in text
    assert "found two memories" in text
    assert "✓" in text


def test_a_failed_tool_is_marked_as_one() -> None:
    renderer, out = _renderer()
    renderer(
        ToolExecutionEndEvent(
            tool_call_id="c1",
            tool_name="place_block",
            result=AgentToolResult(content="declined by you"),
            is_error=True,
        )
    )
    assert "✗" in out.getvalue()


def test_tool_activity_can_be_hidden() -> None:
    """`--quiet` silences tool noise but must not swallow the answer."""
    renderer, out = _renderer(show_tools=False)
    renderer(ToolExecutionStartEvent(tool_call_id="c1", tool_name="recall_memory", args={}))
    assert out.getvalue() == ""


def test_no_ansi_codes_when_not_a_tty() -> None:
    renderer, out = _renderer()
    renderer(MessageEndEvent(message=AssistantMessage(stop_reason="error", error_message="boom")))
    assert "\033[" not in out.getvalue()


async def test_the_stream_wrapper_passes_events_through_unchanged() -> None:
    renderer, out = _renderer()
    message = AssistantMessage(content="hi")
    partial = AssistantMessage(content="hi")
    events = [
        MessageUpdateEvent(
            message=partial,
            assistant_message_event=TextDeltaEvent(content_index=0, delta="hi", partial=partial),
        ),
        MessageEndEvent(message=message),
    ]

    async def source():
        for event in events:
            yield event

    seen = [event async for event in renderer.stream(source())]

    assert seen == events
    assert out.getvalue() == "hi\n"
