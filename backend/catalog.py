from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import oaq


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = PROJECT_ROOT / "data" / "data_catalog.json"
OAQ_SOURCE_ID = "oaq_cpcb"


def load_data_catalog() -> dict[str, Any]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    start_year = payload.get("air_quality_start_year")
    end_year = payload.get("air_quality_end_year")
    sources = payload.get("sources")
    if (
        not isinstance(start_year, int)
        or not isinstance(end_year, int)
        or start_year > end_year
        or not isinstance(sources, list)
    ):
        raise RuntimeError("data_catalog.json is not valid")
    return _with_oaq_coverage(payload)


def _with_oaq_coverage(payload: dict[str, Any]) -> dict[str, Any]:
    """Take the station-data years and the OAQ entry from the synced cache.

    OAQ's coverage grows with every sync, so the catalog file only describes
    the source; dates, record counts and station counts come from
    data/oaq/provenance.json. Without the archive CSVs the period starts with
    OAQ's first year.
    """
    record = oaq.provenance()
    if not record:
        return payload
    first_year = int(str(record["date_start"])[:4])
    last_year = int(str(record["date_end"])[:4])
    has_archive = any(oaq.ARCHIVE_GLOB.parent.glob(oaq.ARCHIVE_GLOB.name))
    payload["air_quality_start_year"] = (
        min(payload["air_quality_start_year"], first_year) if has_archive else first_year
    )
    payload["air_quality_end_year"] = max(payload["air_quality_end_year"], last_year)
    for source in payload["sources"]:
        if source.get("id") == OAQ_SOURCE_ID:
            source["date_start"] = record["date_start"]
            source["date_end"] = record["date_end"]
            source["records"] = record["rows"]
            source["geographic_granularity"] = (
                f"{record['stations']} stations · {record['cities']} cities · "
                f"{record['states']} states/UTs"
            )
            source["synced_at_utc"] = record["generated_at_utc"]
    if not has_archive:
        archive_ids = {"cpcb_historical", "cpcb_2025"}
        payload["sources"] = [s for s in payload["sources"] if s.get("id") not in archive_ids]
    return payload


DATA_CATALOG = load_data_catalog()
AIR_QUALITY_START_YEAR = int(DATA_CATALOG["air_quality_start_year"])
AIR_QUALITY_END_YEAR = int(DATA_CATALOG["air_quality_end_year"])
DATA_SOURCES = list(DATA_CATALOG["sources"])
