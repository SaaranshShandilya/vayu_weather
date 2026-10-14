#!/usr/bin/env python3
"""Build a quality-gated station-day extension from CPCB CAAQMS archives.

The web app's historical table is station-level. This script deliberately
preserves that grain instead of copying the mobile app's city-day aggregate.
It discovers the authoritative CPCB 1-day archive for every station in the
historical table, downloads the requested year, retains only stations with at
least ``--minimum-days-per-station`` reporting days, and writes a separate CSV
plus a machine-readable provenance manifest.

Only Python's standard library is required. Network requests are conservative
and cached outside the repository by default.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence


CPCB_LIST_URL = (
    "https://airquality.cpcb.gov.in/caaqms-common/"
    "dataRepository/file-path"
)
CPCB_DOWNLOAD_URL = (
    "https://airquality.cpcb.gov.in/caaqms-common/"
    "dataRepository/download-excel-file"
)
USER_AGENT = "VayuChat-station-refresh/1.0 (research dataset refresh)"

OUTPUT_COLUMNS = (
    "row_id",
    "Timestamp",
    "State",
    "City",
    "Station",
    "site_id",
    "Year",
    "PM2.5 (µg/m³)",
    "PM10 (µg/m³)",
    "NO (µg/m³)",
    "NO2 (µg/m³)",
    "NOx (ppb)",
    "NH3 (µg/m³)",
    "SO2 (µg/m³)",
    "CO (mg/m³)",
    "Ozone (µg/m³)",
    "AT (°C)",
    "RH (%)",
    "WS (m/s)",
    "WD (deg)",
    "RF (mm)",
    "TOT-RF (mm)",
    "SR (W/mt2)",
    "BP (mmHg)",
    "VWS (m/s)",
)

MEASUREMENT_COLUMNS = OUTPUT_COLUMNS[7:]
CORE_REPORTING_COLUMNS = (
    "PM2.5 (µg/m³)",
    "PM10 (µg/m³)",
    "NO2 (µg/m³)",
    "Ozone (µg/m³)",
)


@dataclass(frozen=True, order=True)
class Station:
    state: str
    city: str
    site_id: str
    name: str


@dataclass(frozen=True)
class StationFile:
    station: Station
    path: Path
    remote_path: str


def request_bytes(
    url: str,
    *,
    payload: Mapping[str, object] | None = None,
    timeout: int = 60,
) -> bytes:
    data = None
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def download_file(url: str, destination: Path, timeout: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(request_bytes(url, timeout=timeout))
    if temporary.stat().st_size < 100:
        raise ValueError(f"Downloaded file is unexpectedly small: {url}")
    temporary.replace(destination)


def load_stations(metadata_path: Path) -> list[Station]:
    stations: set[Station] = set()
    with metadata_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            station = Station(
                state=(row.get("State") or "").strip(),
                city=(row.get("City") or "").strip(),
                site_id=(row.get("site_id") or "").strip(),
                name=(row.get("Station") or "").strip(),
            )
            if all((station.state, station.city, station.site_id, station.name)):
                stations.add(station)
    return sorted(stations)


def discover_station_file(
    station: Station,
    *,
    year: int,
    cache_dir: Path,
    timeout: int,
) -> StationFile:
    payload = {
        "station_id": station.site_id,
        "station_name": station.name,
        "state": "",
        "city": "",
        "frequency": "1D",
        "dataType": "raw",
    }
    response = json.loads(
        request_bytes(CPCB_LIST_URL, payload=payload, timeout=timeout)
    )
    candidates = response.get("data") or response.get("ResponseData") or []
    selected = next(
        (item for item in candidates if str(item.get("year")) == str(year)),
        None,
    )
    if not selected or not selected.get("filepath"):
        raise FileNotFoundError(
            f"No CPCB {year} daily archive for {station.site_id} {station.name}"
        )

    remote_path = str(selected["filepath"])
    destination = cache_dir / f"{station.site_id}-{year}-1day.csv"
    if not destination.exists() or destination.stat().st_size < 100:
        query = urllib.parse.urlencode({"file_name": remote_path})
        download_file(
            f"{CPCB_DOWNLOAD_URL}?{query}",
            destination,
            timeout,
        )
    return StationFile(station, destination, remote_path)


def parse_number(value: str | None) -> str:
    stripped = (value or "").strip()
    if not stripped or stripped.upper() in {"NA", "N/A", "NAN", "NULL"}:
        return ""
    try:
        number = float(stripped)
    except ValueError:
        return ""
    if not math.isfinite(number):
        return ""
    return f"{number:.6f}".rstrip("0").rstrip(".")


def station_rows(
    station_file: StationFile,
    year: int,
) -> tuple[list[dict[str, str]], int, dict[str, int]]:
    rows: list[dict[str, str]] = []
    reporting_days: set[str] = set()
    metric_days = {column: 0 for column in MEASUREMENT_COLUMNS}
    with station_file.path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        missing = {"Timestamp", *MEASUREMENT_COLUMNS} - fields
        if missing:
            raise ValueError(
                f"{station_file.path} lacks expected CPCB columns: "
                f"{sorted(missing)}"
            )
        for source in reader:
            day = (source.get("Timestamp") or "")[:10]
            if not day.startswith(f"{year:04d}-"):
                continue
            measurements = {
                column: parse_number(source.get(column))
                for column in MEASUREMENT_COLUMNS
            }
            if any(measurements[column] for column in CORE_REPORTING_COLUMNS):
                reporting_days.add(day)
            for column, value in measurements.items():
                if value:
                    metric_days[column] += 1
            rows.append(
                {
                    "row_id": "",
                    "Timestamp": day,
                    "State": station_file.station.state,
                    "City": station_file.station.city,
                    "Station": station_file.station.name,
                    "site_id": station_file.station.site_id,
                    "Year": str(year),
                    **measurements,
                }
            )
    return rows, len(reporting_days), metric_days


def build_extension(
    station_files: Sequence[StationFile],
    *,
    year: int,
    minimum_days_per_station: int,
) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    rows: list[dict[str, str]] = []
    station_audit: list[dict[str, object]] = []
    row_id = 0
    for station_file in sorted(
        station_files,
        key=lambda item: (
            item.station.state,
            item.station.city,
            item.station.name,
        ),
    ):
        candidate_rows, reporting_days, metric_days = station_rows(
            station_file,
            year,
        )
        included = reporting_days >= minimum_days_per_station
        station_audit.append(
            {
                **asdict(station_file.station),
                "remote_path": station_file.remote_path,
                "reporting_days": reporting_days,
                "included": included,
                "metric_days": metric_days,
            }
        )
        if not included:
            continue
        for row in candidate_rows:
            row["row_id"] = str(row_id)
            row_id += 1
            rows.append(row)
    rows.sort(
        key=lambda row: (
            row["Timestamp"],
            row["State"],
            row["City"],
            row["Station"],
        )
    )
    for index, row in enumerate(rows):
        row["row_id"] = str(index)
    return rows, station_audit


def write_rows(path: Path, rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=OUTPUT_COLUMNS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    repository = Path(__file__).resolve().parents[1]
    default_year = date.today().year - 1
    default_cache = (
        Path(tempfile.gettempdir())
        / f"vayuchat-web-cpcb-{default_year}"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metadata",
        type=Path,
        default=repository / "data/AQ_met_data.csv",
    )
    parser.add_argument("--year", type=int, default=default_year)
    parser.add_argument(
        "--output",
        type=Path,
        default=repository / f"data/AQ_met_data_{default_year}.csv",
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=default_cache)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--minimum-days-per-station",
        type=int,
        default=330,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 2000 <= args.year <= date.today().year:
        raise ValueError(f"Unreasonable target year: {args.year}")
    if not 1 <= args.workers <= 8:
        raise ValueError("--workers must be between 1 and 8")

    stations = load_stations(args.metadata)
    available: list[StationFile] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                discover_station_file,
                station,
                year=args.year,
                cache_dir=args.cache_dir,
                timeout=args.timeout,
            ): station
            for station in stations
        }
        total = len(futures)
        for completed, future in enumerate(as_completed(futures), start=1):
            station = futures[future]
            try:
                available.append(future.result())
            except Exception as error:
                failures.append(
                    {
                        **asdict(station),
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
            if completed == total or completed % 25 == 0:
                print(
                    f"{completed}/{total} stations checked; "
                    f"{len(available)} archives available",
                    flush=True,
                )

    rows, station_audit = build_extension(
        available,
        year=args.year,
        minimum_days_per_station=args.minimum_days_per_station,
    )
    if not rows:
        raise ValueError("No stations passed the reporting-day quality gate")
    if max(row["Timestamp"] for row in rows) != f"{args.year}-12-31":
        raise ValueError("The retained extension does not reach 31 December")
    write_rows(args.output, rows)

    included = [item for item in station_audit if item["included"]]
    included_cities = {
        (str(item["state"]), str(item["city"]))
        for item in included
    }
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "CPCB CAAQMS Data Repository",
        "source_urls": {
            "listing": CPCB_LIST_URL,
            "download": CPCB_DOWNLOAD_URL,
        },
        "frequency": "1-day station files",
        "year": args.year,
        "date_start": min(row["Timestamp"] for row in rows),
        "date_end": max(row["Timestamp"] for row in rows),
        "quality_gate": {
            "minimum_core_pollutant_reporting_days_per_station": (
                args.minimum_days_per_station
            ),
            "core_pollutants": list(CORE_REPORTING_COLUMNS),
            "missing_values_imputed": False,
        },
        "stations_attempted": len(stations),
        "station_archives_available": len(available),
        "stations_included": len(included),
        "cities_included": len(included_cities),
        "rows": len(rows),
        "station_failures": failures,
        "station_audit": station_audit,
        "output": str(args.output.resolve()),
    }
    manifest_path = args.manifest or args.output.with_suffix(
        ".provenance.json"
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                key: manifest[key]
                for key in (
                    "year",
                    "date_start",
                    "date_end",
                    "stations_attempted",
                    "station_archives_available",
                    "stations_included",
                    "cities_included",
                    "rows",
                )
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
