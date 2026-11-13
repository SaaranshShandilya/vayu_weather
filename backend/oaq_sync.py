"""Mirror OAQ's CPCB station-day data into ``data/oaq/``.

Run by ``scripts/sync_oaq.py`` and, when ``OAQ_REFRESH_HOURS`` is set, by the
app's background refresh. A first run fetches every year OAQ publishes; later
runs re-fetch the current and previous year and keep the rest of the cache.
A station-year is replaced only when OAQ returns data for it, so a failed or
missing download never deletes cached readings. Files are written to a
temporary path and swapped in, so the app never reads a half-written cache.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import duckdb

from .oaq import (
    BASE_URL,
    FIRST_YEAR,
    OAQ_DIR,
    PARAMETERS,
    PROVENANCE_PATH,
    PROVIDER,
    STATION_DAY_PATH,
    STATIONS_PATH,
    OAQClient,
    OAQError,
    cache_age_hours,
    site_id,
)

logger = logging.getLogger("vayuchat.oaq")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# Abort without writing when more downloads fail than this: an outage must not
# leave a cache that silently lacks most stations.
MAX_FAILURE_SHARE = 0.10
_sync_lock = threading.Lock()

ROW_COLUMNS = {
    "timestamp": "DATE",
    "site_id": "VARCHAR",
    "state": "VARCHAR",
    "city": "VARCHAR",
    "station": "VARCHAR",
    "year": "INTEGER",
    **{column: "DOUBLE" for column in PARAMETERS.values()},
}


def default_years(today: dt.date | None = None) -> list[int]:
    current = (today or dt.datetime.now(IST).date()).year
    if STATION_DAY_PATH.exists():
        return [current - 1, current]
    return list(range(FIRST_YEAR, current + 1))


def _device_record(device: dict[str, str]) -> dict[str, Any]:
    def number(value: str) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return {
        "site_id": site_id(device["id"].strip()),
        "state": device.get("state", "").strip(),
        "city": device.get("city", "").strip(),
        "station": (device.get("station_name") or device.get("name") or "").strip(),
        "address": "",
        "latitude": number(device.get("latitude", "")),
        "longitude": number(device.get("longitude", "")),
    }


def _rows(
    payload: dict[str, Any], station: dict[str, Any], year: int, today: str
) -> Iterable[dict[str, Any]]:
    for record in payload.get("data") or []:
        date = str(record.get("date", ""))[:10]
        # Today's value is a mean over a partial day; the next sync takes it.
        if not date.startswith(str(year)) or date >= today:
            continue
        row = {
            "timestamp": date,
            "site_id": station["site_id"],
            "state": station["state"],
            "city": station["city"],
            "station": station["station"],
            "year": year,
        }
        for parameter, column in PARAMETERS.items():
            value = record.get(parameter)
            row[column] = value if isinstance(value, (int, float)) else None
        yield row


def _sql_columns() -> str:
    return "{" + ", ".join(f"'{name}': '{kind}'" for name, kind in ROW_COLUMNS.items()) + "}"


def _write_stations(stations: list[dict[str, Any]]) -> None:
    temporary = STATIONS_PATH.with_suffix(".csv.part")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(stations[0]))
        writer.writeheader()
        writer.writerows(sorted(stations, key=lambda row: row["site_id"]))
    os.replace(temporary, STATIONS_PATH)


def start_background_refresh(
    hours: float,
    on_refresh: Callable[[], None],
) -> threading.Event | None:
    """Re-sync whenever the cache is ``hours`` old; set the event to stop.

    ``on_refresh`` runs after each successful sync (the database reload). A
    failed refresh is logged and retried after an hour; the app keeps serving
    the cache it has.
    """
    if hours <= 0:
        return None
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            age = cache_age_hours()
            due_in = 0.0 if age is None else max(hours - age, 0.0)
            if stop.wait(due_in * 3600):
                return
            try:
                sync()
                on_refresh()
            except Exception:  # noqa: BLE001 - keep serving the old cache
                logger.exception("OAQ refresh failed; retrying in an hour")
                if stop.wait(3600):
                    return

    threading.Thread(target=loop, name="oaq-refresh", daemon=True).start()
    return stop


def sync(
    years: Iterable[int] | None = None,
    *,
    workers: int = 16,
    client: OAQClient | None = None,
    log: Callable[[str], None] = logger.info,
) -> dict[str, Any]:
    """Fetch ``years`` (default: see module docstring) and rewrite the cache."""
    with _sync_lock:
        return _sync(years, workers=workers, client=client, log=log)


def _sync(
    years: Iterable[int] | None,
    *,
    workers: int,
    client: OAQClient | None,
    log: Callable[[str], None],
) -> dict[str, Any]:
    client = client or OAQClient()
    requested_years = sorted(set(years or default_years()))
    stations = [_device_record(device) for device in client.devices() if device.get("id")]
    if not stations:
        raise OAQError("OAQ's CPCB device registry is empty.")
    log(f"OAQ registry: {len(stations)} CPCB stations; fetching years {requested_years}")

    OAQ_DIR.mkdir(parents=True, exist_ok=True)
    rows_path = OAQ_DIR / "fresh_rows.ndjson.part"
    tasks = [(station, year) for station in stations for year in requested_years]
    failures: list[dict[str, Any]] = []
    published = 0
    today = dt.datetime.now(IST).date().isoformat()
    with rows_path.open("w", encoding="utf-8") as output, ThreadPoolExecutor(workers) as pool:
        futures = {
            pool.submit(client.annual, station["site_id"].removeprefix("site_"), year): (station, year)
            for station, year in tasks
        }
        for done, future in enumerate(as_completed(futures), start=1):
            station, year = futures[future]
            try:
                payload = future.result()
            except (OAQError, ValueError) as error:
                failures.append({"site_id": station["site_id"], "year": year, "error": str(error)})
                continue
            if payload is not None:
                published += 1
                for row in _rows(payload, station, year, today):
                    output.write(json.dumps(row) + "\n")
            if done % 500 == 0 or done == len(tasks):
                log(f"  {done}/{len(tasks)} station-years checked, {published} published")

    if len(failures) > MAX_FAILURE_SHARE * len(tasks):
        rows_path.unlink(missing_ok=True)
        raise OAQError(
            f"{len(failures)} of {len(tasks)} OAQ downloads failed; cache left unchanged. "
            f"First error: {failures[0]['error']}"
        )

    connection = duckdb.connect()
    try:
        empty = f"SELECT {', '.join(f'NULL::{kind} AS {name}' for name, kind in ROW_COLUMNS.items())} WHERE false"
        fresh = (
            f"SELECT * FROM read_json(?, format = 'newline_delimited', columns = {_sql_columns()})"
            if rows_path.stat().st_size
            else empty
        )
        connection.execute(
            f"CREATE TABLE fresh AS {fresh}",
            [str(rows_path)] if rows_path.stat().st_size else [],
        )
        if STATION_DAY_PATH.exists():
            connection.execute(
                """
                CREATE TABLE kept AS
                SELECT * FROM read_parquet(?) AS old
                WHERE NOT EXISTS (
                    SELECT 1 FROM fresh
                    WHERE fresh.site_id = old.site_id AND fresh.year = old.year
                )
                """,
                [str(STATION_DAY_PATH)],
            )
        else:
            connection.execute(f"CREATE TABLE kept AS {empty}")
        connection.execute(
            "CREATE TABLE registry (site_id VARCHAR, state VARCHAR, city VARCHAR, station VARCHAR)"
        )
        connection.executemany(
            "INSERT INTO registry VALUES (?, ?, ?, ?)",
            [(s["site_id"], s["state"], s["city"], s["station"]) for s in stations],
        )
        # Retained rows take the registry's current names, so a renamed or
        # re-assigned station reads the same in every year.
        measures = ", ".join(f"rows.{column}" for column in PARAMETERS.values())
        connection.execute(
            f"""
            CREATE TABLE merged AS
            SELECT
                rows.timestamp,
                rows.site_id,
                COALESCE(NULLIF(registry.state, ''), rows.state) AS state,
                COALESCE(NULLIF(registry.city, ''), rows.city) AS city,
                COALESCE(NULLIF(registry.station, ''), rows.station) AS station,
                rows.year,
                {measures}
            FROM (SELECT * FROM kept UNION ALL BY NAME SELECT * FROM fresh) AS rows
            LEFT JOIN registry USING (site_id)
            """
        )
        temporary = STATION_DAY_PATH.with_suffix(".parquet.part")
        connection.execute(
            f"COPY (SELECT * FROM merged ORDER BY site_id, timestamp) TO '{temporary.as_posix()}' "
            "(FORMAT parquet, COMPRESSION zstd)"
        )
        summary = connection.execute(
            """
            SELECT
                COUNT(*), COUNT(DISTINCT site_id), COUNT(DISTINCT city),
                COUNT(DISTINCT state), MIN(timestamp)::VARCHAR, MAX(timestamp)::VARCHAR
            FROM merged
            """
        ).fetchone()
        by_year = connection.execute(
            "SELECT year, COUNT(*), COUNT(DISTINCT site_id) FROM merged GROUP BY year ORDER BY year"
        ).fetchall()
    finally:
        connection.close()
        rows_path.unlink(missing_ok=True)

    if not summary[0]:
        temporary.unlink(missing_ok=True)
        raise OAQError("OAQ returned no CPCB station-days; cache left unchanged.")
    os.replace(temporary, STATION_DAY_PATH)
    _write_stations(stations)

    record = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "OAQ – India's Open Air Quality Platform (https://oaq.notf.in)",
        "provider": PROVIDER,
        "attribution": "Central Pollution Control Board (MoEFCC), Govt. of India, via OAQ",
        "endpoints": {
            "devices": f"{BASE_URL}provider={PROVIDER}/meta/devices.csv",
            "station_day": f"{BASE_URL}provider={PROVIDER}/history/aggregates/{{id}}/annual_{{year}}.json",
        },
        "years_requested": requested_years,
        "stations_in_registry": len(stations),
        "station_years_published": published,
        "failure_count": len(failures),
        "failures": failures[:50],
        "rows": summary[0],
        "stations": summary[1],
        "cities": summary[2],
        "states": summary[3],
        "date_start": summary[4],
        "date_end": summary[5],
        "by_year": [{"year": year, "rows": rows, "stations": count} for year, rows, count in by_year],
    }
    PROVENANCE_PATH.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(
        f"OAQ cache written: {summary[0]:,} station-days, {summary[1]} stations, "
        f"{summary[4]} to {summary[5]}; {len(failures)} failed downloads"
    )
    return record
