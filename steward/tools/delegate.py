"""The delegation seam.

This is the M1 half of the A -> B continuum: the tool exists, its contract is
enforced, and the roster is deliberately **empty**. Promoting a pack to a real
subagent in M3 means registering a ``SubagentSpec`` here and mounting that pack on
the child instead of inline. Nothing else changes.

The contract, enforced by construction rather than by request:

* a subagent gets an **explicit tool allowlist**, never the parent's full list;
* a subagent gets **no memory mount and no profile** — if a task seems to need
  personal data to run, it belongs in the main agent, not in a child;
* only the **structured result** crosses back. The child's transcript is
  discarded. Context isolation is the entire point of delegating.

Writes performed *inside* a child still pass through a consent gate: the child
harness is built with the same gate, so `place_block` in a subagent prompts
exactly as it would in the main agent.
"""

from __future__ import annotations

from collections.abc import Mapping

from steward.policy.scopes import Scope
from steward.tools.spec import SubagentSpec, ToolContext, ToolOutcome, ToolSpec

#: Registered subagents. Empty until M3, and the tool says so plainly rather than
#: pretending it delegated something.
SUBAGENTS: dict[str, SubagentSpec] = {}


def register(spec: SubagentSpec) -> SubagentSpec:
    """Add a subagent to the roster. Idempotent-safe: a duplicate name is an error."""
    if spec.name in SUBAGENTS:
        raise ValueError(f"subagent {spec.name!r} is already registered")
    SUBAGENTS[spec.name] = spec
    return spec


def roster() -> dict[str, SubagentSpec]:
    """A copy, so a caller cannot mutate the roster by accident."""
    return dict(SUBAGENTS)


def _describe(specs: Mapping[str, SubagentSpec]) -> str:
    if not specs:
        return (
            "No subagents are registered yet, so this will always fail. Do not call "
            "it as a fallback — just do the work yourself or say you cannot."
        )
    lines = ["Available subagents:"]
    for spec in sorted(specs.values(), key=lambda item: item.name):
        lines.append(f"  - {spec.name}: {spec.purpose}")
    return "\n".join(lines)


def delegate_tool(specs: Mapping[str, SubagentSpec] | None = None) -> ToolSpec:
    """Build the ``delegate`` tool against a roster (the global one by default)."""
    known = dict(specs if specs is not None else SUBAGENTS)

    async def handler(ctx: ToolContext, arguments: Mapping[str, object]) -> ToolOutcome:
        agent = str(arguments.get("agent", "")).strip()
        task = str(arguments.get("task", "")).strip()

        if not agent:
            return ToolOutcome.error("delegate requires an `agent` name")
        if not task:
            return ToolOutcome.error("delegate requires a non-empty `task`")

        spec = known.get(agent)
        if spec is None:
            available = ", ".join(sorted(known)) if known else "none registered yet"
            return ToolOutcome.error(f"unknown subagent {agent!r}. Available: {available}.")
        if ctx.run_subagent is None:
            return ToolOutcome.error(
                "no subagent runner is wired into this process, so delegation is "
                "unavailable. Do the task yourself instead."
            )

        # Recorded before it runs: a delegation that crashes still appears in the
        # audit log, with the tool subset the child was granted.
        ctx.audit.record(
            "delegation",
            tool="delegate",
            allowed=True,
            session_id=ctx.session_id,
            detail={"agent": spec.name, "task": task, "tools": list(spec.tools)},
        )
        return await ctx.run_subagent(spec, task)

    return ToolSpec(
        name="delegate",
        label="Delegate a bounded task",
        description=(
            "Hand one bounded, self-contained task to a capability-scoped subagent "
            "and receive only its result. Use it for work that has a narrow input "
            "and a narrow output; do not use it to pass along a conversation, and "
            "do not assume the subagent can see anything you can see. " + _describe(known)
        ),
        parameters={
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Name of the subagent to run.",
                },
                "task": {
                    "type": "string",
                    "description": (
                        "The complete task, written so someone with only the subagent's tools could carry it out."
                    ),
                },
            },
            "required": ["agent", "task"],
            "additionalProperties": False,
        },
        handler=handler,
        # Dispatching is not itself a write. Any write performed inside the child
        # is gated by that child's own ConsentGate.
        scope=Scope.READ,
        pack="core",
        guidelines=(
            "Delegate only when the task is bounded and its result fits in a short "
            "summary. The subagent cannot see your conversation or your memory.",
        ),
    )
