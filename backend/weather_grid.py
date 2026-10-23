"""Daily WRF weather analyses with xarray, across four nested domains.

``scripts/build_wrf_weather.py`` writes one NetCDF file per WRF domain, each a
``(time, lat, lon)`` Dataset tagged with its domain:

    d01  all India     27 km  0.25° grid
    d02  North India    9 km  0.08° grid
    d03  South India    9 km  0.08° grid
    d04  East India     9 km  0.08° grid

Each question is answered from the single finest domain whose grid fully
covers the place asked about, and the answer names that domain. Places come
from GeoJSON boundaries (``backend/places.py``): a state, a district, or the
district containing a city's monitors. All-India maps and national rankings
use d01 so every value comes from one consistent grid.

Every analysis is a labelled xarray operation on the cube, never a loop over
rows: a window is ``.sel(time=slice(...))``, a period mean is
``.mean("time")``, a place is ``.where(mask)``, a monthly series is
``.resample(time="MS")``, and a state ranking is ``.groupby(state_index)``.
Each variable is read from disk on first use and cached.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import xarray as xr
from pydantic import BaseModel, Field, model_validator
from shapely.geometry import box

from .models import QueryPlan
from .places import Place, places


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRIDDED_DIR = PROJECT_ROOT / "data" / "gridded"
DOMAIN_NAMES = ("d01", "d02", "d03", "d04")

WEATHER_TOOL_NAMES = frozenset(
    {
        "weather_grid_map",
        "weather_point_series",
        "weather_threshold_map",
        "weather_region_rank",
    }
)

WeatherVariable = Literal[
    "temperature",
    "max_temperature",
    "min_temperature",
    "humidity",
    "wind_speed",
    "max_wind_speed",
    "rainfall",
    "boundary_layer_height",
    "max_boundary_layer_height",
    "solar_radiation",
    "surface_pressure",
]


@dataclass(frozen=True)
class Variable:
    cube: str
    label: str
    unit: str
    scale: str
    # Rainfall is a daily total: periods are summed, not averaged.
    additive: bool = False


VARIABLES: dict[str, Variable] = {
    "temperature": Variable("t2_mean", "daily mean temperature", "°C", "temperature"),
    "max_temperature": Variable("t2_max", "daily maximum temperature", "°C", "temperature"),
    "min_temperature": Variable("t2_min", "daily minimum temperature", "°C", "temperature"),
    "humidity": Variable("rh_mean", "daily mean relative humidity", "%", "humidity"),
    "wind_speed": Variable("wind_mean", "daily mean 10 m wind speed", "m/s", "wind"),
    "max_wind_speed": Variable("wind_max", "daily maximum 10 m wind speed", "m/s", "wind"),
    "rainfall": Variable("rain", "daily rainfall", "mm", "rainfall", additive=True),
    "boundary_layer_height": Variable(
        "pblh_mean", "daily mean boundary layer height", "m", "pblh"
    ),
    "max_boundary_layer_height": Variable(
        "pblh_max", "daily maximum boundary layer height", "m", "pblh"
    ),
    "solar_radiation": Variable(
        "swdown_mean", "daily mean downward solar radiation", "W/m²", "radiation"
    ),
    "surface_pressure": Variable("psfc_mean", "daily mean surface pressure", "hPa", "pressure"),
}

MAX_CELLS_PER_AXIS = 130
# CAMS (83 × 86) animates at full resolution; larger WRF grids are halved.
MAX_ANIMATED_CELLS_PER_AXIS = 90
MAX_FRAMES = 24
HOTSPOT_SPACING_KM = 80
# Context shown around a city or district on its map, in degrees.
PLACE_MARGIN_DEGREES = 0.35
# States and UTs smaller than this (deg², e.g. Delhi, Goa, Sikkim) are mapped
# with their surroundings, like a district; larger ones are drawn alone.
SMALL_STATE_AREA = 2.0
# A finer domain is used for a place when it covers at least this share of
# the place's area; the answer then says which part is left out.
MIN_COVERAGE = 0.95
# Whole 9 km nests that can be mapped by name.
NEST_REGIONS = {"north_india": "d02", "south_india": "d03", "east_india": "d04"}
# Labelled on all-India maps. Others are labelled when the map zooms in.
NATIONAL_LANDMARKS = (
    "Delhi", "Mumbai", "Kolkata", "Chennai", "Bengaluru", "Hyderabad",
    "Ahmedabad", "Jaipur", "Lucknow", "Patna", "Bhopal", "Guwahati",
    "Thiruvananthapuram", "Srinagar", "Bhubaneswar", "Nagpur",
)


class Domain:
    """One WRF domain's daily Dataset, opened lazily, variables cached."""

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path
        # Re-entrant: field() holds it while dataset() opens the file.
        self._lock = threading.RLock()
        self._dataset: xr.Dataset | None = None
        self._loaded: dict[str, xr.DataArray] = {}
        with xr.open_dataset(path) as handle:
            self.attrs = dict(handle.attrs)
            self.lat = handle["lat"].values.astype("float64")
            self.lon = handle["lon"].values.astype("float64")
        self.step = float(self.attrs["resolution_degrees"])
        self.dx_km = float(self.attrs["native_dx_km"])
        self.region = str(self.attrs["region"])
        self._states: list[str] = []
        self.missing_days: list[str] = json.loads(self.attrs["missing_days"])
        self.extent = box(
            self.lon[0] - self.step / 2,
            self.lat[0] - self.step / 2,
            self.lon[-1] + self.step / 2,
            self.lat[-1] + self.step / 2,
        )

    @property
    def states(self) -> list[str]:
        """State names, in the order state_index() numbers them."""
        self.state_index()
        return self._states

    @property
    def label(self) -> str:
        return self.attrs.get("display_label") or f"{self.name} ({self.region}, {self.dx_km:g} km)"

    @property
    def model(self) -> str:
        """Short model name for messages, e.g. WRF or CAMS."""
        return self.attrs.get("model_short", "WRF")

    def dataset(self) -> xr.Dataset:
        if self._dataset is None:
            with self._lock:
                if self._dataset is None:
                    self._dataset = xr.open_dataset(self.path)
        return self._dataset

    def field(self, name: str) -> xr.DataArray:
        """A variable as float32 in memory, NaN on missing days."""
        cached = self._loaded.get(name)
        if cached is not None:
            return cached
        with self._lock:
            if name not in self._loaded:
                self._loaded[name] = self.dataset()[name].astype("float32").load()
            return self._loaded[name]

    def state_index(self) -> xr.DataArray:
        """Each cell's state, from the detailed GeoJSON boundaries.

        Computed when first needed rather than read from the file, so masks
        always match the outlines drawn on maps even for files built earlier.
        """
        cached = self._loaded.get("state_index")
        if cached is not None:
            return cached
        with self._lock:
            if "state_index" not in self._loaded:
                index, names = places.state_index_grid(self.lat, self.lon)
                self._states = names
                self._loaded["state_index"] = xr.DataArray(
                    index, coords={"lat": self.lat, "lon": self.lon}, dims=("lat", "lon")
                )
            return self._loaded["state_index"]


def _load_domains() -> dict[str, Domain]:
    domains = {}
    for name in DOMAIN_NAMES:
        path = GRIDDED_DIR / f"wrf_{name}_daily.nc"
        if path.exists():
            domains[name] = Domain(name, path)
    return domains


DOMAINS = _load_domains()
BASE = DOMAINS.get("d01")
FIRST_DAY = BASE.attrs["time_start"] if BASE else "2023-01-01"
LAST_DAY = BASE.attrs["time_end"] if BASE else "2024-12-31"
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


def nest_bounds(domain: "Domain") -> str:
    return (
        f"{domain.lat[0]:.1f}–{domain.lat[-1]:.1f}°N, "
        f"{domain.lon[0]:.1f}–{domain.lon[-1]:.1f}°E"
    )


def _describe_window() -> str:
    if not BASE:
        return "The WRF weather grid is not installed."
    nests = ", ".join(
        f"{domain.name} {domain.region} ({domain.dx_km:g} km)"
        for domain in DOMAINS.values()
        if domain is not BASE
    )
    return (
        f"Daily WRF model weather for India for IST days {FIRST_DAY} to "
        f"{LAST_DAY}: domain d01 covers all India at {BASE.dx_km:g} km"
        + (f", with finer nests {nests}" if nests else "")
        + ". The server picks the finest domain covering at least 95% of the place. "
        + "".join(
            f"region='{name}' maps the whole {DOMAINS[domain].dx_km:g} km {domain} box "
            f"({nest_bounds(DOMAINS[domain])}); "
            for name, domain in NEST_REGIONS.items()
            if domain in DOMAINS
        )
        + "use region only when the user names North, South or East India as an "
        "area, never for a state or city. This is a "
        "historical model simulation, not station observation and not a "
        f"forecast. The latest day available is {LAST_DAY}: 'the last N days' "
        f"or 'latest' means the N days ending {LAST_DAY}. Dates are "
        "YYYY-MM-DD; omit them for the whole period. Seasons: winter = "
        "Dec–Feb, pre-monsoon/summer = Mar–May, monsoon = Jun–Sep, "
        "post-monsoon = Oct–Nov."
    )


WINDOW_DESCRIPTION = _describe_window()


class WeatherArgs(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}


class WeatherWindowArgs(WeatherArgs):
    variable: WeatherVariable
    start_date: str = Field(default=FIRST_DAY, pattern=DATE_PATTERN)
    end_date: str = Field(default=LAST_DAY, pattern=DATE_PATTERN)

    @model_validator(mode="after")
    def validate_window(self):
        try:
            start = dt.date.fromisoformat(self.start_date)
            end = dt.date.fromisoformat(self.end_date)
        except ValueError as error:
            raise ValueError(f"invalid date: {error}") from None
        if start > end:
            raise ValueError("start_date must not be later than end_date")
        first, last = dt.date.fromisoformat(FIRST_DAY), dt.date.fromisoformat(LAST_DAY)
        # A window entirely outside the grid stays as asked, so the answer can
        # say so plainly; one that partly overlaps is clipped.
        if not (end < first or start > last):
            self.start_date = max(start, first).isoformat()
            self.end_date = min(end, last).isoformat()
        return self

    @property
    def outside_grid(self) -> bool:
        return self.end_date < FIRST_DAY or self.start_date > LAST_DAY


NestRegion = Literal["north_india", "south_india", "east_india"]


class WeatherRegionArgs(WeatherWindowArgs):
    state: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=80)
    region: NestRegion | None = None

    @model_validator(mode="after")
    def validate_region(self):
        # The most specific place wins: a city, then a state, then a region.
        # Routers often add a city's state as context.
        if self.city:
            self.state = self.region = None
        elif self.state:
            self.region = None
        return self


Statistic = Literal["mean", "total", "max", "min"]


def _check_statistic(statistic: str, variable: str) -> None:
    if statistic == "total" and not VARIABLES[variable].additive:
        raise ValueError("statistic='total' applies to rainfall only")


class WeatherGridMapArgs(WeatherRegionArgs):
    statistic: Literal["mean", "total", "max", "min", "sequence"] = "mean"
    order: Literal["highest", "lowest"] = "highest"

    @model_validator(mode="after")
    def validate_statistic(self):
        _check_statistic(self.statistic, self.variable)
        return self


class WeatherPointSeriesArgs(WeatherWindowArgs):
    city: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    region: NestRegion | None = None
    frequency: Literal["daily", "weekly", "monthly"] = "daily"

    @model_validator(mode="after")
    def validate_location(self):
        has_point = self.latitude is not None or self.longitude is not None
        if has_point and (self.latitude is None or self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        if not (self.city or self.state or self.region or has_point):
            raise ValueError("give a location: city, state, region, or latitude/longitude")
        # The most specific location wins: a coordinate, then a city, then a
        # state, then a region. Routers often add a city's state as context.
        if has_point:
            self.city = self.state = self.region = None
        elif self.city:
            self.state = self.region = None
        elif self.state:
            self.region = None
        return self


class WeatherThresholdArgs(WeatherRegionArgs):
    threshold: float = Field(ge=-100, le=100_000)
    direction: Literal["above", "below"] = "above"


class WeatherRegionRankArgs(WeatherWindowArgs):
    level: Literal["state", "city"] = "state"
    statistic: Statistic = "mean"
    order: Literal["highest", "lowest"] = "highest"
    limit: int = Field(default=10, ge=1, le=40)

    @model_validator(mode="after")
    def validate_statistic(self):
        _check_statistic(self.statistic, self.variable)
        return self


ARGUMENT_MODELS: dict[str, type[WeatherArgs]] = {
    "weather_grid_map": WeatherGridMapArgs,
    "weather_point_series": WeatherPointSeriesArgs,
    "weather_threshold_map": WeatherThresholdArgs,
    "weather_region_rank": WeatherRegionRankArgs,
}


@dataclass(frozen=True)
class GriddedOutput:
    plan: QueryPlan
    columns: list[str]
    rows: list[dict[str, Any]]
    grid: dict[str, Any] | None


@dataclass(frozen=True)
class Region:
    """Where a question applies, on the domain chosen for it."""

    domain: Domain
    lat: slice  # Crop shown on the map, as coordinate slices.
    lon: slice
    mask: xr.DataArray  # On the crop: True for cells the statistics use.
    label: str
    note: str  # Which boundary and cells were used, for the method note.
    place: Place | None = None
    # Show the cells around the place (outlined) rather than blanking them.
    context: bool = False
    # Which domain was used and why, including any part left out.
    domain_note: str = ""


def _distance_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    mean_lat = math.radians((lat_a + lat_b) / 2)
    dy = (lat_a - lat_b) * 111.32
    dx = (lon_a - lon_b) * 111.32 * math.cos(mean_lat)
    return math.hypot(dx, dy)


def _round(value: float, digits: int = 1) -> float | None:
    return None if not math.isfinite(value) else round(float(value), digits)


def _refusal(message: str) -> GriddedOutput:
    return GriddedOutput(
        plan=QueryPlan(in_scope=False, refusal=message),
        columns=[],
        rows=[],
        grid=None,
    )


def _wind_direction(u: float, v: float) -> float:
    """Meteorological direction the wind blows from, in degrees."""
    return round((math.degrees(math.atan2(-u, -v)) + 360) % 360, 0)


def _compass(degrees: float) -> str:
    points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return points[int((degrees + 22.5) // 45) % 8]


def finest_domain(
    bounds: tuple[float, float, float, float], domains: dict[str, Domain] | None = None
) -> Domain | None:
    """The finest-resolution domain whose grid fully covers (lon0, lat0, lon1, lat1)."""
    area = box(*bounds)
    covering = [domain for domain in (domains or DOMAINS).values() if domain.extent.covers(area)]
    if not covering:
        return None
    return min(covering, key=lambda domain: (domain.step, domain.extent.area))


def coverage(domain: Domain, geometry) -> float:
    """Share of a place's area inside a domain's grid, 0-1."""
    if geometry.area == 0:
        return 1.0 if domain.extent.covers(geometry) else 0.0
    return geometry.intersection(domain.extent).area / geometry.area


def best_domain(geometry, domains: dict[str, Domain] | None = None) -> tuple[Domain, float]:
    """The finest domain covering at least MIN_COVERAGE of the place.

    If none does (a place straddling the edge of India's grid), the domain
    covering the most of it is used, and its coverage is reported.
    """
    scored = [(domain, coverage(domain, geometry)) for domain in (domains or DOMAINS).values()]
    eligible = [item for item in scored if item[1] >= MIN_COVERAGE]
    if eligible:
        return min(eligible, key=lambda item: (item[0].step, -item[1], item[0].extent.area))
    return max(scored, key=lambda item: (item[1], -item[0].step))


def _left_out(domain: Domain, geometry) -> str:
    """Which side of a place lies outside a domain, e.g. 'north of 30.84°N'."""
    outside = geometry.difference(domain.extent)
    west, south, east, north = domain.extent.bounds
    o_west, o_south, o_east, o_north = outside.bounds
    sides = []
    if o_north > north + 1e-6:
        sides.append(f"north of {north:.2f}°N")
    if o_south < south - 1e-6:
        sides.append(f"south of {south:.2f}°N")
    if o_east > east + 1e-6:
        sides.append(f"east of {east:.2f}°E")
    if o_west < west - 1e-6:
        sides.append(f"west of {west:.2f}°E")
    return " and ".join(sides) or "at its edge"


def domain_note(
    domain: Domain, share: float, place_name: str, geometry, domains: dict[str, Domain] | None = None
) -> str:
    """Why this domain, and what of the place it leaves out."""
    domains = domains or DOMAINS
    note = f" Domain used: {domain.label}"
    finer = [d for d in domains.values() if d.step < domain.step and coverage(d, geometry) > 0]
    if share >= 0.999:
        note += ", which covers all of this place."
    else:
        note += (
            f", which covers {share * 100:.1f}% of {place_name}; the remaining "
            f"{(1 - share) * 100:.1f}% ({_left_out(domain, geometry)}) lies outside "
            "it and is not included."
        )
    if finer and domain.step == max(d.step for d in domains.values()):
        best = max(finer, key=lambda d: coverage(d, geometry))
        note += (
            f" The finer {best.label} covers only {coverage(best, geometry) * 100:.0f}% "
            f"of it, below the {MIN_COVERAGE * 100:.0f}% needed to use it."
        )
    return note


class WeatherGrid:
    """Answers weather questions from the domain datasets."""

    def __init__(self, domains: dict[str, Domain] | None = None, base: Domain | None = None) -> None:
        self._domains = domains
        self._base = base

    @property
    def domains(self) -> dict[str, Domain]:
        return self._domains if self._domains is not None else DOMAINS

    @property
    def base(self) -> Domain | None:
        """The domain covering all of India, used for national maps and ranks."""
        return self._base if self._base is not None or self._domains is not None else BASE

    @property
    def available(self) -> bool:
        return self.base is not None

    # The time step of this engine's data, for map captions.
    time_unit = "days"

    def _payload_source(self, domain: Domain) -> str:
        return f"WRF {domain.label} daily weather"

    # Geography -----------------------------------------------------------

    @staticmethod
    def _cells_inside(domain: Domain, place: Place, lat: slice, lon: slice) -> tuple[xr.DataArray, bool]:
        """Cells whose centres fall inside the place; else the nearest one (True)."""
        crop = domain.state_index().sel(lat=lat, lon=lon)
        lon2d, lat2d = np.meshgrid(crop["lon"].values, crop["lat"].values)
        inside = xr.DataArray(
            places.cells_inside(place, lon2d, lat2d), coords=crop.coords, dims=crop.dims
        )
        if bool(inside.any()):
            return inside, False
        nearest = crop.sel(lat=place.latitude, lon=place.longitude, method="nearest")
        return (crop["lat"] == nearest["lat"]) & (crop["lon"] == nearest["lon"]), True

    def _state_region(self, name: str) -> Region | str:
        place = places.state(name)
        if place is None:
            return (
                f"'{name}' is not a recognised Indian state or union territory. "
                "Use its full name, for example Uttar Pradesh."
            )
        domain, share = best_domain(place.geometry, self.domains)
        index = domain.states.index(place.name) if place.name in domain.states else -1
        cells = domain.state_index() == index
        how = f"the {int(cells.sum())} grid cells of {domain.label} whose centres fall inside {place.note}"
        if not bool(cells.any()):
            # Smaller than a cell (Delhi on the 0.4° CAMS grid, island UTs):
            # use the cell nearest the place, as for a small district.
            nearest = cells.sel(lat=place.latitude, lon=place.longitude, method="nearest")
            cells = (cells["lat"] == nearest["lat"]) & (cells["lon"] == nearest["lon"])
            how = (
                f"the {domain.label} grid cell nearest the centre of {place.note}, which "
                f"is smaller than one {domain.step}° cell"
            )
        lat_values = cells["lat"].where(cells.any("lon"), drop=True)
        lon_values = cells["lon"].where(cells.any("lat"), drop=True)
        small = place.geometry.area < SMALL_STATE_AREA
        pad = max(PLACE_MARGIN_DEGREES, 2.5 * domain.step) if small else domain.step
        lat = slice(float(lat_values.min()) - pad, float(lat_values.max()) + pad)
        lon = slice(float(lon_values.min()) - pad, float(lon_values.max()) + pad)
        return Region(
            domain,
            lat,
            lon,
            cells.sel(lat=lat, lon=lon),
            place.name,
            how,
            place,
            context=small,
            domain_note=domain_note(domain, share, place.name, place.geometry, self.domains),
        )

    def _place_region(self, name: str) -> Region | str:
        place = places.find(name)
        if place is None:
            return (
                f"No state, district or monitored city called '{name}' was found "
                "in the boundary files, so VayuChat cannot place it on the "
                "weather grid. Name a state or district, or give a latitude and "
                "longitude."
            )
        if place.kind == "state":
            return self._state_region(place.name)
        lon0, lat0, lon1, lat1 = place.geometry.bounds
        domain, share = best_domain(place.geometry, self.domains)
        # Context around the place: at least 2.5 cells, so coarse grids show some.
        margin = max(PLACE_MARGIN_DEGREES, 2.5 * domain.step)
        view = (lon0 - margin, lat0 - margin, lon1 + margin, lat1 + margin)
        lat = slice(view[1], view[3])
        lon = slice(view[0], view[2])
        inside, nearest_only = self._cells_inside(domain, place, lat, lon)
        cells = int(inside.sum())
        how = (
            f"the {domain.label} grid cell nearest the centre of {place.note}, "
            f"which is smaller than one {domain.step}° cell"
            if nearest_only
            else f"the {cells} grid cell{'s' if cells != 1 else ''} of {domain.label} "
            f"whose centres fall inside {place.note}"
        )
        return Region(
            domain,
            lat,
            lon,
            inside,
            place.name,
            how,
            place,
            context=True,
            domain_note=domain_note(domain, share, place.name, place.geometry, self.domains),
        )

    def _nest_region(self, name: str) -> Region | str:
        """A whole 9 km nest, e.g. North India = all of d02 inside India."""
        domain = DOMAINS.get(NEST_REGIONS[name])
        if domain is None:
            return f"The {name.replace('_', ' ')} WRF domain is not installed."
        india = domain.state_index() >= 0
        label = name.replace("_", " ").title()
        return Region(
            domain,
            slice(None),
            slice(None),
            india,
            label,
            f"all {int(india.sum()):,} grid cells of {domain.label} inside India's state boundaries",
            domain_note=(
                f" Domain used: {domain.label}, mapped whole: {label} here means this "
                f"domain's box, {nest_bounds(domain)}; parts of {label} outside it are not included."
            ),
        )

    def _region(self, state: str | None, city: str | None, region: str | None = None) -> Region | str:
        if city:
            return self._place_region(city)
        if state:
            return self._state_region(state)
        if region:
            return self._nest_region(region)
        base = self.base
        india = base.state_index() >= 0
        return Region(
            base,
            slice(None),
            slice(None),
            india,
            "India",
            f"all grid cells of {base.label} inside India's state boundaries",
            domain_note=(
                f" Domain used: {base.label}, the only domain covering all of India."
                if len(self.domains) > 1
                else f" Grid: {base.label}."
            ),
        )

    def _nearest_city(self, latitude: float, longitude: float) -> tuple[str, float]:
        best_name, best_distance = "", math.inf
        for city, centre, _ in places.cities():
            distance = _distance_km(latitude, longitude, centre.y, centre.x)
            if distance < best_distance:
                best_name, best_distance = city, distance
        return best_name, best_distance

    def _landmarks(self, region: Region, lats: np.ndarray, lons: np.ndarray) -> list[dict[str, Any]]:
        inside = [
            (city, centre, count)
            for city, centre, count in places.cities()
            if lats[0] <= centre.y <= lats[-1] and lons[0] <= centre.x <= lons[-1]
        ]
        if region.label == "India":
            chosen = [item for item in inside if item[0] in NATIONAL_LANDMARKS]
        else:
            chosen = sorted(inside, key=lambda item: (-item[2], item[0]))[:8]
        return [
            {"name": city, "latitude": round(centre.y, 4), "longitude": round(centre.x, 4)}
            for city, centre, _ in sorted(chosen, key=lambda item: item[0])
        ]

    # Time ----------------------------------------------------------------

    @staticmethod
    def _period(args: WeatherWindowArgs) -> slice:
        return slice(args.start_date, args.end_date)

    @staticmethod
    def _window_label(args: WeatherWindowArgs) -> str:
        start = dt.date.fromisoformat(args.start_date)
        end = dt.date.fromisoformat(args.end_date)
        if start == end:
            return start.strftime("%d %b %Y")
        return f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"

    @staticmethod
    def _missing_days(domain: Domain, args: WeatherWindowArgs) -> list[str]:
        return [day for day in domain.missing_days if args.start_date <= day <= args.end_date]

    def _gap_note(self, domain: Domain, args: WeatherWindowArgs) -> str:
        missing = self._missing_days(domain, args)
        if not missing:
            return ""
        if len(missing) == 1:
            return (
                f" {missing[0]} has no WRF output (damaged source files) and is "
                "excluded; totals cover only the remaining days."
            )
        return (
            f" {len(missing)} days in this window ({', '.join(missing)}) have no "
            "WRF output (damaged source files) and are excluded; totals cover "
            "only the remaining days."
        )

    def _empty_window(self, domain: Domain, args: WeatherWindowArgs) -> str | None:
        days = pd.date_range(args.start_date, args.end_date, freq="D")
        if len(days) and len(self._missing_days(domain, args)) >= len(days):
            return (
                f"The WRF output for {self._window_label(args)} is missing "
                "(damaged source files), so there is no value to report."
            )
        return None

    # Formatting ----------------------------------------------------------

    @staticmethod
    def _source_note(domain: Domain) -> str:
        return (
            f" Source: WRF v4.7.1 simulation, domain {domain.label}, reduced to "
            f"IST days on a {domain.step}° grid. Model output, not station "
            "observation."
        )

    @staticmethod
    def _reduce(cube: xr.DataArray, statistic: str) -> xr.DataArray:
        """Collapse the time axis, skipping missing days."""
        if statistic == "total":
            return cube.sum("time", min_count=1)
        if statistic == "max":
            return cube.max("time")
        if statistic == "min":
            return cube.min("time")
        return cube.mean("time")

    @staticmethod
    def _statistic_words(statistic: str, variable: Variable) -> str:
        """'mean 10 m wind speed', 'total rainfall', 'highest daily maximum temperature'."""
        label = variable.label
        if statistic == "total":
            return "total " + label.removeprefix("daily ")
        if statistic == "mean":
            return "mean " + label.removeprefix("daily mean ")
        return {"max": "highest ", "min": "lowest "}[statistic] + label

    def _grid_payload(
        self,
        frames: list[tuple[str, xr.DataArray]],
        region: Region,
        *,
        value_label: str,
        unit: str,
        scale: str,
        scale_max: float | None = None,
    ) -> dict[str, Any]:
        domain = region.domain
        # A large state or the country is drawn alone; a city, district or
        # small UT keeps its surroundings, with its boundary outlined.
        masked = not region.context
        cap = MAX_CELLS_PER_AXIS if len(frames) == 1 else MAX_ANIMATED_CELLS_PER_AXIS
        first = frames[0][1]
        factor = max(1, math.ceil(max(first.sizes["lat"], first.sizes["lon"]) / cap))
        # One decimal even for animations: whole units band narrow ranges
        # (a 6 °C winter week would show six colours). <= ~0.5 MB at the cap.
        digits = 1
        rendered = []
        coarse = first
        for label, field in frames:
            if masked:
                field = field.where(region.mask)
            coarse = field.coarsen(lat=factor, lon=factor, boundary="trim").mean() if factor > 1 else field
            values = coarse.values.ravel()
            rendered.append(
                {
                    "label": label,
                    "values": [None if not math.isfinite(v) else round(float(v), digits) for v in values],
                }
            )
        lats = first["lat"].values
        lons = first["lon"].values
        payload = {
            "latitudes": [round(float(v), 4) for v in coarse["lat"].values],
            "longitudes": [round(float(v), 4) for v in coarse["lon"].values],
            "frames": rendered,
            "value_label": value_label,
            "unit": unit,
            "scale": scale,
            "scale_max": scale_max,
            "landmarks": self._landmarks(region, lats, lons),
            "source_shape": [
                int(domain.dataset().sizes["time"]),
                len(domain.lat),
                len(domain.lon),
            ],
            "resolution_degrees": round(domain.step * factor, 4),
            "boundaries": True,
            "source_label": self._payload_source(domain),
            "time_unit": self.time_unit,
            "domain": domain.name,
            "domain_label": domain.label,
            "outline": region.place.outline() if region.place is not None else [],
            "outline_label": region.place.name if region.place is not None else "",
        }
        return payload

    def _extremes(
        self,
        field: xr.DataArray,
        region: Region,
        value_key: str,
        order: str,
        limit: int = 10,
        digits: int = 1,
    ) -> list[dict[str, Any]]:
        """Most extreme cells, spread out so one weather system is not listed ten times."""
        cells = field.where(region.mask).stack(cell=("lat", "lon")).dropna("cell")
        cells = cells.sortby(cells, ascending=order != "highest")
        states = region.domain.state_index().sel(lat=field["lat"], lon=field["lon"])
        spacing = 0 if region.context else HOTSPOT_SPACING_KM
        picked: list[tuple[float, float]] = []
        rows = []
        for lat, lon, value in zip(cells["lat"].values, cells["lon"].values, cells.values):
            lat, lon = float(lat), float(lon)
            if spacing and any(_distance_km(lat, lon, a, b) < spacing for a, b in picked):
                continue
            picked.append((lat, lon))
            city, distance = self._nearest_city(lat, lon)
            number = int(states.sel(lat=lat, lon=lon))
            rows.append(
                {
                    "rank": len(rows) + 1,
                    "latitude": round(lat, 2),
                    "longitude": round(lon, 2),
                    value_key: _round(float(value), digits),
                    "state": region.domain.states[number] if number >= 0 else "outside India",
                    "nearest_city": city,
                    "distance_km": round(distance, 0),
                }
            )
            if len(rows) == limit:
                break
        return rows

    # Analyses ------------------------------------------------------------

    def run(self, name: str, arguments: WeatherArgs) -> GriddedOutput:
        if not self.available:
            return _refusal(
                "The WRF weather grid is not installed. Run "
                "scripts/build_wrf_weather.py to create it."
            )
        if isinstance(arguments, WeatherWindowArgs) and arguments.outside_grid:
            return _refusal(
                f"The WRF weather grid covers {FIRST_DAY} to {LAST_DAY}, so "
                f"{self._window_label(arguments)} is outside it. Measured "
                "station weather (temperature, humidity, wind, rainfall) "
                "for 2017–2025 is available through station rankings and "
                "pollution–weather relationships."
            )
        if name == "weather_grid_map":
            return self._grid_map(arguments)  # type: ignore[arg-type]
        if name == "weather_point_series":
            return self._point_series(arguments)  # type: ignore[arg-type]
        if name == "weather_threshold_map":
            return self._threshold(arguments)  # type: ignore[arg-type]
        if name == "weather_region_rank":
            return self._region_rank(arguments)  # type: ignore[arg-type]
        raise ValueError(f"Unknown weather analysis: {name}")

    def _sequence_frames(
        self, cube: xr.DataArray, variable: Variable
    ) -> tuple[list[tuple[str, xr.DataArray]], int]:
        days = cube.sizes["time"]
        block = max(1, math.ceil(days / MAX_FRAMES))
        statistic = "total" if variable.additive else "mean"
        frames = []
        for offset in range(0, days, block):
            chunk = cube.isel(time=slice(offset, offset + block))
            first = pd.Timestamp(chunk["time"].values[0])
            if block == 1:
                label = first.strftime("%d %b %Y")
            else:
                last = pd.Timestamp(chunk["time"].values[-1])
                label = f"{first.strftime('%d %b')} – {last.strftime('%d %b %Y')}"
            frames.append((label, self._reduce(chunk, statistic)))
        return frames, block

    def _grid_map(self, args: WeatherGridMapArgs) -> GriddedOutput:
        region = self._region(args.state, args.city, args.region)
        if isinstance(region, str):
            return _refusal(region)
        domain = region.domain
        empty = self._empty_window(domain, args)
        if empty:
            return _refusal(empty)
        variable = VARIABLES[args.variable]
        cube = domain.field(variable.cube).sel(
            time=self._period(args), lat=region.lat, lon=region.lon
        )
        mask = region.mask.sel(lat=cube["lat"], lon=cube["lon"])
        region = replace(region, mask=mask)
        period = self._window_label(args)
        days = cube.sizes["time"]

        if args.statistic == "sequence":
            frames, block = self._sequence_frames(cube, variable)
            reduction = "total" if variable.additive else "mean"
            field = self._reduce(cube, reduction)
            value_key = f"{reduction}_{args.variable}"
            stat_words = self._statistic_words(reduction, variable)
            step = "day" if block == 1 else f"{block}-day {'total' if variable.additive else 'mean'}"
            title = f"{variable.label.capitalize()} by {step}, {region.label}, {period}"
            value_label = variable.label.capitalize()
        else:
            field = self._reduce(cube, args.statistic)
            value_key = f"{args.statistic}_{args.variable}"
            stat_words = self._statistic_words(args.statistic, variable)
            frames = [(period, field)]
            title = f"{stat_words.capitalize()}, {region.label}, {period}"
            value_label = stat_words.capitalize()
            block = 0

        unit = variable.unit
        values = field.where(mask)
        rows = self._extremes(field, region, value_key, args.order)
        if not rows:
            return _refusal(f"No grid cells fall inside {region.label}.")
        top = rows[0]
        cells = int(values.count())
        summary = (
            f"Across {region.label}, the {stat_words} for {period} averages "
            f"{float(values.mean()):.1f} {unit}"
            + (
                f" over {cells:,} grid cells, ranging from {float(values.min()):.1f} "
                f"to {float(values.max()):.1f} {unit}. The {args.order} cell is "
                f"{top[value_key]} {unit} at {top['latitude']:.2f}°N, "
                f"{top['longitude']:.2f}°E in {top['state']}, "
                f"{top['distance_km']:.0f} km from {top['nearest_city']}."
                if cells > 1
                else f" in its grid cell at {top['latitude']:.2f}°N, {top['longitude']:.2f}°E."
            )
            + region.domain_note
            + self._gap_note(domain, args)
        )
        if args.statistic == "sequence":
            reduction_text = (
                "shows each day"
                if block == 1
                else f"groups the days into {block}-day blocks"
            ) + f" ({len(frames)} frames); the table ranks the {stat_words} over the window"
        elif args.statistic == "total":
            reduction_text = "summed the daily totals"
        else:
            reduction_text = f"took the {stat_words.split(' ')[0]} over the days"
        method = (
            f"Took the WRF {variable.label} cube ({days} days × "
            f"{cube.sizes['lat']} × {cube.sizes['lon']} cells) for {period} and "
            f"{reduction_text} with one xarray operation. Statistics use "
            f"{region.note}."
            + (
                " The map also shows the surrounding cells for context, with the boundary outlined."
                if region.context
                else ""
            )
            + region.domain_note
            + self._source_note(domain)
        )
        grid = self._grid_payload(frames, region, value_label=value_label, unit=unit, scale=variable.scale)
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="grid_heatmap",
                title=title,
                x_key="rank",
                y_keys=[value_key],
            ),
            columns=list(rows[0]),
            rows=rows,
            grid=grid,
        )

    def _locate(self, args: WeatherPointSeriesArgs) -> tuple[Domain, xr.DataArray, str, str, str] | str:
        """The domain, a full-grid cell mask, the place name, a method phrase, and the domain note."""
        if args.state or args.city or args.region:
            region = self._region(args.state, args.city, args.region)
            if isinstance(region, str):
                return region
            mask = region.mask.reindex_like(region.domain.state_index(), fill_value=False)
            name = f"{region.label} (area average)" if int(mask.sum()) > 1 else region.label
            return region.domain, mask, name, f"the area mean of {region.note}", region.domain_note
        latitude, longitude = float(args.latitude), float(args.longitude)  # type: ignore[arg-type]
        domain = finest_domain((longitude, latitude, longitude, latitude), self.domains)
        name = f"{latitude:.3f}°N, {longitude:.3f}°E"
        if domain is None:
            return f"{name} is outside the {self.base.model} grid."
        grid = domain.state_index()
        cell = grid.sel(lat=latitude, lon=longitude, method="nearest")
        mask = (grid["lat"] == cell["lat"]) & (grid["lon"] == cell["lon"])
        distance = _distance_km(latitude, longitude, float(cell["lat"]), float(cell["lon"]))
        return (
            domain,
            mask,
            name,
            f"the {domain.label} grid cell nearest the given coordinate (cell centre "
            f"{float(cell['lat']):.2f}°N, {float(cell['lon']):.2f}°E, {distance:.0f} km "
            f"away). A cell value is an average over a {domain.step}° cell, not a point measurement",
            f" Domain used: {domain.label}, the finest WRF domain containing this point.",
        )

    @staticmethod
    def _resample(series: xr.DataArray, frequency: str, additive: bool) -> tuple[xr.DataArray, xr.DataArray]:
        """Period values and the number of days with data in each period."""
        if frequency == "daily":
            return series, series.notnull().astype(int)
        rule = "MS" if frequency == "monthly" else "W-MON"
        grouped = series.resample(time=rule, label="left", closed="left")
        values = grouped.sum(min_count=1) if additive else grouped.mean()
        counts = series.notnull().resample(time=rule, label="left", closed="left").sum()
        return values, counts

    def _point_series(self, args: WeatherPointSeriesArgs) -> GriddedOutput:
        located = self._locate(args)
        if isinstance(located, str):
            return _refusal(located)
        domain, mask, place, origin, note = located
        empty = self._empty_window(domain, args)
        if empty:
            return _refusal(empty)
        variable = VARIABLES[args.variable]
        cube = domain.field(variable.cube).sel(time=self._period(args))
        series = cube.where(mask).mean(["lat", "lon"])
        valid = int(series.count())
        wind = args.variable in {"wind_speed", "max_wind_speed"}
        if wind:
            u_series = domain.field("u10_mean").sel(time=self._period(args)).where(mask).mean(["lat", "lon"])
            v_series = domain.field("v10_mean").sel(time=self._period(args)).where(mask).mean(["lat", "lon"])
            u_period, _ = self._resample(u_series, args.frequency, additive=False)
            v_period, _ = self._resample(v_series, args.frequency, additive=False)

        values, counts = self._resample(series, args.frequency, variable.additive)
        value_key = args.variable
        column = "date" if args.frequency == "daily" else "period"
        rows = []
        for index, stamp in enumerate(values["time"].values):
            stamp = pd.Timestamp(stamp)
            key = stamp.strftime("%Y-%m") if args.frequency == "monthly" else stamp.strftime("%Y-%m-%d")
            row: dict[str, Any] = {column: key, value_key: _round(float(values[index]))}
            if args.frequency != "daily":
                row["days"] = int(counts[index])
            if wind and math.isfinite(float(u_period[index])):
                direction = _wind_direction(float(u_period[index]), float(v_period[index]))
                row["wind_direction"] = direction
                row["wind_from"] = _compass(direction)
            rows.append(row)

        period = self._window_label(args)
        aggregate = "total" if variable.additive else "mean"
        overall = float(series.sum(min_count=1) if variable.additive else series.mean())
        preposition = "Across" if int(mask.sum()) > 1 else "At"
        summary = (
            f"{preposition} {place}, the {self._statistic_words(aggregate, variable)} for {period} is "
            f"{overall:.1f} {variable.unit}"
            + (
                f" ({float(series.mean()):.1f} {variable.unit} per day)"
                if variable.additive and valid > 1
                else ""
            )
            + "."
        )
        if valid > 1:
            peak = series.idxmax("time")
            low = series.idxmin("time")
            summary += (
                f" The highest day is {pd.Timestamp(peak.values).strftime('%d %b %Y')} at "
                f"{float(series.sel(time=peak)):.1f} {variable.unit} and the lowest is "
                f"{pd.Timestamp(low.values).strftime('%d %b %Y')} at "
                f"{float(series.sel(time=low)):.1f} {variable.unit}."
            )
        if wind:
            direction = _wind_direction(float(u_series.mean()), float(v_series.mean()))
            summary += f" The mean wind blows from the {_compass(direction)} ({direction:.0f}°)."
        summary += note + self._gap_note(domain, args)
        method = (
            f"Read {origin} straight from the WRF {variable.label} cube for "
            f"{valid} days"
            + (
                f", then {'summed' if variable.additive else 'averaged'} the days "
                f"in each {'week (Monday start)' if args.frequency == 'weekly' else 'month'} "
                "with xarray resample"
                if args.frequency != "daily"
                else ""
            )
            + (". Wind direction is from the mean 10 m wind vector" if wind else "")
            + "."
            + note
            + self._source_note(domain)
        )
        columns = list(rows[0]) if rows else []
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="bar" if variable.additive and args.frequency != "daily" else "line",
                title=f"{variable.label.capitalize()} at {place}, {period} ({domain.name})",
                x_key=column,
                y_keys=[value_key],
            ),
            columns=columns,
            rows=rows,
            grid=None,
        )

    def _threshold(self, args: WeatherThresholdArgs) -> GriddedOutput:
        region = self._region(args.state, args.city, args.region)
        if isinstance(region, str):
            return _refusal(region)
        domain = region.domain
        empty = self._empty_window(domain, args)
        if empty:
            return _refusal(empty)
        variable = VARIABLES[args.variable]
        cube = domain.field(variable.cube).sel(time=self._period(args), lat=region.lat, lon=region.lon)
        mask = region.mask.sel(lat=cube["lat"], lon=cube["lon"])
        region = replace(region, mask=mask)
        meets = cube > args.threshold if args.direction == "above" else cube < args.threshold
        day_count = meets.sum("time").astype("float32")
        present = cube.where(mask).notnull().any(["lat", "lon"])
        cells = int(mask.sum())
        daily_share = (meets.where(mask).sum(["lat", "lon"]) / cells * 100).where(present)

        monthly = daily_share.resample(time="MS")
        rows = []
        for (stamp, shares) in monthly:
            if int(shares.count()) == 0:
                continue
            rows.append(
                {
                    "period": pd.Timestamp(stamp).strftime("%Y-%m"),
                    "days": int(shares.count()),
                    "mean_area_meeting_pct": _round(float(shares.mean())),
                    "peak_area_meeting_pct": _round(float(shares.max())),
                    "peak_day": pd.Timestamp(shares.idxmax("time").values).strftime("%Y-%m-%d"),
                }
            )
        counts = day_count.where(mask)
        word = "above" if args.direction == "above" else "below"
        condition = f"{variable.label} {word} {args.threshold:g} {variable.unit}"
        period = self._window_label(args)
        most = counts.where(counts == counts.max(), drop=True)
        lat = float(most["lat"].values[0])
        lon = float(most["lon"].values[0])
        city, distance = self._nearest_city(lat, lon)
        summary = (
            f"For {period} ({int(present.sum())} days), {float((counts > 0).sum() / counts.count()) * 100:.0f}% of "
            f"{region.label} has at least one day with {condition}. A grid cell "
            f"averages {float(counts.mean()):.1f} such days; the most is {float(counts.max()):.0f} "
            f"days at {lat:.2f}°N, {lon:.2f}°E, {distance:.0f} km from {city}. "
            f"On a typical day {float(daily_share.mean()):.1f}% of the area meets the condition."
            + region.domain_note
            + self._gap_note(domain, args)
        )
        method = (
            f"Compared every daily value in the WRF {variable.label} cube "
            f"({cube.sizes['time']} days × {cube.sizes['lat']} × {cube.sizes['lon']} cells) with "
            f"{args.threshold:g} {variable.unit} in one xarray operation, then "
            "counted qualifying days per cell (the map) and the qualifying share "
            f"of {region.note} per day, summarised by month (the table)."
            + region.domain_note
            + self._source_note(domain)
        )
        grid = self._grid_payload(
            [(period, day_count)],
            region,
            value_label=f"Days with {condition}",
            unit="days",
            scale="days",
            scale_max=float(max(1.0, float(counts.max()))),
        )
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="grid_heatmap",
                title=f"Days with {condition}, {region.label}, {period}",
                x_key="period",
                y_keys=["mean_area_meeting_pct"],
            ),
            columns=list(rows[0]) if rows else [],
            rows=rows,
            grid=grid,
        )

    def _region_rank(self, args: WeatherRegionRankArgs) -> GriddedOutput:
        # A national ranking needs one consistent grid: always the base domain.
        domain = self.base
        empty = self._empty_window(domain, args)
        if empty:
            return _refusal(empty)
        variable = VARIABLES[args.variable]
        field = self._reduce(domain.field(variable.cube).sel(time=self._period(args)), args.statistic)
        value_key = f"{args.statistic}_{args.variable}"
        entries = []
        if args.level == "state":
            index = domain.state_index().rename("state_index")
            by_state = field.groupby(index).mean()
            cell_counts = index.groupby(index).count()
            for number, value in zip(by_state["state_index"].values, by_state.values):
                if number < 0:
                    continue
                entries.append(
                    {
                        "state": domain.states[int(number)],
                        value_key: _round(float(value)),
                        "grid_cells": int(cell_counts.sel(state_index=number)),
                    }
                )
            ranked = {entry["state"] for entry in entries}
            unresolved = [name for name in domain.states if name not in ranked]
        else:
            lats = xr.DataArray([c[1].y for c in places.cities()], dims="city")
            lons = xr.DataArray([c[1].x for c in places.cities()], dims="city")
            at_cities = field.sel(lat=lats, lon=lons, method="nearest")
            for (city, centre, _), value in zip(places.cities(), at_cities.values):
                if domain.extent.contains(centre):
                    entries.append({"city": city, value_key: _round(float(value))})
            unresolved = []
        entries = [entry for entry in entries if entry[value_key] is not None]
        entries.sort(key=lambda entry: entry[value_key], reverse=args.order == "highest")
        rows = [{"rank": number + 1, **entry} for number, entry in enumerate(entries[: args.limit])]
        stat_words = self._statistic_words(args.statistic, variable)
        period = self._window_label(args)
        label_key = "state" if args.level == "state" else "city"
        listed = "; ".join(f"{row[label_key]} {row[value_key]} {variable.unit}" for row in rows[:5])
        summary = (
            f"Ranked by {stat_words} for {period}, the {args.order} "
            f"{'states and union territories' if args.level == 'state' else 'cities'} "
            f"are: {listed}. All values come from WRF domain {domain.label}, so "
            "every place is compared on the same grid."
            + self._gap_note(domain, args)
        )
        if args.level == "state":
            how = (
                "grouped the cells by the state or union territory containing "
                "their centres (xarray groupby) and took each group's mean"
            )
            if unresolved:
                how += f" ({', '.join(unresolved)} are smaller than one cell and are not ranked)"
        else:
            how = (
                "read the grid cell nearest each station-registry city's centre "
                f"({len(entries)} cities)"
            )
        reduction = (
            "summed the daily totals"
            if args.statistic == "total"
            else f"took the {stat_words.split(' ')[0]} over the days"
        )
        method = (
            f"For {period}, {reduction} of the WRF {variable.label} cube per cell, "
            f"then {how}."
            + self._source_note(domain)
        )
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="bar",
                title=f"{args.level.capitalize()}s by {stat_words}, {period}",
                x_key=label_key,
                y_keys=[value_key],
            ),
            columns=list(rows[0]) if rows else [],
            rows=rows,
            grid=None,
        )


weather_grid = WeatherGrid()
