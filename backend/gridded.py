"""Gridded forecast analyses over a [time, latitude, longitude] cube.

Station data is a list of points, so VayuChat answers station questions with
SQL. Model and emulator forecasts are a full grid for every hour: the bundled
sample alone is 72 × 61 × 61 = 267,912 values per pollutant, and real emulator
output at 400 m would be tens of millions. Flattening that into rows and
aggregating with SQL would be slow and wasteful, so these analyses work on the
array directly: a time-window mean is one reduction over axis 0, an exceedance
map is one comparison and a sum, and a point forecast is one index lookup.

Only compact results leave this module: summary rows for the table and the
presenter, and at most one downsampled grid (or a capped set of frames) for
the heatmap.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from .models import QueryPlan


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRIDDED_DIR = PROJECT_ROOT / "data" / "gridded"
ARRAY_PATH = GRIDDED_DIR / "delhi_ncr_forecast.npz"
METADATA_PATH = GRIDDED_DIR / "delhi_ncr_forecast.json"
STATIONS_PATH = PROJECT_ROOT / "data" / "stations.csv"

GRIDDED_TOOL_NAMES = frozenset(
    {"forecast_grid_map", "forecast_point_series", "forecast_exceedance_map"}
)
POLLUTANT_LABELS = {"pm25": "PM2.5", "pm10": "PM10"}
# CPCB 24-hour NAAQS, used only to describe the forecast, never to alter it.
DAILY_STANDARDS = {"pm25": 60.0, "pm10": 100.0}
MAX_GRID_CELLS_PER_AXIS = 120
MAX_FRAMES = 24
CITY_ALIASES = {
    "new delhi": "delhi",
    "nct of delhi": "delhi",
    "gurgaon": "gurugram",
    "sonepat": "sonipat",
}


def load_metadata() -> dict[str, Any]:
    try:
        return json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


METADATA = load_metadata()
LAST_HOUR = max(0, int((METADATA.get("shape") or [72])[0]) - 1)


def _describe_window() -> str:
    if not METADATA:
        return "The gridded forecast file is not installed."
    issued = dt.datetime.fromisoformat(METADATA["time_start"])
    days = []
    for day in range(math.ceil((LAST_HOUR + 1) / 24)):
        date = (issued + dt.timedelta(days=day)).strftime("%d %b %Y")
        days.append(f"day {day + 1} = hours {day * 24}–{min(LAST_HOUR, day * 24 + 23)} ({date})")
    lat0, lat1 = METADATA["latitude_range"]
    lon0, lon1 = METADATA["longitude_range"]
    kind = "SYNTHETIC sample" if METADATA.get("synthetic") else "model"
    return (
        f"Hourly {kind} forecast grid over Delhi NCR ({lat0}–{lat1}°N, "
        f"{lon0}–{lon1}°E, {METADATA['resolution_degrees']}° cells) issued "
        f"{issued.strftime('%d %b %Y %H:%M')} IST. Lead hours run 0–{LAST_HOUR}: "
        + "; ".join(days)
        + ". Hour h is 00:00 IST plus h hours, so 15:00 on day 1 is hour 15."
    )


WINDOW_DESCRIPTION = _describe_window()


class GridArgs(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}


class ForecastWindowArgs(GridArgs):
    pollutant: Literal["pm25", "pm10"]
    start_hour: int = Field(default=0, ge=0, le=LAST_HOUR)
    end_hour: int = Field(default=LAST_HOUR, ge=0, le=LAST_HOUR)

    @model_validator(mode="after")
    def validate_window(self):
        if self.start_hour > self.end_hour:
            raise ValueError("start_hour must not be later than end_hour")
        return self


class ForecastGridMapArgs(ForecastWindowArgs):
    statistic: Literal["mean", "max", "hourly"] = "mean"
    city: str | None = Field(default=None, max_length=80)


class ForecastPointSeriesArgs(ForecastWindowArgs):
    city: str | None = Field(default=None, max_length=80)
    station: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def validate_location(self):
        has_point = self.latitude is not None or self.longitude is not None
        if has_point and (self.latitude is None or self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        given = sum(bool(value) for value in (self.city, self.station, has_point))
        if given != 1:
            raise ValueError(
                "give exactly one location: city, station, or latitude/longitude"
            )
        return self


class ForecastExceedanceArgs(ForecastWindowArgs):
    threshold: float = Field(gt=0, le=1_000)
    city: str | None = Field(default=None, max_length=80)


ARGUMENT_MODELS: dict[str, type[GridArgs]] = {
    "forecast_grid_map": ForecastGridMapArgs,
    "forecast_point_series": ForecastPointSeriesArgs,
    "forecast_exceedance_map": ForecastExceedanceArgs,
}


@dataclass(frozen=True)
class Monitor:
    state: str
    city: str
    station: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class GriddedOutput:
    plan: QueryPlan
    columns: list[str]
    rows: list[dict[str, Any]]
    grid: dict[str, Any] | None


@dataclass(frozen=True)
class Region:
    rows: slice
    cols: slice
    label: str


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


class GriddedForecast:
    """The bundled forecast cube, loaded once on first use."""

    def __init__(self, array_path: Path = ARRAY_PATH) -> None:
        self._array_path = array_path
        self._lock = threading.Lock()
        self._cubes: dict[str, np.ndarray] | None = None
        self.latitudes = np.empty(0)
        self.longitudes = np.empty(0)
        self.times: list[dt.datetime] = []
        self.monitors: list[Monitor] = []

    @property
    def available(self) -> bool:
        return self._array_path.exists()

    def _load(self) -> dict[str, np.ndarray]:
        if self._cubes is not None:
            return self._cubes
        with self._lock:
            if self._cubes is None:
                with np.load(self._array_path) as archive:
                    self.latitudes = archive["latitude"].astype(np.float64)
                    self.longitudes = archive["longitude"].astype(np.float64)
                    self.times = [
                        dt.datetime.fromisoformat(str(stamp))
                        for stamp in archive["time"]
                    ]
                    cubes = {
                        key: archive[key].astype(np.float32)
                        for key in POLLUTANT_LABELS
                        if key in archive.files
                    }
                self.monitors = self._load_monitors()
                self._cubes = cubes
        return self._cubes

    def _load_monitors(self) -> list[Monitor]:
        lat0, lat1 = self.latitudes[0], self.latitudes[-1]
        lon0, lon1 = self.longitudes[0], self.longitudes[-1]
        monitors = []
        with STATIONS_PATH.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    latitude = float(row["latitude"])
                    longitude = float(row["longitude"])
                except (TypeError, ValueError):
                    continue
                if lat0 <= latitude <= lat1 and lon0 <= longitude <= lon1:
                    monitors.append(
                        Monitor(
                            state=row["state"],
                            city=row["city"],
                            station=row["station"],
                            latitude=latitude,
                            longitude=longitude,
                        )
                    )
        return monitors

    # Geography -----------------------------------------------------------

    def _bounds_text(self) -> str:
        return (
            f"{self.latitudes[0]:.1f}–{self.latitudes[-1]:.1f}°N, "
            f"{self.longitudes[0]:.1f}–{self.longitudes[-1]:.1f}°E"
        )

    def _city_monitors(self, city: str) -> list[Monitor]:
        key = city.strip().casefold()
        key = CITY_ALIASES.get(key, key)
        return [monitor for monitor in self.monitors if monitor.city.casefold() == key]

    def city_centroids(self) -> dict[str, tuple[float, float]]:
        grouped: dict[str, list[Monitor]] = {}
        for monitor in self.monitors:
            grouped.setdefault(monitor.city, []).append(monitor)
        return {
            city: (
                float(np.mean([m.latitude for m in members])),
                float(np.mean([m.longitude for m in members])),
            )
            for city, members in grouped.items()
        }

    def _region(self, city: str | None) -> Region | str:
        full = Region(slice(None), slice(None), "the Delhi NCR forecast domain")
        if not city:
            return full
        members = self._city_monitors(city)
        if not members:
            return (
                f"{city} has no monitoring stations inside the gridded forecast "
                f"domain ({self._bounds_text()}), so VayuChat cannot locate it "
                "on this grid."
            )
        pad = 0.08
        lat_lo = min(m.latitude for m in members) - pad
        lat_hi = max(m.latitude for m in members) + pad
        lon_lo = min(m.longitude for m in members) - pad
        lon_hi = max(m.longitude for m in members) + pad
        rows = np.flatnonzero((self.latitudes >= lat_lo) & (self.latitudes <= lat_hi))
        cols = np.flatnonzero((self.longitudes >= lon_lo) & (self.longitudes <= lon_hi))
        return Region(
            slice(int(rows[0]), int(rows[-1]) + 1),
            slice(int(cols[0]), int(cols[-1]) + 1),
            f"{members[0].city} and surroundings",
        )

    def _nearest_monitor(self, latitude: float, longitude: float) -> tuple[Monitor, float]:
        best = min(
            self.monitors,
            key=lambda m: _distance_km(latitude, longitude, m.latitude, m.longitude),
        )
        return best, _distance_km(latitude, longitude, best.latitude, best.longitude)

    def _landmarks(self, lats: np.ndarray, lons: np.ndarray) -> list[dict[str, Any]]:
        return [
            {"name": city, "latitude": round(lat, 4), "longitude": round(lon, 4)}
            for city, (lat, lon) in sorted(self.city_centroids().items())
            if lats[0] <= lat <= lats[-1] and lons[0] <= lon <= lons[-1]
        ]

    # Formatting ----------------------------------------------------------

    def _time_label(self, hour: int) -> str:
        return self.times[hour].strftime("%d %b %H:%M")

    def _window_label(self, start: int, end: int) -> str:
        if start == end:
            return f"{self._time_label(start)} IST"
        return f"{self._time_label(start)} – {self._time_label(end)} IST"

    def _source_note(self) -> str:
        if METADATA.get("synthetic", True):
            return (
                " This is a SYNTHETIC sample with the structure of emulator "
                "output, not a real forecast."
            )
        return f" Source: {METADATA.get('provider', 'model output')}."

    @staticmethod
    def _coarsen(field: np.ndarray) -> tuple[np.ndarray, int]:
        """Block-average a 2-D field so neither axis exceeds the cell cap."""
        factor = max(1, math.ceil(max(field.shape) / MAX_GRID_CELLS_PER_AXIS))
        if factor == 1:
            return field, 1
        ny = field.shape[0] // factor * factor
        nx = field.shape[1] // factor * factor
        blocks = field[:ny, :nx].reshape(ny // factor, factor, nx // factor, factor)
        return np.nanmean(blocks, axis=(1, 3)), factor

    def _coarsen_axis(self, values: np.ndarray, factor: int) -> list[float]:
        usable = len(values) // factor * factor
        return [
            round(float(v), 4)
            for v in values[:usable].reshape(-1, factor).mean(axis=1)
        ]

    def _grid_payload(
        self,
        frames: list[tuple[str, np.ndarray]],
        lats: np.ndarray,
        lons: np.ndarray,
        *,
        value_label: str,
        unit: str,
        scale: str,
        scale_max: float | None,
        pollutant: str,
    ) -> dict[str, Any]:
        rendered = []
        factor = 1
        # Animated frames are rounded to whole units to keep the payload small.
        digits = 1 if len(frames) == 1 else 0
        for label, field in frames:
            coarse, factor = self._coarsen(field)
            rendered.append(
                {
                    "label": label,
                    "values": [
                        None if not math.isfinite(v) else round(float(v), digits)
                        for v in coarse.ravel()
                    ],
                }
            )
        cube = self._load()[pollutant]
        return {
            "latitudes": self._coarsen_axis(lats, factor),
            "longitudes": self._coarsen_axis(lons, factor),
            "frames": rendered,
            "value_label": value_label,
            "unit": unit,
            "scale": scale,
            "scale_max": scale_max,
            "landmarks": self._landmarks(lats, lons),
            "source_shape": list(cube.shape),
            "resolution_degrees": round(float(lats[1] - lats[0]) * factor, 4)
            if len(lats) > 1
            else 0.0,
            "synthetic": bool(METADATA.get("synthetic", True)),
        }

    def _hotspots(
        self,
        field: np.ndarray,
        lats: np.ndarray,
        lons: np.ndarray,
        value_key: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Highest cells, at least ~6 km apart so one plume is not listed ten times."""
        order = np.argsort(field, axis=None)[::-1]
        picked: list[tuple[float, float]] = []
        rows = []
        for flat_index in order:
            i, j = np.unravel_index(int(flat_index), field.shape)
            lat, lon = float(lats[i]), float(lons[j])
            if any(_distance_km(lat, lon, a, b) < 6 for a, b in picked):
                continue
            picked.append((lat, lon))
            monitor, distance = self._nearest_monitor(lat, lon)
            rows.append(
                {
                    "rank": len(rows) + 1,
                    "latitude": round(lat, 3),
                    "longitude": round(lon, 3),
                    value_key: _round(float(field[i, j])),
                    "nearest_monitor": monitor.station,
                    "monitor_city": monitor.city,
                    "distance_km": round(distance, 1),
                }
            )
            if len(rows) == limit:
                break
        return rows

    # Analyses ------------------------------------------------------------

    def run(self, name: str, arguments: GridArgs) -> GriddedOutput:
        if not self.available:
            return _refusal(
                "The gridded forecast dataset is not installed. Run "
                "scripts/build_gridded_forecast.py to create it."
            )
        cubes = self._load()
        pollutant = getattr(arguments, "pollutant")
        if pollutant not in cubes:
            return _refusal(
                f"The gridded forecast does not contain {POLLUTANT_LABELS[pollutant]}."
            )
        if name == "forecast_grid_map":
            return self._grid_map(arguments)  # type: ignore[arg-type]
        if name == "forecast_point_series":
            return self._point_series(arguments)  # type: ignore[arg-type]
        if name == "forecast_exceedance_map":
            return self._exceedance(arguments)  # type: ignore[arg-type]
        raise ValueError(f"Unknown gridded analysis: {name}")

    def _grid_map(self, args: ForecastGridMapArgs) -> GriddedOutput:
        region = self._region(args.city)
        if isinstance(region, str):
            return _refusal(region)
        label = POLLUTANT_LABELS[args.pollutant]
        cube = self._load()[args.pollutant][
            args.start_hour : args.end_hour + 1, region.rows, region.cols
        ]
        lats = self.latitudes[region.rows]
        lons = self.longitudes[region.cols]
        window = self._window_label(args.start_hour, args.end_hour)
        hours = cube.shape[0]

        if args.statistic == "max":
            field = cube.max(axis=0)
            value_key = f"max_{args.pollutant}"
            stat_word = "peak hourly"
        else:
            field = cube.mean(axis=0)
            value_key = f"mean_{args.pollutant}"
            stat_word = "mean"

        if args.statistic == "hourly":
            stride = max(1, math.ceil(hours / MAX_FRAMES))
            frames = [
                (f"{self._time_label(hour)} IST", self._load()[args.pollutant][hour, region.rows, region.cols])
                for hour in range(args.start_hour, args.end_hour + 1, stride)
            ]
            value_label = f"Hourly {label}"
            title = f"Hourly forecast {label}, {window}"
        else:
            frames = [(window, field)]
            value_label = f"{stat_word.capitalize()} {label}"
            title = f"Forecast {stat_word} {label}, {window}"

        standard = DAILY_STANDARDS[args.pollutant]
        above = float((field > standard).mean() * 100)
        i, j = np.unravel_index(int(np.argmax(field)), field.shape)
        peak_monitor, peak_distance = self._nearest_monitor(float(lats[i]), float(lons[j]))
        rows = self._hotspots(field, lats, lons, value_key)
        summary = (
            f"Across {region.label}, the forecast {stat_word} {label} for "
            f"{window} averages {field.mean():.0f} µg/m³ over "
            f"{field.size:,} grid cells. The highest cell reaches "
            f"{field.max():.0f} µg/m³ at {lats[i]:.2f}°N, {lons[j]:.2f}°E, "
            f"{peak_distance:.1f} km from {peak_monitor.station}. "
            f"{above:.0f}% of the area is above the CPCB 24-hour standard of "
            f"{standard:.0f} µg/m³ on this measure."
        )
        if args.statistic == "hourly":
            reduction = (
                f"showed hourly fields every {max(1, math.ceil(hours / MAX_FRAMES))} h "
                "and took the time mean for hotspots"
            )
        else:
            reduction = f"took the {stat_word} over time"
        method = (
            f"Took the {label} forecast cube ({hours} hours × {len(lats)} × "
            f"{len(lons)} cells) for {window} and {reduction} with one array "
            "operation, not row-by-row SQL. Hotspots are the "
            "highest cells at least 6 km apart, labelled with the nearest CPCB "
            "monitor. The standard comparison is descriptive only."
            + self._source_note()
        )
        grid = self._grid_payload(
            frames,
            lats,
            lons,
            value_label=value_label,
            unit="µg/m³",
            scale=args.pollutant,
            scale_max=None,
            pollutant=args.pollutant,
        )
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
            columns=list(rows[0]) if rows else [],
            rows=rows,
            grid=grid,
        )

    def _locate(self, args: ForecastPointSeriesArgs) -> tuple[float, float, str] | str:
        if args.latitude is not None and args.longitude is not None:
            return args.latitude, args.longitude, f"{args.latitude:.3f}°N, {args.longitude:.3f}°E"
        if args.station:
            needle = args.station.casefold()
            matches = [m for m in self.monitors if needle in m.station.casefold()]
            if not matches:
                return (
                    f"No monitoring station matching '{args.station}' lies inside "
                    f"the gridded forecast domain ({self._bounds_text()})."
                )
            monitor = matches[0]
            return monitor.latitude, monitor.longitude, monitor.station
        members = self._city_monitors(args.city or "")
        if not members:
            return (
                f"{args.city} has no monitoring stations inside the gridded "
                f"forecast domain ({self._bounds_text()}), so VayuChat cannot "
                "locate it on this grid."
            )
        return (
            float(np.mean([m.latitude for m in members])),
            float(np.mean([m.longitude for m in members])),
            members[0].city,
        )

    def _point_series(self, args: ForecastPointSeriesArgs) -> GriddedOutput:
        located = self._locate(args)
        if isinstance(located, str):
            return _refusal(located)
        latitude, longitude, place = located
        step = float(self.latitudes[1] - self.latitudes[0])
        if not (
            self.latitudes[0] - step / 2 <= latitude <= self.latitudes[-1] + step / 2
            and self.longitudes[0] - step / 2 <= longitude <= self.longitudes[-1] + step / 2
        ):
            return _refusal(
                f"{place} is outside the gridded forecast domain ({self._bounds_text()})."
            )
        i = int(np.argmin(np.abs(self.latitudes - latitude)))
        j = int(np.argmin(np.abs(self.longitudes - longitude)))
        series = self._load()[args.pollutant][args.start_hour : args.end_hour + 1, i, j]
        label = POLLUTANT_LABELS[args.pollutant]
        rows = [
            {
                "forecast_time": self._time_label(hour),
                "lead_hour": hour,
                args.pollutant: _round(float(value)),
            }
            for hour, value in zip(range(args.start_hour, args.end_hour + 1), series)
        ]
        peak = int(np.argmax(series))
        low = int(np.argmin(series))
        standard = DAILY_STANDARDS[args.pollutant]
        window = self._window_label(args.start_hour, args.end_hour)
        summary = (
            f"At {place}, forecast {label} for {window} averages "
            f"{series.mean():.0f} µg/m³. It peaks at {series[peak]:.0f} µg/m³ "
            f"at {self._time_label(args.start_hour + peak)} and is lowest at "
            f"{series[low]:.0f} µg/m³ at {self._time_label(args.start_hour + low)}. "
            f"{int((series > standard).sum())} of {len(series)} hours are above "
            f"{standard:.0f} µg/m³."
        )
        method = (
            f"Read the single grid cell nearest {place}"
            + (
                f", taken as the centre of its {len(self._city_monitors(args.city))} monitors"
                if args.city
                else ""
            )
            + f" (cell centre "
            f"{self.latitudes[i]:.2f}°N, {self.longitudes[j]:.2f}°E, about "
            f"{_distance_km(latitude, longitude, self.latitudes[i], self.longitudes[j]):.1f} km away) "
            "straight from the forecast cube. A cell value is an average over "
            "the whole cell, not a point measurement."
            + self._source_note()
        )
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="line",
                title=f"Forecast {label} at {place}, {window}",
                x_key="forecast_time",
                y_keys=[args.pollutant],
            ),
            columns=["forecast_time", "lead_hour", args.pollutant],
            rows=rows,
            grid=None,
        )

    def _exceedance(self, args: ForecastExceedanceArgs) -> GriddedOutput:
        region = self._region(args.city)
        if isinstance(region, str):
            return _refusal(region)
        label = POLLUTANT_LABELS[args.pollutant]
        cube = self._load()[args.pollutant][
            args.start_hour : args.end_hour + 1, region.rows, region.cols
        ]
        lats = self.latitudes[region.rows]
        lons = self.longitudes[region.cols]
        exceeds = cube > args.threshold
        hours_above = exceeds.sum(axis=0).astype(np.float32)
        hourly_share = exceeds.mean(axis=(1, 2)) * 100
        hours = cube.shape[0]
        window = self._window_label(args.start_hour, args.end_hour)

        days: dict[dt.date, list[int]] = {}
        for hour in range(args.start_hour, args.end_hour + 1):
            days.setdefault(self.times[hour].date(), []).append(hour)
        rows = []
        for day, day_hours in days.items():
            shares = hourly_share[[hour - args.start_hour for hour in day_hours]]
            peak = int(np.argmax(shares))
            rows.append(
                {
                    "forecast_day": day.strftime("%d %b %Y"),
                    "hours": len(day_hours),
                    "mean_area_above_pct": _round(float(shares.mean())),
                    "peak_area_above_pct": _round(float(shares.max())),
                    "peak_time": self._time_label(day_hours[peak]),
                }
            )
        any_hour = float((hours_above > 0).mean() * 100)
        half = float((hours_above > hours / 2).mean() * 100)
        summary = (
            f"For {window}, {any_hour:.0f}% of {region.label} is forecast above "
            f"{args.threshold:g} µg/m³ {label} for at least one hour, and "
            f"{half:.0f}% is above it for more than half of the {hours} hours. "
            f"On average {hourly_share.mean():.0f}% of the area is above the "
            "threshold in any given hour."
        )
        method = (
            f"Compared every value in the {label} cube ({hours} hours × "
            f"{len(lats)} × {len(lons)} cells) with {args.threshold:g} µg/m³ "
            "in one array operation, then counted exceeding hours per cell (the "
            "map) and the exceeding share of cells per hour (the table)."
            + self._source_note()
        )
        grid = self._grid_payload(
            [(window, hours_above)],
            lats,
            lons,
            value_label=f"Hours above {args.threshold:g} µg/m³ {label}",
            unit="hours",
            scale="hours",
            scale_max=float(hours),
            pollutant=args.pollutant,
        )
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="grid_heatmap",
                title=f"Hours above {args.threshold:g} µg/m³ {label}, {window}",
                x_key="forecast_day",
                y_keys=["mean_area_above_pct"],
            ),
            columns=list(rows[0]) if rows else [],
            rows=rows,
            grid=grid,
        )


gridded_forecast = GriddedForecast()
