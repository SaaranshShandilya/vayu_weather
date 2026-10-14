"""A provider-agnostic lease pool for rate-limited API keys.

Nothing here imports Gemini, FastAPI, or any application module. To reuse the
pool elsewhere, bracket the outbound call with acquire/release and, when the
provider reports quota differently, supply your own predicates:

    key = await pool.acquire()
    try:
        response = await call(client_for(key))
    except Exception as exc:
        pool.release(key, error=exc)
        raise
    pool.release(key)

The pool assumes a single asyncio event loop: every state mutation happens
between await points, so no lock is required.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

try:  # pragma: no cover - the fallback runs only where tzdata is absent
    from zoneinfo import ZoneInfo

    PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover
    PACIFIC = timezone(timedelta(hours=-8), "PST")


logger = logging.getLogger("vayuchat.keypool")

WINDOW_SECONDS = 60.0
DEFAULT_COOLDOWN_SECONDS = 30.0
MAX_COOLDOWN_SECONDS = 120.0
DEFAULT_WAIT_SECONDS = 25.0

_RETRY_DELAY_PATTERNS = (
    r"retry[_\- ]?delay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)\s*s",
    r"retry[_\- ]?(?:in|after)\D{0,12}?(\d+(?:\.\d+)?)\s*(?:s\b|seconds?)",
    r"retry[_\- ]?after['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)",
)
_RATE_LIMIT_MARKERS = (
    "429",
    "resource_exhausted",
    "resource exhausted",
    "rate limit",
    "ratelimit",
    "quota exceeded",
    "exceeded your current quota",
)
_DAILY_QUOTA_MARKERS = (
    "per day",
    "perday",
    "per-day",
    "daily limit",
    "requests per day",
)
_RETRIABLE_MARKERS = (
    "500",
    "502",
    "503",
    "504",
    "internal error",
    "internal server",
    "unavailable",
    "overloaded",
    "deadline exceeded",
    "timeout",
    "timed out",
    "client has been closed",
    "connection reset",
    "connection aborted",
    "connection error",
    "broken pipe",
    "server disconnected",
    "remotedisconnected",
    "winerror 10053",
    "winerror 10054",
    "ssl",
)
_FATAL_MARKERS = (
    "api key not valid",
    "api_key_invalid",
    "permission_denied",
    "unauthenticated",
    "invalid_argument",
)


class PoolExhausted(RuntimeError):
    """Raised when no key gained headroom before the caller's deadline."""


def fingerprint(key: str) -> str:
    """A stable, non-reversible label.

    Enough to tell keys apart in logs and status output, never enough to use
    one.
    """
    cleaned = key.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 8:
        return "..."
    return f"{cleaned[:4]}...{cleaned[-4:]}"


def _text(error: object) -> str:
    return str(error).casefold()


def is_rate_limited(error: object) -> bool:
    """Does this error mean the key itself is out of headroom?"""
    message = _text(error)
    return any(marker in message for marker in _RATE_LIMIT_MARKERS)


def is_daily_quota(error: object) -> bool:
    """A 429 naming a per-day quota: the key is done until Pacific midnight."""
    message = _text(error)
    return is_rate_limited(error) and any(
        marker in message for marker in _DAILY_QUOTA_MARKERS
    )


def is_retriable(error: object) -> bool:
    """Is the request worth re-sending on another key?

    Deliberately separate from is_rate_limited: a dropped socket is worth
    retrying but says nothing about the key's quota, and a malformed 400 is
    neither.
    """
    message = _text(error)
    if any(marker in message for marker in _FATAL_MARKERS):
        return False
    if is_rate_limited(error):
        return True
    return any(marker in message for marker in _RETRIABLE_MARKERS)


def retry_delay_seconds(error: object) -> float | None:
    """The delay the provider named in its own error, when it named one.

    Gemini states it twice and disagrees with itself: a rounded RetryInfo
    field ("retryDelay": "3s") and an exact sentence ("Please retry in
    3.786691374s"). The larger one is the one that actually clears.
    """
    message = str(error)
    delays = []
    for pattern in _RETRY_DELAY_PATTERNS:
        for match in re.finditer(pattern, message, re.IGNORECASE):
            try:
                delays.append(float(match.group(1)))
            except ValueError:  # pragma: no cover - the regex captures digits
                continue
    return max(delays) if delays else None


def next_pacific_midnight(now: float) -> float:
    """Epoch seconds of the next Google free-tier daily reset."""
    local = datetime.fromtimestamp(now, PACIFIC)
    midnight = (local + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return midnight.timestamp()


@dataclass
class KeyState:
    """Everything the scheduler knows about one key."""

    key: str
    recent: deque[float] = field(default_factory=deque)
    day_count: int = 0
    day_stamp: str = ""
    cooldown_until: float = 0.0
    inflight: int = 0
    last_used: float = 0.0
    total_requests: int = 0
    rate_limit_hits: int = 0
    failures: int = 0
    window_seconds: float = WINDOW_SECONDS

    @property
    def label(self) -> str:
        return fingerprint(self.key)

    def roll(self, now: float) -> None:
        """Expire the rolling minute window and the Pacific-day counter."""
        cutoff = now - self.window_seconds
        while self.recent and self.recent[0] <= cutoff:
            self.recent.popleft()
        stamp = datetime.fromtimestamp(now, PACIFIC).strftime("%Y-%m-%d")
        if stamp != self.day_stamp:
            self.day_stamp = stamp
            self.day_count = 0

    def free_at(self, now: float, rpm: int, rpd: int) -> float:
        """Collapse cooldown, per-minute, and per-day limits into one moment.

        Reducing every constraint to a timestamp is what keeps the wait loop
        simple: the scheduler never reasons about why a key is unavailable,
        only until when.
        """
        when = self.cooldown_until
        if rpm and len(self.recent) >= rpm:
            when = max(when, self.recent[0] + self.window_seconds)
        if rpd and self.day_count >= rpd:
            when = max(when, next_pacific_midnight(now))
        return when

    def snapshot(self, now: float, rpm: int, rpd: int) -> dict:
        ready_at = self.free_at(now, rpm, rpd)
        return {
            "fingerprint": self.label,
            "inflight": self.inflight,
            "recent_minute": len(self.recent),
            "day_count": self.day_count,
            "total_requests": self.total_requests,
            "rate_limit_hits": self.rate_limit_hits,
            "failures": self.failures,
            "cooldown_seconds": round(max(0.0, self.cooldown_until - now), 1),
            "available_in_seconds": round(max(0.0, ready_at - now), 1),
            "available": ready_at <= now,
        }


class KeyPool:
    """Hands each outbound request the key with the most headroom right now."""

    def __init__(
        self,
        keys: Iterable[str],
        *,
        rpm: int = 0,
        rpd: int = 0,
        wait_s: float = DEFAULT_WAIT_SECONDS,
        window_seconds: float = WINDOW_SECONDS,
        rate_limited: Callable[[object], bool] = is_rate_limited,
        daily_quota: Callable[[object], bool] = is_daily_quota,
        retriable: Callable[[object], bool] = is_retriable,
        retry_delay: Callable[[object], float | None] = retry_delay_seconds,
    ) -> None:
        unique: list[str] = []
        for key in keys:
            cleaned = key.strip()
            if cleaned and cleaned not in unique:
                unique.append(cleaned)
        if not unique:
            raise ValueError("KeyPool requires at least one API key.")
        self.rpm = max(0, int(rpm))
        self.rpd = max(0, int(rpd))
        self.wait_s = max(0.0, float(wait_s))
        self._states = [
            KeyState(key=key, window_seconds=window_seconds) for key in unique
        ]
        self._by_key = {state.key: state for state in self._states}
        self._waiters: list[asyncio.Future] = []
        self._rate_limited = rate_limited
        self._daily_quota = daily_quota
        self._retriable = retriable
        self._retry_delay = retry_delay

    def __len__(self) -> int:
        return len(self._states)

    @property
    def keys(self) -> Sequence[str]:
        return tuple(self._by_key)

    def is_retriable(self, error: object) -> bool:
        return self._retriable(error)

    def is_rate_limited(self, error: object) -> bool:
        return self._rate_limited(error)

    async def acquire(
        self,
        timeout: float | None = None,
        exclude: Iterable[str] = (),
    ) -> str:
        """Lease the least busy key with headroom, waiting when none has it.

        exclude skips keys the caller has already failed on, so failover
        spreads across distinct keys instead of retrying the same one.
        """
        budget = self.wait_s if timeout is None else max(0.0, float(timeout))
        deadline = time.time() + budget
        skip = set(exclude)
        while True:
            now = time.time()
            leased = self._try_lease(now, skip)
            if leased is not None:
                return leased
            remaining = deadline - now
            if remaining <= 0:
                raise PoolExhausted(
                    f"No API key had headroom within {budget:.1f}s "
                    f"({len(self._states)} key(s) in the pool)."
                )
            next_free = self._next_free(now, skip)
            wait_for = remaining
            if next_free is not None:
                wait_for = min(remaining, max(next_free - now, 0.01))
            await self._sleep_until_wake(wait_for)

    def release(self, key: str, error: object | None = None) -> None:
        """Return a lease, and let the pool learn from a failure.

        A plain 429 parks the key for the delay the provider named; a 429
        naming a daily quota parks it until the next Pacific midnight; a
        transport failure parks nothing, because a broken pipe is not a quota
        signal.
        """
        state = self._by_key.get(key)
        if state is None:
            return
        state.inflight = max(0, state.inflight - 1)
        if error is not None:
            state.failures += 1
            now = time.time()
            if self._rate_limited(error):
                state.rate_limit_hits += 1
                if self._daily_quota(error):
                    state.cooldown_until = max(
                        state.cooldown_until,
                        next_pacific_midnight(now),
                    )
                    logger.warning(
                        "Key %s parked until the daily quota resets",
                        state.label,
                    )
                else:
                    named = self._retry_delay(error)
                    delay = DEFAULT_COOLDOWN_SECONDS if named is None else named
                    delay = min(max(delay, 1.0), MAX_COOLDOWN_SECONDS)
                    state.cooldown_until = max(state.cooldown_until, now + delay)
                    logger.warning(
                        "Key %s rate limited; parked for %.0fs",
                        state.label,
                        delay,
                    )
        self._wake()

    def snapshot(self) -> list[dict]:
        """Per-key status. Keys appear only as fingerprints, never in full."""
        now = time.time()
        rows = []
        for state in self._states:
            state.roll(now)
            rows.append(state.snapshot(now, self.rpm, self.rpd))
        return rows

    def stats(self) -> dict:
        now = time.time()
        for state in self._states:
            state.roll(now)
        return {
            "keys": len(self._states),
            "available": sum(
                1
                for state in self._states
                if state.free_at(now, self.rpm, self.rpd) <= now
            ),
            "inflight": sum(state.inflight for state in self._states),
            "waiting": len(self._waiters),
            "rpm": self.rpm,
            "rpd": self.rpd,
        }

    def _try_lease(self, now: float, skip: set[str]) -> str | None:
        best: tuple[tuple[int, int, float], KeyState] | None = None
        for state in self._states:
            if state.key in skip:
                continue
            state.roll(now)
            if state.free_at(now, self.rpm, self.rpd) > now:
                continue
            rank = (state.inflight, len(state.recent), state.last_used)
            if best is None or rank < best[0]:
                best = (rank, state)
        if best is None:
            return None
        state = best[1]
        # The request is recorded at lease time, not at completion, so the
        # rolling window doubles as admission control: the caller that would
        # overrun the limit waits instead of discovering it the hard way.
        state.recent.append(now)
        state.day_count += 1
        state.inflight += 1
        state.last_used = now
        state.total_requests += 1
        return state.key

    def _next_free(self, now: float, skip: set[str]) -> float | None:
        soonest: float | None = None
        for state in self._states:
            if state.key in skip:
                continue
            ready_at = state.free_at(now, self.rpm, self.rpd)
            if soonest is None or ready_at < soonest:
                soonest = ready_at
        return soonest

    async def _sleep_until_wake(self, seconds: float) -> None:
        """Sleep until a key frees itself, or until a release wakes us early."""
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, timeout=seconds)
        except (asyncio.TimeoutError, TimeoutError):
            pass
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    def _wake(self) -> None:
        for waiter in list(self._waiters):
            if not waiter.done():
                waiter.set_result(None)
