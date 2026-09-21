"""Shared fixtures.

Everything here is local and deterministic: no provider, no network, no token
spent. That is a property of the design, not a convenience — the memory, policy and
tool layers never touch the harness, so they are testable in isolation.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from tau_agent import AssistantMessage, ToolCall
from tau_ai.events import (
    AssistantDoneEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

from steward.config import Paths, ProviderSettings, Settings
from steward.memory.dna import DnaMemory
from steward.memory.store import MemoryStore
from steward.packs import default_packs
from steward.policy.audit import AuditLog
from steward.policy.consent import ConsentGate
from steward.policy.scopes import ScopeRegistry
from steward.tools.registry import Mounted, mount
from steward.tools.spec import ToolContext


@pytest.fixture
def store(tmp_path: Path) -> Iterator[MemoryStore]:
    instance = MemoryStore(tmp_path / "steward.db")
    yield instance
    instance.close()


@pytest.fixture
def dna(store: MemoryStore) -> DnaMemory:
    return DnaMemory(store)


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def mounted() -> Mounted:
    return mount(default_packs())


@pytest.fixture
def scopes(mounted: Mounted) -> ScopeRegistry:
    return mounted.scopes


@pytest.fixture
def gate(scopes: ScopeRegistry, audit: AuditLog) -> ConsentGate:
    """A gate with no prompter — the non-interactive default, which fails closed."""
    return ConsentGate(scopes=scopes, allowlist=frozenset(), audit=audit)


@pytest.fixture
def ctx(store: MemoryStore, dna: DnaMemory, audit: AuditLog, mounted: Mounted) -> ToolContext:
    """A tool context wired to a temp store. `settings` is unused by the core pack."""
    return ToolContext(
        settings=None,
        store=store,
        dna=dna,
        audit=audit,
        scopes=mounted.scopes,
        session_id="test-session",
    )


def tool_by_name(mounted: Mounted, name: str):
    return mounted.tool_by_name()[name]


# ---------------------------------------------------------------------------
# Spending model budget
# ---------------------------------------------------------------------------

#: Marks a test that would call a real provider and cost money. `uv run pytest` skips
#: these, so the default is free; run them deliberately with
#: `STEWARD_LIVE=1 uv run pytest -m live`. `test_no_accidental_spend.py` enforces that
#: anything carrying `@pytest.mark.live` also carries this.
requires_live = pytest.mark.skipif(
    os.environ.get("STEWARD_LIVE") != "1",
    reason="needs a real provider and spends tokens; set STEWARD_LIVE=1 to allow it",
)


# ---------------------------------------------------------------------------
# Spine helpers
#
# The harness is driven with Tau's `FakeProvider`, which replays scripted
# assistant streams. So the whole spine — tool dispatch, the consent gate, memory,
# audit, the session file — runs offline and deterministically, spending nothing.
# ---------------------------------------------------------------------------


def settings_for(tmp_path: Path, *, allowlist: frozenset[str] = frozenset()) -> Settings:
    """Settings pointed entirely at `tmp_path`, so no real data is ever touched."""
    paths = Paths(
        home=tmp_path,
        db=tmp_path / "steward.db",
        sessions=tmp_path / "sessions",
        audit=tmp_path / "audit.jsonl",
        vault=tmp_path / "vault",
        identity=tmp_path / "identity",
    ).ensure()
    return Settings(
        paths=paths,
        provider=ProviderSettings(api_key="test-key", base_url="http://127.0.0.1:9/v1", model="test-model"),
        max_turns=4,
        consent_allowlist=allowlist,
    )


def text_stream(text: str) -> list:
    """A complete assistant response: say `text`, then stop."""
    partial = AssistantMessage(content=text)
    return [
        AssistantStartEvent(partial=AssistantMessage()),
        TextDeltaEvent(content_index=0, delta=text, partial=partial),
        TextEndEvent(content_index=0, content=text, partial=partial),
        AssistantDoneEvent(reason="stop", message=partial),
    ]


def tool_stream(name: str, arguments: dict) -> list:
    """A complete assistant response: request one tool call."""
    call = ToolCall(id=f"call-{name}", name=name, arguments=arguments)
    partial = AssistantMessage(content=[call])
    return [
        AssistantStartEvent(partial=AssistantMessage()),
        ToolCallStartEvent(content_index=0, partial=partial),
        ToolCallEndEvent(content_index=0, tool_call=call, partial=partial),
        AssistantDoneEvent(reason="toolUse", message=partial),
    ]


@dataclass(frozen=True, slots=True)
class Streams:
    """Scripted provider responses, so a test reads like a transcript."""

    text: Callable[[str], list]
    tool: Callable[[str, dict], list]


@pytest.fixture
def settings_factory(tmp_path: Path):
    """`settings_factory(allowlist=...)` — always scoped to this test's tmp_path."""

    def build(**kwargs):
        return settings_for(tmp_path, **kwargs)

    return build


@pytest.fixture
def vault_ctx(tmp_path: Path, store: MemoryStore, dna: DnaMemory, audit: AuditLog, mounted: Mounted):
    """A tool context whose settings point at a temp vault, for the notes pack.

    The notes tools read the vault path out of settings, so unlike the core pack they
    need a real `Settings` rather than `None`.
    """
    return ToolContext(
        settings=settings_for(tmp_path),
        store=store,
        dna=dna,
        audit=audit,
        scopes=mounted.scopes,
        session_id="test-session",
    )


@pytest.fixture
def streams() -> Streams:
    return Streams(text=text_stream, tool=tool_stream)
