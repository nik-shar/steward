"""Capability packs.

A pack is a coherent set of capabilities plus the guidelines that steer them. It is
mounted either inline on the main agent (topology A) or on its own child harness
(topology B) — the pack itself does not change, only where it runs. That is what
makes the A -> B move a rename instead of a rewrite.
"""

from __future__ import annotations

from jarvis.packs.calendar import calendar_pack
from jarvis.packs.core import core_pack
from jarvis.packs.notes import notes_pack
from jarvis.tools.registry import CapabilityPack


def default_packs() -> tuple[CapabilityPack, ...]:
    """What a normal run mounts.

    Adding a pack here is the entire act of giving Jarvis a capability — and because
    `--doctor` prints every mounted tool alongside its scope, that act is visible
    rather than implicit.
    """
    return (core_pack(), calendar_pack(), notes_pack())


__all__ = ["calendar_pack", "core_pack", "default_packs", "notes_pack"]
