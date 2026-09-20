"""Drawing Tau's event stream in a terminal.

The event stream is the contract, so this is *one* renderer of it. The web frontend
in M5 will consume exactly the same events — which is why nothing here reaches into
the harness for state, and why this is a module rather than a pile of prints in the
CLI.

This file is one of the **two** places allowed to import Tau (the other is
`brain.py`). The invariant that matters is that no pack, policy, memory or identity
module does: those are the layers that must stay swappable and independently
testable. `tests/test_tau_boundary.py` enforces it.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterable
from typing import TextIO

from tau_agent import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    AssistantMessage,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnStartEvent,
)
from tau_ai import TextDeltaEvent, ThinkingDeltaEvent

DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
RED = "\033[31m"


class TerminalRenderer:
    """Streams assistant text as it arrives; reports tool activity as it happens.

    Usable two ways: as a harness event listener (``harness.subscribe(renderer)``)
    or by wrapping the event stream (``renderer.stream(harness.prompt(...))``).
    """

    def __init__(
        self,
        *,
        out: TextIO | None = None,
        show_tools: bool = True,
        show_thinking: bool = False,
        verbose: bool = False,
        colour: bool | None = None,
    ) -> None:
        self._out = out or sys.stdout
        self._show_tools = show_tools
        self._show_thinking = show_thinking
        self._verbose = verbose
        self._colour = self._out.isatty() if colour is None else colour
        self._mid_line = False
        self._thinking = False

    # -- painting ----------------------------------------------------------

    def _paint(self, text: str, code: str) -> str:
        if not self._colour:
            return text
        return f"{code}{text}{RESET}"

    def write(self, text: str) -> None:
        self._out.write(text)
        self._out.flush()

    def _newline_if_needed(self) -> None:
        if self._mid_line:
            self.write("\n")
            self._mid_line = False

    # -- the listener ------------------------------------------------------

    def __call__(self, event: AgentEvent) -> None:
        """Render one event. Signature matches Tau's `EventListener`."""
        if isinstance(event, ToolExecutionStartEvent):
            self._on_tool_start(event)
        elif isinstance(event, ToolExecutionEndEvent):
            self._on_tool_end(event)
        elif isinstance(event, MessageUpdateEvent):
            self._on_update(event)
        elif isinstance(event, MessageEndEvent):
            self._on_message_end(event)
        elif self._verbose and isinstance(event, (AgentStartEvent, AgentEndEvent, TurnStartEvent)):
            self._newline_if_needed()
            self.write(self._paint(f"[{event.type}]\n", DIM))

    def _on_update(self, event: MessageUpdateEvent) -> None:
        nested = event.assistant_message_event
        if isinstance(nested, TextDeltaEvent):
            if self._thinking:
                self._newline_if_needed()
                self._thinking = False
            self.write(nested.delta)
            self._mid_line = True
        elif isinstance(nested, ThinkingDeltaEvent) and self._show_thinking:
            if not self._thinking:
                self._newline_if_needed()
                self._thinking = True
            self.write(self._paint(nested.delta, DIM))

    def _on_message_end(self, event: MessageEndEvent) -> None:
        self._thinking = False
        message = event.message

        # A failed turn MUST be visible. Without this, a provider rejection or a
        # timeout produces silence — and silence reads as "Jarvis had nothing to
        # say", which is the one impression a tool-using agent must never give.
        # (This was a real bug: a 401 on the model call printed nothing at all.)
        if isinstance(message, AssistantMessage) and message.stop_reason in {"error", "aborted"}:
            self._newline_if_needed()
            detail = message.error_message or message.stop_reason
            origin = ""
            if message.provider not in (None, "", "unknown"):
                origin = f" [{message.provider} {message.model}]"
            self.write(self._paint(f"\n! the model call failed{origin}: {detail}\n", RED))
            return

        self._newline_if_needed()

    def _on_tool_start(self, event: ToolExecutionStartEvent) -> None:
        if not self._show_tools:
            return
        self._newline_if_needed()
        args = ", ".join(f"{name}={value!r}" for name, value in sorted(event.args.items()))
        if len(args) > 120:
            args = args[:117] + "..."
        self.write(self._paint(f"  → {event.tool_name}({args})\n", CYAN))

    def _on_tool_end(self, event: ToolExecutionEndEvent) -> None:
        if not self._show_tools:
            return
        self._newline_if_needed()
        text = " ".join(event.result.text.split())
        if len(text) > 160:
            text = text[:157] + "..."
        marker = "✗" if event.is_error else "✓"
        line = f"  {marker} {event.tool_name}: {text}\n"
        self.write(self._paint(line, RED if event.is_error else DIM))

    # -- stream wrapper ----------------------------------------------------

    async def stream(self, events: AsyncIterator[AgentEvent]) -> AsyncIterator[AgentEvent]:
        """Render as events pass through, yielding them unchanged."""
        async for event in events:
            self(event)
            yield event

    def finish(self) -> None:
        self._newline_if_needed()


def render_audit(lines: Iterable[str], out: TextIO | None = None) -> None:
    """Print audit lines. Kept here so all terminal output goes through one place."""
    handle = out or sys.stdout
    for line in lines:
        handle.write(line + "\n")
    handle.flush()
