"""Core pack: knowing about the person.

Mounted inline and permanently. These stay in the main agent **by design** — the
moment "what do I know about this person" moves into a subagent, the main agent is
guessing about its own user and the whole topology argument collapses.

Scopes: reading is ``READ``; writing memory is ``INTERNAL`` — audited, not gated.
Interrupting someone to confirm that Steward remembered their name would make the
system unusable, and the blast radius is an append-only log whose every row records
where it came from and how sure it was.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from steward.clock import now_ms
from steward.memory.dna import ConfidenceCeilingError, DnaError
from steward.memory.recall import recall
from steward.policy.scopes import Scope
from steward.tools.delegate import delegate_tool
from steward.tools.registry import CapabilityPack
from steward.tools.spec import SubagentSpec, ToolContext, ToolOutcome, ToolSpec

GUIDELINES = """\
- `get_profile` and `recall_memory` are how you check what you already know before
  asking. Prefer them to a question you could have answered yourself.
- `remember` is silent. Store what matters; do not announce that you stored it.
- When something you stored turns out to be wrong, pass `corrects` with the memory
  id, so the old belief is superseded rather than erased.
- Never state an inferred memory as though the person had told you. The `source` on
  every memory is there to be respected, not smoothed over.
"""

_KINDS = ("fact", "preference", "goal", "observation", "relationship")
_SOURCES = ("user_stated", "tool", "reflection", "inferred")


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: object) -> float | None:
    """Parse an optional float, ignoring anything that is not plainly numeric."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


async def get_profile(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    rows = ctx.store.query("SELECT key, value, confidence, source FROM profile_facts ORDER BY key")
    memories = ctx.dna.count()
    if not rows and memories == 0:
        return ToolOutcome.ok("Nothing is known yet — this is a cold start. Ask rather than assume.")
    lines = [f"- {row['key']}: {row['value']} ({row['source']}, {row['confidence']:.2f})" for row in rows]
    lines.append(f"- {memories} current memories in the organic store")
    return ToolOutcome.ok("\n".join(lines), details={"facts": len(rows), "memories": memories})


async def recall_memory(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    query = _text(arguments.get("query"))
    limit = int(_number(arguments.get("limit")) or 5)
    limit = max(1, min(25, limit))

    hits = recall(ctx.store, query, limit=limit)
    if not hits:
        return ToolOutcome.ok(f"No memories match {query!r}." if query else "No memories are stored yet.")
    return ToolOutcome.ok(
        "\n".join(f"- {hit.render()}" for hit in hits),
        details={"count": len(hits)},
    )


async def remember(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    text = _text(arguments.get("text"))
    kind = _text(arguments.get("kind")) or "fact"
    source = _text(arguments.get("source")) or "user_stated"
    evidence = _text(arguments.get("evidence")) or None
    corrects = _text(arguments.get("corrects")) or None
    confidence = _number(arguments.get("confidence"))

    try:
        if corrects:
            memory = ctx.dna.supersede(
                corrects, text, kind=kind, source=source, confidence=confidence, evidence=evidence
            )
        else:
            memory = ctx.dna.write(text, kind=kind, source=source, confidence=confidence, evidence=evidence)
    except ConfidenceCeilingError as exc:
        # The refusal is itself evidence worth keeping: it shows the model tried
        # to overstate something.
        ctx.audit.record(
            "memory_refused",
            tool="remember",
            allowed=False,
            reason=str(exc),
            session_id=ctx.session_id,
            detail={"source": source, "confidence": exc.confidence, "ceiling": exc.ceiling},
        )
        return ToolOutcome.error(
            f"Memory refused: {exc} Report the refusal rather than retrying with a different number to get past it."
        )
    except DnaError as exc:
        ctx.audit.record(
            "memory_refused",
            tool="remember",
            allowed=False,
            reason=str(exc),
            session_id=ctx.session_id,
            detail={"source": source},
        )
        return ToolOutcome.error(f"Memory refused: {exc}")

    ctx.audit.record(
        "memory_write",
        tool="remember",
        allowed=True,
        session_id=ctx.session_id,
        detail={
            "id": memory.id,
            "kind": memory.kind,
            "source": memory.source,
            "confidence": memory.confidence,
            "supersedes": memory.supersedes,
        },
    )
    verb = "superseded" if memory.supersedes else "stored"
    return ToolOutcome.ok(
        f"{verb}: {memory.kind} @ {memory.confidence:.2f} from {memory.source} (id {memory.id})",
        details={"id": memory.id, "supersedes": memory.supersedes},
    )


async def set_profile_fact(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    key = _text(arguments.get("key"))
    value = _text(arguments.get("value"))
    if not key or not value:
        return ToolOutcome.error("set_profile_fact requires both `key` and `value`")
    source = _text(arguments.get("source")) or "user_stated"
    confidence = _number(arguments.get("confidence"))
    if confidence is None:
        confidence = 0.9

    # Upsert is correct here: profile_facts is a key/value projection, not a
    # record. The append-only history of how it changed lives in dna_memory.
    ctx.store.execute(
        """
        INSERT INTO profile_facts (key, value, confidence, source, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            confidence = excluded.confidence,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (key, value, confidence, source, now_ms()),
    )
    ctx.store.commit()
    ctx.audit.record(
        "memory_write",
        tool="set_profile_fact",
        allowed=True,
        session_id=ctx.session_id,
        detail={"key": key, "source": source, "confidence": confidence},
    )
    return ToolOutcome.ok(f"profile fact {key!r} set")


MEMORY_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_profile",
        label="Read the profile",
        description=(
            "Read the stable facts Steward holds about the person, plus a count of "
            "stored memories. Call this before asking something you may already know."
        ),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=get_profile,
        scope=Scope.READ,
        pack="core",
    ),
    ToolSpec(
        name="recall_memory",
        label="Recall memories",
        description=(
            "Search what Steward remembers about the person, ranked by relevance, "
            "recency and confidence. Use a short, specific query."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 25,
                    "description": "Maximum results (default 5).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=recall_memory,
        scope=Scope.READ,
        pack="core",
    ),
    ToolSpec(
        name="remember",
        label="Store a memory",
        description=(
            "Append a memory about the person. Confidence is capped by source: "
            "user_stated 0.95, tool 0.85, reflection 0.70, inferred 0.60. A write "
            "above the cap is refused — lower the confidence or change the source. "
            "To correct an existing memory, pass its id as `corrects`."
        ),
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The memory, in one clear sentence."},
                "kind": {"type": "string", "enum": list(_KINDS)},
                "source": {"type": "string", "enum": list(_SOURCES)},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "evidence": {"type": "string", "description": "Why this is believed."},
                "corrects": {"type": "string", "description": "Id of the memory this replaces."},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        handler=remember,
        scope=Scope.INTERNAL,
        pack="core",
    ),
    ToolSpec(
        name="set_profile_fact",
        label="Set a profile fact",
        description="Set a stable fact about the person: name, timezone, working hours, role.",
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
                "source": {"type": "string", "enum": list(_SOURCES)},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            "required": ["key", "value"],
            "additionalProperties": False,
        },
        handler=set_profile_fact,
        scope=Scope.INTERNAL,
        pack="core",
    ),
)


def core_pack(subagents: Mapping[str, SubagentSpec] | None = None) -> CapabilityPack:
    """The core pack, with the delegate tool bound to the current roster."""
    return CapabilityPack(
        name="core",
        summary="Identity, memory, and delegation.",
        tools=(*MEMORY_TOOLS, delegate_tool(subagents)),
        guidelines=GUIDELINES,
    )
