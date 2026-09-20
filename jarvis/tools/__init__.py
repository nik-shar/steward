"""Jarvis's tool vocabulary, packs, and the delegation seam.

Nothing in this package imports Tau. Packs are data; `brain.py` is the one place
that turns a `ToolSpec` into something the harness can call.
"""

from jarvis.tools.delegate import SUBAGENTS, delegate_tool, register, roster
from jarvis.tools.registry import CapabilityPack, Mounted, MountError, mount
from jarvis.tools.spec import (
    JsonValue,
    SubagentRunner,
    SubagentSpec,
    ToolContext,
    ToolHandler,
    ToolOutcome,
    ToolSpec,
)

__all__ = [
    "SUBAGENTS",
    "CapabilityPack",
    "JsonValue",
    "MountError",
    "Mounted",
    "SubagentRunner",
    "SubagentSpec",
    "ToolContext",
    "ToolHandler",
    "ToolOutcome",
    "ToolSpec",
    "delegate_tool",
    "mount",
    "register",
    "roster",
]
