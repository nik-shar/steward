"""The notes pack: reading and appending markdown in the vault.

Four tools over plain files — the cheapest capability in the system, and the one
that makes everything else reachable, because notes are where thinking lives.

**The security property is path containment.** A model supplies these paths, so a
path is a piece of untrusted input: `../../.ssh/id_rsa` must not be readable just
because the model asked nicely. Every path is resolved and checked against the vault
root, and anything that escapes is refused before a file is opened.
`tests/test_notes_pack.py` attacks this directly.

Appending is `Scope.WRITE` (gated). Listing, reading and searching are reads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from steward.tools.spec import ToolContext

#: Markdown only, and nothing enormous. A note is a note.
SUFFIX = ".md"
MAX_READ_BYTES = 256 * 1024
MAX_SEARCH_HITS = 40
MAX_LIST = 200

GUIDELINES = """\
- Paths are relative to the vault root, for example `Daily Notes/2026-09-20.md`.
- `append_note` is the only tool that writes, and the person is asked first. It
  appends — it never overwrites, so nothing you write can destroy what is there.
- Prefer appending a dated daily note over inventing new folder structures.
- Do not dump a whole note back at them. Quote the part that answers the question.
"""


class VaultError(RuntimeError):
    """Raised when a path is not a usable note inside the vault."""


def vault_root(ctx: ToolContext) -> Path:
    root = Path(str(ctx.settings.paths.vault)).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _contained(ctx: ToolContext, raw: str, *, relative_only: str) -> Path:
    """Resolve `raw` inside the vault, or refuse.

    The check is on the *resolved* path, after symlinks, so neither `..` nor a
    symlink planted inside the vault can point outside it.
    """
    text = str(raw).strip()
    if not text:
        raise VaultError(f"{relative_only} is required")
    if text.startswith(("/", "~")) or ":" in text:
        raise VaultError(f"{text!r} must be a relative path inside the vault")

    root = vault_root(ctx)
    candidate = (root / text).resolve()
    if candidate != root and root not in candidate.parents:
        raise VaultError(f"{text!r} resolves outside the vault, so it is refused")
    return candidate


def resolve_note(ctx: ToolContext, relative: str) -> Path:
    """Resolve a markdown note inside the vault, or refuse."""
    candidate = _contained(ctx, relative, relative_only="a note path")
    if candidate.suffix.lower() != SUFFIX:
        raise VaultError(f"notes are markdown; {relative!r} needs a {SUFFIX} extension")
    return candidate


def resolve_folder(ctx: ToolContext, relative: str) -> Path:
    """Resolve a folder inside the vault; empty means the vault root."""
    if not str(relative).strip():
        return vault_root(ctx)
    return _contained(ctx, relative, relative_only="a folder path")


def relative_to_vault(ctx: ToolContext, path: Path) -> str:
    return path.relative_to(vault_root(ctx)).as_posix()


def as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
