"""Session persistence: one append-only JSONL transcript per session.

The caller owns storage. Tau's harness takes messages in and hands new messages
back and never touches a file itself — which is the right boundary, because where a
transcript lives is a product decision, not a harness decision.

The on-disk format is Tau's own ``SessionEntry`` JSONL, so a transcript written here
stays readable by Tau's session tooling and vice versa.

This is one of the three Tau-facing modules: drive (`brain.py`), persist (here),
display (`events/render.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from tau_agent import AgentMessage, MessageEntry
from tau_agent.session import SessionJsonlError, entry_from_json_line, entry_to_json_line


def new_session_id() -> str:
    return uuid4().hex


def session_path(root: Path | str, session_id: str) -> Path:
    return Path(root).expanduser() / f"{session_id}.jsonl"


def load_messages(path: Path) -> list[AgentMessage]:
    """Prior transcript messages, in order.

    A line that will not decode is skipped rather than fatal. A process killed
    mid-write leaves a torn tail, and losing an entire conversation to a
    half-written final line would be the wrong trade.
    """
    if not path.is_file():
        return []
    messages: list[AgentMessage] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            entry = entry_from_json_line(raw)
        except SessionJsonlError:
            continue
        if isinstance(entry, MessageEntry):
            messages.append(entry.message)
    return messages


def append_messages(path: Path, messages: Sequence[AgentMessage]) -> int:
    """Append messages as ``MessageEntry`` records. Returns how many were written."""
    if not messages:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    # `entry_to_json_line` already terminates the line — do not add another.
    with path.open("a", encoding="utf-8") as handle:
        for message in messages:
            handle.write(entry_to_json_line(MessageEntry(message=message)))
            written += 1
        handle.flush()
    return written


def latest_session(root: Path | str) -> str | None:
    """The most recently modified session id, for resuming."""
    directory = Path(root).expanduser()
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
    return files[0].stem if files else None
