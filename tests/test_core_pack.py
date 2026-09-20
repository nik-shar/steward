"""The core pack, driven through its handlers.

These capabilities stay in the main agent permanently, so they are tested directly
rather than through a model. The properties that matter:

* a write above its source's ceiling is refused **and the refusal is audited**;
* `corrects` supersedes instead of editing;
* recall never surfaces a superseded memory;
* a question with punctuation in it does not blow up FTS5;
* a cold start says so instead of inventing a profile.
"""

from __future__ import annotations

from jarvis.packs.core import get_profile, recall_memory, remember, set_profile_fact
from jarvis.tools.spec import ToolContext

# -- the cold start ------------------------------------------------------------


async def test_cold_start_admits_it_knows_nothing(ctx: ToolContext) -> None:
    outcome = await get_profile(ctx, {})

    assert not outcome.is_error
    assert "cold start" in outcome.text
    assert "assume" in outcome.text


async def test_profile_reports_facts_and_the_memory_count(ctx: ToolContext) -> None:
    await set_profile_fact(ctx, {"key": "name", "value": "Nik"})
    await remember(ctx, {"text": "Works in Bengaluru"})

    outcome = await get_profile(ctx, {})
    assert "name: Nik" in outcome.text
    assert "1 current memories" in outcome.text
    assert outcome.details["memories"] == 1


async def test_set_profile_fact_upserts_rather_than_duplicating(ctx: ToolContext) -> None:
    await set_profile_fact(ctx, {"key": "timezone", "value": "Asia/Kolkata"})
    await set_profile_fact(ctx, {"key": "timezone", "value": "Asia/Tokyo"})

    rows = ctx.store.query("SELECT value FROM profile_facts WHERE key = 'timezone'")
    assert len(rows) == 1
    assert rows[0]["value"] == "Asia/Tokyo"


async def test_set_profile_fact_requires_both_arguments(ctx: ToolContext) -> None:
    outcome = await set_profile_fact(ctx, {"key": "name"})
    assert outcome.is_error and "key" in outcome.text


# -- remembering ---------------------------------------------------------------


async def test_remember_writes_and_audits(ctx: ToolContext) -> None:
    outcome = await remember(ctx, {"text": "Prefers to work in the morning", "kind": "preference"})

    assert not outcome.is_error
    assert ctx.dna.count() == 1
    assert "memory_write" in [record.kind for record in ctx.audit.read()]


async def test_remember_refuses_above_the_ceiling_and_audits_the_refusal(
    ctx: ToolContext,
) -> None:
    """The refusal is evidence too — it shows the model tried to overstate."""
    outcome = await remember(ctx, {"text": "Probably job hunting", "source": "inferred", "confidence": 0.95})

    assert outcome.is_error
    assert "refused" in outcome.text
    assert ctx.dna.count() == 0

    refusals = [record for record in ctx.audit.read() if record.kind == "memory_refused"]
    assert len(refusals) == 1
    assert refusals[0].detail["ceiling"] == 0.6
    assert refusals[0].allowed is False


async def test_remember_corrects_by_superseding(ctx: ToolContext) -> None:
    first = await remember(ctx, {"text": "Works at Acme"})
    original_id = first.details["id"]

    second = await remember(ctx, {"text": "Works at Initech", "corrects": original_id})

    assert not second.is_error
    assert ctx.dna.count() == 1
    original = ctx.dna.get(original_id)
    assert original is not None
    assert original.superseded_by == second.details["id"]
    assert original.text == "Works at Acme"  # history is intact


async def test_remember_refuses_an_unknown_kind(ctx: ToolContext) -> None:
    outcome = await remember(ctx, {"text": "Something", "kind": "hunch"})
    assert outcome.is_error and "refused" in outcome.text


# -- recalling -----------------------------------------------------------------


async def test_recall_matches_on_content(ctx: ToolContext) -> None:
    await remember(ctx, {"text": "Training for a half marathon in November", "kind": "goal"})
    await remember(ctx, {"text": "Prefers tea over coffee", "kind": "preference"})

    outcome = await recall_memory(ctx, {"query": "marathon"})

    assert "marathon" in outcome.text
    assert "tea" not in outcome.text


async def test_recall_never_surfaces_a_superseded_memory(ctx: ToolContext) -> None:
    first = await remember(ctx, {"text": "Lives in Pune"})
    await remember(ctx, {"text": "Lives in Bengaluru", "corrects": first.details["id"]})

    outcome = await recall_memory(ctx, {"query": "lives"})

    assert "Bengaluru" in outcome.text
    assert "Pune" not in outcome.text


async def test_recall_survives_punctuation_and_operators(ctx: ToolContext) -> None:
    """Unquoted user text is FTS5 syntax; a stray quote or '-' would raise."""
    await remember(ctx, {"text": "Learning Rust this quarter"})

    outcome = await recall_memory(ctx, {"query": 'what\'s the -plan* "quoted" OR'})

    assert not outcome.is_error


async def test_recall_on_an_empty_store_says_so(ctx: ToolContext) -> None:
    outcome = await recall_memory(ctx, {"query": "anything"})
    assert not outcome.is_error
    assert "no memories match" in outcome.text.lower()


async def test_recall_clamps_an_absurd_limit(ctx: ToolContext) -> None:
    await remember(ctx, {"text": "One"})
    outcome = await recall_memory(ctx, {"query": "one", "limit": 9999})
    assert not outcome.is_error
