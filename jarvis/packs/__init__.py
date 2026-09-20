"""Capability packs.

A pack is a coherent set of capabilities with the guidelines that steer them. It is
mounted either inline on the main agent (topology A) or on its own child harness
(topology B) — the pack itself does not change, only where it runs. That is what
makes the A -> B move a rename instead of a rewrite.
"""

from jarvis.packs.core import core_pack, default_packs

__all__ = ["core_pack", "default_packs"]
