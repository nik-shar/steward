"""Lexical recall over organic memory, with a recency and confidence nudge.

Deliberately **not** embeddings. At personal scale FTS5 plus a small ranking term
is instant, inspectable, and has no model to call — and when it stops being
enough you will know exactly which queries failed, because you can read the
ranking. Swapping in a vector store later is a change to this one module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from steward.clock import age_days
from steward.memory.dna import Memory
from steward.memory.store import MemoryStore

# Ranking weights. Lexical relevance is normalised to [0, 1] across the candidate
# set, so these two are directly comparable as "how much a day of freshness is
# worth" and "how much certainty is worth".
RECENCY_WEIGHT = 1.5
HALF_LIFE_DAYS = 45.0
CONFIDENCE_WEIGHT = 1.0

_MAX_TOKENS = 24
_MIN_TOKEN_LEN = 2


@dataclass(frozen=True, slots=True)
class ScoredMemory:
    """A memory plus the reasons it surfaced, so a bad ranking is diagnosable."""

    memory: Memory
    score: float
    lexical: float
    recency: float

    def render(self) -> str:
        return f"[{self.memory.kind} {self.memory.confidence:.2f} via {self.memory.source}] {self.memory.text}"


def _fts_expression(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Every token is quoted and joined with OR. An unquoted user question can
    otherwise be parsed as FTS5 syntax — ``-`` and ``*`` become operators, and a
    stray quote is a hard error raised from inside a recall call.
    """
    tokens = [t for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) >= _MIN_TOKEN_LEN]
    if not tokens:
        return ""
    # Deduplicate while preserving order, then cap: a long question should not
    # outrank a precise one just by having more words.
    unique = list(dict.fromkeys(t.lower() for t in tokens))[:_MAX_TOKENS]
    return " OR ".join(f'"{token}"' for token in unique)


def _recency(timestamp_ms: int) -> float:
    """Exponential decay in [0, 1]: 1.0 today, 0.5 at the half-life."""
    return 0.5 ** (age_days(timestamp_ms) / HALF_LIFE_DAYS)


def recall(store: MemoryStore, query: str, *, limit: int = 5) -> list[ScoredMemory]:
    """Find the currently-believed memories most relevant to `query`.

    Superseded rows can never surface — a corrected memory must not compete with
    its own correction.
    """
    expression = _fts_expression(query)
    if not expression:
        return recent(store, limit=limit)

    rows = store.query(
        """
        SELECT d.*, bm25(dna_memory_fts) AS bm25_rank
        FROM dna_memory_fts
        JOIN dna_memory d ON d.id = dna_memory_fts.id
        WHERE dna_memory_fts MATCH ? AND d.superseded_by IS NULL
        ORDER BY bm25_rank
        LIMIT ?
        """,
        (expression, max(limit * 4, limit)),
    )
    if not rows:
        return []

    # bm25 in SQLite is negative with lower = better, so -bm25 is a positive
    # relevance score. Normalising by the best hit in this set keeps the weight
    # meaningful regardless of how many terms matched.
    raw = [(-float(row["bm25_rank"]), Memory.from_row(row)) for row in rows]
    best = max(score for score, _ in raw) or 1.0

    scored = [
        ScoredMemory(
            memory=memory,
            score=(score / best) + RECENCY_WEIGHT * _recency(memory.created_at) + CONFIDENCE_WEIGHT * memory.confidence,
            lexical=score / best,
            recency=_recency(memory.created_at),
        )
        for score, memory in raw
    ]
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]


def recent(store: MemoryStore, *, limit: int = 5) -> list[ScoredMemory]:
    """The freshest memories, for when there is no query to match against."""
    rows = store.query(
        "SELECT * FROM current_dna_memory ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return [
        ScoredMemory(
            memory=Memory.from_row(row),
            score=_recency(Memory.from_row(row).created_at),
            lexical=0.0,
            recency=_recency(Memory.from_row(row).created_at),
        )
        for row in rows
    ]
