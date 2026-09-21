"""Time helpers, in one place so every store agrees on what "now" is."""

from __future__ import annotations

from datetime import UTC, datetime
from time import time


def now_ms() -> int:
    """Current Unix time in milliseconds — the only timestamp format stored."""
    return int(time() * 1000)


def to_iso(timestamp_ms: int) -> str:
    """Render a stored timestamp for humans."""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).astimezone().isoformat(timespec="seconds")


def age_days(timestamp_ms: int, *, now: int | None = None) -> float:
    """Age in days, used for recency decay in recall."""
    current = now if now is not None else now_ms()
    return max(0.0, (current - timestamp_ms) / 86_400_000)
