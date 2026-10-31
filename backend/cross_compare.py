"""Compare two state-level measures from different datasets.

Answers questions such as "which states have the highest PM2.5 per fire
detection?" or "is PM2.5 associated with fires across states?" by putting two
measures side by side for every state over a shared IST date window:

* fires: NASA FIRMS detections (count) or fire radiative power (MW), summed;
* CAMS: forecast pollutant concentration, the area mean over the state's
  cells and the hours in the window. Past days use the newest run covering
  each hour (stitched across the stored runs), so they are the model's best
  short-range estimate, not measurements;
* stations: CPCB station measurements, the mean of each station's daily
  values in the window, stations weighted equally within a state.

Each measure has its own date coverage, and the comparison uses only the
days both cover. When they do not overlap, the answer says so instead of
pretending. Ratios exclude states where the denominator is too small to be
meaningful, and the correlation across states is reported as an association.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import xarray as xr
from pydantic import Field, model_validator

from .database import database
from .firms_fires import fires
from .models import QueryPlan
from .weather_grid import DATE_PATTERN, Domain, GriddedOutput, WeatherArgs, _refusal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CAMS_DIR = PROJECT_ROOT / "data" / "cams"

Measure = Literal[
    "fire_detections",
    "fire_radiative_power",
    "cams_pm25",
    "cams_pm10",
    "cams_no2",
    "cams_ozone",
    "station_pm25",
    "station_pm10",
    "station_no2",
]
MEASURES: dict[str, dict[str, str]] = {
    "fire_detections": {"label": "fire detections", "unit": "detections", "source": "NASA FIRMS"},
    "fire_radiative_power": {"label": "fire radiative power", "unit": "MW", "source": "NASA FIRMS"},
    "cams_pm25": {"label": "CAMS PM2.5", "unit": "µg/m³", "source": "CAMS forecast", "cube": "pm25"},
    "cams_pm10": {"label": "CAMS PM10", "unit": "µg/m³", "source": "CAMS forecast", "cube": "pm10"},
    "cams_no2": {"label": "CAMS NO₂", "unit": "µg/m³", "source": "CAMS forecast", "cube": "no2"},
    "cams_ozone": {"label": "CAMS ozone", "unit": "µg/m³", "source": "CAMS forecast", "cube": "o3"},
    "station_pm25": {"label": "CPCB station PM2.5", "unit": "µg/m³", "source": "CPCB stations", "column": "pm25"},
    "station_pm10": {"label": "CPCB station PM10", "unit": "µg/m³", "source": "CPCB stations", "column": "pm10"},
    "station_no2": {"label": "CPCB station NO₂", "unit": "µg/m³", "source": "CPCB stations", "column": "no2"},
}
# A ratio's denominator must be at least this to be meaningful.
MIN_DENOMINATOR = {"fire_detections": 10.0, "fire_radiative_power": 50.0}
MIN_STATION_DAYS = 3

COMPARE_TOOL_NAMES = frozenset({"state_compare"})

TOOL_GUIDE = (
    "Measures: fire_detections and fire_radiative_power (NASA FIRMS satellite fires), "
    "cams_pm25, cams_pm10, cams_no2, cams_ozone (CAMS model forecast, stitched across "
    "runs for past days), station_pm25, station_pm10, station_no2 (CPCB monitors, "
    "2017-2025). Only days covered by both measures are used; the server says when "
    "they do not overlap. Dates are IST, YYYY-MM-DD; omit them to use the whole overlap."
)


class StateCompareArgs(WeatherArgs):
    x_measure: Measure
    y_measure: Measure
    start_date: str | None = Field(default=None, pattern=DATE_PATTERN)
    end_date: str | None = Field(default=None, pattern=DATE_PATTERN)
    rank_by: Literal["ratio", "x", "y"] = "ratio"
    order: Literal["highest", "lowest"] = "highest"
    limit: int = Field(default=10, ge=1, le=40)

    @model_validator(mode="after")
    def validate_measures(self):
        if self.x_measure == self.y_measure:
            raise ValueError("x_measure and y_measure must differ")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be later than end_date")
        return self


# Coverage --------------------------------------------------------------


def _cams_runs() -> list[Path]:
    return sorted(CAMS_DIR.glob("cams_forecast_*.nc"))


def _coverage(measure: str) -> tuple[str, str] | None:
    if measure.startswith("fire_"):
        return fires.coverage()
    if measure.startswith("cams_"):
        runs = _cams_runs()
        if not runs:
            return None
        with xr.open_dataset(runs[0]) as first, xr.open_dataset(runs[-1]) as last:
            return first.attrs["time_start"][:10], last.attrs["time_end"][:10]
    stats = database.stats()
    return str(stats["first_date"])[:10], str(stats["last_date"])[:10]


# State values ----------------------------------------------------------


def _fire_values(measure: str, start: str, end: str) -> pd.Series:
    frame = fires.frame()
    rows = frame[(frame["date_ist"] >= start) & (frame["date_ist"] <= end) & (frame["confidence"] != "low")]
    grouped = rows.groupby("state")
    return grouped.size().astype(float) if measure == "fire_detections" else grouped["frp_mw"].sum()


def _cams_values(measure: str, start: str, end: str) -> tuple[pd.Series, str]:
    """State means of the hourly field, newest run winning for each hour."""
    cube_name = MEASURES[measure]["cube"]
    pieces: dict[pd.Timestamp, xr.DataArray] = {}
    used = []
    for path in _cams_runs():  # oldest first: newer runs overwrite overlapping hours
        with xr.open_dataset(path) as run:
            window = run[cube_name].sel(time=slice(f"{start}T00:00", f"{end}T23:59")).load()
        if window.sizes["time"]:
            used.append(path.stem.split("_")[-1])
            for stamp in window["time"].values:
                pieces[pd.Timestamp(stamp)] = window.sel(time=stamp)
    if not pieces:
        return pd.Series(dtype=float), ""
    hourly = xr.concat([pieces[k] for k in sorted(pieces)], dim="time")
    field = hourly.mean("time")
    domain = Domain("cams", _cams_runs()[-1])
    index = domain.state_index()
    means = field.groupby(index.rename("state_index")).mean()
    values = {
        domain.states[int(n)]: float(v)
        for n, v in zip(means["state_index"].values, means.values)
        if n >= 0 and math.isfinite(float(v))
    }
    note = f"{len(pieces)} hourly steps stitched from {len(used)} CAMS runs ({used[0]}–{used[-1]} UTC)"
    return pd.Series(values), note


def _station_values(measure: str, start: str, end: str) -> pd.Series:
    column = MEASURES[measure]["column"]
    sql = f"""
        SELECT state, avg(station_mean) AS value
        FROM (
            SELECT state, station, avg({column}) AS station_mean
            FROM air_quality
            WHERE timestamp BETWEEN DATE '{start}' AND DATE '{end}' AND {column} IS NOT NULL
            GROUP BY state, station
            HAVING count(*) >= {MIN_STATION_DAYS}
        )
        GROUP BY state
    """
    _, rows, _ = database.execute(database.validate_sql(sql), max_rows=100)
    return pd.Series({row["state"]: float(row["value"]) for row in rows})


def _values(measure: str, start: str, end: str) -> tuple[pd.Series, str]:
    if measure.startswith("fire_"):
        return _fire_values(measure, start, end), "FIRMS detections, low confidence excluded, summed over the window"
    if measure.startswith("cams_"):
        series, note = _cams_values(measure, start, end)
        return series, f"CAMS area mean over each state's cells: {note}"
    return _station_values(measure, start, end), (
        f"CPCB station means (stations with at least {MIN_STATION_DAYS} days), stations weighted equally"
    )


def _label(start: str, end: str) -> str:
    a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    return f"{a:%d %b %Y}" if a == b else f"{a:%d %b} – {b:%d %b %Y}"


def _fmt(value: float, unit: str) -> str:
    return f"{value:,.1f} {unit}" if abs(value) < 1000 else f"{value:,.0f} {unit}"


def state_compare(args: StateCompareArgs) -> GriddedOutput:
    x_info, y_info = MEASURES[args.x_measure], MEASURES[args.y_measure]
    x_cover, y_cover = _coverage(args.x_measure), _coverage(args.y_measure)
    if x_cover is None or y_cover is None:
        missing = x_info if x_cover is None else y_info
        return _refusal(f"{missing['source']} data are not installed, so this comparison cannot be made.")
    first = max(x_cover[0], y_cover[0])
    last = min(x_cover[1], y_cover[1])
    if first > last:
        return _refusal(
            f"{x_info['label'][0].upper() + x_info['label'][1:]} covers {x_cover[0]} to {x_cover[1]} "
            f"while {y_info['label']} covers {y_cover[0]} to {y_cover[1]}, so the two have no days in "
            "common and cannot be compared. "
            + (
                "CPCB station data end in December 2025 while the fire detections start in "
                "October 2026; use CAMS PM2.5 (a model estimate) for the fire period instead."
                if {args.x_measure[:4], args.y_measure[:4]} >= {"fire", "stat"}
                else ""
            )
        )
    start = max(args.start_date or first, first)
    end = min(args.end_date or last, last)
    if start > end:
        return _refusal(f"Both measures are available only for {first} to {last} (IST).")

    x_values, x_note = _values(args.x_measure, start, end)
    y_values, y_note = _values(args.y_measure, start, end)
    states = sorted(set(x_values.index) | set(y_values.index))
    table = pd.DataFrame(
        {
            "x": x_values.reindex(states),
            "y": y_values.reindex(states),
        },
        index=states,
    )
    # States with no fire detections have zero fires, not missing data.
    for column, measure in (("x", args.x_measure), ("y", args.y_measure)):
        if measure.startswith("fire_"):
            table[column] = table[column].fillna(0.0)
    table = table.dropna()
    minimum = MIN_DENOMINATOR.get(args.y_measure, 0.0)
    eligible = table[table["y"] > minimum]
    table["ratio"] = np.where(table["y"] > minimum, table["x"] / table["y"].where(table["y"] > minimum), np.nan)

    sort_column = {"ratio": "ratio", "x": "x", "y": "y"}[args.rank_by]
    ranked = table.dropna(subset=[sort_column]).sort_values(sort_column, ascending=args.order == "lowest")
    x_key, y_key = args.x_measure, args.y_measure
    ratio_key = f"{args.x_measure}_per_{args.y_measure}"
    rows = [
        {
            "rank": n + 1,
            "state": state,
            x_key: round(float(r.x), 2),
            y_key: round(float(r.y), 2),
            ratio_key: None if not math.isfinite(r.ratio) else round(float(r.ratio), 4),
        }
        for n, (state, r) in enumerate(ranked.head(args.limit).iterrows())
    ]

    pair = table[["x", "y"]]
    pearson = float(pair["x"].corr(pair["y"])) if len(pair) >= 3 else float("nan")
    spearman = float(pair["x"].rank().corr(pair["y"].rank())) if len(pair) >= 3 else float("nan")
    period = _label(start, end)
    ratio_words = f"{x_info['label']} per {y_info['label'].removesuffix('s') if y_info['unit'] == 'detections' else y_info['label']}"
    if args.rank_by == "ratio":
        listed = "; ".join(f"{r['state']} {r[ratio_key]:.3g}" for r in rows[:5])
        lead = (
            f"Ranked by {ratio_words} ({x_info['unit']} per {y_info['unit'].removesuffix('s') if y_info['unit'] == 'detections' else y_info['unit']}) "
            f"for {period}, the {args.order} states are: {listed or 'none'}."
        )
    else:
        key, info = (x_key, x_info) if args.rank_by == "x" else (y_key, y_info)
        listed = "; ".join(f"{r['state']} {_fmt(r[key], info['unit'])}" for r in rows[:5])
        lead = f"Ranked by {info['label']} for {period}, the {args.order} states are: {listed or 'none'}."
    summary = (
        lead
        + (
            f" Across {len(pair)} states, {x_info['label']} and {y_info['label']} have a Pearson "
            f"correlation of {pearson:.2f} (Spearman {spearman:.2f}); this is an association, not "
            "evidence that one causes the other."
            if math.isfinite(pearson)
            else ""
        )
        + (
            f" Ratios use only the {len(eligible)} states with more than {minimum:g} {y_info['unit']}."
            if minimum and args.rank_by == "ratio"
            else ""
        )
    )
    method = (
        f"For {period} (the days both datasets cover), {args.x_measure}: {x_note}; "
        f"{args.y_measure}: {y_note}. Each state's two values were joined by state name"
        + (f"; the ratio is {x_key} ÷ {y_key}" if args.rank_by == "ratio" else "")
        + ". "
        + (
            "CAMS values are model estimates, not measurements, on a ~44 km grid. "
            if "cams_" in args.x_measure + args.y_measure
            else ""
        )
        + (
            "FIRMS counts are satellite detections, not fires or burned area, and include "
            "industrial heat sources. We acknowledge the use of data from NASA's FIRMS. "
            if "fire_" in args.x_measure + args.y_measure
            else ""
        )
        + (
            "Contains modified Copernicus Atmosphere Monitoring Service information."
            if "cams_" in args.x_measure + args.y_measure
            else ""
        )
    ).strip()
    return GriddedOutput(
        plan=QueryPlan(
            in_scope=True,
            summary_template=summary,
            method_note=method,
            visualization="bar",
            title=(
                f"States by {ratio_words}, {period}" if args.rank_by == "ratio"
                else f"States by {(x_info if args.rank_by == 'x' else y_info)['label']}, {period}"
            ),
            x_key="state",
            y_keys=[ratio_key if args.rank_by == "ratio" else (x_key if args.rank_by == "x" else y_key)],
        ),
        columns=list(rows[0]) if rows else [],
        rows=rows,
        grid=None,
    )


class CrossCompare:
    def run(self, name: str, arguments: Any) -> GriddedOutput:
        if name != "state_compare":
            raise ValueError(f"Unknown comparison: {name}")
        return state_compare(arguments)


cross_compare = CrossCompare()
