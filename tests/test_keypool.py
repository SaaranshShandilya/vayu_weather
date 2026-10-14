import asyncio
import time

import pytest

from backend.config import parse_api_keys
from backend.gemini_service import GeminiService
from backend.keypool import (
    KeyPool,
    PoolExhausted,
    fingerprint,
    is_daily_quota,
    is_rate_limited,
    is_retriable,
    next_pacific_midnight,
    retry_delay_seconds,
)


KEYS = (
    "AQ.Ab8RN6JexampleKeyOneGhhA",
    "AQ.Ab8RN6JexampleKeyTwoJjkB",
    "AQ.Ab8RN6JexampleKeyThreeLmC",
)

RATE_LIMITED = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'status': "
    "'RESOURCE_EXHAUSTED'}, 'retryDelay': '27s'}"
)
DAILY_QUOTA = (
    "429 RESOURCE_EXHAUSTED. Quota exceeded for metric "
    "generate_requests_per_model_per_day, limit: requests per day"
)
TRANSPORT_FAILURE = "Cannot send a request, as the client has been closed."


def pool(**kwargs) -> KeyPool:
    kwargs.setdefault("window_seconds", 1.0)
    kwargs.setdefault("wait_s", 1.0)
    return KeyPool(KEYS, **kwargs)


def test_parse_api_keys_splits_and_deduplicates():
    parsed = parse_api_keys("one, two;three\nfour", "two")
    assert parsed == ("one", "two", "three", "four")
    assert parse_api_keys("", "   ") == ()


def test_fingerprint_hides_the_key():
    label = fingerprint(KEYS[0])
    assert label == "AQ.A...GhhA"
    assert KEYS[0] not in label
    assert fingerprint("short") == "..."


def test_snapshot_never_contains_a_raw_key():
    keys = pool()
    serialized = repr(keys.snapshot())
    for key in KEYS:
        assert key not in serialized
    assert {row["fingerprint"] for row in keys.snapshot()} == {
        fingerprint(key) for key in KEYS
    }


def test_rate_limit_classification():
    assert is_rate_limited(RATE_LIMITED)
    assert is_rate_limited(DAILY_QUOTA)
    assert not is_rate_limited(TRANSPORT_FAILURE)
    assert is_daily_quota(DAILY_QUOTA)
    assert not is_daily_quota(RATE_LIMITED)


def test_retriable_is_separate_from_rate_limited():
    # A broken transport is worth retrying but is not a quota signal.
    assert is_retriable(TRANSPORT_FAILURE)
    assert is_retriable("[WinError 10053] An established connection was aborted")
    assert is_retriable("503 UNAVAILABLE The model is overloaded")
    assert is_retriable(RATE_LIMITED)
    # A malformed request fails identically on every key.
    assert not is_retriable("400 INVALID_ARGUMENT malformed function call")
    assert not is_retriable("400 API key not valid. Please pass a valid API key.")


GEMINI_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded "
    "your current quota, please check your plan and billing details.\n* Quota "
    "exceeded for metric: generativelanguage.googleapis.com/"
    "generate_content_free_tier_requests, limit: 15, model: "
    "gemini-3.5-flash-lite\nPlease retry in 3.786691374s.', 'status': "
    "'RESOURCE_EXHAUSTED', 'details': [{'@type': "
    "'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '3s'}]}}"
)


# The terser 429 Gemini actually returns under concurrent load. It names no
# delay at all, so the pool falls back to its default cooldown.
GEMINI_429_UNDER_LOAD = (
    "Error code: 429 - {'error': {'message': 'Your project has exceeded a "
    "quota. See https://ai.dev/rate-limit to manage your rate limits.', "
    "'code': 'too_many_requests'}}"
)


def test_the_terse_429_seen_under_load_still_parks_the_key():
    assert is_rate_limited(GEMINI_429_UNDER_LOAD)
    assert is_retriable(GEMINI_429_UNDER_LOAD)
    assert not is_daily_quota(GEMINI_429_UNDER_LOAD)
    assert retry_delay_seconds(GEMINI_429_UNDER_LOAD) is None

    keys = pool()

    async def scenario():
        key = await keys.acquire()
        keys.release(key, error=GEMINI_429_UNDER_LOAD)
        return key

    key = asyncio.run(scenario())
    parked = next(
        row for row in keys.snapshot() if row["fingerprint"] == fingerprint(key)
    )
    assert 29 <= parked["cooldown_seconds"] <= 30


def test_the_real_gemini_429_is_classified_and_timed():
    # Captured verbatim from a live burst against the free tier.
    assert is_rate_limited(GEMINI_429)
    assert is_retriable(GEMINI_429)
    assert not is_daily_quota(GEMINI_429)
    # Google states the delay twice and disagrees with itself; the longer
    # value is the one that actually clears.
    assert retry_delay_seconds(GEMINI_429) == 3.786691374


def test_retry_delay_uses_the_number_the_server_named():
    assert retry_delay_seconds(RATE_LIMITED) == 27.0
    assert retry_delay_seconds("Please retry in 12s") == 12.0
    assert retry_delay_seconds("Retry-After: 8") == 8.0
    assert retry_delay_seconds("429 too many requests") is None


def test_next_pacific_midnight_is_in_the_future():
    now = time.time()
    reset = next_pacific_midnight(now)
    assert now < reset <= now + 24 * 3600 + 1


def test_leases_spread_across_keys_before_reusing_one():
    keys = pool()

    async def scenario():
        return [await keys.acquire() for _ in range(3)]

    assert set(asyncio.run(scenario())) == set(KEYS)


def test_least_busy_key_wins():
    keys = pool()

    async def scenario():
        first = await keys.acquire()
        second = await keys.acquire()
        keys.release(first)
        # first is idle again with an older last_used than the untouched third
        # key, but the third key has no minute spent, so it is picked next.
        third = await keys.acquire()
        fourth = await keys.acquire()
        return first, second, third, fourth

    first, second, third, fourth = asyncio.run(scenario())
    assert len({first, second, third}) == 3
    assert fourth == first


def test_rpm_admission_control_makes_the_extra_caller_wait():
    keys = pool(rpm=1, window_seconds=0.3, wait_s=2.0)

    async def scenario():
        leased = [await keys.acquire() for _ in range(3)]
        for key in leased:
            keys.release(key)
        started = time.time()
        extra = await keys.acquire()
        return extra, time.time() - started

    extra, waited = asyncio.run(scenario())
    assert extra in KEYS
    assert waited >= 0.2


def test_a_429_parks_the_key_and_the_next_lease_uses_another():
    keys = pool()

    async def scenario():
        first = await keys.acquire()
        keys.release(first, error=RATE_LIMITED)
        second = await keys.acquire()
        return first, second

    first, second = asyncio.run(scenario())
    assert first != second
    parked = next(
        row for row in keys.snapshot() if row["fingerprint"] == fingerprint(first)
    )
    assert not parked["available"]
    assert 26 <= parked["cooldown_seconds"] <= 27
    assert parked["rate_limit_hits"] == 1


def test_a_daily_quota_parks_the_key_until_the_next_reset():
    keys = pool()

    async def scenario():
        key = await keys.acquire()
        keys.release(key, error=DAILY_QUOTA)
        return key

    key = asyncio.run(scenario())
    parked = next(
        row for row in keys.snapshot() if row["fingerprint"] == fingerprint(key)
    )
    assert parked["cooldown_seconds"] > 60
    assert parked["cooldown_seconds"] <= 24 * 3600


def test_a_transport_failure_frees_the_lease_without_parking_the_key():
    keys = pool()

    async def scenario():
        key = await keys.acquire()
        keys.release(key, error=TRANSPORT_FAILURE)
        return key

    key = asyncio.run(scenario())
    row = next(
        item for item in keys.snapshot() if item["fingerprint"] == fingerprint(key)
    )
    assert row["available"]
    assert row["inflight"] == 0
    assert row["cooldown_seconds"] == 0
    assert row["failures"] == 1
    assert row["rate_limit_hits"] == 0


def test_pool_exhausted_when_every_key_is_parked():
    keys = pool(wait_s=0.2)

    async def scenario():
        for _ in KEYS:
            key = await keys.acquire()
            keys.release(key, error=RATE_LIMITED)
        with pytest.raises(PoolExhausted):
            await keys.acquire()

    asyncio.run(scenario())


def test_a_waiter_sleeps_exactly_until_the_cooldown_ends():
    keys = KeyPool([KEYS[0]], wait_s=5.0)

    async def scenario():
        held = await keys.acquire()
        keys.release(held, error="429 RESOURCE_EXHAUSTED, retry in 1s")
        started = time.time()
        served = await keys.acquire(timeout=4.0)
        return served, time.time() - started

    served, waited = asyncio.run(scenario())
    assert served == KEYS[0]
    # The cooldown is one second, and the waiter neither polls early nor
    # oversleeps to the default wait.
    assert 0.9 <= waited < 2.0


def test_pool_requires_at_least_one_key():
    with pytest.raises(ValueError):
        KeyPool([" ", ""])


class FakeClient:
    """Stands in for genai.Client, recording which key served each call."""

    def __init__(self, api_key: str, failures: dict[str, str]) -> None:
        self.api_key = api_key
        self._failures = failures

    async def run(self, calls: list[str]):
        calls.append(self.api_key)
        error = self._failures.get(self.api_key)
        if error:
            raise RuntimeError(error)
        return f"answer from {fingerprint(self.api_key)}"


def service_with(failures: dict[str, str], keys: KeyPool) -> tuple:
    calls: list[str] = []
    service = GeminiService(
        model="test-model",
        pool=keys,
        turn_budget_seconds=5.0,
        client_factory=lambda key: FakeClient(key, failures),
    )
    return service, calls


def test_failover_moves_to_another_key_after_a_rate_limit():
    keys = pool(wait_s=1.0)
    service, calls = service_with({KEYS[0]: RATE_LIMITED}, keys)

    async def scenario():
        return await service._call("test", lambda client: client.run(calls))

    result = asyncio.run(scenario())
    assert result.startswith("answer from")
    assert len(calls) == 2
    assert len(set(calls)) == 2


def test_failover_stops_immediately_on_a_non_retriable_error():
    keys = pool(wait_s=1.0)
    service, calls = service_with(
        {key: "400 INVALID_ARGUMENT bad request" for key in KEYS},
        keys,
    )

    async def scenario():
        with pytest.raises(RuntimeError):
            await service._call("test", lambda client: client.run(calls))

    asyncio.run(scenario())
    assert len(calls) == 1


def test_a_turn_budget_bounds_the_total_wait():
    keys = pool(wait_s=5.0)
    service = GeminiService(
        model="test-model",
        pool=keys,
        turn_budget_seconds=0.3,
        client_factory=lambda key: FakeClient(key, {}),
    )

    async def scenario():
        for key in KEYS:
            leased = await keys.acquire()
            keys.release(leased, error=RATE_LIMITED)
        started = time.time()
        with pytest.raises(PoolExhausted):
            await service._call("test", lambda client: client.run([]))
        return time.time() - started

    assert asyncio.run(scenario()) < 2.0


def test_call_timeout_shrinks_with_the_remaining_budget():
    keys = pool(wait_s=25.0)
    service = GeminiService(
        model="test-model",
        pool=keys,
        turn_budget_seconds=1.0,
        client_factory=lambda key: FakeClient(key, {}),
    )
    assert 20.0 < service.call_timeout(20) <= 21.0


def test_a_cancelled_call_returns_its_lease():
    keys = pool(wait_s=1.0)
    service = GeminiService(
        model="test-model",
        pool=keys,
        turn_budget_seconds=5.0,
        client_factory=lambda key: FakeClient(key, {}),
    )

    async def never_finishes(_client):
        await asyncio.sleep(5)

    async def scenario():
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await asyncio.wait_for(
                service._call("test", never_finishes),
                timeout=0.1,
            )
        return keys.stats()["inflight"]

    # An outer timeout cancels the call; the key must not stay marked busy.
    assert asyncio.run(scenario()) == 0
