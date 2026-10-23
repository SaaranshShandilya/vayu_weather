"""Find a named place's boundary and coordinates from GeoJSON.

A place is looked up, in order, as:

1. a state or union territory in ``public/india-states.geojson``
   ("Delhi" is the National Capital Territory, not just New Delhi district);
2. a district in ``data/india-districts.geojson`` (geoBoundaries IND ADM2,
   2021), with common city-name aliases ("Bengaluru" -> Bangalore);
3. a city in the CPCB station registry that is not itself a district
   ("Hyderabad"): the district containing the centre of its stations.

Every match carries its boundary polygon, a representative point inside it
(the "latitude/longitude of the place"), the state, and a note saying which
boundary was used, so answers can say exactly what area they describe.
"""

from __future__ import annotations

import csv
import json
import threading
from dataclasses import dataclass
from pathlib import Path

import shapely
from shapely.geometry import Point, mapping, shape
from shapely.geometry.base import BaseGeometry


PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Detailed states merged from the district boundaries (scripts/prepare_states.py);
# the simplified browser layer is the fallback.
STATES_PATH = PROJECT_ROOT / "data" / "india-states-detailed.geojson"
if not STATES_PATH.exists():
    STATES_PATH = PROJECT_ROOT / "public" / "india-states.geojson"
DISTRICTS_PATH = PROJECT_ROOT / "data" / "india-districts.geojson"
STATIONS_PATH = PROJECT_ROOT / "data" / "stations.csv"

# City names people use -> the name in the boundary files.
ALIASES = {
    "new delhi": "delhi",
    "nct of delhi": "delhi",
    "delhi ncr": "delhi",
    "bengaluru": "bangalore",
    "bengaluru urban": "bangalore",
    "gurugram": "gurgaon",
    "bombay": "mumbai",
    "calcutta": "kolkata",
    "madras": "chennai",
    "trivandrum": "thiruvananthapuram",
    "poona": "pune",
    "baroda": "vadodara",
    "mysuru": "mysore",
    "mangaluru": "dakshina kannada",
    "mangalore": "dakshina kannada",
    "belagavi": "belgaum",
    "kalaburagi": "gulbarga",
    "prayagraj": "allahabad",
    "orissa": "odisha",
    "uttaranchal": "uttarakhand",
    "pondicherry": "puducherry",
    "j&k": "jammu and kashmir",
    "jammu & kashmir": "jammu and kashmir",
}


@dataclass(frozen=True)
class Place:
    name: str
    kind: str  # "state", "district" or "station city"
    state: str
    geometry: BaseGeometry
    latitude: float
    longitude: float
    note: str

    def outline(self, tolerance: float | None = None) -> list[list[list[float]]]:
        """Exterior rings as [lon, lat] lists, simplified for drawing.

        The default tolerance scales with the place (0.2% of its larger side,
        at least ~50 m), so Delhi keeps its detail and large states stay light.
        """
        if tolerance is None:
            west, south, east, north = self.geometry.bounds
            tolerance = max(0.0005, 0.002 * max(east - west, north - south))
        simple = self.geometry.simplify(tolerance, preserve_topology=True)
        polygons = getattr(simple, "geoms", [simple])
        return [
            [[round(x, 4), round(y, 4)] for x, y in polygon.exterior.coords]
            for polygon in polygons
            if not polygon.is_empty
        ]


def _key(name: str) -> str:
    key = " ".join(name.strip().casefold().replace("district", "").split())
    return ALIASES.get(key, key)


class PlaceIndex:
    """State, district and station-city boundaries, loaded once on first use."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False
        self.states: dict[str, tuple[str, BaseGeometry]] = {}
        self.districts: dict[str, list[tuple[str, str, BaseGeometry]]] = {}
        # Station-registry cities: name, centre of their monitors, monitor count.
        self.station_cities: dict[str, tuple[str, Point, int]] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            for feature in json.loads(STATES_PATH.read_text(encoding="utf-8"))["features"]:
                name = feature["properties"]["state"]
                self.states[_key(name)] = (name, shape(feature["geometry"]).buffer(0))
            for feature in json.loads(DISTRICTS_PATH.read_text(encoding="utf-8"))["features"]:
                district = feature["properties"]["district"]
                state = feature["properties"]["state"]
                geometry = shape(feature["geometry"]).buffer(0)
                self.districts.setdefault(_key(district), []).append((district, state, geometry))
            grouped: dict[str, list[tuple[float, float, str]]] = {}
            with STATIONS_PATH.open(encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    try:
                        lat, lon = float(row["latitude"]), float(row["longitude"])
                    except (TypeError, ValueError):
                        continue
                    grouped.setdefault(row["city"], []).append((lat, lon, row["state"]))
            for city, rows in grouped.items():
                lat = sum(r[0] for r in rows) / len(rows)
                lon = sum(r[1] for r in rows) / len(rows)
                self.station_cities[_key(city)] = (city, Point(lon, lat), len(rows))
            self._loaded = True

    @staticmethod
    def _place(name: str, kind: str, state: str, geometry: BaseGeometry, note: str) -> Place:
        point = geometry.representative_point()
        return Place(name, kind, state, geometry, round(point.y, 4), round(point.x, 4), note)

    def state(self, name: str) -> Place | None:
        self._load()
        match = self.states.get(_key(name))
        if match is None:
            return None
        state_name, geometry = match
        return self._place(state_name, "state", state_name, geometry, f"the {state_name} state/UT boundary")

    def find(self, name: str, state: str | None = None) -> Place | None:
        """A city, district or state by name; `state` disambiguates districts."""
        self._load()
        key = _key(name)
        # Delhi, Chandigarh and Puducherry are both a city and a UT: the UT
        # boundary is the whole city.
        if key in self.states:
            return self.state(name)
        matches = self.districts.get(key, [])
        if state:
            matches = [m for m in matches if m[1].casefold() == state.strip().casefold()] or matches
        if matches:
            district, district_state, geometry = matches[0]
            note = f"the {district} district boundary ({district_state})"
            if len(matches) > 1:
                note += f"; {len(matches)} districts share this name, pass a state to choose"
            return self._place(district, "district", district_state, geometry, note)
        city = self.station_cities.get(key)
        if city is not None:
            city_name, centre, _ = city
            for districts in self.districts.values():
                for district, district_state, geometry in districts:
                    if geometry.contains(centre):
                        return self._place(
                            city_name,
                            "station city",
                            district_state,
                            geometry,
                            f"{district} district ({district_state}), which contains "
                            f"{city_name}'s monitoring stations",
                        )
        return None

    def cities(self) -> list[tuple[str, Point, int]]:
        """Station-registry cities: name, centre of their monitors, monitor count."""
        self._load()
        return list(self.station_cities.values())

    def state_index_grid(self, lat, lon):
        """Each grid-cell centre's state index (-1 outside India) and the names."""
        import numpy as np

        self._load()
        lon2d, lat2d = np.meshgrid(lon, lat)
        index = np.full(lat2d.shape, -1, dtype=np.int16)
        names = []
        for number, (name, geometry) in enumerate(self.states.values()):
            names.append(name)
            inside = shapely.contains_xy(geometry, lon2d, lat2d)
            index[inside & (index < 0)] = number
        return index, names

    def cells_inside(self, place: Place, lon2d, lat2d):
        """Boolean mask of grid-cell centres inside the place's boundary."""
        return shapely.contains_xy(place.geometry, lon2d, lat2d)


places = PlaceIndex()


def to_geojson(place: Place) -> dict:
    return {"type": "Feature", "properties": {"name": place.name}, "geometry": mapping(place.geometry)}
