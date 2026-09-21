"""The command line.

One process, no server. `steward "..."` runs a single turn, bare `steward` opens a
session, and `steward --doctor` reports what is wired without spending a token.

Consent is asked **here**, because this is the only layer with a terminal.
Everything below takes a `Prompter` and does not care where the answer came from —
which is how a web frontend will later supply a coroutine instead.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from steward.brain import BrainError, Steward
from steward.config import ConfigError, Settings
from steward.events.render import TerminalRenderer
from steward.policy.audit import AuditLog
from steward.policy.consent import Prompter
from steward.session import latest_session


def _terminal_prompter() -> Prompter:
    """Ask on stdin. Anything other than an explicit yes is a refusal."""

    def ask(question: str, arguments: object) -> bool:
        sys.stdout.write(f"\n  {question} [y/N] ")
        sys.stdout.flush()
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            return False
        return answer in {"y", "yes"}

    return ask


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steward",
        description="A personal main agent that owns your context and delegates the work.",
    )
    parser.add_argument("prompt", nargs="*", help="A one-shot prompt. Omit it for an interactive session.")
    parser.add_argument("--doctor", action="store_true", help="Report what is wired, then exit. Spends no tokens.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Pre-approve every write for this run. Recorded in the audit log.",
    )
    parser.add_argument("--session", default=None, help="Session id to resume or create.")
    parser.add_argument("--continue", dest="continue_", action="store_true", help="Resume the most recent session.")
    parser.add_argument("--audit", action="store_true", help="Print the audit tail, then exit.")
    parser.add_argument("--audit-limit", type=int, default=20)
    parser.add_argument("--thinking", action="store_true", help="Render the thinking stream.")
    parser.add_argument("--quiet", action="store_true", help="Hide tool activity.")
    parser.add_argument("--verbose", action="store_true", help="Also show agent/turn boundaries.")
    return parser


def _print_status(steward: Steward) -> None:
    status = steward.status()
    print("steward — wiring report (no tokens spent)\n")
    print(f"  model           {status['model']}")
    print(f"  endpoint        {status['base_url']}")
    print(f"  session         {status['session_id']}  ->  {status['session_file']}")
    print(f"  data            {status['db']}")
    print(f"  audit           {status['audit']}")
    print(f"  memories        {status['memories']}")
    print(f"  packs           {', '.join(status['packs'])}")
    identity = ", ".join(status["identity_files"]) or "(none — shipped defaults only)"
    print(f"  identity        {identity}")
    print("\n  tools")
    for tool in status["tools"]:
        print(f"    {tool['name']:<18} {tool['scope']:<9} {tool['pack']}")
    needing = status["consent_required"]
    if needing:
        print(f"\n  writes needing consent: {', '.join(needing)}")
    else:
        print("\n  no mounted tool requires consent")
    if status["allowlisted"]:
        print(f"  allowlisted: {', '.join(status['allowlisted'])}")


def _print_audit(steward: Steward, limit: int) -> None:
    records = steward.audit.tail(limit)
    if not records:
        print("the audit log is empty")
        return
    for record in records:
        print(record.line())


async def _turn(steward: Steward, renderer: TerminalRenderer, prompt: str) -> None:
    try:
        async for _event in renderer.stream(steward.ask(prompt)):
            pass
    except KeyboardInterrupt:
        renderer.write("\n[interrupted]\n")
    except Exception as exc:  # noqa: BLE001 - the CLI is the last line of defence
        renderer.write(f"\n[error] {type(exc).__name__}: {exc}\n")
    finally:
        renderer.finish()


async def _repl(steward: Steward, renderer: TerminalRenderer) -> int:
    print(f"steward ready — {steward.settings.provider.model}")
    print(f"session {steward.session_id}  ->  {steward.session_file}")
    print("Ctrl-D or /exit to leave.  /status what it can do.  /audit what it did.\n")
    while True:
        try:
            line = await asyncio.to_thread(input, "you ▸ ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        text = line.strip()
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            return 0
        if text == "/status":
            _print_status(steward)
            continue
        if text == "/audit":
            _print_audit(steward, 10)
            continue
        await _turn(steward, renderer, text)


async def _run(steward: Steward, args: argparse.Namespace) -> int:
    renderer = TerminalRenderer(
        show_tools=not args.quiet,
        show_thinking=args.thinking,
        verbose=args.verbose,
    )
    prompt = " ".join(args.prompt).strip()
    if prompt:
        await _turn(steward, renderer, prompt)
        return 0
    return await _repl(steward, renderer)


async def _with_close(steward: Steward, args: argparse.Namespace) -> int:
    try:
        return await _run(steward, args)
    finally:
        await steward.aclose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        settings = Settings.load()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if args.audit:
        for record in AuditLog(settings.paths.audit).tail(args.audit_limit):
            print(record.line())
        return 0

    session_id = args.session
    if args.continue_ and not session_id:
        session_id = latest_session(settings.paths.sessions)
        if session_id is None:
            print("no previous session to continue", file=sys.stderr)
            return 2

    try:
        steward = Steward(
            settings,
            prompter=_terminal_prompter(),
            session_id=session_id,
            allow_all_writes=args.yes,
        )
    except BrainError as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 2

    if args.doctor:
        _print_status(steward)
        asyncio.run(steward.aclose())
        return 0

    try:
        return asyncio.run(_with_close(steward, args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
