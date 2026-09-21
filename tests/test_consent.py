"""The consent gate.

This is the only thing standing between a model's plan and your real calendar, so
its failure modes get more tests than its happy path. In particular it must **fail
closed**: an unknown tool, or a run with no way to ask, must both refuse.
"""

from __future__ import annotations

from pathlib import Path

from steward.policy.audit import AuditLog
from steward.policy.consent import ConsentDecision, ConsentGate, describe_call, redact
from steward.policy.scopes import Scope, ScopeRegistry


def _registry() -> ScopeRegistry:
    registry = ScopeRegistry()
    registry.register("recall_memory", "core", Scope.READ)
    registry.register("remember", "core", Scope.INTERNAL)
    registry.register("place_block", "calendar", Scope.WRITE)
    return registry


async def _yes(question: str, arguments: object) -> bool:
    return True


async def _no(question: str, arguments: object) -> bool:
    return False


def _gate(path: Path, *, allowlist: frozenset[str] = frozenset(), prompter=None) -> ConsentGate:
    return ConsentGate(
        scopes=_registry(),
        allowlist=allowlist,
        audit=AuditLog(path),
        prompter=prompter,
        session_id="s1",
    )


# -- what each scope means -----------------------------------------------------


async def test_read_is_allowed_without_asking(tmp_path: Path) -> None:
    decision = await _gate(tmp_path / "a.jsonl").check("recall_memory", {})
    assert decision.allowed and decision.silent


async def test_internal_write_is_allowed_but_audited(tmp_path: Path) -> None:
    """Memory writes must not nag; they are still recorded."""
    path = tmp_path / "a.jsonl"
    decision = await _gate(path).check("remember", {"text": "likes tea"})

    assert decision.allowed and decision.silent
    assert [record.kind for record in AuditLog(path).read()] == ["consent"]


async def test_external_write_without_a_prompter_is_denied(tmp_path: Path) -> None:
    """A one-shot `steward "block 2 hours"` has no TTY. It must refuse, not proceed."""
    decision = await _gate(tmp_path / "a.jsonl").check("place_block", {"slot": 28})

    assert not decision.allowed
    assert decision.reason and "no way to ask" in decision.reason


async def test_external_write_denied_when_declined(tmp_path: Path) -> None:
    decision = await _gate(tmp_path / "a.jsonl", prompter=_no).check("place_block", {"slot": 28})
    assert not decision.allowed
    assert decision.reason == "declined by you"


async def test_external_write_allowed_when_approved(tmp_path: Path) -> None:
    decision = await _gate(tmp_path / "a.jsonl", prompter=_yes).check("place_block", {"slot": 28})
    assert decision.allowed and not decision.silent


async def test_allowlisted_write_skips_the_prompt(tmp_path: Path) -> None:
    gate = _gate(tmp_path / "a.jsonl", allowlist=frozenset({"place_block"}), prompter=_no)
    decision = await gate.check("place_block", {"slot": 28})
    assert decision.allowed and decision.reason == "allowlisted"


# -- fail closed ---------------------------------------------------------------


async def test_unknown_tool_is_denied(tmp_path: Path) -> None:
    """An unregistered tool is a bug, and a bug must not be able to write."""
    decision = await _gate(tmp_path / "a.jsonl").check("unregistered_tool", {})
    assert not decision.allowed
    assert decision.reason and "no declared capability scope" in decision.reason


async def test_unknown_tool_is_denied_even_when_allowlisted(tmp_path: Path) -> None:
    """The allowlist must not become a way around the registry."""
    gate = _gate(tmp_path / "a.jsonl", allowlist=frozenset({"unregistered_tool"}))
    assert not (await gate.check("unregistered_tool", {})).allowed


# -- the audit trail -----------------------------------------------------------


async def test_every_verdict_is_recorded_including_allows(tmp_path: Path) -> None:
    """A log of denials alone cannot tell you what Steward did."""
    path = tmp_path / "a.jsonl"
    gate = _gate(path, prompter=_yes)
    await gate.check("recall_memory", {})
    await gate.check("place_block", {"slot": 28})
    await gate.check("unregistered_tool", {})

    records = AuditLog(path).read()
    assert [record.allowed for record in records] == [True, True, False]
    assert [record.tool for record in records] == ["recall_memory", "place_block", "unregistered_tool"]


def test_audit_redacts_secret_shaped_arguments() -> None:
    """The audit log outlives the turn; a key written once is leaked forever."""
    safe = redact({"api_key": "sk-live-123", "token": "abc", "slot": 28, "nested": {"password": "x"}})

    assert safe["api_key"] == "***redacted***"
    assert safe["token"] == "***redacted***"
    assert safe["nested"] == {"password": "***redacted***"}
    assert safe["slot"] == 28


async def test_audit_never_stores_a_raw_secret(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    await _gate(path).check("remember", {"text": "ok", "api_key": "sk-live-supersecret"})

    assert "supersecret" not in path.read_text(encoding="utf-8")


def test_describe_call_is_answerable() -> None:
    assert describe_call("place_block", {"slot": 28, "label": "deep work"}) == (
        "Allow place_block(label='deep work', slot=28)?"
    )
    assert describe_call("place_block", {}) == "Allow place_block?"


def test_decision_constructors() -> None:
    assert ConsentDecision.allow().allowed
    denied = ConsentDecision.deny("nope")
    assert not denied.allowed and denied.reason == "nope"
