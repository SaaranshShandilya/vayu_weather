from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "refresh_cpcb_station_data.py"
)
SPEC = importlib.util.spec_from_file_location(
    "refresh_cpcb_station_data",
    SCRIPT_PATH,
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_station_file(path: Path, days: int, pm25: str = "50") -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["Timestamp", *MODULE.MEASUREMENT_COLUMNS],
        )
        writer.writeheader()
        for day in range(1, days + 1):
            row = {
                column: ""
                for column in ["Timestamp", *MODULE.MEASUREMENT_COLUMNS]
            }
            row["Timestamp"] = f"2025-01-{day:02d}"
            row["PM2.5 (µg/m³)"] = pm25
            writer.writerow(row)


def station_file(path: Path, site_id: str) -> object:
    return MODULE.StationFile(
        station=MODULE.Station(
            state="Delhi",
            city="Delhi",
            site_id=site_id,
            name=f"Station {site_id}",
        ),
        path=path,
        remote_path=f"/{path.name}",
    )


def test_station_rows_preserve_station_grain(tmp_path):
    path = tmp_path / "station.csv"
    write_station_file(path, 2)
    rows, reporting_days, metric_days = MODULE.station_rows(
        station_file(path, "site_1"),
        2025,
    )
    assert reporting_days == 2
    assert metric_days["PM2.5 (µg/m³)"] == 2
    assert rows[0]["City"] == "Delhi"
    assert rows[0]["Station"] == "Station site_1"
    assert rows[0]["Year"] == "2025"


def test_quality_gate_excludes_partial_station_year(tmp_path):
    complete = tmp_path / "complete.csv"
    partial = tmp_path / "partial.csv"
    write_station_file(complete, 3)
    write_station_file(partial, 1)
    rows, audit = MODULE.build_extension(
        [
            station_file(complete, "site_complete"),
            station_file(partial, "site_partial"),
        ],
        year=2025,
        minimum_days_per_station=2,
    )
    assert {row["site_id"] for row in rows} == {"site_complete"}
    assert {item["site_id"]: item["included"] for item in audit} == {
        "site_complete": True,
        "site_partial": False,
    }


def test_missing_core_pollutants_do_not_count_as_reporting_days(tmp_path):
    path = tmp_path / "weather-only.csv"
    write_station_file(path, 2, pm25="")
    rows, reporting_days, _ = MODULE.station_rows(
        station_file(path, "site_weather"),
        2025,
    )
    assert len(rows) == 2
    assert reporting_days == 0
