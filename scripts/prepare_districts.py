"""Bundle India's district boundaries for place lookups.

Source: geoBoundaries gbOpen IND ADM2 (2021; Pathways Data Pvt. Ltd. and
lgdirectory.gov.in), Open Data Commons Open Database License 1.0. Download the
simplified GeoJSON, then:

    python scripts/prepare_districts.py geoBoundaries-IND-ADM2_simplified.geojson

Each district is tagged with the state that contains its interior point (from
public/india-states.geojson), so same-named districts in different states stay
distinct. Coordinates are rounded to 4 decimals (~11 m), well below the 9 km
WRF nests.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from shapely.geometry import mapping, shape


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATES_PATH = PROJECT_ROOT / "public" / "india-states.geojson"
OUTPUT_PATH = PROJECT_ROOT / "data" / "india-districts.geojson"


def _rounded(node):
    if isinstance(node, float):
        return round(node, 4)
    if isinstance(node, (list, tuple)):
        return [_rounded(item) for item in node]
    return node


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()

    states = [
        (feature["properties"]["state"], shape(feature["geometry"]).buffer(0))
        for feature in json.loads(STATES_PATH.read_text(encoding="utf-8"))["features"]
    ]
    source = json.loads(args.source.read_text(encoding="utf-8"))
    features = []
    for feature in source["features"]:
        geometry = shape(feature["geometry"]).buffer(0)
        point = geometry.representative_point()
        state = next((name for name, polygon in states if polygon.contains(point)), None)
        if state is None:
            # Coastal districts can sit just outside the simplified state layer.
            state = min(states, key=lambda item: item[1].distance(point))[0]
        features.append(
            {
                "type": "Feature",
                "properties": {"district": feature["properties"]["shapeName"], "state": state},
                "geometry": {
                    "type": mapping(geometry)["type"],
                    "coordinates": _rounded(mapping(geometry)["coordinates"]),
                },
            }
        )
    collection = {
        "type": "FeatureCollection",
        "source": "geoBoundaries gbOpen IND ADM2 (2021), ODbL 1.0",
        "features": features,
    }
    OUTPUT_PATH.write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(PROJECT_ROOT)}: {len(features)} districts, {OUTPUT_PATH.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
