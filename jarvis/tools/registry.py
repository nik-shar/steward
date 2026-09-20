"""Mounting capability packs.

A pack is a bag of tools plus the guidelines that steer them. Mounting does three
things and refuses to do a fourth:

1. collects the tool list the harness will offer,
2. builds the ``ScopeRegistry`` from those same tools, so the registry can never
   drift from what is actually offered,
3. concatenates the packs' guidelines into the system prompt.

What it refuses: a duplicate tool name across packs, or a tool with no scope. Two
packs claiming `place_block` is a silent behaviour swap waiting to happen, and it
should fail at startup rather than at 3am.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from jarvis.policy.scopes import ScopeRegistry
from jarvis.tools.spec import ToolSpec


@dataclass(frozen=True, slots=True)
class CapabilityPack:
    """A coherent set of capabilities — mounted inline now, promotable later.

    The whole point of this type is that promoting a pack into its own subagent
    (topology A -> B) does not change the pack, only where it is mounted.
    """

    name: str
    summary: str
    tools: tuple[ToolSpec, ...]
    guidelines: str = ""


@dataclass(frozen=True, slots=True)
class Mounted:
    """The result of mounting packs: everything the brain needs, in one place."""

    tools: tuple[ToolSpec, ...]
    scopes: ScopeRegistry
    guidelines: str
    packs: tuple[str, ...]

    def tool_by_name(self) -> dict[str, ToolSpec]:
        return {tool.name: tool for tool in self.tools}


class MountError(RuntimeError):
    """Raised when packs cannot be mounted coherently."""


def mount(packs: Sequence[CapabilityPack]) -> Mounted:
    """Mount packs in order. Raises rather than resolving a conflict silently."""
    tools: list[ToolSpec] = []
    scopes = ScopeRegistry()
    sections: list[str] = []

    for pack in packs:
        if not pack.tools:
            raise MountError(f"pack {pack.name!r} declares no tools")
        for tool in pack.tools:
            scopes.register(tool.name, pack.name, tool.scope)
            tools.append(tool)
        if pack.guidelines.strip():
            sections.append(pack.guidelines.strip())

    if not tools:
        raise MountError("no tools mounted; Jarvis would have nothing to do")

    return Mounted(
        tools=tuple(tools),
        scopes=scopes,
        guidelines="\n\n".join(sections),
        packs=tuple(pack.name for pack in packs),
    )
