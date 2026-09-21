"""The notes pack: markdown in the vault.

Reading is cheap and safe; `append_note` is the only write and it is gated. The pack
exists so the steward can answer "what did I write about X", and so a thought raised
in conversation lands in the vault instead of evaporating — which is most of what
makes a steward worth talking to twice.
"""

from __future__ import annotations

from steward.packs.notes.tools import append_note, list_notes, read_note, search_notes
from steward.policy.scopes import Scope
from steward.tools.registry import CapabilityPack
from steward.tools.spec import ToolSpec

GUIDELINES = """\
- Paths are relative to the vault root, for example `Daily Notes/2026-09-20.md`.
  Absolute paths, `..` and symlinks out of the vault are refused.
- `append_note` is the only tool that writes, and the person is asked first. It
  appends — there is no path here that can overwrite or truncate a note.
- Prefer appending to a dated daily note over inventing new folder structures.
- Do not dump a whole note back at them. Quote the line that answers the question.
"""

_PATH = {"type": "string", "description": "Path relative to the vault root, ending in .md"}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_notes",
        label="List notes",
        description="List the notes in a vault folder, newest first.",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "Folder relative to the vault root. Default: everything."}
            },
            "additionalProperties": False,
        },
        handler=list_notes,
        scope=Scope.READ,
        pack="notes",
    ),
    ToolSpec(
        name="read_note",
        label="Read a note",
        description="Read one note from the vault.",
        parameters={
            "type": "object",
            "properties": {"path": _PATH},
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=read_note,
        scope=Scope.READ,
        pack="notes",
    ),
    ToolSpec(
        name="search_notes",
        label="Search notes",
        description=(
            "Find vault notes containing a phrase, returning the matching line from each. "
            "Use this before claiming the vault does not mention something."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Phrase to look for, case-insensitive."},
                "folder": {"type": "string", "description": "Limit the search to a folder."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=search_notes,
        scope=Scope.READ,
        pack="notes",
    ),
    ToolSpec(
        name="append_note",
        label="Append to a note",
        description=(
            "Append a timestamped section to a note, creating the note and its folders if "
            "they do not exist. Appends only — it never overwrites. This writes to the real "
            "vault and the person is asked first."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": _PATH,
                "text": {"type": "string", "description": "Markdown to append."},
            },
            "required": ["path", "text"],
            "additionalProperties": False,
        },
        handler=append_note,
        scope=Scope.WRITE,
        pack="notes",
        guidelines=("Append the smallest useful thing. A note is not a transcript.",),
    ),
)


def notes_pack() -> CapabilityPack:
    """The vault as a mountable pack."""
    return CapabilityPack(
        name="notes",
        summary="Read, search and append markdown in the vault.",
        tools=TOOLS,
        guidelines=GUIDELINES,
    )
