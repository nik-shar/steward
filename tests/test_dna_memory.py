"""Organic memory: the two rules that make it trustworthy.

Rule 1 — append-only with supersede: a correction never erases what was previously
believed, so you can always read back what Steward used to think and when it changed.

Rule 2 — ceilings enforced in code: an inferred memory cannot be written at the
confidence of a stated one, and the refusal is an exception rather than a clamp.
Clamping would be worse than refusing — the model would believe it succeeded.
"""

from __future__ import annotations

import pytest

from steward.memory.dna import (
    CONFIDENCE_CEILINGS,
    ConfidenceCeilingError,
    DnaError,
    DnaMemory,
    UnknownSourceError,
)


def test_write_and_read_back(dna: DnaMemory) -> None:
    memory = dna.write("Prefers to work in the mornings", kind="preference", source="user_stated")
    assert memory.is_current
    assert memory.confidence <= memory.ceiling
    assert dna.get(memory.id) == memory
    assert dna.count() == 1


def test_inferred_memory_cannot_claim_stated_confidence(dna: DnaMemory) -> None:
    """The ceiling is the point: refused, not silently reduced."""
    with pytest.raises(ConfidenceCeilingError) as caught:
        dna.write("Probably job hunting", source="inferred", confidence=0.9)

    assert caught.value.ceiling == CONFIDENCE_CEILINGS["inferred"]
    assert caught.value.confidence == 0.9
    # Nothing was written. A clamp would have left a row behind.
    assert dna.count() == 0


def test_refusal_message_does_not_invite_a_retry_hack(dna: DnaMemory) -> None:
    with pytest.raises(ConfidenceCeilingError) as caught:
        dna.write("Guessing", source="inferred", confidence=0.99)
    assert "do not retry the same number" in str(caught.value)


def test_default_confidence_respects_ceiling_per_source(dna: DnaMemory) -> None:
    for source, ceiling in CONFIDENCE_CEILINGS.items():
        memory = dna.write(f"something from {source}", source=source)  # type: ignore[arg-type]
        assert memory.confidence <= ceiling


def test_exactly_at_the_ceiling_is_allowed(dna: DnaMemory) -> None:
    ceiling = CONFIDENCE_CEILINGS["tool"]
    memory = dna.write("Read from a tool", source="tool", confidence=ceiling)
    assert memory.confidence == ceiling


def test_unknown_source_raises_rather_than_defaulting(dna: DnaMemory) -> None:
    with pytest.raises(UnknownSourceError):
        dna.write("mystery", source="vibes")  # type: ignore[arg-type]


def test_unknown_kind_raises(dna: DnaMemory) -> None:
    with pytest.raises(DnaError):
        dna.write("something", kind="hunch")  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_empty_text_is_refused(dna: DnaMemory, text: str) -> None:
    with pytest.raises(DnaError):
        dna.write(text)


def test_negative_confidence_is_refused(dna: DnaMemory) -> None:
    with pytest.raises(DnaError):
        dna.write("negative", confidence=-0.5)


# -- append-only with supersede ------------------------------------------------


def test_supersede_appends_and_keeps_history(dna: DnaMemory) -> None:
    original = dna.write("Works at Acme", kind="fact")
    corrected = dna.supersede(original.id, "Works at Initech", kind="fact")

    # The correction is a new row that points backwards...
    assert corrected.supersedes == original.id
    # ...and the old row is marked, never edited or deleted.
    old = dna.get(original.id)
    assert old is not None
    assert old.text == "Works at Acme"
    assert old.superseded_by == corrected.id
    assert not old.is_current

    assert dna.history(corrected.id) == [old, corrected]
    assert dna.count() == 1


def test_superseded_memories_drop_out_of_current(dna: DnaMemory) -> None:
    original = dna.write("Lives in Pune", kind="fact")
    dna.supersede(original.id, "Lives in Bengaluru", kind="fact")

    current = dna.current()
    assert [memory.text for memory in current] == ["Lives in Bengaluru"]


def test_cannot_supersede_an_already_superseded_memory(dna: DnaMemory) -> None:
    """Otherwise a correction chain forks and the 'current' row becomes ambiguous."""
    original = dna.write("First", kind="fact")
    first_fix = dna.supersede(original.id, "Second", kind="fact")

    with pytest.raises(DnaError, match="already superseded"):
        dna.supersede(original.id, "Third", kind="fact")

    assert first_fix.is_current


def test_cannot_supersede_an_unknown_id(dna: DnaMemory) -> None:
    with pytest.raises(DnaError, match="unknown memory"):
        dna.supersede("nope", "correction")


def test_supersede_inherits_kind_when_not_given(dna: DnaMemory) -> None:
    original = dna.write("Initially a goal", kind="goal")
    corrected = dna.supersede(original.id, "Revised")
    assert corrected.kind == "goal"
