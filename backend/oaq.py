"""OAQ (https://oaq.notf.in) client and the local cache of its CPCB data.

OAQ serves files from a Google Cloud CDN under https://oaq.notf.in/v1/. Every
request carries a signed URL prefix that the OAQ data broker issues for 24
hours in exchange for an API key (``oaq_key`` in .env). VayuChat uses the CPCB
provider:

    provider=cpcb/meta/devices.csv                     station registry
    provider=cpcb/history/aggregates/{id}/annual_{y}.json   station-day values

``scripts/sync_oaq.py`` mirrors these into ``data/oaq/``, and the database
loads that cache, so a question never waits on the network:

    data/oaq/cpcb_station_day.parquet   one row per station and IST day
    data/oaq/stations.csv               the OAQ station registry
    data/oaq/provenance.json            what was fetched, when, and coverage

OAQ publishes CPCB station-days from 2021. The bundled CPCB archive CSVs, when
present, fill only the station-years OAQ does not publish.
"""

from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import config  # noqa: F401  (loads .env into the environment)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OAQ_DIR = DATA_DIR / "oaq"
STATION_DAY_PATH = OAQ_DIR / "cpcb_station_day.parquet"
STATIONS_PATH = OAQ_DIR / "stations.csv"
PROVENANCE_PATH = OAQ_DIR / "provenance.json"
ARCHIVE_STATIONS_PATH = DATA_DIR / "stations.csv"
ARCHIVE_GLOB = DATA_DIR / "AQ_met_data*.csv"

CDN_ORIGIN = "https://oaq.notf.in"
BASE_URL = f"{CDN_ORIGIN}/v1/"
BROKER_URL = "https://us-central1-oaqdms.cloudfunctions.net/brokerData"
PROVIDER = "cpcb"
USER_AGENT = "VayuChat-oaq-sync/1.0 (Sustainability Lab, IIT Gandhinagar)"
# OAQ's earliest CPCB station-day year; earlier years return 404.
FIRST_YEAR = 2021

# OAQ parameter -> air_quality column. Units are OAQ's harmonised CPCB units,
# which match the archive's: µg/m³, CO mg/m³, NOx ppb, °C, %, m/s, deg, mm, W/m².
# OAQ's rf is the day's total (it matches the archive's TOT-RF, and is about
# 96 times its RF, the mean of 15-minute readings), so it is total_rainfall.
PARAMETERS = {
    "pm25": "pm25",
    "pm10": "pm10",
    "no2": "no2",
    "nox": "nox",
    "nh3": "nh3",
    "so2": "so2",
    "co": "co",
    "o3": "ozone",
    "temp": "temperature",
    "humid": "humidity",
    "ws": "wind_speed",
    "wd": "wind_direction",
    "rf": "total_rainfall",
    "sr": "solar_radiation",
}
# OAQ values VayuChat treats as missing: (column, first day, last day, why).
# Checked against CPCB's own archive over 600,000 shared station-days.
SUSPECT_VALUES = (
    (
        "co",
        "2025-01-01",
        "2026-02-28",
        "OAQ's CO falls to a monthly median of 0.00-0.33 mg/m³ (0.5-0.9 before "
        "and after; the CPCB archive stays at 0.64 for 2025), so it is not on "
        "the mg/m³ scale of the rest of the record",
    ),
)
STATION_COLUMNS = ("site_id", "state", "city", "station", "address", "latitude", "longitude")


def api_key() -> str:
    return (os.environ.get("oaq_key") or os.environ.get("OAQ_KEY") or "").strip()


def site_id(oaq_id: str) -> str:
    """OAQ station 1406 is the archive's site_1406."""
    return f"site_{oaq_id}"


class OAQError(RuntimeError):
    pass


class OAQClient:
    """Signed GETs against the OAQ CDN, re-signing when the session expires."""

    def __init__(self, key: str | None = None, timeout: int = 60) -> None:
        self.key = api_key() if key is None else key
        if not self.key:
            raise OAQError("No OAQ API key: set oaq_key in .env or the environment.")
        self.timeout = timeout
        self._signature = ""
        self._expires = 0.0
        self._lock = threading.Lock()

    def _request(self, url: str) -> tuple[int, bytes]:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def signature(self, *, renew: bool = False) -> str:
        with self._lock:
            if renew or not self._signature or time.time() > self._expires:
                query = urllib.parse.urlencode(
                    {"action": "api_session", "token": self.key, "domain": CDN_ORIGIN}
                )
                status, body = self._request(f"{BROKER_URL}?{query}")
                try:
                    signature = json.loads(body)["signature"]
                except (ValueError, KeyError, TypeError):
                    signature = ""
                if status != 200 or not signature:
                    raise OAQError(f"OAQ broker handshake failed (HTTP {status}).")
                self._signature = signature
                # Sessions last 24 hours; renew well before the edge rejects them.
                self._expires = time.time() + 20 * 3600
            return self._signature

    def get(self, path: str, *, retries: int = 3) -> bytes | None:
        """The file at ``path`` under /v1/, or None when OAQ has no such file."""
        renewed = False
        for attempt in range(retries):
            try:
                status, body = self._request(f"{BASE_URL}{path}?{self.signature()}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                if attempt == retries - 1:
                    raise OAQError(f"OAQ request failed for {path}: {error}") from error
                time.sleep(2**attempt)
                continue
            if status == 200:
                return body
            if status == 404:
                return None
            if status == 403 and not renewed:
                self.signature(renew=True)
                renewed = True
                continue
            if status >= 500 or status == 429:
                time.sleep(2**attempt)
                continue
            raise OAQError(f"OAQ returned HTTP {status} for {path}.")
        raise OAQError(f"OAQ request failed after {retries} attempts for {path}.")

    def devices(self) -> list[dict[str, str]]:
        body = self.get(f"provider={PROVIDER}/meta/devices.csv")
        if body is None:
            raise OAQError("OAQ has no CPCB device registry.")
        return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))

    def annual(self, oaq_id: str, year: int) -> dict[str, Any] | None:
        body = self.get(f"provider={PROVIDER}/history/aggregates/{oaq_id}/annual_{year}.json")
        return None if body is None else json.loads(body)


def read_station_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def station_records() -> list[dict[str, str]]:
    """OAQ's station registry, plus archive-only stations, one row per site_id.

    OAQ owns names and coordinates. The bundled archive registry supplies
    addresses, which OAQ does not publish, and the few stations OAQ no longer
    lists but the archive still has readings for.
    """
    archive = {row["site_id"]: row for row in read_station_csv(ARCHIVE_STATIONS_PATH)}
    merged: dict[str, dict[str, str]] = {}
    for row in read_station_csv(STATIONS_PATH):
        record = {column: row.get(column, "") for column in STATION_COLUMNS}
        if not record["address"]:
            record["address"] = archive.get(record["site_id"], {}).get("address", "")
        merged[record["site_id"]] = record
    for identifier, row in archive.items():
        merged.setdefault(identifier, {column: row.get(column, "") for column in STATION_COLUMNS})
    return sorted(merged.values(), key=lambda row: (row["state"], row["city"], row["station"]))


def provenance() -> dict[str, Any] | None:
    try:
        return json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def cache_age_hours() -> float | None:
    if not STATION_DAY_PATH.exists():
        return None
    return (time.time() - STATION_DAY_PATH.stat().st_mtime) / 3600
