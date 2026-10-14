from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = PROJECT_ROOT / "data" / "data_catalog.json"


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
    return payload


DATA_CATALOG = load_data_catalog()
AIR_QUALITY_START_YEAR = int(DATA_CATALOG["air_quality_start_year"])
AIR_QUALITY_END_YEAR = int(DATA_CATALOG["air_quality_end_year"])
DATA_SOURCES = list(DATA_CATALOG["sources"])
