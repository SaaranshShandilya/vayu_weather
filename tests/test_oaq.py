"""OAQ sync and merge rules, run against a fake OAQ client (no network)."""

import csv
import json

import duckdb
import pytest

from backend import oaq, oaq_sync
from backend.oaq import OAQError


DEVICES = [
    {"id": "1406", "station_name": "Secretariat, Amaravati - APPCB", "latitude": "16.5", "longitude": "80.5",
     "city": "Amaravati", "state": "Andhra Pradesh"},
    {"id": "5405", "station_name": "Alandi, Pimpri-Chinchwad - IITM", "latitude": "18.6", "longitude": "73.9",
     "city": "Pimpri-Chinchwad", "state": "Maharashtra"},
]


def day(date, pm25, **values):
    return {"date": date, "pm25": pm25, "o3": values.get("o3"), "temp": values.get("temp"), "unknown": 1}


class FakeClient:
    def __init__(self, annual, devices=DEVICES):
        self._annual = annual
        self._devices = devices
        self.calls = []

    def devices(self):
        return self._devices

    def annual(self, oaq_id, year):
        self.calls.append((oaq_id, year))
        value = self._annual.get((oaq_id, year))
        if isinstance(value, Exception):
            raise value
        return None if value is None else {"id": oaq_id, "year": year, "data": value}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    paths = {
        "OAQ_DIR": tmp_path,
        "STATION_DAY_PATH": tmp_path / "cpcb_station_day.parquet",
        "STATIONS_PATH": tmp_path / "stations.csv",
        "PROVENANCE_PATH": tmp_path / "provenance.json",
    }
    for name, path in paths.items():
        monkeypatch.setattr(oaq, name, path)
        monkeypatch.setattr(oaq_sync, name, path, raising=False)
    monkeypatch.setattr(oaq, "ARCHIVE_STATIONS_PATH", tmp_path / "archive_stations.csv")
    return paths


def rows(path):
    return duckdb.sql(
        f"SELECT site_id, timestamp::VARCHAR, year, pm25, ozone, temperature, city "
        f"FROM read_parquet('{path}') ORDER BY site_id, timestamp"
    ).fetchall()


def test_sync_maps_oaq_parameters_onto_air_quality_columns(cache):
    client = FakeClient({
        ("1406", 2025): [day("2025-01-01", 79.92, o3=31.2, temp=21.7), day("2025-01-02", 72.0)],
        ("5405", 2025): [day("2025-01-01T00:00:00+05:30", 40.0)],
    })
    record = oaq_sync.sync([2025], client=client, log=lambda _: None)

    assert rows(cache["STATION_DAY_PATH"]) == [
        ("site_1406", "2025-01-01", 2025, 79.92, 31.2, 21.7, "Amaravati"),
        ("site_1406", "2025-01-02", 2025, 72.0, None, None, "Amaravati"),
        ("site_5405", "2025-01-01", 2025, 40.0, None, None, "Pimpri-Chinchwad"),
    ]
    assert record["rows"] == 3 and record["stations"] == 2
    assert (record["date_start"], record["date_end"]) == ("2025-01-01", "2025-01-02")
    assert json.loads(cache["PROVENANCE_PATH"].read_text(encoding="utf-8"))["rows"] == 3
    with cache["STATIONS_PATH"].open(encoding="utf-8") as handle:
        assert [row["site_id"] for row in csv.DictReader(handle)] == ["site_1406", "site_5405"]


def test_incremental_sync_replaces_only_station_years_oaq_returned(cache):
    oaq_sync.sync([2024, 2025], client=FakeClient({
        ("1406", 2024): [day("2024-06-01", 10.0)],
        ("1406", 2025): [day("2025-06-01", 20.0)],
        ("5405", 2025): [day("2025-06-01", 30.0)],
    }), log=lambda _: None)
    # 2025 revised for 1406; 5405 not published this time; 2024 not requested.
    oaq_sync.sync([2025], client=FakeClient({("1406", 2025): [day("2025-06-01", 21.0)]}), log=lambda _: None)

    assert [(r[0], r[1], r[3]) for r in rows(cache["STATION_DAY_PATH"])] == [
        ("site_1406", "2024-06-01", 10.0),
        ("site_1406", "2025-06-01", 21.0),
        ("site_5405", "2025-06-01", 30.0),
    ]


def test_sync_with_many_failures_leaves_the_cache_unchanged(cache):
    oaq_sync.sync([2025], client=FakeClient({("1406", 2025): [day("2025-06-01", 20.0)]}), log=lambda _: None)
    before = cache["STATION_DAY_PATH"].read_bytes()
    broken = FakeClient({("1406", 2025): OAQError("HTTP 500"), ("5405", 2025): OAQError("HTTP 500")})

    with pytest.raises(OAQError, match="cache left unchanged"):
        oaq_sync.sync([2025], client=broken, log=lambda _: None)
    assert cache["STATION_DAY_PATH"].read_bytes() == before


def test_first_sync_fetches_every_published_year_then_only_recent_ones(cache):
    import datetime as dt

    today = dt.date(2026, 11, 10)
    assert oaq_sync.default_years(today) == list(range(oaq.FIRST_YEAR, 2027))
    cache["STATION_DAY_PATH"].write_bytes(b"")
    assert oaq_sync.default_years(today) == [2025, 2026]


def test_station_registry_prefers_oaq_and_keeps_archive_only_stations(cache):
    with oaq.ARCHIVE_STATIONS_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=oaq.STATION_COLUMNS)
        writer.writeheader()
        writer.writerow({"site_id": "site_5405", "state": "Maharashtra", "city": "Pune", "station": "Alandi, Pune",
                         "address": "Alandi Road", "latitude": "18.6", "longitude": "73.9"})
        writer.writerow({"site_id": "site_9", "state": "Delhi", "city": "Delhi", "station": "Old",
                         "address": "", "latitude": "28.6", "longitude": "77.2"})
    oaq_sync._write_stations([oaq_sync._device_record(device) for device in DEVICES])

    merged = {row["site_id"]: row for row in oaq.station_records()}
    assert set(merged) == {"site_1406", "site_5405", "site_9"}
    assert merged["site_5405"]["city"] == "Pimpri-Chinchwad"
    assert merged["site_5405"]["address"] == "Alandi Road"
    assert merged["site_9"]["station"] == "Old"


def test_client_requires_a_key(monkeypatch):
    monkeypatch.delenv("oaq_key", raising=False)
    monkeypatch.delenv("OAQ_KEY", raising=False)
    with pytest.raises(OAQError, match="oaq_key"):
        oaq.OAQClient()


def test_archive_fills_only_station_years_oaq_does_not_publish(cache, tmp_path, monkeypatch):
    from backend.database import AirQualityDatabase

    oaq_sync.sync([2021], client=FakeClient({("1406", 2021): [day("2021-03-01", 50.0)]}), log=lambda _: None)
    from scripts.refresh_cpcb_station_data import OUTPUT_COLUMNS

    with (tmp_path / "AQ_met_data.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS[1:])
        writer.writeheader()
        for date, state, city, site, year, pm25, no in [
            ("2020-03-01", "Andhra Pradesh", "Amaravati", "site_1406", 2020, 90.0, 5.0),
            ("2021-03-01", "Andhra Pradesh", "Amaravati", "site_1406", 2021, 99.0, 5.0),
            ("2021-03-01", "Delhi", "Delhi", "site_9", 2021, 70.0, ""),
        ]:
            writer.writerow({"Timestamp": date, "State": state, "City": city, "Station": "Gone" if site == "site_9" else "Old name",
                             "site_id": site, "Year": year, "PM2.5 (µg/m³)": pm25, "NO (µg/m³)": no})
    monkeypatch.setattr("backend.database.ARCHIVE_GLOB", tmp_path / "AQ_met_data*.csv")
    monkeypatch.setattr("backend.database.STATION_DAY_PATH", cache["STATION_DAY_PATH"])
    database = AirQualityDatabase()
    database.initialize()

    _, result, _ = database.execute(
        "SELECT site_id, year, station, pm25, no, source FROM air_quality ORDER BY site_id, year"
    )
    assert result == [
        {"site_id": "site_1406", "year": 2020, "station": "Secretariat, Amaravati - APPCB",
         "pm25": 90.0, "no": 5.0, "source": "CPCB archive"},
        {"site_id": "site_1406", "year": 2021, "station": "Secretariat, Amaravati - APPCB",
         "pm25": 50.0, "no": None, "source": "OAQ"},
        {"site_id": "site_9", "year": 2021, "station": "Gone", "pm25": 70.0, "no": None, "source": "CPCB archive"},
    ]


def test_suspect_oaq_co_is_replaced_by_the_archive_or_left_missing(cache, tmp_path, monkeypatch):
    from backend.database import AirQualityDatabase
    from scripts.refresh_cpcb_station_data import OUTPUT_COLUMNS

    payload = [
        {"date": "2024-12-31", "pm25": 1.0, "co": 0.8},
        {"date": "2025-01-01", "pm25": 1.0, "co": 0.01},
        {"date": "2025-01-02", "pm25": 1.0, "co": 0.02},
    ]
    oaq_sync.sync([2024, 2025], client=FakeClient({
        ("1406", 2024): payload[:1], ("1406", 2025): payload[1:],
    }), log=lambda _: None)
    with (tmp_path / "AQ_met_data.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS[1:])
        writer.writeheader()
        writer.writerow({"Timestamp": "2025-01-01", "site_id": "site_1406", "Year": 2025, "CO (mg/m³)": 0.9})
    monkeypatch.setattr("backend.database.ARCHIVE_GLOB", tmp_path / "AQ_met_data*.csv")
    monkeypatch.setattr("backend.database.STATION_DAY_PATH", cache["STATION_DAY_PATH"])
    database = AirQualityDatabase()
    database.initialize()

    _, result, _ = database.execute("SELECT timestamp, co, source FROM air_quality ORDER BY timestamp")
    assert result == [
        {"timestamp": "2024-12-31", "co": 0.8, "source": "OAQ"},
        {"timestamp": "2025-01-01", "co": 0.9, "source": "OAQ"},
        {"timestamp": "2025-01-02", "co": None, "source": "OAQ"},
    ]


def test_sync_skips_the_partial_current_day(cache):
    import datetime as dt

    today = dt.datetime.now(oaq_sync.IST).date()
    yesterday = today - dt.timedelta(days=1)
    # Yesterday may fall in the previous year (1 January).
    annual = {("1406", today.year): [day(today.isoformat(), 20.0)]}
    annual.setdefault(("1406", yesterday.year), []).insert(0, day(yesterday.isoformat(), 10.0))
    oaq_sync.sync({yesterday.year, today.year}, client=FakeClient(annual), log=lambda _: None)
    assert [r[1] for r in rows(cache["STATION_DAY_PATH"])] == [yesterday.isoformat()]
