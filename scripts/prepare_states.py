"""Build detailed state boundaries by merging the bundled district boundaries.

The district file (data/india-districts.geojson, geoBoundaries gbOpen IND
ADM2 2021 from the Local Government Directory, ODbL 1.0) is far more detailed
than the old simplified state layer: Delhi has ~1,800 vertices instead of 23,
and Chandigarh is its real shape instead of a box. Each district already
carries its state, so a state is the union of its districts.

Writes:

* data/india-states-detailed.geojson: full detail, used by the server for
  place lookups, grid masks and the outlines drawn on maps;
* public/india-states.geojson: simplified to ~0.002° (~200 m) for the
  browser's state overlay and state choropleths.

State names are kept exactly as before, so analytical tables still match.

    python scripts/prepare_states.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import shapely
from shapely.geometry import mapping, shape

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DISTRICTS_PATH = PROJECT_ROOT / "data" / "india-districts.geojson"
DETAILED_PATH = PROJECT_ROOT / "data" / "india-states-detailed.geojson"
BROWSER_PATH = PROJECT_ROOT / "public" / "india-states.geojson"
# Closes hairline gaps between neighbouring simplified district polygons.
SLIVER = 0.0005
BROWSER_TOLERANCE = 0.002


def _rounded(node, digits: int):
    if isinstance(node, float):
        return round(node, digits)
    if isinstance(node, (list, tuple)):
        return [_rounded(item, digits) for item in node]
    return node


def _feature(name: str, geometry, digits: int) -> dict:
    shaped = mapping(geometry)
    if shaped["type"] == "Polygon":
        shaped = {"type": "MultiPolygon", "coordinates": [shaped["coordinates"]]}
    return {
        "type": "Feature",
        "properties": {"state": name},
        "geometry": {"type": shaped["type"], "coordinates": _rounded(shaped["coordinates"], digits)},
    }


def main() -> None:
    districts = json.loads(DISTRICTS_PATH.read_text(encoding="utf-8"))["features"]
    order = [feature["properties"]["state"] for feature in json.loads(BROWSER_PATH.read_text(encoding="utf-8"))["features"]]
    grouped = defaultdict(list)
    for feature in districts:
        grouped[feature["properties"]["state"]].append(shape(feature["geometry"]).buffer(0))
    missing = [name for name in order if name not in grouped]
    if missing:
        raise SystemExit(f"No districts for: {missing}")

    detailed, browser = [], []
    for name in order:
        merged = shapely.union_all(grouped[name]).buffer(SLIVER).buffer(-SLIVER)
        detailed.append(_feature(name, merged, 4))
        browser.append(_feature(name, merged.simplify(BROWSER_TOLERANCE, preserve_topology=True), 4))
        print(f"{name:42} {shapely.get_num_coordinates(merged):>7} vertices")
    source = "Merged from geoBoundaries gbOpen IND ADM2 (2021) districts, ODbL 1.0, by scripts/prepare_states.py"
    for path, features in ((DETAILED_PATH, detailed), (BROWSER_PATH, browser)):
        path.write_text(
            json.dumps({"type": "FeatureCollection", "source": source, "features": features}, separators=(",", ":")),
            encoding="utf-8",
        )
        print(f"wrote {path.relative_to(PROJECT_ROOT)} ({path.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
