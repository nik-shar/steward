"""Append-only audit log.

Deliberately JSONL and deliberately *outside* SQLite: an audit trail must not live
inside the thing it audits, and for something you read when you are suspicious,
greppability beats query power.

Everything safety-relevant lands here — every tool call, every consent verdict,
every refused memory write, every delegation. If you cannot answer "why did it do
that?" from this file, the file is wrong.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from steward.clock import now_ms, to_iso

AUDIT_KINDS = frozenset({"tool_call", "consent", "memory_write", "memory_refused", "delegation", "error"})


@dataclass(frozen=True, slots=True)
class AuditRecord:
    at: int
    kind: str
    tool: str | None = None
    allowed: bool | None = None
    reason: str | None = None
    session_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        """One human-readable line, for `steward --audit`."""
        parts = [to_iso(self.at), self.kind]
        if self.tool:
            parts.append(self.tool)
        if self.allowed is not None:
            parts.append("allowed" if self.allowed else "DENIED")
        if self.reason:
            parts.append(f"({self.reason})")
        return "  ".join(parts)


class AuditLog:
    """Appends records; never rewrites or truncates the file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        kind: str,
        *,
        tool: str | None = None,
        allowed: bool | None = None,
        reason: str | None = None,
        session_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditRecord:
        if kind not in AUDIT_KINDS:
            raise ValueError(f"unknown audit kind {kind!r}; known: {sorted(AUDIT_KINDS)}")
        record = AuditRecord(
            at=now_ms(),
            kind=kind,
            tool=tool,
            allowed=allowed,
            reason=reason,
            session_id=session_id,
            detail=detail or {},
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
            handle.flush()
        return record

    def read(self) -> list[AuditRecord]:
        if not self.path.exists():
            return []
        records: list[AuditRecord] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                # A torn final line must not make the whole trail unreadable.
                continue
            records.append(
                AuditRecord(
                    at=payload.get("at", 0),
                    kind=payload.get("kind", "unknown"),
                    tool=payload.get("tool"),
                    allowed=payload.get("allowed"),
                    reason=payload.get("reason"),
                    session_id=payload.get("session_id"),
                    detail=payload.get("detail") or {},
                )
            )
        return records

    def tail(self, count: int = 20) -> list[AuditRecord]:
        return self.read()[-count:]
