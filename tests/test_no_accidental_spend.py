"""Guards that keep the test suite from spending model budget.

The suite is offline by design: every end-to-end test injects Tau's `FakeProvider`, so
`uv run pytest` makes no network call and costs nothing. That is worth defending
structurally rather than with a note, because the failure mode is invisible — a test
that quietly bills you looks exactly like a test that passes.

These walk the sources with `ast`, so they cannot be fooled by an import that only
happens at runtime.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_FILES = sorted((ROOT / "tests").glob("*.py"))

#: Anything whose presence means a test *could* build a real model client.
#: Constructing one of these is free (no request until a stream is consumed), but it
#: is the door to a bill, so it stays shut.
LIVE_PROVIDER_TYPES = frozenset(
    {
        "OpenAICompatibleProvider",
        "AnthropicProvider",
        "GoogleGenerativeAIProvider",
        "OpenAICodexProvider",
        "MistralConversationsProvider",
    }
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            names |= {alias.name.split(".")[-1] for alias in node.names}
    return names


def _decorator_names(node: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            names.add(ast.unparse(target))
        elif isinstance(target, ast.Name):
            names.add(target.id)
    return names


def test_there_are_test_files() -> None:
    assert len(TEST_FILES) > 10


@pytest.mark.parametrize("path", TEST_FILES, ids=lambda item: item.name)
def test_no_test_imports_a_real_provider(path: Path) -> None:
    offenders = _imported_names(_tree(path)) & LIVE_PROVIDER_TYPES
    assert not offenders, (
        f"{path.name} imports {sorted(offenders)}. A test that can reach a real provider "
        "spends budget the moment it fails; inject FakeProvider instead."
    )


@pytest.mark.parametrize("path", TEST_FILES, ids=lambda item: item.name)
def test_every_steward_is_handed_an_explicit_provider(path: Path) -> None:
    """`Steward(...)` without `provider=` builds a real client from `.env`.

    Passing one is what makes the end-to-end tests offline, so it is checked rather
    than trusted.
    """
    missing: list[int] = []
    for node in ast.walk(_tree(path)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Steward"
            and "provider" not in {keyword.arg for keyword in node.keywords if keyword.arg}
        ):
            missing.append(node.lineno)

    assert not missing, (
        f"{path.name} builds Steward without `provider=` at line(s) {missing}, which "
        "would reach a real provider through .env."
    )


@pytest.mark.parametrize("path", TEST_FILES, ids=lambda item: item.name)
def test_a_live_test_is_also_guarded(path: Path) -> None:
    """`@pytest.mark.live` alone is only a label — it still runs under plain pytest.

    Any function carrying it must also carry `requires_live`, which skips unless
    `STEWARD_LIVE=1` was set on purpose.
    """
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.FunctionDef):
            continue
        decorators = _decorator_names(node)
        if "pytest.mark.live" in decorators:
            assert "requires_live" in decorators, (
                f"{path.name}::{node.name} is marked live without `requires_live`, so "
                "`uv run pytest` would run it and spend budget."
            )
