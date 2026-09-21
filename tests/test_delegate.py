"""The delegation contract.

M1 ships the seam with an empty roster, so what is tested here is the *contract*
that M3 will rely on:

* an unknown agent fails loudly and names what is available;
* an empty roster says so plainly instead of pretending it delegated something;
* the delegation is audited **before** it runs, with the tool subset it granted;
* the runner receives exactly the spec, and only the result comes back.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from steward.tools.delegate import SUBAGENTS, delegate_tool, register, roster
from steward.tools.spec import SubagentSpec, ToolContext, ToolOutcome

RESEARCHER = SubagentSpec(
    name="researcher",
    purpose="find and summarise external sources",
    system="You research one question and reply with findings only.",
    tools=("get_profile",),
)


def test_roster_starts_empty() -> None:
    """M1 ships the seam, not a roster. If this fails, M3 has landed."""
    assert roster() == {}


def test_roster_is_a_copy() -> None:
    """A caller must not be able to corrupt the global roster by accident."""
    snapshot = roster()
    snapshot["sneaky"] = RESEARCHER
    assert "sneaky" not in roster()


def test_register_refuses_a_duplicate_name() -> None:
    original = dict(SUBAGENTS)
    try:
        register(RESEARCHER)
        with pytest.raises(ValueError, match="already registered"):
            register(RESEARCHER)
    finally:
        SUBAGENTS.clear()
        SUBAGENTS.update(original)


# -- failing honestly ----------------------------------------------------------


async def test_unknown_agent_lists_what_is_available(ctx: ToolContext) -> None:
    tool = delegate_tool({"researcher": RESEARCHER})
    outcome = await tool.handler(ctx, {"agent": "nobody", "task": "do a thing"})

    assert outcome.is_error
    assert "unknown subagent 'nobody'" in outcome.text
    assert "researcher" in outcome.text


async def test_empty_roster_says_so_plainly(ctx: ToolContext) -> None:
    tool = delegate_tool({})
    outcome = await tool.handler(ctx, {"agent": "researcher", "task": "do a thing"})

    assert outcome.is_error
    assert "none registered yet" in outcome.text


async def test_missing_task_is_refused(ctx: ToolContext) -> None:
    tool = delegate_tool({"researcher": RESEARCHER})
    outcome = await tool.handler(ctx, {"agent": "researcher", "task": "   "})
    assert outcome.is_error and "non-empty" in outcome.text


async def test_missing_agent_is_refused(ctx: ToolContext) -> None:
    outcome = await delegate_tool({}).handler(ctx, {"task": "something"})
    assert outcome.is_error and "agent" in outcome.text


async def test_no_runner_is_reported_rather_than_faked(ctx: ToolContext) -> None:
    """With no runner wired, steward must not invent a plausible result."""
    assert ctx.run_subagent is None
    outcome = await delegate_tool({"researcher": RESEARCHER}).handler(ctx, {"agent": "researcher", "task": "find x"})

    assert outcome.is_error
    assert "no subagent runner" in outcome.text


# -- the contract itself -------------------------------------------------------


async def test_runner_receives_the_spec_and_a_stripped_task(ctx: ToolContext) -> None:
    seen: dict[str, object] = {}

    async def runner(spec: SubagentSpec, task: str) -> ToolOutcome:
        seen["spec"] = spec
        seen["task"] = task
        return ToolOutcome.ok("three sources found")

    wired = replace(ctx, run_subagent=runner)
    outcome = await delegate_tool({"researcher": RESEARCHER}).handler(
        wired, {"agent": "researcher", "task": "  find sources on X  "}
    )

    assert outcome.text == "three sources found"
    assert seen["spec"] is RESEARCHER
    assert seen["task"] == "find sources on X"


async def test_delegation_is_audited_with_the_granted_tools(ctx: ToolContext) -> None:
    """Recorded before it runs, so a delegation that crashes still appears."""

    async def runner(spec: SubagentSpec, task: str) -> ToolOutcome:
        return ToolOutcome.ok("done")

    wired = replace(ctx, run_subagent=runner)
    await delegate_tool({"researcher": RESEARCHER}).handler(wired, {"agent": "researcher", "task": "find x"})

    delegations = [record for record in ctx.audit.read() if record.kind == "delegation"]
    assert len(delegations) == 1
    assert delegations[0].detail["agent"] == "researcher"
    assert delegations[0].detail["tools"] == ["get_profile"]


def test_delegate_does_not_write_so_it_does_not_prompt(mounted) -> None:
    """Dispatching is not a write. Any write inside the child is gated by the child."""
    from steward.policy.scopes import Scope

    assert mounted.scopes.scope_of("delegate") is Scope.READ
    assert not mounted.scopes.requires_consent("delegate", frozenset())


def test_delegate_is_offered_to_the_model(mounted) -> None:
    spec = mounted.tool_by_name()["delegate"]
    assert spec.parameters["required"] == ["agent", "task"]
    assert spec.parameters["additionalProperties"] is False
