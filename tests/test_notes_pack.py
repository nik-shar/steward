"""The notes pack, through its handlers.

Pure handler tests. The security property that makes these tools safe to expose —
path containment — gets its own file, `test_notes_containment.py`, because it is the
one thing here that could cost you real data if it broke.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.packs.notes.tools import append_note, list_notes, read_note, search_notes
from jarvis.policy.scopes import Scope

NOTE = "Daily Notes/2026-09-24.md"


async def test_append_creates_the_note_and_its_folders(vault_ctx) -> None:
    outcome = await append_note(vault_ctx, {"path": NOTE, "text": "Started the roadmap."})

    assert not outcome.is_error
    assert outcome.text == f"created {NOTE}"
    assert (Path(vault_ctx.settings.paths.vault) / NOTE).is_file()


async def test_append_never_overwrites(vault_ctx) -> None:
    """The guarantee that makes an agent safe around a vault it did not write."""
    await append_note(vault_ctx, {"path": NOTE, "text": "first thought"})
    await append_note(vault_ctx, {"path": NOTE, "text": "second thought"})

    body = (Path(vault_ctx.settings.paths.vault) / NOTE).read_text(encoding="utf-8")
    assert "first thought" in body
    assert "second thought" in body


async def test_appending_to_an_existing_note_says_appended(vault_ctx) -> None:
    await append_note(vault_ctx, {"path": NOTE, "text": "one"})
    second = await append_note(vault_ctx, {"path": NOTE, "text": "two"})

    assert second.text == f"appended to {NOTE}"
    assert second.details["created"] is False


async def test_read_round_trips(vault_ctx) -> None:
    await append_note(vault_ctx, {"path": NOTE, "text": "the plan"})

    outcome = await read_note(vault_ctx, {"path": NOTE})
    assert not outcome.is_error
    assert "the plan" in outcome.text


async def test_a_missing_note_says_it_is_missing(vault_ctx) -> None:
    outcome = await read_note(vault_ctx, {"path": "nowhere/nothing.md"})

    assert outcome.is_error
    assert "no note at" in outcome.text


async def test_list_notes_finds_nested_files(vault_ctx) -> None:
    await append_note(vault_ctx, {"path": "a.md", "text": "one"})
    await append_note(vault_ctx, {"path": "sub/b.md", "text": "two"})

    outcome = await list_notes(vault_ctx, {})

    assert not outcome.is_error
    assert "a.md" in outcome.text and "sub/b.md" in outcome.text
    assert outcome.details["count"] == 2


async def test_list_notes_can_be_limited_to_a_folder(vault_ctx) -> None:
    await append_note(vault_ctx, {"path": "a.md", "text": "one"})
    await append_note(vault_ctx, {"path": "sub/b.md", "text": "two"})

    outcome = await list_notes(vault_ctx, {"folder": "sub"})

    assert "sub/b.md" in outcome.text
    assert "a.md" not in outcome.text


async def test_list_on_an_empty_vault_says_so(vault_ctx) -> None:
    outcome = await list_notes(vault_ctx, {})

    assert not outcome.is_error
    assert "no notes" in outcome.text


async def test_search_returns_the_matching_line(vault_ctx) -> None:
    await append_note(vault_ctx, {"path": NOTE, "text": "Reading about HNSW indexes"})
    await append_note(vault_ctx, {"path": "other.md", "text": "unrelated"})

    outcome = await search_notes(vault_ctx, {"query": "hnsw"})

    assert not outcome.is_error
    assert NOTE in outcome.text
    assert "HNSW" in outcome.text
    assert "other.md" not in outcome.text


async def test_search_says_so_when_nothing_matches(vault_ctx) -> None:
    await append_note(vault_ctx, {"path": NOTE, "text": "something"})

    outcome = await search_notes(vault_ctx, {"query": "quantum chromodynamics"})

    assert not outcome.is_error
    assert "nothing in the vault mentions" in outcome.text


async def test_search_requires_a_query(vault_ctx) -> None:
    assert (await search_notes(vault_ctx, {"query": "  "})).is_error


async def test_append_requires_text(vault_ctx) -> None:
    assert (await append_note(vault_ctx, {"path": NOTE, "text": "  "})).is_error


async def test_append_is_audited(vault_ctx) -> None:
    await append_note(vault_ctx, {"path": NOTE, "text": "hello"})

    records = [record for record in vault_ctx.audit.read() if record.kind == "tool_call"]
    assert records[-1].tool == "append_note"
    assert records[-1].detail["path"] == NOTE
    assert records[-1].detail["created"] is True


# -- scopes --------------------------------------------------------------------


def test_append_is_a_write_and_needs_consent(mounted) -> None:
    assert mounted.scopes.scope_of("append_note") is Scope.WRITE
    assert mounted.scopes.requires_consent("append_note", frozenset())


def test_note_reads_never_need_consent(mounted) -> None:
    for tool in ("list_notes", "read_note", "search_notes"):
        assert mounted.scopes.scope_of(tool) is Scope.READ
        assert not mounted.scopes.requires_consent(tool, frozenset())
