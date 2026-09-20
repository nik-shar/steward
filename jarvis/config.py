"""Configuration: paths, provider settings, and safety tunables.

This module deliberately does **not** import Tau. It holds plain values; turning
them into a Tau provider is `brain.py`'s single job. That split is what keeps the
harness swappable — if Tau's API moves or you outgrow it, nothing here changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_HOME = Path.home() / ".jarvis"
DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
DEFAULT_MODEL = "Qwen/Qwen3-235B-A22B-Instruct-2507"
PACKAGE_DIR = Path(__file__).resolve().parent


class ConfigError(RuntimeError):
    """Raised when the environment is not usable, with a fixable message."""


def load_env() -> None:
    """Load `.env` from the working tree, then from ``JARVIS_HOME``.

    Existing environment variables always win, so a shell export overrides the
    file — which is what you want when scripting a one-off run.
    """
    load_dotenv(override=False)
    home = Path(os.environ.get("JARVIS_HOME", str(DEFAULT_HOME))).expanduser()
    load_dotenv(home / ".env", override=False)


def _env_text(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not value.strip():
        raise ConfigError(f"{name} is not set. Run `cp .env.example .env` and fill it in.")
    return value.strip()


@dataclass(frozen=True, slots=True)
class Paths:
    """Where personal data lives — deliberately outside the repository."""

    home: Path
    db: Path
    sessions: Path
    audit: Path
    vault: Path
    identity: Path

    @classmethod
    def from_env(cls) -> Paths:
        home = Path(os.environ.get("JARVIS_HOME", str(DEFAULT_HOME))).expanduser()
        vault = Path(os.environ.get("JARVIS_VAULT", str(home / "vault"))).expanduser()
        return cls(
            home=home,
            db=home / "jarvis.db",
            sessions=home / "sessions",
            audit=home / "audit.jsonl",
            vault=vault,
            identity=home / "identity",
        )

    def ensure(self) -> Paths:
        """Create the data directories. Idempotent."""
        for directory in (self.home, self.sessions, self.identity):
            directory.mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    """An OpenAI-compatible endpoint. Switching provider is not a code change."""

    api_key: str
    base_url: str
    model: str
    provider_name: str = "nebius"

    @classmethod
    def from_env(cls) -> ProviderSettings:
        """Read provider settings from the environment.

        ``JARVIS_*`` is the canonical trio and wins when present. Otherwise the
        ``NEBIUS_*`` trio is used **as a unit** — key, URL and model are never
        mixed across vendors, because a key paired with another vendor's URL fails
        as a confusing 401 instead of as a clear configuration error.
        """
        if os.environ.get("JARVIS_API_KEY", "").strip():
            return cls(
                api_key=_env_text("JARVIS_API_KEY"),
                base_url=os.environ.get("JARVIS_BASE_URL", DEFAULT_BASE_URL).strip(),
                model=os.environ.get("JARVIS_MODEL", DEFAULT_MODEL).strip(),
                provider_name=os.environ.get("JARVIS_PROVIDER", "openai-compatible").strip(),
            )
        return cls(
            api_key=_env_text("NEBIUS_API_KEY"),
            base_url=os.environ.get("NEBIUS_BASE_URL", DEFAULT_BASE_URL).strip(),
            model=os.environ.get("NEBIUS_MODEL", DEFAULT_MODEL).strip(),
            provider_name="nebius",
        )


def _parse_allowlist(raw: str | None) -> frozenset[str]:
    """Parse ``JARVIS_CONSENT_ALLOWLIST``.

    Empty, ``none``, or ``-`` all mean "ask about every write" — which is the
    correct default. Trust is added one tool at a time, after watching it behave
    in the audit log.
    """
    if not raw or raw.strip().lower() in {"none", "-"}:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    paths: Paths
    provider: ProviderSettings
    max_turns: int
    consent_allowlist: frozenset[str]

    @classmethod
    def load(cls) -> Settings:
        load_env()
        raw_turns = os.environ.get("JARVIS_MAX_TURNS", "12").strip()
        try:
            max_turns = int(raw_turns)
        except ValueError as exc:
            raise ConfigError(f"JARVIS_MAX_TURNS must be an integer, got {raw_turns!r}") from exc
        if max_turns < 1:
            raise ConfigError("JARVIS_MAX_TURNS must be at least 1")
        return cls(
            paths=Paths.from_env().ensure(),
            provider=ProviderSettings.from_env(),
            max_turns=max_turns,
            consent_allowlist=_parse_allowlist(os.environ.get("JARVIS_CONSENT_ALLOWLIST")),
        )
