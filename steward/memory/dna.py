"""Organic memory: append-only, superseding, with ceilings enforced in code.

Two rules, and the reason for each:

**Append-only with supersede.** A correction writes a new row and points at the
old one. Nothing is ever edited or deleted, so the store stays auditable and a
bad reflection is recoverable rather than destructive.

**Confidence ceilings in code, not in a prompt.** A model told "cap inferred
memories at 0.60" will eventually write 0.9. So the arithmetic lives here and the
write is refused outright. `ceiling` is persisted on the row so a later reader can
see what the store was allowed to believe at the time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from steward.clock import now_ms
from steward.memory.store import MemoryStore

MemoryKind = Literal["fact", "preference", "goal", "observation", "relationship"]
MemorySource = Literal["user_stated", "tool", "reflection", "inferred"]

MEMORY_KINDS: frozenset[str] = frozenset({"fact", "preference", "goal", "observation", "relationship"})

# How much each kind of evidence is allowed to claim. A statement from the person
# is the strongest thing Steward can hold; its own inference is the weakest.
CONFIDENCE_CEILINGS: dict[str, float] = {
    "user_stated": 0.95,
    "tool": 0.85,
    "reflection": 0.70,
    "inferred": 0.60,
}

DEFAULT_CONFIDENCE: dict[str, float] = {
    "user_stated": 0.90,
    "tool": 0.80,
    "reflection": 0.60,
    "inferred": 0.50,
}


class DnaError(RuntimeError):
    """Base class for organic-memory failures."""


class ConfidenceCeilingError(DnaError):
    """Raised when a write claims more certainty than its source permits."""

    def __init__(self, source: str, confidence: float, ceiling: float) -> None:
        super().__init__(
            f"refused: source {source!r} may claim at most {ceiling:.2f}, got {confidence:.2f}. "
            "Lower the confidence or change the source — do not retry the same number."
        )
        self.source = source
        self.confidence = confidence
        self.ceiling = ceiling


class UnknownSourceError(DnaError):
    """Raised for a source with no defined ceiling — fail loud, never assume."""

    def __init__(self, source: str) -> None:
        super().__init__(f"unknown memory source {source!r}; known: {sorted(CONFIDENCE_CEILINGS)}")
        self.source = source


@dataclass(frozen=True, slots=True)
class Memory:
    """One row of `dna_memory`."""

    id: str
    text: str
    kind: str
    confidence: float
    ceiling: float
    source: str
    evidence: str | None
    created_at: int
    superseded_by: str | None
    supersedes: str | None

    @property
    def is_current(self) -> bool:
        """True when no successor has replaced this memory."""
        return self.superseded_by is None

    @classmethod
    def from_row(cls, row: Any) -> Memory:
        data = dict(row)
        return cls(
            id=data["id"],
            text=data["text"],
            kind=data["kind"],
            confidence=data["confidence"],
            ceiling=data["ceiling"],
            source=data["source"],
            evidence=data["evidence"],
            created_at=data["created_at"],
            superseded_by=data["superseded_by"],
            supersedes=data["supersedes"],
        )


class DnaMemory:
    """Reads and writes `dna_memory`. Every write goes through `MemoryStore`."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    # -- writes ------------------------------------------------------------

    def write(
        self,
        text: str,
        *,
        kind: MemoryKind = "fact",
        source: MemorySource = "user_stated",
        confidence: float | None = None,
        evidence: str | None = None,
    ) -> Memory:
        """Append one memory. Raises rather than silently clamping the confidence."""
        return self._insert(
            text=text,
            kind=kind,
            source=source,
            confidence=confidence,
            evidence=evidence,
            supersedes=None,
        )

    def supersede(
        self,
        old_id: str,
        text: str,
        *,
        kind: MemoryKind | None = None,
        source: MemorySource = "user_stated",
        confidence: float | None = None,
        evidence: str | None = None,
    ) -> Memory:
        """Correct a memory by appending a successor and pointing the old row at it."""
        previous = self.get(old_id)
        if previous is None:
            raise DnaError(f"cannot supersede unknown memory {old_id!r}")
        if not previous.is_current:
            raise DnaError(
                f"memory {old_id!r} was already superseded by {previous.superseded_by!r}; "
                "supersede the current row instead"
            )
        successor = self._insert(
            text=text,
            kind=kind or previous.kind,
            source=source,
            confidence=confidence,
            evidence=evidence,
            supersedes=old_id,
        )
        # The only UPDATE in this module, and it points forward rather than
        # rewriting history: the old row and its text stay exactly as written.
        self._store.execute(
            "UPDATE dna_memory SET superseded_by = ? WHERE id = ?",
            (successor.id, old_id),
        )
        self._store.commit()
        return successor

    def _insert(
        self,
        *,
        text: str,
        kind: str,
        source: str,
        confidence: float | None,
        evidence: str | None,
        supersedes: str | None,
    ) -> Memory:
        if source not in CONFIDENCE_CEILINGS:
            raise UnknownSourceError(source)
        if kind not in MEMORY_KINDS:
            raise DnaError(f"unknown memory kind {kind!r}; known: {sorted(MEMORY_KINDS)}")

        snippet = text.strip()
        if not snippet:
            raise DnaError("refused: empty memory text")

        ceiling = CONFIDENCE_CEILINGS[source]
        claimed = DEFAULT_CONFIDENCE[source] if confidence is None else float(confidence)
        if claimed > ceiling:
            raise ConfidenceCeilingError(source, claimed, ceiling)
        if claimed < 0.0:
            raise DnaError(f"confidence must be within [0, 1], got {claimed}")

        memory_id = uuid4().hex
        self._store.execute(
            """
            INSERT INTO dna_memory
                (id, text, kind, confidence, ceiling, source, evidence, created_at,
                 superseded_by, supersedes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (memory_id, snippet, kind, claimed, ceiling, source, evidence, now_ms(), supersedes),
        )
        self._store.commit()

        stored = self.get(memory_id)
        if stored is None:  # pragma: no cover - would mean the insert was lost
            raise DnaError(f"memory {memory_id!r} did not persist")
        return stored

    # -- reads -------------------------------------------------------------

    def get(self, memory_id: str) -> Memory | None:
        row = self._store.query_one("SELECT * FROM dna_memory WHERE id = ?", (memory_id,))
        return Memory.from_row(row) if row is not None else None

    def current(self, limit: int = 50) -> list[Memory]:
        """What Steward currently believes, newest first."""
        rows = self._store.query(
            "SELECT * FROM current_dna_memory ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [Memory.from_row(row) for row in rows]

    def history(self, memory_id: str) -> list[Memory]:
        """The correction chain behind a memory, oldest first.

        This is what append-only buys: you can always see what Steward used to
        believe and when it changed its mind.
        """
        chain: list[Memory] = []
        seen: set[str] = set()
        cursor: str | None = memory_id
        while cursor and cursor not in seen:
            seen.add(cursor)
            node = self.get(cursor)
            if node is None:
                break
            chain.append(node)
            cursor = node.supersedes
        return list(reversed(chain))

    def count(self) -> int:
        """Number of currently-believed memories."""
        row = self._store.query_one("SELECT COUNT(*) AS n FROM current_dna_memory")
        return int(row["n"]) if row is not None else 0
