# Jarvis

A personal Jarvis: **one main agent that owns everything about *you*** — identity,
memory, and consent — and delegates the work to capability-scoped subagents.

Python only. One process. No server to run.

---

## The shape

```
        you ──▶ main agent ──▶ delegate(agent, task) ──▶ subagent
                   │                                        │
        identity + memory + consent                    narrow tools,
        (the only layer that knows you)                narrow memory scope
                   │                                        │
                   ◀────────── {summary, artifacts, memory_writes}
```

Two rules carry the whole design:

1. **Memory-of-you and consent live at the top layer, always.** A subagent never
   holds your profile and never decides on its own whether to write. That is what
   keeps the main agent coherent and the blast radius small.
2. **A subagent's transcript never crosses back.** The parent sends a task and
   receives a structured result. Context isolation is the point of delegating.

## Where the brain comes from

The conversation loop, provider adapters, session persistence and compaction are
**Tau's** (`tau_agent`), embedded as a library — never its CLI. Verified against
`tau-ai` 0.4.4:

- `AgentHarnessConfig.tools` — Jarvis supplies its own tools
- `before_tool_call` — the consent gate (`ToolCall -> (allowed, reason)`)
- `after_tool_call` — the audit hook
- `FakeProvider` — the whole spine is testable with **zero tokens**

Tau deliberately does **not** ship subagents, MCP, personal-data tools or memory.
Those are precisely what this repo builds.

> `jarvis/brain.py` is the **only** module that imports `tau_agent`. Tau is young;
> when its API moves, one file changes.

## Layout

```
jarvis/
├── config.py        paths + provider settings (no Tau import)
├── brain.py         the Tau boundary — the only tau_agent importer
├── identity/        persona.md + guidelines.md → the system prompt
├── memory/          SQLite store, append-only DNA memory, recall
├── policy/          capability scopes, consent gate, audit log
├── tools/           CapabilityPack protocol + delegate()
├── packs/           capability packs (core, calendar, …)
├── events/          AgentEvent → terminal
└── cli.py
```

## Running

```bash
uv sync
cp .env.example .env      # then fill in your provider key
uv run jarvis --doctor    # what is wired, without spending a token
uv run jarvis "hello"     # one turn
uv run jarvis             # interactive session
uv run jarvis --audit     # what it actually did
uv run pytest             # 114 tests, zero tokens
```

`jarvis --doctor` is the first thing to run after any config change: it prints the
model, endpoint, data paths, and every mounted tool with its **scope**, so a
capability can never be added without you seeing what it is allowed to do.

The provider is any OpenAI-compatible endpoint — `JARVIS_API_KEY`,
`JARVIS_BASE_URL`, `JARVIS_MODEL`. Switching between Nebius, OpenRouter, or a
local server is an env change, not a code change.

## Status

- [x] **M0** — project grounded; `.env` gitignored, data lives in `~/.jarvis/`
- [x] **M1** — spine: brain + memory + consent + audit + `delegate()`
- [ ] **M2** — first real pack (`calendar`) + `notes`
- [ ] **M3** — promote a pack to a real child harness
- [ ] **M4** — recall quality + identity from markdown
- [ ] **M5** — interfaces (web, Telegram)
- [ ] **M6** — proactive scheduler

### What M1 verified

- A **live turn** on `openai/gpt-4o-mini`: the model called `remember` at exactly
  its `user_stated` ceiling of 0.95, then `get_profile`, and answered from what it
  had stored. Both verdicts are in the audit log.
- The end-to-end path runs **offline** too, driven by Tau's `FakeProvider`, so the
  tool loop, the consent gate and the session file are all testable with no tokens.
- Three safety properties are asserted against the **real** loop, not mocks:
  a denied write never reaches its handler; a write inside a **subagent** is gated
  the same way; and a failed model call is **visible** rather than silent.

### The trap this design exists to avoid

Tau's `before_tool_call` hook returns `(blocked, reason)` where **`True` means
block**, while the consent gate returns an `allowed` flag. Getting that inversion
wrong makes every write silently permitted. `tests/test_brain_gate.py` drives a
WRITE tool through the real harness to prove the polarity, and
`tests/test_tau_boundary.py` proves Tau stays confined to three files.

