"""Assembling the system prompt from markdown.

Identity is markdown on purpose. You edit `persona.md` or `guidelines.md`, and the
next turn uses it — no code change, and no prompt buried in a Python string where
you will never find it again.

Two layers, and the split matters:

1. **Shipped** — `persona.md` and `guidelines.md` live in this package, are
   versioned, and are safe to put in a public repo.
2. **Personal** — anything in ``JARVIS_HOME/identity/*.md`` is private, never
   committed, and appended after the shipped layer. This is where your own
   context and your own overrides go.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

SHIPPED_DIR = Path(__file__).resolve().parent

PERSONA_FILE = "persona.md"
GUIDELINES_FILE = "guidelines.md"


def read_markdown(path: Path) -> str:
    """Read a markdown file, or return empty for a missing one.

    Missing personal identity is normal, not an error — Jarvis should run from a
    bare checkout with nothing but a provider key.
    """
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def shipped_identity() -> tuple[str, str]:
    return read_markdown(SHIPPED_DIR / PERSONA_FILE), read_markdown(SHIPPED_DIR / GUIDELINES_FILE)


def personal_identity(identity_dir: Path) -> str:
    """Every ``*.md`` in the private identity directory, sorted by filename.

    Sorted so the prompt is stable between turns: an unstable system prompt
    defeats provider prompt caching and makes behaviour look non-deterministic.
    """
    if not identity_dir.is_dir():
        return ""
    sections: list[str] = []
    for path in sorted(identity_dir.glob("*.md")):
        text = read_markdown(path)
        if text:
            sections.append(f"## {path.stem}\n\n{text}")
    return "\n\n".join(sections)


def _now_line(moment: datetime | None = None) -> str:
    """The current time, stated explicitly.

    Without this the model guesses the date, and a mentor that does not know what
    day it is will happily schedule something for last Tuesday.
    """
    stamp = moment or datetime.now().astimezone()
    return stamp.strftime("%A, %d %B %Y, %H:%M %Z (local)")


def build_system_prompt(
    *,
    pack_guidelines: str = "",
    personal: str = "",
    mounted_packs: tuple[str, ...] = (),
    moment: datetime | None = None,
) -> str:
    """Compose the full system prompt, in a fixed section order."""
    persona, guidelines = shipped_identity()
    blocks: list[str] = []

    if persona:
        blocks.append(persona)
    if guidelines:
        blocks.append(guidelines)
    if personal:
        blocks.append(f"# What you know about them\n\n{personal}")
    if pack_guidelines.strip():
        blocks.append(f"# Active capabilities\n\n{pack_guidelines.strip()}")
    if mounted_packs:
        blocks.append(f"# Mounted packs\n\n{', '.join(mounted_packs)}")
    blocks.append(f"# Right now\n\n{_now_line(moment)}")

    return "\n\n---\n\n".join(blocks)
