"""The notes pack's four handlers: three reads and one append.

The append is the point. Notes are where thinking lives, so a mentor that can add to
them but never overwrite them is useful and safe at the same time — `append_note`
opens in append mode and there is no code path here that truncates a file.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis.packs.notes.paths import (
    MAX_LIST,
    MAX_READ_BYTES,
    MAX_SEARCH_HITS,
    SUFFIX,
    VaultError,
    as_text,
    relative_to_vault,
    resolve_folder,
    resolve_note,
)
from jarvis.tools.spec import ToolContext, ToolOutcome


def _markdown_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob(f"*{SUFFIX}") if path.is_file())


async def list_notes(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    """List the notes in a folder, newest first."""
    try:
        folder = resolve_folder(ctx, as_text(arguments.get("folder")))
    except VaultError as exc:
        return ToolOutcome.error(str(exc))

    if not folder.is_dir():
        return ToolOutcome.ok(f"no such folder in the vault: {relative_to_vault(ctx, folder)}")

    files = _markdown_files(folder)
    if not files:
        return ToolOutcome.ok(f"no notes in {relative_to_vault(ctx, folder) or '.'} yet", details={"count": 0})

    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    shown = files[:MAX_LIST]
    lines = [f"{relative_to_vault(ctx, path)}" for path in shown]
    if len(files) > len(shown):
        lines.append(f"... and {len(files) - len(shown)} more")
    return ToolOutcome.ok("\n".join(lines), details={"count": len(files)})


async def read_note(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    """Read one note."""
    try:
        path = resolve_note(ctx, as_text(arguments.get("path")))
    except VaultError as exc:
        return ToolOutcome.error(str(exc))

    if not path.is_file():
        return ToolOutcome.error(f"no note at {relative_to_vault(ctx, path)}")

    size = path.stat().st_size
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = size > MAX_READ_BYTES
    body = text[:MAX_READ_BYTES] if truncated else text
    if truncated:
        body += f"\n\n[truncated: {size} bytes total]"
    return ToolOutcome.ok(body, details={"path": relative_to_vault(ctx, path), "bytes": size})


async def search_notes(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    """Find notes containing a phrase, with the matching line."""
    needle = as_text(arguments.get("query")).lower()
    if not needle:
        return ToolOutcome.error("a search query is required")
    try:
        folder = resolve_folder(ctx, as_text(arguments.get("folder")))
    except VaultError as exc:
        return ToolOutcome.error(str(exc))

    hits: list[str] = []
    scanned = 0
    for path in _markdown_files(folder):
        scanned += 1
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            if needle in line.lower():
                hits.append(f"{relative_to_vault(ctx, path)}: {line.strip()[:160]}")
                break
        if len(hits) >= MAX_SEARCH_HITS:
            break

    if not hits:
        return ToolOutcome.ok(f"nothing in the vault mentions {needle!r}", details={"scanned": scanned})
    return ToolOutcome.ok("\n".join(hits), details={"hits": len(hits), "scanned": scanned})


async def append_note(ctx: ToolContext, arguments: Mapping[str, Any]) -> ToolOutcome:
    """Append to a note, creating it and its folder if needed. Never overwrites."""
    try:
        path = resolve_note(ctx, as_text(arguments.get("path")))
    except VaultError as exc:
        return ToolOutcome.error(str(exc))

    text = as_text(arguments.get("text"))
    if not text:
        return ToolOutcome.error("nothing to append")

    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    stamp = datetime.now().strftime("%H:%M")
    # A leading separator only when there is already content, so a fresh note does
    # not open with a stray blank line.
    separator = "\n" if existed and path.stat().st_size else ""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{separator}## {stamp}\n\n{text}\n")

    relative = relative_to_vault(ctx, path)
    ctx.audit.record(
        "tool_call",
        tool="append_note",
        allowed=True,
        session_id=ctx.session_id,
        detail={"path": relative, "created": not existed, "chars": len(text)},
    )
    return ToolOutcome.ok(
        f"{'created' if not existed else 'appended to'} {relative}",
        details={"path": relative, "created": not existed},
    )
