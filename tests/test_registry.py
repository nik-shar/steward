"""Mounting packs: the invariants that must not be resolved silently.

Two things can go wrong when packs meet, and both are startup errors rather than
runtime surprises:

* two packs claim the same tool name — a silent behaviour swap waiting to happen;
* a tool has no declared scope — which the consent gate would fail closed on, at
  runtime, invisibly.
"""

from __future__ import annotations

import pytest

from steward.packs import core_pack, default_packs
from steward.policy.scopes import Scope, ScopeRegistryError
from steward.tools.registry import CapabilityPack, MountError, mount
from steward.tools.spec import ToolOutcome, ToolSpec


async def _noop(ctx, arguments):  # pragma: no cover - never executed here
    return ToolOutcome.ok("noop")


def _tool(name: str, scope: Scope, pack: str = "test") -> ToolSpec:
    return ToolSpec(
        name=name,
        label=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        handler=_noop,
        scope=scope,
        pack=pack,
    )


def test_default_packs_mount() -> None:
    mounted = mount(default_packs())

    assert mounted.packs == ("core", "calendar", "notes")
    names = {tool.name for tool in mounted.tools}
    assert names == {
        # core — identity, memory, delegation
        "get_profile",
        "recall_memory",
        "remember",
        "set_profile_fact",
        "delegate",
        # calendar — reads then writes
        "get_day",
        "find_availability",
        "place_block",
        "remove_block",
        # notes — reads then one append
        "list_notes",
        "read_note",
        "search_notes",
        "append_note",
    }
    # The registry is built from the very tools on offer, so it cannot drift.
    assert mounted.scopes.missing_scope(list(names)) == []


def test_the_exact_set_of_world_writing_tools() -> None:
    """An inventory of every capability that can change something outside Steward.

    This is the test to read when you want to know what Steward is *allowed to do*.
    Adding a write tool anywhere should fail here, loudly, and make whoever added it
    decide consciously that it needs consent.
    """
    mounted = mount(default_packs())
    writes = {capability.tool for capability in mounted.scopes.writes()}

    assert writes == {"place_block", "remove_block", "append_note"}


def test_core_alone_never_touches_the_outside_world() -> None:
    """Core writes only to Steward's own memory, so mounting it alone prompts for nothing."""
    mounted = mount([core_pack()])
    assert mounted.scopes.writes() == []


def test_memory_tools_are_internal_not_read() -> None:
    """`remember` writes, but not to the world — so it is audited, not gated."""
    mounted = mount(default_packs())
    assert mounted.scopes.scope_of("get_profile") is Scope.READ
    assert mounted.scopes.scope_of("remember") is Scope.INTERNAL
    assert mounted.scopes.scope_of("set_profile_fact") is Scope.INTERNAL
    assert not mounted.scopes.requires_consent("remember", frozenset())


def test_duplicate_tool_name_across_packs_is_refused() -> None:
    pack_a = CapabilityPack(name="a", summary="", tools=(_tool("shared", Scope.READ),))
    pack_b = CapabilityPack(name="b", summary="", tools=(_tool("shared", Scope.READ),))

    with pytest.raises(ScopeRegistryError, match="already registered"):
        mount([pack_a, pack_b])


def test_pack_with_no_tools_is_refused() -> None:
    with pytest.raises(MountError, match="no tools"):
        mount([CapabilityPack(name="empty", summary="", tools=())])


def test_mounting_nothing_is_refused() -> None:
    with pytest.raises(MountError):
        mount([])


def test_guidelines_are_concatenated_and_blank_ones_skipped() -> None:
    pack_a = CapabilityPack(
        name="a",
        summary="",
        tools=(_tool("one", Scope.READ),),
        guidelines="Rule A",
    )
    pack_b = CapabilityPack(
        name="b",
        summary="",
        tools=(_tool("two", Scope.READ),),
        guidelines="   ",
    )

    mounted = mount([pack_a, pack_b])
    assert mounted.guidelines == "Rule A"


def test_growing_the_roster_updates_the_delegate_description() -> None:
    """The delegate tool must describe the roster it actually has."""
    from steward.tools.spec import SubagentSpec

    without = mount([core_pack()]).tool_by_name()["delegate"]
    with_one = mount(
        [
            core_pack(
                {
                    "researcher": SubagentSpec(
                        name="researcher", purpose="find things", system="s", tools=("get_profile",)
                    )
                }
            )
        ]
    ).tool_by_name()["delegate"]

    assert "No subagents are registered yet" in without.description
    assert "researcher: find things" in with_one.description
