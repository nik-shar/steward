"""Frontends. One renderer today, the same event stream behind any other tomorrow."""

from steward.events.render import TerminalRenderer, render_audit

__all__ = ["TerminalRenderer", "render_audit"]
