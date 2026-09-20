"""The consent gate.

This speaks primitives — ``check(tool_name, arguments) -> ConsentDecision`` — and
knows nothing about Tau. `brain.py` adapts it onto the harness's
``before_tool_call`` hook, which is what lets the entire safety path be tested
without a provider, a harness, or a token.

Standing order #2 from `guidelines.md`: never work around the gate. So the gate
**fails closed** and it records *every* verdict, including the ones it allows. An
audit log that only holds denials cannot tell you what Jarvis did.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any

from jarvis.policy.audit import AuditLog
from jarvis.policy.scopes import Scope, ScopeRegistry

Arguments = Mapping[str, Any]

# Ask the person. Returns True to allow. May be sync or async so the CLI can pass
# a plain function and a future web frontend can pass a coroutine.
Prompter = Callable[[str, Arguments], Awaitable[bool] | bool]

_SECRET_KEY_HINTS = ("key", "token", "secret", "password", "authorization", "credential")
_REDACTED = "***redacted***"


def redact(arguments: Arguments) -> dict[str, Any]:
    """Mask secret-looking values before they reach the audit log.

    Keyed on the argument *name*, because that is all we can know statically. The
    audit log is a file on disk that outlives the turn; a provider key written
    into it once is a key leaked forever.
    """
    safe: dict[str, Any] = {}
    for name, value in arguments.items():
        if any(hint in name.lower() for hint in _SECRET_KEY_HINTS):
            safe[name] = _REDACTED
        elif isinstance(value, Mapping):
            safe[name] = redact(value)
        else:
            safe[name] = value
    return safe


@dataclass(frozen=True, slots=True)
class ConsentDecision:
    allowed: bool
    reason: str | None = None
    # True when the tool was allowed without asking, so the UI can stay quiet.
    silent: bool = False

    @classmethod
    def allow(cls, *, reason: str | None = None, silent: bool = False) -> ConsentDecision:
        return cls(allowed=True, reason=reason, silent=silent)

    @classmethod
    def deny(cls, reason: str) -> ConsentDecision:
        return cls(allowed=False, reason=reason)


def describe_call(tool: str, arguments: Arguments) -> str:
    """A one-line question a human can actually answer."""
    if not arguments:
        return f"Allow {tool}?"
    rendered = ", ".join(f"{name}={value!r}" for name, value in sorted(arguments.items()))
    return f"Allow {tool}({rendered})?"


class ConsentGate:
    """Decides whether a tool call may proceed, and records the decision."""

    def __init__(
        self,
        *,
        scopes: ScopeRegistry,
        allowlist: frozenset[str],
        audit: AuditLog,
        prompter: Prompter | None = None,
        session_id: str | None = None,
    ) -> None:
        self._scopes = scopes
        self._allowlist = allowlist
        self._audit = audit
        self._prompter = prompter
        self._session_id = session_id

    @property
    def allowlist(self) -> frozenset[str]:
        return self._allowlist

    async def check(self, tool: str, arguments: Arguments) -> ConsentDecision:
        capability = self._scopes.capability_of(tool)
        decision = await self._decide(tool, arguments, capability)
        self._audit.record(
            "consent",
            tool=tool,
            allowed=decision.allowed,
            reason=decision.reason,
            session_id=self._session_id,
            detail={"arguments": redact(arguments)},
        )
        return decision

    async def _decide(self, tool: str, arguments: Arguments, capability: Any) -> ConsentDecision:
        if capability is None:
            # Fail closed. An unregistered tool is a bug, and a bug must not be
            # able to write to a calendar.
            return ConsentDecision.deny(f"{tool!r} has no declared capability scope")

        if capability.scope is not Scope.WRITE:
            return ConsentDecision.allow(silent=True)

        if tool in self._allowlist:
            return ConsentDecision.allow(reason="allowlisted", silent=True)

        if self._prompter is None:
            return ConsentDecision.deny(
                f"{tool!r} writes, and this run has no way to ask you "
                "(non-interactive). Re-run with --yes to grant consent up front."
            )

        answer = self._prompter(describe_call(tool, arguments), arguments)
        granted = await answer if isawaitable(answer) else answer
        return ConsentDecision.allow(reason="approved by you") if granted else ConsentDecision.deny("declined by you")
