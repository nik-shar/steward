"""Path containment: the property that makes the notes pack safe to expose.

Paths in these tools are **model-supplied**, which makes them untrusted input. If any
of these attacks succeeds, a model can read your SSH keys and write over your
dotfiles — and a consent prompt reading "allow append_note?" would have asked the
wrong question entirely.

Every test here asserts on the *resolved* path, after symlinks, because that is what
the resolver checks. A check performed on the string before resolution is no check at
all.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.packs.notes.tools import append_note, list_notes, read_note, search_notes


def _vault(ctx) -> Path:
    root = Path(ctx.settings.paths.vault)
    root.mkdir(parents=True, exist_ok=True)
    return root


async def test_parent_traversal_is_refused(vault_ctx) -> None:
    for evil in ("../secrets.md", "sub/../../secrets.md", "/etc/passwd.md", "~/notes.md"):
        outcome = await read_note(vault_ctx, {"path": evil})
        assert outcome.is_error, f"{evil!r} was allowed through"


async def test_traversal_cannot_be_used_to_write_either(vault_ctx) -> None:
    root = _vault(vault_ctx)

    outcome = await append_note(vault_ctx, {"path": "../escape.md", "text": "nope"})

    assert outcome.is_error
    assert not (root.parent / "escape.md").exists()


async def test_a_listing_cannot_escape_the_vault(vault_ctx) -> None:
    outcome = await list_notes(vault_ctx, {"folder": "../.."})
    assert outcome.is_error


async def test_a_search_cannot_escape_the_vault(vault_ctx) -> None:
    outcome = await search_notes(vault_ctx, {"query": "password", "folder": "../.."})
    assert outcome.is_error


async def test_a_symlink_to_a_file_outside_the_vault_is_refused(vault_ctx, tmp_path) -> None:
    root = _vault(vault_ctx)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("private", encoding="utf-8")
    (root / "link.md").symlink_to(outside / "secret.md")

    outcome = await read_note(vault_ctx, {"path": "link.md"})

    assert outcome.is_error
    assert "outside the vault" in outcome.text


async def test_a_symlinked_directory_cannot_be_written_through(vault_ctx, tmp_path) -> None:
    """The strongest assertion available: nothing landed outside the vault."""
    root = _vault(vault_ctx)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "out").symlink_to(elsewhere, target_is_directory=True)

    outcome = await append_note(vault_ctx, {"path": "out/evil.md", "text": "nope"})

    assert outcome.is_error
    assert not (elsewhere / "evil.md").exists()


async def test_a_symlinked_directory_hides_nothing_from_a_search(vault_ctx, tmp_path) -> None:
    root = _vault(vault_ctx)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "leak.md").write_text("secret phrase", encoding="utf-8")
    (root / "out").symlink_to(elsewhere, target_is_directory=True)

    outcome = await search_notes(vault_ctx, {"query": "secret phrase"})

    assert "leak.md" not in outcome.text


async def test_non_markdown_is_refused(vault_ctx) -> None:
    outcome = await read_note(vault_ctx, {"path": "notes.txt"})

    assert outcome.is_error
    assert "markdown" in outcome.text


async def test_a_path_with_no_name_is_refused(vault_ctx) -> None:
    assert (await read_note(vault_ctx, {"path": "   "})).is_error


async def test_the_vault_root_itself_is_not_a_note(vault_ctx) -> None:
    """An empty path must not resolve to the vault directory and be read as a note."""
    assert (await read_note(vault_ctx, {"path": "."})).is_error
