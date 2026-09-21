"""The single writer of the SQLite store.

Nothing else in Steward opens a connection or runs SQL. That is the whole point —
one writer means one place an invariant can live, and one place to look when the
data is wrong.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from steward.clock import now_ms

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_VERSION = 1


class MemoryStore:
    """Owns the connection, the pragmas, and the schema."""

    def __init__(self, db_path: Path | str) -> None:
        self.path = Path(db_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        # WAL so a read (recall during a turn) never blocks the write that a
        # tool is making in the same turn.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # -- schema ------------------------------------------------------------

    def _migrate(self) -> None:
        """Apply schema.sql and stamp the version.

        schema.sql is written with CREATE IF NOT EXISTS throughout, so applying
        it repeatedly is safe. Real migrations get their own numbered steps here
        once the schema starts changing; the version row is the hook for that.
        """
        self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        if row is None or row["v"] is None:
            self._conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, now_ms()),
            )
        self._conn.commit()

    # -- access ------------------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        """Escape hatch for the memory modules in this package only."""
        return self._conn

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self._conn.execute(sql, params))

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, params).fetchone()

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
