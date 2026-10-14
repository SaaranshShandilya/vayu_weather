"""Check the configured Gemini keys, and prove they hold independent quota.

Gemini's free tier meters per Google Cloud project, not per API key: ten keys
minted in one project share one project's quota, so a pool built from them
buys nothing. --burst is the proof. It drives the first key until Google rate
limits it, then immediately probes every other key: a key that still answers
belongs to a different project.

    python scripts/check_keys.py             # one probe per key
    python scripts/check_keys.py --burst 40  # prove the keys are independent

Keys are read from GEMINI_API_KEYS (comma or whitespace separated), falling
back to GEMINI_API_KEY. Only fingerprints are printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from google import genai
from google.genai import types

from backend.config import get_settings
from backend.keypool import fingerprint, is_daily_quota, is_rate_limited


PROBE_PROMPT = "Reply with the single word: ok"


@dataclass
class Probe:
    key: str
    ok: bool
    rate_limited: bool
    daily: bool
    elapsed_ms: int
    detail: str

    @property
    def label(self) -> str:
        return fingerprint(self.key)

    @property
    def status(self) -> str:
        if self.ok:
            return "ok"
        if self.daily:
            return "daily quota"
        if self.rate_limited:
            return "rate limited"
        return "error"


async def probe(client: genai.Client, key: str, model: str) -> Probe:
    started = time.perf_counter()
    try:
        await client.aio.models.generate_content(
            model=model,
            contents=PROBE_PROMPT,
            config=types.GenerateContentConfig(max_output_tokens=8),
        )
    except Exception as exc:  # noqa: BLE001 - every failure mode is reportable
        return Probe(
            key=key,
            ok=False,
            rate_limited=is_rate_limited(exc),
            daily=is_daily_quota(exc),
            elapsed_ms=round((time.perf_counter() - started) * 1_000),
            detail=" ".join(str(exc).split())[:160],
        )
    return Probe(
        key=key,
        ok=True,
        rate_limited=False,
        daily=False,
        elapsed_ms=round((time.perf_counter() - started) * 1_000),
        detail="",
    )


async def check_each(keys: tuple[str, ...], model: str) -> list[Probe]:
    clients = {key: genai.Client(api_key=key) for key in keys}
    return list(
        await asyncio.gather(*(probe(clients[key], key, model) for key in keys))
    )


async def burst_check(
    keys: tuple[str, ...],
    model: str,
    size: int,
) -> dict:
    """Saturate the first key, then see which other keys still answer."""
    clients = {key: genai.Client(api_key=key) for key in keys}
    target = keys[0]
    started = time.perf_counter()
    results = await asyncio.gather(
        *(probe(clients[target], target, model) for _ in range(size))
    )
    burst_seconds = time.perf_counter() - started
    limited = sum(1 for item in results if item.rate_limited)
    succeeded = sum(1 for item in results if item.ok)
    others = [
        await probe(clients[key], key, model)
        for key in keys[1:]
    ]
    return {
        "target": fingerprint(target),
        "burst_size": size,
        "burst_seconds": round(burst_seconds, 2),
        "succeeded": succeeded,
        "rate_limited": limited,
        "failed": len(results) - succeeded - limited,
        "target_hit_its_limit": limited > 0,
        "others": [
            {
                "fingerprint": item.label,
                "status": item.status,
                "independent": item.ok,
                "detail": item.detail,
            }
            for item in others
        ],
    }


def print_probes(probes: list[Probe]) -> None:
    print(f"{'key':<14} {'status':<13} {'ms':>6}  detail")
    for item in probes:
        print(
            f"{item.label:<14} {item.status:<13} {item.elapsed_ms:>6}  "
            f"{item.detail}"
        )
    working = sum(1 for item in probes if item.ok)
    print(f"\n{working}/{len(probes)} key(s) answered.")


def print_burst(report: dict) -> None:
    print(
        f"Burst of {report['burst_size']} on {report['target']} in "
        f"{report['burst_seconds']}s: {report['succeeded']} ok, "
        f"{report['rate_limited']} rate limited, {report['failed']} other error."
    )
    if not report["target_hit_its_limit"]:
        print(
            "\nThe target key never hit its limit, so this run proves nothing "
            "about project independence. Re-run with a larger --burst."
        )
        return
    print("\nImmediately after that limit, the other keys reported:")
    for row in report["others"]:
        verdict = (
            "independent project"
            if row["independent"]
            else "SHARES the target's quota (same project?)"
        )
        print(f"  {row['fingerprint']:<14} {row['status']:<13} {verdict}")
    independent = sum(1 for row in report["others"] if row["independent"])
    print(
        f"\n{independent}/{len(report['others'])} other key(s) still had "
        "headroom while the target was rate limited."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--burst",
        nargs="?",
        type=int,
        const=40,
        default=0,
        help="Saturate the first key with N requests (default 40), then "
        "probe the others to prove they hold separate quota.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model to probe with. Defaults to GEMINI_MODEL.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = parser.parse_args()

    settings = get_settings()
    keys = settings.gemini_api_keys
    model = args.model or settings.gemini_model
    if not keys:
        print("No keys configured. Set GEMINI_API_KEYS or GEMINI_API_KEY.")
        return 2

    if args.burst:
        if len(keys) < 2:
            print("--burst needs at least two keys to compare.")
            return 2
        report = asyncio.run(burst_check(keys, model, args.burst))
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print_burst(report)
        return 0 if report["target_hit_its_limit"] else 1

    probes = asyncio.run(check_each(keys, model))
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "fingerprint": item.label,
                        "status": item.status,
                        "elapsed_ms": item.elapsed_ms,
                        "detail": item.detail,
                    }
                    for item in probes
                ],
                indent=2,
            )
        )
    else:
        print_probes(probes)
    return 0 if all(item.ok for item in probes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
