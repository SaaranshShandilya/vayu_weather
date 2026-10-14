import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_state_boundary_asset_has_expected_coverage() -> None:
    payload = json.loads((ROOT / "public" / "india-states.geojson").read_text())
    features = payload["features"]
    names = [feature["properties"]["state"] for feature in features]

    assert payload["type"] == "FeatureCollection"
    assert len(features) == 36
    assert len(names) == len(set(names))
    assert "Telangana" in names
    assert "Delhi" in names
    assert "Chandigarh" in names
    assert all(
        feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        for feature in features
    )


def test_boundary_source_is_disclosed_in_catalog() -> None:
    catalog = json.loads((ROOT / "data" / "data_catalog.json").read_text())
    sources = {source["id"]: source for source in catalog["sources"]}

    boundary = sources["state_boundaries"]
    assert boundary["records"] == 36
    assert "choropleths" in boundary["quality"]
