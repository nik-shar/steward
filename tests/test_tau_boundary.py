"""The Tau boundary is an architectural invariant, so it gets a test.

The Tau-facing edge is three modules, one per job: `brain.py` drives the harness,
`session.py` persists the transcript, `events/render.py` displays the stream.
Nothing in `memory/`, `policy/`, `identity/`, `tools/` or `packs/` may import Tau —
that is what keeps a pack unit-testable with no provider and no token, and what
makes replacing the harness a one-file change instead of an archaeological dig.

This walks the source with `ast` rather than importing anything, so it cannot be
fooled by an import that only happens at runtime.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "jarvis"

#: The only modules allowed to reach Tau. Keep this list short and deliberate —
#: adding to it should feel like a decision, because it is one.
TAU_FACING = {"brain.py", "session.py", "events/render.py"}

TAU_ROOTS = ("tau_agent", "tau_ai", "tau_coding")


def _module_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _tau_imports(path: Path) -> list[str]:
    """Every Tau module this file imports, by fully-qualified name."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return [name for name in found if name.split(".")[0] in TAU_ROOTS]


def test_package_has_sources() -> None:
    """Guard against the walker silently finding nothing and passing vacuously."""
    assert len(_module_files()) > 10


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: str(p.relative_to(PACKAGE)))
def test_only_the_edge_imports_tau(path: Path) -> None:
    relative = path.relative_to(PACKAGE).as_posix()
    if relative in TAU_FACING:
        return
    offenders = _tau_imports(path)
    assert not offenders, (
        f"{relative} imports {offenders}. Tau must stay confined to {sorted(TAU_FACING)} — "
        "move the logic behind a jarvis type instead, or extend the adapter in brain.py."
    )


def test_the_edge_files_actually_import_tau() -> None:
    """The other direction: if these stop importing Tau, the boundary moved."""
    for relative in sorted(TAU_FACING):
        assert _tau_imports(PACKAGE / relative), f"{relative} no longer imports Tau; update TAU_FACING in this test."
