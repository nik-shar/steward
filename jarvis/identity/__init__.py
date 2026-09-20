"""Identity: persona and standing orders as markdown, assembled into a prompt."""

from jarvis.identity.system import (
    build_system_prompt,
    personal_identity,
    read_markdown,
    shipped_identity,
)

__all__ = [
    "build_system_prompt",
    "personal_identity",
    "read_markdown",
    "shipped_identity",
]
