#!/usr/bin/env python3
"""Mirror CPCB station-day data from OAQ (https://oaq.notf.in) into data/oaq/.

Needs the OAQ API key in .env (oaq_key=...). The first run fetches every year
OAQ publishes (2021 onwards); later runs refresh the current and previous
year and keep everything else. See backend/oaq.py for the endpoints used.

    python scripts/sync_oaq.py                 # first run: all years; then: last two
    python scripts/sync_oaq.py --all           # re-fetch every year
    python scripts/sync_oaq.py --years 2024 2025
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from backend.oaq import FIRST_YEAR, OAQError  # noqa: E402
from backend.oaq_sync import IST, sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="re-fetch every year OAQ publishes")
    group.add_argument("--years", type=int, nargs="+", help="re-fetch only these years")
    parser.add_argument("--workers", type=int, default=16, help="concurrent downloads (default 16)")
    args = parser.parse_args()

    years = None
    if args.all:
        years = range(FIRST_YEAR, dt.datetime.now(IST).year + 1)
    elif args.years:
        years = args.years
    try:
        record = sync(years, workers=args.workers, log=print)
    except OAQError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for entry in record["by_year"]:
        print(f"  {entry['year']}: {entry['rows']:>8,} station-days from {entry['stations']} stations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
