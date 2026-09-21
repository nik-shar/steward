"""Capability scopes: the single answer to "may this be offered, and does it need
consent?".

Three scopes, and the distinction between the last two is the important one:

``READ``
    Observes something. Safe, needs nothing.
``INTERNAL``
    Writes to Steward's *own* stores — memory, the audit log. Audited, not gated.
    Nagging for consent on every remembered fact would make the system unusable,
    and the blast radius is Steward's own memory, which is append-only.
``WRITE``
    Affects the world outside Steward — a calendar, a file, a message. Consent
    required unless the tool is explicitly allowlisted.

The registry **fails closed**: a tool with no registered scope requires consent.
Adding a capability without deciding what it is allowed to do should be annoying.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Scope(StrEnum):
    READ = "read"
    INTERNAL = "internal"
    WRITE = "write"


class ScopeRegistryError(RuntimeError):
    """Raised on a duplicate or malformed scope registration."""


@dataclass(frozen=True, slots=True)
class Capability:
    tool: str
    pack: str
    scope: Scope

    @property
    def needs_consent(self) -> bool:
        return self.scope is Scope.WRITE


class ScopeRegistry:
    """Built from the mounted packs, so it can never disagree with them."""

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}

    def register(self, tool: str, pack: str, scope: Scope) -> Capability:
        if tool in self._capabilities:
            existing = self._capabilities[tool]
            raise ScopeRegistryError(
                f"tool {tool!r} already registered by pack {existing.pack!r}; pack {pack!r} cannot claim it too"
            )
        capability = Capability(tool=tool, pack=pack, scope=scope)
        self._capabilities[tool] = capability
        return capability

    def scope_of(self, tool: str) -> Scope | None:
        capability = self._capabilities.get(tool)
        return capability.scope if capability else None

    def capability_of(self, tool: str) -> Capability | None:
        return self._capabilities.get(tool)

    def requires_consent(self, tool: str, allowlist: frozenset[str]) -> bool:
        """Whether a call to `tool` must be confirmed before it runs.

        Fails closed twice over: an unknown tool needs consent, and only
        ``WRITE`` is ever exemptible.
        """
        capability = self._capabilities.get(tool)
        if capability is None:
            return True
        if capability.scope is not Scope.WRITE:
            return False
        return tool not in allowlist

    def tools(self) -> list[Capability]:
        return sorted(self._capabilities.values(), key=lambda item: (item.pack, item.tool))

    def writes(self) -> list[Capability]:
        return [item for item in self.tools() if item.needs_consent]

    def by_pack(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for capability in self.tools():
            grouped.setdefault(capability.pack, []).append(capability.tool)
        return grouped

    def missing_scope(self, offered: list[str]) -> list[str]:
        """Tools being offered to the model with no declared scope.

        Used as a startup invariant: an unregistered tool would fail closed at
        runtime, which is safe but silent. This makes it loud instead.
        """
        return [tool for tool in offered if tool not in self._capabilities]

    def __len__(self) -> int:
        return len(self._capabilities)
