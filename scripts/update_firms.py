"""Fetch NASA FIRMS active-fire detections over India for VayuChat.

Source: NASA Fire Information for Resource Management System (FIRMS),
https://firms.modaps.eosdis.nasa.gov. Four satellite products:

    VIIRS S-NPP, NOAA-20, NOAA-21   375 m pixels, ~01:30 and ~13:30 local overpasses
    MODIS Terra/Aqua (C6.1)         1 km pixels, ~10:30/13:30 and 22:30/01:30

The rolling 7-day near-real-time (NRT) files need no key and are fetched on
every run, so the archive grows from the first run onwards. With FIRMS_MAP_KEY
in .env, --backfill START END also fetches older dates through the FIRMS
country API (standard-processing archive where available).

Each detection is kept once (satellite + position + time), restricted to
India, and tagged with its state and district from the GeoJSON boundaries.
Times are converted from UTC to IST. Everything is stored in one Parquet table:

    data/firms/fires.parquet

    python scripts/update_firms.py
    python scripts/update_firms.py --backfill 2024-10-01 2024-11-30
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import sys
import urllib.request
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import shapely

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from backend.config import load_env_file  # noqa: E402
from backend.places import places  # noqa: E402

FIRMS_DIR = PROJECT_ROOT / "data" / "firms"
TABLE = FIRMS_DIR / "fires.parquet"
NRT = "https://firms.modaps.eosdis.nasa.gov/data/active_fire"
API = "https://firms.modaps.eosdis.nasa.gov/api/country/csv"
IST = pd.Timedelta(hours=5, minutes=30)

# label -> (7-day NRT file, archive source names tried newest-quality first)
PRODUCTS = {
    "VIIRS S-NPP": ("suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_South_Asia_7d.csv", ["VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"]),
    "VIIRS NOAA-20": ("noaa-20-viirs-c2/csv/J1_VIIRS_C2_South_Asia_7d.csv", ["VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT"]),
    "VIIRS NOAA-21": ("noaa-21-viirs-c2/csv/J2_VIIRS_C2_South_Asia_7d.csv", ["VIIRS_NOAA21_NRT"]),
    "MODIS": ("modis-c6.1/csv/MODIS_C6_1_South_Asia_7d.csv", ["MODIS_SP", "MODIS_NRT"]),
}
COLUMNS = [
    "product", "latitude", "longitude", "time_utc", "time_ist", "date_ist",
    "confidence", "frp_mw", "daynight", "state", "district", "processing",
]


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=180) as response:
        return response.read().decode("utf-8")


def _confidence(raw: pd.Series, product: str) -> pd.Series:
    """VIIRS reports low/nominal/high; MODIS a 0-100 score, mapped to the same."""
    if product == "MODIS":
        score = pd.to_numeric(raw, errors="coerce")
        return pd.cut(score, [-1, 29, 79, 100], labels=["low", "nominal", "high"]).astype(str)
    return raw.astype(str).str.lower().replace({"l": "low", "n": "nominal", "h": "high"})


def normalise(frame: pd.DataFrame, product: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS)
    time_utc = pd.to_datetime(
        frame["acq_date"].astype(str) + " " + frame["acq_time"].astype(str).str.zfill(4),
        format="%Y-%m-%d %H%M",
    )
    out = pd.DataFrame(
        {
            "product": product,
            "latitude": frame["latitude"].astype(float).round(5),
            "longitude": frame["longitude"].astype(float).round(5),
            "time_utc": time_utc,
            "time_ist": time_utc + IST,
            "confidence": _confidence(frame["confidence"], product),
            "frp_mw": pd.to_numeric(frame["frp"], errors="coerce").astype(float),
            "daynight": frame["daynight"].astype(str),
            "processing": frame["version"].astype(str),
        }
    )
    out["date_ist"] = out["time_ist"].dt.date.astype(str)
    return tag_places(out)


def tag_places(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep detections inside India and add their state and district."""
    places.cities()  # loads the boundary files
    points = shapely.points(frame["longitude"].to_numpy(), frame["latitude"].to_numpy())
    entries = [entry for group in places.districts.values() for entry in group]
    tree = shapely.STRtree([geometry for _, _, geometry in entries])
    point_index, district_index = tree.query(points, predicate="within")
    district = np.full(len(frame), None, dtype=object)
    state = np.full(len(frame), None, dtype=object)
    for p, d in zip(point_index, district_index):
        if district[p] is None:
            district[p], state[p] = entries[d][0], entries[d][1]
    frame = frame.assign(state=state, district=district)
    return frame[frame["state"].notna()][COLUMNS]


def fetch_nrt() -> pd.DataFrame:
    frames = []
    for product, (path, _) in PRODUCTS.items():
        try:
            text = _get(f"{NRT}/{path}")
        except OSError as error:
            print(f"{product}: NRT download failed ({error})")
            continue
        frames.append(normalise(pd.read_csv(io.StringIO(text)), product))
        print(f"{product}: {len(frames[-1]):,} detections in India (last 7 days)")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)


def fetch_archive(key: str, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Country API, 10 days per request, standard processing where it exists."""
    frames = []
    for product, (_, sources) in PRODUCTS.items():
        day = start
        while day <= end:
            span = min(10, (end - day).days + 1)
            got = None
            for source in sources:
                text = _get(f"{API}/{key}/{source}/IND/{span}/{day.isoformat()}")
                if text.lower().startswith("invalid") or "error" in text[:80].lower():
                    raise SystemExit(f"FIRMS API refused the request: {text[:200]}")
                frame = pd.read_csv(io.StringIO(text)) if text.strip() else pd.DataFrame()
                if not frame.empty:
                    got = normalise(frame, product)
                    break
            if got is not None:
                frames.append(got)
            print(f"{product} {day} +{span}d: {0 if got is None else len(got):,} detections")
            day += dt.timedelta(days=span)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)


def merge(new: pd.DataFrame) -> int:
    """Add detections to the table once each; standard processing beats NRT."""
    FIRMS_DIR.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect()
    connection.register("new_rows", new)
    existing = f"SELECT * FROM read_parquet('{TABLE}')" if TABLE.exists() else "SELECT * FROM new_rows WHERE false"
    connection.execute(
        f"""
        CREATE TABLE merged AS
        SELECT * EXCLUDE (rank) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY product, round(latitude, 3), round(longitude, 3), time_utc
                ORDER BY (processing NOT LIKE '%NRT%') DESC
            ) AS rank
            FROM ({existing} UNION ALL SELECT * FROM new_rows)
        ) WHERE rank = 1
        ORDER BY time_utc
        """
    )
    temporary = TABLE.with_suffix(".tmp.parquet")
    connection.execute(f"COPY merged TO '{temporary}' (FORMAT parquet, COMPRESSION zstd)")
    count = connection.execute("SELECT count(*) FROM merged").fetchone()[0]
    temporary.replace(TABLE)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="Archive dates (IST), needs FIRMS_MAP_KEY")
    args = parser.parse_args()
    if args.backfill:
        load_env_file()
        key = os.environ.get("FIRMS_MAP_KEY", "").strip()
        if not key:
            raise SystemExit("Set FIRMS_MAP_KEY in .env (free from https://firms.modaps.eosdis.nasa.gov/api/map_key/).")
        start, end = (dt.date.fromisoformat(value) for value in args.backfill)
        new = fetch_archive(key, start, end)
    else:
        new = fetch_nrt()
    total = merge(new)
    span = duckdb.sql(f"SELECT min(date_ist), max(date_ist) FROM read_parquet('{TABLE}')").fetchone()
    print(f"{TABLE.relative_to(PROJECT_ROOT)}: {total:,} detections, {span[0]} to {span[1]} (IST)")


if __name__ == "__main__":
    main()
