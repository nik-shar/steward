"""The Tau boundary: driving the agent loop.

Three modules are allowed to touch Tau, one per job: this one **drives** it,
`session.py` **persists** its transcript, and `events/render.py` **displays** its
stream. Everything above them speaks Steward's own types — so a Tau upgrade, a
breaking change, or a decision to own the loop lands in these three files and
nowhere else. `tests/test_tau_boundary.py` enforces that.

What this module translates:

* ``ProviderSettings`` -> ``OpenAICompatibleProvider`` (any OpenAI-compatible URL)
* ``ToolSpec`` -> ``AgentTool`` (schema, async executor, prompt guidance)
* ``ConsentGate`` -> the ``before_tool_call`` hook, **with inverted polarity**
* ``SubagentSpec`` -> a child ``AgentHarness`` (the delegation seam)
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from tau_agent import (
    AgentEndEvent,
    AgentEvent,
    AgentHarness,
    AgentHarnessConfig,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    MessageEndEvent,
    TextContent,
    ToolCall,
)
from tau_agent.loop import BeforeToolCall
from tau_agent.provider import ModelProvider
from tau_ai import OpenAICompatibleConfig, OpenAICompatibleProvider

from steward.clock import now_ms
from steward.config import Settings
from steward.identity.system import build_system_prompt, personal_identity
from steward.memory.dna import DnaMemory
from steward.memory.store import MemoryStore
from steward.packs import default_packs
from steward.policy.audit import AuditLog
from steward.policy.consent import ConsentGate, Prompter
from steward.session import append_messages, load_messages, new_session_id, session_path
from steward.tools.registry import CapabilityPack, Mounted, mount
from steward.tools.spec import (
    SubagentRunner,
    SubagentSpec,
    ToolContext,
    ToolOutcome,
    ToolSpec,
)


class BrainError(RuntimeError):
    """Raised when Steward cannot be assembled coherently."""


class ToolFailure(RuntimeError):
    """A tool's own structured failure, surfaced to the model as an error.

    Raised rather than returned so the harness marks the result ``is_error``. The
    model has to be able to distinguish "this failed" from "this succeeded and here
    is the answer" — otherwise it reports a failure as a finding.
    """


def build_provider(settings: Settings) -> OpenAICompatibleProvider:
    """Build the model provider from plain settings.

    ``provider_name`` lands in the transcript and the audit trail, so a response
    can be traced back to the endpoint that served it.
    """
    return OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            api_key=settings.provider.api_key,
            base_url=settings.provider.base_url,
            provider_name=settings.provider.provider_name,
        )
    )


def to_tau_tool(spec: ToolSpec, ctx: ToolContext) -> AgentTool:
    """Adapt one ``ToolSpec`` into the harness's tool type.

    Two conversions carry weight:

    * a tool that raises is audited and then re-raised, because Tau converts a
      raised exception into an ``is_error`` result;
    * ``prompt_guidelines`` ride along with the tool, so a capability carries its
      own instructions instead of every pack's landing in one system prompt.
    """

    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, Any],
        signal: Any = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        try:
            outcome = await spec.handler(ctx, arguments)
        except Exception as exc:
            ctx.audit.record(
                "error",
                tool=spec.name,
                allowed=False,
                reason=f"{type(exc).__name__}: {exc}",
                session_id=ctx.session_id,
            )
            raise
        if outcome.is_error:
            raise ToolFailure(outcome.text)
        return AgentToolResult(
            content=[TextContent(text=outcome.text)],
            details=outcome.details,
            terminate=True if outcome.terminate else None,
        )

    return AgentTool(
        name=spec.name,
        label=spec.label,
        description=spec.description,
        parameters=dict(spec.parameters),
        execute_fn=execute,
        prompt_guidelines=spec.guidelines,
        execution_mode=spec.execution,
    )


def build_consent_hook(gate: ConsentGate) -> BeforeToolCall:
    """Adapt ``ConsentGate`` onto Tau's ``before_tool_call`` hook.

    Tau's hook returns ``(blocked, reason)`` — **True means block**. Steward's gate
    returns an ``allowed`` flag. Inverting those two is the single most dangerous
    mistake available in this codebase, so the conversion is written out
    explicitly rather than folded into one clever expression.
    """

    async def before_tool_call(call: ToolCall) -> tuple[bool, str | None]:
        decision = await gate.check(call.name, call.arguments)
        if decision.allowed:
            return (False, None)
        return (True, decision.reason or f"{call.name} was blocked")

    return before_tool_call


class Steward:
    """One runnable Steward: settings, memory, policy, packs, and the harness.

    Stateful across turns by design — the harness holds the conversation, so an
    interactive session is just a loop of `ask()` calls on one instance.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        packs: Sequence[CapabilityPack] | None = None,
        prompter: Prompter | None = None,
        session_id: str | None = None,
        resume: bool = True,
        allow_all_writes: bool = False,
        provider: ModelProvider | None = None,
    ) -> None:
        self.settings = settings
        self.session_id = session_id or new_session_id()
        self._store = MemoryStore(settings.paths.db)
        self._dna = DnaMemory(self._store)
        self._audit = AuditLog(settings.paths.audit)
        self._mounted = mount(packs if packs is not None else default_packs())

        # Startup invariant, made loud: an unscoped tool would fail closed at
        # runtime — safe, but silent and awkward to debug.
        offered = [tool.name for tool in self._mounted.tools]
        missing = self._mounted.scopes.missing_scope(offered)
        if missing:
            raise BrainError(f"tools offered with no declared capability scope: {missing}")

        if allow_all_writes:
            allowlist = frozenset(capability.tool for capability in self._mounted.scopes.writes())
            # Explicit and recorded. Never a config default, never implicit.
            self._audit.record(
                "consent",
                allowed=True,
                reason="--yes: every write pre-approved for this run",
                session_id=self.session_id,
                detail={"tools": sorted(allowlist)},
            )
        else:
            allowlist = settings.consent_allowlist
        self._allowlist = allowlist

        self._gate = ConsentGate(
            scopes=self._mounted.scopes,
            allowlist=allowlist,
            audit=self._audit,
            prompter=prompter,
            session_id=self.session_id,
        )

        # Built before the context, because the runner closes over `self` and the
        # context is frozen once constructed.
        runner = self._make_subagent_runner()
        self._ctx = ToolContext(
            settings=settings,
            store=self._store,
            dna=self._dna,
            audit=self._audit,
            scopes=self._mounted.scopes,
            run_subagent=runner,
            session_id=self.session_id,
        )

        self._provider = provider or build_provider(settings)
        self.system_prompt = build_system_prompt(
            pack_guidelines=self._mounted.guidelines,
            personal=personal_identity(settings.paths.identity),
            mounted_packs=self._mounted.packs,
        )
        self._session_path = session_path(settings.paths.sessions, self.session_id)
        history: list[AgentMessage] = load_messages(self._session_path) if resume else []

        self._harness = AgentHarness(
            AgentHarnessConfig(
                provider=self._provider,
                model=settings.provider.model,
                system=self.system_prompt,
                tools=[to_tau_tool(spec, self._ctx) for spec in self._mounted.tools],
                max_turns=settings.max_turns,
                session_id=self.session_id,
                before_tool_call=build_consent_hook(self._gate),
            ),
            messages=history,
        )

    @classmethod
    def from_env(cls, **kwargs: Any) -> Steward:
        """Build from the environment, which is how the CLI starts."""
        return cls(Settings.load(), **kwargs)

    # -- accessors ---------------------------------------------------------

    @property
    def harness(self) -> AgentHarness:
        return self._harness

    @property
    def mounted(self) -> Mounted:
        return self._mounted

    @property
    def audit(self) -> AuditLog:
        return self._audit

    @property
    def store(self) -> MemoryStore:
        return self._store

    @property
    def dna(self) -> DnaMemory:
        return self._dna

    @property
    def session_file(self) -> Path:
        return self._session_path

    # -- running -----------------------------------------------------------

    async def ask(self, text: str) -> AsyncIterator[AgentEvent]:
        """Run one turn, yielding events. The turn is persisted as it completes."""
        async for event in self._harness.prompt(text):
            if isinstance(event, AgentEndEvent):
                append_messages(self._session_path, event.messages)
                self._record_turn(event.messages)
            yield event

    def _record_turn(self, messages: Sequence[AgentMessage]) -> None:
        """Append the turn to episodic memory.

        Cheap, and it is what makes "what were we doing last week" answerable
        without an LLM summarising anything.
        """
        final = next(
            (
                message
                for message in reversed(messages)
                if isinstance(message, AssistantMessage) and message.text.strip()
            ),
            None,
        )
        if final is None:
            return
        self._store.execute(
            "INSERT INTO episodic_events (id, at, kind, summary) VALUES (?, ?, ?, ?)",
            (uuid4().hex, now_ms(), "turn", final.text.strip()[:280]),
        )
        self._store.commit()

    # -- delegation --------------------------------------------------------

    def _make_subagent_runner(self) -> SubagentRunner:
        """Build the child-harness factory that ``delegate`` calls.

        The contract is enforced here rather than requested in a prompt:

        * the child gets an **explicit tool subset**, checked against what is
          actually mounted — a spec naming a phantom tool is an error, not a
          silent downgrade;
        * the child gets **no memory, no profile, and no secrets**;
        * the child's **transcript is discarded** — only its final message returns.
        """
        specs = self._mounted.tool_by_name()

        async def run(spec: SubagentSpec, task: str) -> ToolOutcome:
            absent = [name for name in spec.tools if name not in specs]
            if absent:
                return ToolOutcome.error(f"subagent {spec.name!r} requests tools that are not mounted: {absent}")

            child = AgentHarness(
                AgentHarnessConfig(
                    provider=self._provider,
                    model=spec.model or self.settings.provider.model,
                    system=spec.system,
                    tools=[to_tau_tool(specs[name], self._ctx) for name in spec.tools],
                    max_turns=spec.max_turns,
                    session_id=f"{self.session_id}:{spec.name}",
                    # A write inside a child still prompts: same gate, same allowlist.
                    before_tool_call=build_consent_hook(self._gate),
                )
            )

            transcripts: list[str] = []
            async for event in child.prompt(task):
                if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
                    if event.message.text.strip():
                        transcripts.append(event.message.text.strip())

            if not transcripts:
                return ToolOutcome.error(f"subagent {spec.name!r} returned nothing")
            return ToolOutcome.ok(transcripts[-1], details={"agent": spec.name, "turns": len(transcripts)})

        return run

    # -- introspection -----------------------------------------------------

    def status(self) -> dict[str, Any]:
        """A zero-token report of what is wired, before spending anything."""
        return {
            "session_id": self.session_id,
            "session_file": str(self._session_path),
            "model": self.settings.provider.model,
            "base_url": self.settings.provider.base_url,
            "packs": list(self._mounted.packs),
            "tools": [
                {"name": item.tool, "scope": item.scope.value, "pack": item.pack}
                for item in self._mounted.scopes.tools()
            ],
            "consent_required": [
                item.tool for item in self._mounted.scopes.writes() if item.tool not in self._allowlist
            ],
            "allowlisted": sorted(self._allowlist),
            "memories": self._dna.count(),
            "db": str(self._store.path),
            "audit": str(self._audit.path),
            "identity_files": sorted(path.name for path in self.settings.paths.identity.glob("*.md")),
        }

    async def aclose(self) -> None:
        """Release the provider and the store.

        `aclose` is not part of Tau's `ModelProvider` protocol — `FakeProvider`
        has no such method — so it is looked up rather than assumed.
        """
        closer = getattr(self._provider, "aclose", None)
        if closer is not None:
            await closer()
        self._store.close()
