"""Jarvis's own tool vocabulary.

Deliberately **not** Tau's ``AgentTool``. A pack is data: it declares a name, a
schema, a handler, and — critically — a ``Scope`` saying what it is allowed to do.
No pack imports the harness, so every pack is unit-testable with no provider and
no token spent, and Tau stays confined to one adapter file.

The translation from ``ToolSpec`` to Tau's ``AgentTool`` lives in ``brain.py``.
That is the seam that makes the brain replaceable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from jarvis.policy.scopes import Scope

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

ExecutionMode = Literal["parallel", "sequential"]


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """What a tool reports back to the model."""

    text: str
    details: JsonValue = None
    is_error: bool = False
    terminate: bool = False

    @classmethod
    def ok(cls, text: str, *, details: JsonValue = None, terminate: bool = False) -> ToolOutcome:
        return cls(text=text, details=details, terminate=terminate)

    @classmethod
    def error(cls, text: str) -> ToolOutcome:
        """A failure the model is allowed to see and react to.

        Errors are outcomes, not exceptions. A tool that blows up must not take
        the turn down with it — the model needs to be told what went wrong so it
        can say so instead of inventing an answer.
        """
        return cls(text=text, is_error=True)


@dataclass(frozen=True, slots=True)
class SubagentSpec:
    """A capability-scoped child agent.

    Note what is absent: no profile, no memory mount, no secrets. A subagent gets
    a prompt, a model, and an explicit allowlist of tool names. If a task seems to
    need personal data to run, that is the signal it belongs in the main agent.
    """

    name: str
    purpose: str
    system: str
    tools: tuple[str, ...]
    model: str | None = None
    max_turns: int = 8


# Built by brain.py, which is the only place that can construct a child harness.
# Injected into ToolContext so delegate() is testable with a fake runner.
SubagentRunner: TypeAlias = Callable[[SubagentSpec, str], Awaitable[ToolOutcome]]


ToolHandler: TypeAlias = Callable[["ToolContext", Mapping[str, Any]], Awaitable[ToolOutcome]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One capability offered to the model."""

    name: str
    label: str
    description: str
    parameters: Mapping[str, JsonValue]
    handler: ToolHandler
    scope: Scope
    pack: str = "core"
    guidelines: tuple[str, ...] = ()
    execution: ExecutionMode = "parallel"

    @property
    def writes(self) -> bool:
        """Whether this changes the world outside Jarvis (and so needs consent)."""
        return self.scope is Scope.WRITE


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a tool may touch, passed explicitly.

    A tool receives this; it does not import the store, build a connection, or
    reach for a global. That is what keeps tools independent of each other and
    of how they were mounted.
    """

    settings: Any
    store: Any
    dna: Any
    audit: Any
    scopes: Any
    run_subagent: SubagentRunner | None = None
    session_id: str | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)
