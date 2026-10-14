from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


KEY_SEPARATORS = re.compile(r"[\s,;]+")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Read a local .env into the process environment, if one exists.

    Real environment variables always win, so a Hugging Face secret or an
    exported shell value is never overridden by a stale local file.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition("=")
        if not separator:
            continue
        name = name.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


load_env_file()


def parse_api_keys(*values: str) -> tuple[str, ...]:
    """Split comma, semicolon, or whitespace separated keys, preserving order.

    Duplicates are dropped: a key repeated across GEMINI_API_KEYS and
    GEMINI_API_KEY is still one key with one quota.
    """
    keys: list[str] = []
    for value in values:
        for candidate in KEY_SEPARATORS.split(value or ""):
            cleaned = candidate.strip()
            if cleaned and cleaned not in keys:
                keys.append(cleaned)
    return tuple(keys)


@dataclass(frozen=True)
class Settings:
    gemini_api_keys: tuple[str, ...]
    app_password: str
    gemini_model: str
    cookie_name: str
    cookie_ttl_seconds: int
    key_rpm: int
    key_rpd: int
    key_wait_seconds: float
    turn_budget_seconds: float
    api_concurrency_limit: int

    @property
    def gemini_api_key(self) -> str:
        """The first configured key, for callers that want exactly one."""
        return self.gemini_api_keys[0] if self.gemini_api_keys else ""

    @property
    def is_configured(self) -> bool:
        return bool(self.gemini_api_keys and self.app_password.strip())


def _raw_env(*names: str) -> str:
    """First non-empty value among these variable names."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _int_env(names: tuple[str, ...], default: int) -> int:
    try:
        return int(_raw_env(*names) or default)
    except ValueError:
        return default


def _float_env(names: tuple[str, ...], default: float) -> float:
    try:
        return float(_raw_env(*names) or default)
    except ValueError:
        return default


def get_settings() -> Settings:
    key_wait_seconds = _float_env(
        ("GEMINI_KEY_WAIT_SECONDS", "GEMINI_KEY_WAIT_S"),
        25.0,
    )
    return Settings(
        gemini_api_keys=parse_api_keys(
            os.getenv("GEMINI_API_KEYS", ""),
            os.getenv("GEMINI_API_KEY", ""),
        ),
        app_password=os.getenv("APP_PASSWORD", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        cookie_name="vayuchat_session",
        cookie_ttl_seconds=60 * 60 * 12,
        # Local pacing is off unless a limit is configured. Google no longer
        # publishes per-model free-tier numbers, so a hardcoded guess is either
        # wasteful or useless; with pacing off the pool still rotates keys and
        # still honours the cooldown a 429 asks for.
        key_rpm=_int_env(("GEMINI_KEY_RPM", "GEMINI_RPM_PER_KEY"), 0),
        key_rpd=_int_env(("GEMINI_KEY_RPD", "GEMINI_RPD_PER_KEY"), 0),
        key_wait_seconds=key_wait_seconds,
        # One turn is two to four calls. The budget has to leave room for a
        # full lease wait plus the calls after it, or configuring a long wait
        # would have no effect.
        turn_budget_seconds=_float_env(
            ("GEMINI_TURN_BUDGET_SECONDS", "GEMINI_TURN_BUDGET_S"),
            max(45.0, key_wait_seconds + 20.0),
        ),
        # Concurrent /api/chat turns. 0 means no cap; the key pool is the real
        # limiter, this only stops a flood from queueing inside the process.
        api_concurrency_limit=_int_env(
            ("API_CONCURRENCY_LIMIT", "API_THREAD_LIMIT"),
            0,
        ),
    )
