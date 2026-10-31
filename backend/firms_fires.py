"""NASA FIRMS active-fire detections: maps, daily series and rankings.

``scripts/update_firms.py`` keeps ``data/firms/fires.parquet``: one row per
satellite detection over India (VIIRS on S-NPP, NOAA-20 and NOAA-21 at 375 m;
MODIS at 1 km) with its IST time, fire radiative power (FRP, MW), confidence,
state and district.

Detections are points, so maps bin them onto a grid sized to the area asked
about (about 120 cells across: 0.25° for India, 0.1° for a state, 0.05° for
a district) and reuse the gridded map format, so zoom, outlines, animation
and downloads work as for the weather and forecast grids. Statistics count
detections inside the place's boundary; the map also shows fires around it.
"""

from __future__ import annotations

import datetime as dt
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import duckdb
import numpy as np
import pandas as pd
import shapely
from pydantic import Field, model_validator

from .models import QueryPlan
from .places import Place, places
from .weather_grid import (
    DATE_PATTERN,
    NATIONAL_LANDMARKS,
    GriddedOutput,
    WeatherArgs,
    _refusal,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TABLE = PROJECT_ROOT / "data" / "firms" / "fires.parquet"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

FIRE_TOOL_NAMES = frozenset({"fire_map", "fire_series", "fire_rank"})
CELL_STEPS = (0.05, 0.1, 0.2, 0.25, 0.5)
MAX_CELLS = 130
MAX_FRAMES = 24

CAVEAT = (
    " Source: NASA FIRMS active-fire detections (VIIRS 375 m on S-NPP, NOAA-20 and "
    "NOAA-21; MODIS 1 km), near-real-time processing. Counts are satellite "
    "detections at overpass times (about 01:30, 10:30, 13:30 and 22:30 local), "
    "summed over all satellites: one fire can be detected more than once, and fires "
    "under cloud or smoke, or too small or brief, are missed. They are not burned "
    "area. Persistent detections at coal fields, steel plants and refineries are "
    "industrial heat sources, not crop fires. We acknowledge the use of data from "
    "NASA's FIRMS, part of NASA's Earth Science Data and Information System (ESDIS)."
)


class FireTable:
    """The detection table, reloaded when the update script rewrites it."""

    def __init__(self, path: Path = TABLE) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._mtime = 0.0
        self._frame = pd.DataFrame()

    def frame(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame()
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            with self._lock:
                if mtime != self._mtime:
                    self._frame = duckdb.sql(f"SELECT * FROM read_parquet('{self.path}')").df()
                    self._mtime = mtime
        return self._frame

    def coverage(self) -> tuple[str, str] | None:
        frame = self.frame()
        if frame.empty:
            return None
        return str(frame["date_ist"].min()), str(frame["date_ist"].max())


fires = FireTable()


def routing_context() -> str:
    span = fires.coverage()
    if span is None:
        return "No FIRMS fire detections are installed."
    return f"NASA FIRMS fire detections cover IST dates {span[0]} to {span[1]}."


TOOL_GUIDE = (
    "NASA FIRMS satellite active-fire detections over India (VIIRS 375 m, MODIS 1 km), "
    "used for crop-residue (stubble) burning, forest fires and other open burning. "
    "The conversation context gives the dates covered. Dates are IST days, "
    "YYYY-MM-DD; omit them for the last 7 days covered. measure='count' counts "
    "detections, 'frp' sums fire radiative power (MW, a proxy for fire intensity). "
    "confidence='nominal_high' (default) drops low-confidence detections."
)


class FireArgs(WeatherArgs):
    start_date: str | None = Field(default=None, pattern=DATE_PATTERN)
    end_date: str | None = Field(default=None, pattern=DATE_PATTERN)
    confidence: Literal["nominal_high", "all"] = "nominal_high"

    @model_validator(mode="after")
    def validate_window(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be later than end_date")
        return self


class FirePlaceArgs(FireArgs):
    state: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_place(self):
        if self.city:
            self.state = None
        return self


class FireMapArgs(FirePlaceArgs):
    measure: Literal["count", "frp"] = "count"
    statistic: Literal["total", "sequence"] = "total"


class FireSeriesArgs(FirePlaceArgs):
    pass


class FireRankArgs(FireArgs):
    level: Literal["district", "state"] = "district"
    measure: Literal["count", "frp"] = "count"
    state: str | None = Field(default=None, max_length=80)
    limit: int = Field(default=10, ge=1, le=40)


ARGUMENT_MODELS = {"fire_map": FireMapArgs, "fire_series": FireSeriesArgs, "fire_rank": FireRankArgs}


@dataclass(frozen=True)
class Area:
    label: str
    place: Place | None
    bounds: tuple[float, float, float, float]  # lon0, lat0, lon1, lat1 of the map
    note: str


def _nice_step(span: float) -> float:
    return next((step for step in CELL_STEPS if span / step <= MAX_CELLS), CELL_STEPS[-1])


class FireAnalyses:
    def _window(self, args: FireArgs) -> tuple[pd.DataFrame, str, str] | str:
        frame = fires.frame()
        if frame.empty:
            return "No FIRMS fire detections are installed yet. Run scripts/update_firms.py."
        first, last = fires.coverage()
        end = args.end_date or last
        start = args.start_date or (
            dt.date.fromisoformat(end) - dt.timedelta(days=6)
        ).isoformat()
        if end < first or start > last:
            return (
                f"FIRMS detections are installed for {first} to {last} (IST), so "
                f"{start}{'' if start == end else ' to ' + end} is outside them. Older "
                "seasons need a FIRMS archive backfill (a free FIRMS MAP_KEY)."
            )
        start, end = max(start, first), min(end, last)
        rows = frame[(frame["date_ist"] >= start) & (frame["date_ist"] <= end)]
        if args.confidence == "nominal_high":
            rows = rows[rows["confidence"] != "low"]
        return rows, start, end

    @staticmethod
    def _label(start: str, end: str) -> str:
        a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
        return f"{a:%d %b %Y}" if a == b else f"{a:%d %b} – {b:%d %b %Y}"

    def _area(self, state: str | None, city: str | None) -> Area | str:
        india = (68.0, 6.5, 97.5, 37.0)
        if not state and not city:
            return Area("India", None, india, "all detections in India")
        place = places.state(state) if state else places.find(city)
        if place is None:
            return f"'{state or city}' was not found in the state, district or city boundaries."
        west, south, east, north = place.geometry.bounds
        margin = max(0.15, 0.08 * max(east - west, north - south))
        return Area(
            place.name,
            place,
            (west - margin, south - margin, east + margin, north + margin),
            f"detections inside {place.note}",
        )

    @staticmethod
    def _inside(rows: pd.DataFrame, area: Area) -> pd.DataFrame:
        if area.place is None:
            return rows
        if area.place.kind == "state":
            return rows[rows["state"] == area.place.name]
        inside = shapely.contains_xy(area.place.geometry, rows["longitude"].to_numpy(), rows["latitude"].to_numpy())
        return rows[inside]

    def _grid(self, rows: pd.DataFrame, area: Area, measure: str, frames: list[tuple[str, pd.DataFrame]]):
        lon0, lat0, lon1, lat1 = area.bounds
        step = _nice_step(max(lon1 - lon0, lat1 - lat0))
        lon_edges = np.arange(math.floor(lon0 / step) * step, lon1 + step, step)
        lat_edges = np.arange(math.floor(lat0 / step) * step, lat1 + step, step)
        lats = (lat_edges[:-1] + lat_edges[1:]) / 2
        lons = (lon_edges[:-1] + lon_edges[1:]) / 2
        index, _ = places.state_index_grid(lats, lons)
        outside = index < 0
        rendered = []
        for label, part in frames:
            weights = part["frp_mw"].to_numpy() if measure == "frp" else None
            counts, _, _ = np.histogram2d(
                part["latitude"].to_numpy(), part["longitude"].to_numpy(), bins=[lat_edges, lon_edges], weights=weights
            )
            values = np.where(outside, np.nan, counts)
            rendered.append(
                {"label": label, "values": [None if not math.isfinite(v) else round(float(v), 1) for v in values.ravel()]}
            )
        maximum = max((v for f in rendered for v in f["values"] if v is not None), default=1.0)
        cities = [
            c for c in places.cities()
            if lat_edges[0] <= c[1].y <= lat_edges[-1] and lon_edges[0] <= c[1].x <= lon_edges[-1]
        ]
        chosen = (
            [c for c in cities if c[0] in NATIONAL_LANDMARKS]
            if area.place is None
            else sorted(cities, key=lambda c: (-c[2], c[0]))[:8]
        )
        return {
            "latitudes": [round(float(v), 4) for v in lats],
            "longitudes": [round(float(v), 4) for v in lons],
            "frames": rendered,
            "value_label": "Fire radiative power" if measure == "frp" else "Fire detections",
            "unit": "MW" if measure == "frp" else "detections",
            "scale": "fires",
            "scale_max": float(maximum),
            "landmarks": [{"name": c[0], "latitude": round(c[1].y, 4), "longitude": round(c[1].x, 4)} for c in chosen],
            "source_shape": [len(frames), len(lats), len(lons)],
            "resolution_degrees": step,
            "boundaries": True,
            "source_label": f"NASA FIRMS detections binned to {step}° cells",
            "time_unit": "days",
            "domain": "firms",
            "domain_label": "NASA FIRMS (VIIRS 375 m, MODIS 1 km)",
            "outline": area.place.outline() if area.place is not None else [],
            "outline_label": area.place.name if area.place is not None else "",
        }

    def run(self, name: str, arguments: WeatherArgs) -> GriddedOutput:
        if name == "fire_map":
            return self._map(arguments)  # type: ignore[arg-type]
        if name == "fire_series":
            return self._series(arguments)  # type: ignore[arg-type]
        if name == "fire_rank":
            return self._rank(arguments)  # type: ignore[arg-type]
        raise ValueError(f"Unknown fire analysis: {name}")

    def _map(self, args: FireMapArgs) -> GriddedOutput:
        area = self._area(args.state, args.city)
        if isinstance(area, str):
            return _refusal(area)
        window = self._window(args)
        if isinstance(window, str):
            return _refusal(window)
        rows, start, end = window
        lon0, lat0, lon1, lat1 = area.bounds
        shown = rows[rows["longitude"].between(lon0, lon1) & rows["latitude"].between(lat0, lat1)]
        inside = self._inside(rows, area)
        period = self._label(start, end)
        if args.statistic == "sequence":
            days = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d")[-MAX_FRAMES:]
            frames = [(f"{dt.date.fromisoformat(d):%a %d %b %Y}", shown[shown["date_ist"] == d]) for d in days]
        else:
            frames = [(period, shown)]
        grid = self._grid(shown, area, args.measure, frames)
        if inside.empty:
            summary = f"No {'' if args.confidence == 'all' else 'nominal- or high-confidence '}fire detections were recorded in {area.label} for {period}."
            rows_out: list[dict[str, Any]] = []
        else:
            by_district = (
                inside.groupby(["district", "state"])
                .agg(detections=("frp_mw", "size"), frp_mw=("frp_mw", "sum"))
                .sort_values("frp_mw" if args.measure == "frp" else "detections", ascending=False)
                .reset_index()
            )
            rows_out = [
                {"rank": n + 1, "district": r.district, "state": r.state, "detections": int(r.detections), "frp_mw": round(float(r.frp_mw), 1)}
                for n, r in enumerate(by_district.head(10).itertuples())
            ]
            peak = inside.groupby("date_ist").size().sort_values(ascending=False)
            summary = (
                f"FIRMS recorded {len(inside):,} fire detections in {area.label} for {period} "
                f"(total fire radiative power {inside['frp_mw'].sum():,.0f} MW), most on "
                f"{dt.date.fromisoformat(peak.index[0]):%d %b} ({int(peak.iloc[0]):,}). The most active "
                f"district is {rows_out[0]['district']} ({rows_out[0]['state']}) with "
                f"{rows_out[0]['detections']:,} detections."
            )
        method = (
            f"Took {area.note} for {period}"
            + (" excluding low-confidence detections" if args.confidence == "nominal_high" else "")
            + f", then binned {'their fire radiative power' if args.measure == 'frp' else 'them'} onto "
            f"{grid['resolution_degrees']}° cells"
            + (f", one frame per IST day" if args.statistic == "sequence" else "")
            + ". The map also shows detections around the place."
            + CAVEAT
        )
        title = (
            f"{'Daily ' if args.statistic == 'sequence' else ''}"
            f"{'Fire radiative power' if args.measure == 'frp' else 'Fire detections'}, {area.label}, {period}"
        )
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="grid_heatmap",
                title=title,
                x_key="district",
                y_keys=["detections"],
            ),
            columns=list(rows_out[0]) if rows_out else [],
            rows=rows_out,
            grid=grid,
        )

    def _series(self, args: FireSeriesArgs) -> GriddedOutput:
        area = self._area(args.state, args.city)
        if isinstance(area, str):
            return _refusal(area)
        window = self._window(args)
        if isinstance(window, str):
            return _refusal(window)
        rows, start, end = window
        inside = self._inside(rows, area)
        days = pd.date_range(start, end, freq="D").strftime("%Y-%m-%d")
        daily = inside.groupby("date_ist").agg(
            detections=("frp_mw", "size"),
            frp_mw=("frp_mw", "sum"),
            night=("daynight", lambda d: int((d == "N").sum())),
        ).reindex(days, fill_value=0)
        rows_out = [
            {"date": d, "detections": int(r.detections), "frp_mw": round(float(r.frp_mw), 1), "night_detections": int(r.night)}
            for d, r in zip(days, daily.itertuples())
        ]
        period = self._label(start, end)
        peak = daily["detections"].idxmax()
        summary = (
            f"In {area.label}, FIRMS recorded {int(daily['detections'].sum()):,} fire detections for {period}, "
            f"averaging {daily['detections'].mean():.0f} a day; the most were on "
            f"{dt.date.fromisoformat(peak):%d %b} ({int(daily['detections'].max()):,})."
        )
        method = (
            f"Counted {area.note} per IST day"
            + (" excluding low-confidence detections" if args.confidence == "nominal_high" else "")
            + "; days with no detections are shown as zero."
            + CAVEAT
        )
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="bar",
                title=f"Daily fire detections, {area.label}, {period}",
                x_key="date",
                y_keys=["detections"],
            ),
            columns=list(rows_out[0]),
            rows=rows_out,
            grid=None,
        )

    def _rank(self, args: FireRankArgs) -> GriddedOutput:
        window = self._window(args)
        if isinstance(window, str):
            return _refusal(window)
        rows, start, end = window
        scope = "India"
        if args.state:
            place = places.state(args.state)
            if place is None:
                return _refusal(f"'{args.state}' is not a recognised state or union territory.")
            rows = rows[rows["state"] == place.name]
            scope = place.name
        keys = ["district", "state"] if args.level == "district" else ["state"]
        grouped = rows.groupby(keys).agg(detections=("frp_mw", "size"), frp_mw=("frp_mw", "sum")).reset_index()
        grouped = grouped.sort_values("frp_mw" if args.measure == "frp" else "detections", ascending=False)
        rows_out = [
            {"rank": n + 1, **{k: getattr(r, k) for k in keys}, "detections": int(r.detections), "frp_mw": round(float(r.frp_mw), 1)}
            for n, r in enumerate(grouped.head(args.limit).itertuples())
        ]
        period = self._label(start, end)
        label_key = keys[0]
        listed = "; ".join(f"{r[label_key]} {r['detections']:,}" for r in rows_out[:5])
        summary = (
            f"Ranked by {'fire radiative power' if args.measure == 'frp' else 'fire detections'} for {period} "
            f"in {scope}, the top {args.level}s are: {listed or 'none recorded'}."
        )
        method = (
            f"Grouped FIRMS detections for {period} by the {args.level} containing them"
            + (" (low-confidence detections excluded)" if args.confidence == "nominal_high" else "")
            + "."
            + CAVEAT
        )
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="bar",
                title=f"{label_key.capitalize()}s by fire detections, {scope}, {period}",
                x_key=label_key,
                y_keys=["frp_mw" if args.measure == "frp" else "detections"],
            ),
            columns=list(rows_out[0]) if rows_out else [],
            rows=rows_out,
            grid=None,
        )


fire_analyses = FireAnalyses()
