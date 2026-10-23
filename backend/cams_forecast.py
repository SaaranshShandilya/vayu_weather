"""CAMS air-quality forecasts, answered with xarray like the WRF weather grid.

``scripts/update_cams_forecast.py`` writes one NetCDF file per CAMS global
forecast run (``data/cams/cams_forecast_<run>.nc``): hourly (time, lat, lon)
fields on the 0.4° CAMS grid, 0-120 hours ahead, with times in IST, PM and
gases in µg/m³ (CO in mg/m³), aerosol optical depth, and each cell's state.

The engine always serves the newest run on disk, so a timer that fetches new
runs updates the app without a restart. It reuses the WRF engine's geography
(GeoJSON places, state masks, outlines, extremes and map payloads) and adds
hourly analyses: maps and animations, place series, hours above a threshold,
and state or city rankings. Every answer names the run it came from and says
these are model forecasts, not measurements.
"""

from __future__ import annotations

import datetime as dt
import math
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import xarray as xr
from pydantic import Field, model_validator

from .models import QueryPlan
from .places import places
from .weather_grid import (
    DATE_PATTERN,
    Domain,
    GriddedOutput,
    Region,
    Variable,
    WeatherArgs,
    WeatherGrid,
    _refusal,
    _round,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CAMS_DIR = PROJECT_ROOT / "data" / "cams"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

CAMS_TOOL_NAMES = frozenset(
    {
        "cams_forecast_map",
        "cams_forecast_series",
        "cams_forecast_threshold_map",
        "cams_forecast_rank",
    }
)

CamsPollutant = Literal["pm25", "pm10", "no2", "ozone", "so2", "co", "aod", "dust_aod"]

VARIABLES: dict[str, Variable] = {
    "pm25": Variable("pm25", "PM2.5", "µg/m³", "pm25"),
    "pm10": Variable("pm10", "PM10", "µg/m³", "pm10"),
    "no2": Variable("no2", "NO₂", "µg/m³", "pollutant"),
    "ozone": Variable("o3", "ozone", "µg/m³", "pollutant"),
    "so2": Variable("so2", "SO₂", "µg/m³", "pollutant"),
    "co": Variable("co", "CO", "mg/m³", "pollutant"),
    "aod": Variable("aod550", "total aerosol optical depth (550 nm)", "", "aod"),
    "dust_aod": Variable("duaod550", "dust aerosol optical depth (550 nm)", "", "aod"),
}
# CPCB 24-hour standards, used to describe daily forecast means only.
DAILY_STANDARDS = {"pm25": 60.0, "pm10": 100.0}
MAX_FRAMES = 24
FRAME_STEPS_HOURS = (1, 3, 6, 12, 24)


def _amount(value: float, variable: Variable) -> str:
    """A value with its unit; unitless optical depths get two decimals."""
    return f"{value:.1f} {variable.unit}" if variable.unit else f"{value:.2f}"


class CamsLatest:
    """The newest processed CAMS run on disk, reloaded when a newer one appears."""

    def __init__(self, directory: Path = CAMS_DIR) -> None:
        self.directory = directory
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._domain: Domain | None = None

    def domain(self) -> Domain | None:
        files = sorted(self.directory.glob("cams_forecast_*.nc"))
        if not files:
            return None
        newest = files[-1]
        if newest != self._path:
            with self._lock:
                if newest != self._path:
                    self._domain = Domain("cams", newest)
                    self._path = newest
        return self._domain


latest = CamsLatest()


def _window(domain: Domain) -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(domain.attrs["time_start"]), pd.Timestamp(domain.attrs["time_end"])


def _run_label(domain: Domain) -> str:
    return f"{pd.Timestamp(domain.attrs['run_ist']):%d %b %Y %H:%M} IST"


def routing_context() -> str:
    """Today's date and the forecast window, for the router (changes with each run)."""
    today = dt.datetime.now(IST)
    text = f"Today is {today:%A %d %B %Y}, {today:%H:%M} IST."
    domain = latest.domain()
    if domain is None:
        return text + " No CAMS forecast is installed."
    start, end = _window(domain)
    return (
        text
        + f" The latest CAMS forecast run was issued {_run_label(domain)} and covers "
        f"{start:%A %d %B %Y %H:%M} to {end:%A %d %B %Y %H:%M} IST "
        f"(dates {start:%Y-%m-%d} to {end:%Y-%m-%d})."
    )


TOOL_GUIDE = (
    "CAMS (Copernicus Atmosphere Monitoring Service) global air-quality forecast on a "
    "0.4° (~44 km) grid, hourly for 5 days from the latest run; the conversation "
    "context gives today's date and the exact forecast window. Pollutants: pm25, pm10, "
    "no2, ozone, so2 (µg/m³), co (mg/m³), aod and dust_aod (aerosol optical depth). "
    "Dates are IST days, YYYY-MM-DD; omit them for the whole forecast. Model "
    "forecast, not measurement; it is coarse for cities and its PM2.5 can differ from "
    "CPCB monitors over South Asia."
)


class CamsWindowArgs(WeatherArgs):
    pollutant: CamsPollutant
    start_date: str | None = Field(default=None, pattern=DATE_PATTERN)
    end_date: str | None = Field(default=None, pattern=DATE_PATTERN)

    @model_validator(mode="after")
    def validate_window(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be later than end_date")
        return self


class CamsRegionArgs(CamsWindowArgs):
    state: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_region(self):
        if self.city:
            self.state = None
        return self


class CamsMapArgs(CamsRegionArgs):
    statistic: Literal["mean", "max", "min", "sequence"] = "mean"
    order: Literal["highest", "lowest"] = "highest"


class CamsSeriesArgs(CamsWindowArgs):
    city: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    frequency: Literal["hourly", "daily"] = "hourly"

    @model_validator(mode="after")
    def validate_location(self):
        has_point = self.latitude is not None or self.longitude is not None
        if has_point and (self.latitude is None or self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        if not (self.city or self.state or has_point):
            raise ValueError("give a location: city, state, or latitude/longitude")
        if has_point:
            self.city = self.state = None
        elif self.city:
            self.state = None
        return self

    @property
    def region(self) -> None:
        """CAMS has one grid: no named WRF regions (read by the shared locator)."""
        return None


class CamsThresholdArgs(CamsRegionArgs):
    threshold: float = Field(gt=0, le=100_000)


class CamsRankArgs(CamsWindowArgs):
    level: Literal["state", "city"] = "state"
    statistic: Literal["mean", "max"] = "mean"
    order: Literal["highest", "lowest"] = "highest"
    limit: int = Field(default=10, ge=1, le=40)


ARGUMENT_MODELS = {
    "cams_forecast_map": CamsMapArgs,
    "cams_forecast_series": CamsSeriesArgs,
    "cams_forecast_threshold_map": CamsThresholdArgs,
    "cams_forecast_rank": CamsRankArgs,
}


class CamsForecast(WeatherGrid):
    """Answers forecast questions from the newest CAMS run."""

    time_unit = "hours"

    def __init__(self, source: CamsLatest = latest) -> None:
        super().__init__(domains={})
        self._source = source

    @property
    def domains(self) -> dict[str, Domain]:
        domain = self._source.domain()
        return {"cams": domain} if domain else {}

    @property
    def base(self) -> Domain | None:
        return self._source.domain()

    def _payload_source(self, domain: Domain) -> str:
        return f"CAMS forecast run of {_run_label(domain)}"

    @staticmethod
    def _source_note(domain: Domain) -> str:
        return (
            f" Source: CAMS global forecast run issued {_run_label(domain)} "
            f"({domain.step}° grid, ~{domain.dx_km:g} km), Copernicus Atmosphere "
            "Monitoring Service, CC-BY-4.0. Model forecast, not measurement; grid "
            "cells are area averages and can differ from CPCB monitors."
        )

    # Time --------------------------------------------------------------

    def _period(self, args: CamsWindowArgs) -> tuple[slice, str] | str:
        domain = self.base
        start, end = _window(domain)
        first = args.start_date or f"{start:%Y-%m-%d}"
        last = args.end_date or f"{end:%Y-%m-%d}"
        if last < f"{start:%Y-%m-%d}" or first > f"{end:%Y-%m-%d}":
            return (
                f"The latest CAMS forecast (issued {_run_label(domain)}) covers "
                f"{start:%d %b %Y %H:%M} to {end:%d %b %Y %H:%M} IST, so "
                f"{first}{'' if first == last else ' to ' + last} is outside it. For "
                "past days, use the CPCB station observations."
            )
        window = slice(f"{first}T00:00", f"{last}T23:59")
        times = domain.dataset()["time"].sel(time=window)
        label = (
            f"{pd.Timestamp(times.values[0]):%d %b %H:%M} – "
            f"{pd.Timestamp(times.values[-1]):%d %b %Y %H:%M} IST"
        )
        return window, label

    # Analyses ------------------------------------------------------------

    def run(self, name: str, arguments: WeatherArgs) -> GriddedOutput:
        if self.base is None:
            return _refusal(
                "No CAMS forecast is installed yet. Run scripts/update_cams_forecast.py."
            )
        if name == "cams_forecast_map":
            return self._map(arguments)  # type: ignore[arg-type]
        if name == "cams_forecast_series":
            return self._series(arguments)  # type: ignore[arg-type]
        if name == "cams_forecast_threshold_map":
            return self._threshold(arguments)  # type: ignore[arg-type]
        if name == "cams_forecast_rank":
            return self._rank(arguments)  # type: ignore[arg-type]
        raise ValueError(f"Unknown CAMS analysis: {name}")

    def _cube(self, args: CamsWindowArgs, region: Region | None = None):
        period = self._period(args)
        if isinstance(period, str):
            return period
        window, label = period
        variable = VARIABLES[args.pollutant]
        cube = self.base.field(variable.cube).sel(time=window)
        if region is not None:
            cube = cube.sel(lat=region.lat, lon=region.lon)
        return cube, label, variable

    def _frames(self, cube: xr.DataArray) -> tuple[list[tuple[str, xr.DataArray]], int]:
        hours = cube.sizes["time"]
        step = next((s for s in FRAME_STEPS_HOURS if math.ceil(hours / s) <= MAX_FRAMES), 24)
        frames = []
        for offset in range(0, hours, step):
            chunk = cube.isel(time=slice(offset, offset + step))
            first = pd.Timestamp(chunk["time"].values[0])
            label = f"{first:%a %d %b %H:%M} IST"
            if chunk.sizes["time"] > 1:
                last = pd.Timestamp(chunk["time"].values[-1])
                label = f"{first:%a %d %b %H:%M}–{last:%H:%M} IST (mean)"
            frames.append((label, chunk.mean("time")))
        return frames, step

    def _map(self, args: CamsMapArgs) -> GriddedOutput:
        region = self._region(args.state, args.city)
        if isinstance(region, str):
            return _refusal(region)
        cut = self._cube(args, region)
        if isinstance(cut, str):
            return _refusal(cut)
        cube, period, variable = cut
        domain = region.domain
        region = replace(region, mask=region.mask.sel(lat=cube["lat"], lon=cube["lon"]))
        hours = cube.sizes["time"]
        if args.statistic == "sequence":
            frames, step = self._frames(cube)
            field = cube.mean("time")
            value_key = f"mean_{args.pollutant}"
            words = f"mean {variable.label}"
            title = f"{variable.label} forecast every {step} h, {region.label}, {period}"
            value_label = f"{variable.label} forecast"
        else:
            field = self._reduce(cube, args.statistic)
            value_key = f"{args.statistic}_{args.pollutant}"
            words = {"mean": "mean", "max": "peak hourly", "min": "lowest hourly"}[args.statistic] + f" {variable.label}"
            frames = [(period, field)]
            title = f"Forecast {words}, {region.label}, {period}"
            value_label = f"Forecast {words}"
            step = 0
        unit = variable.unit
        values = field.where(region.mask)
        rows = self._extremes(field, region, value_key, args.order, digits=1 if unit else 2)
        if not rows:
            return _refusal(f"No grid cells fall inside {region.label}.")
        top = rows[0]
        cells = int(values.count())
        summary = (
            f"For {period}, the CAMS forecast {words} across {region.label} averages "
            f"{_amount(float(values.mean()), variable)}"
            + (
                f" over {cells:,} grid cells, ranging from {_amount(float(values.min()), variable)} to "
                f"{_amount(float(values.max()), variable)}. The {args.order} cell is "
                f"{_amount(top[value_key], variable)} at {top['latitude']:.2f}°N, {top['longitude']:.2f}°E in "
                f"{top['state']}, {top['distance_km']:.0f} km from {top['nearest_city']}."
                if cells > 1
                else f" in its grid cell at {top['latitude']:.2f}°N, {top['longitude']:.2f}°E."
            )
            + f" Forecast issued {_run_label(domain)}."
        )
        reduction = (
            f"averaged every {step} h into {len(frames)} frames" if args.statistic == "sequence"
            else f"took the {words.split(' ')[0]} over {hours} hourly steps"
        )
        method = (
            f"Took the CAMS {variable.label} forecast cube ({hours} hours × {cube.sizes['lat']} × "
            f"{cube.sizes['lon']} cells) for {period} and {reduction} with xarray. Statistics "
            f"use {region.note}."
            + (" The map also shows the surrounding cells, with the boundary outlined." if region.context else "")
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

    def _series(self, args: CamsSeriesArgs) -> GriddedOutput:
        located = self._locate(args)  # type: ignore[arg-type]
        if isinstance(located, str):
            return _refusal(located)
        domain, mask, place, origin, _ = located
        cut = self._cube(args)
        if isinstance(cut, str):
            return _refusal(cut)
        cube, period, variable = cut
        series = cube.where(mask).mean(["lat", "lon"])
        unit = variable.unit
        digits = 1 if unit else 2
        key = args.pollutant
        rows: list[dict[str, Any]] = []
        if args.frequency == "hourly":
            for stamp, value in zip(series["time"].values, series.values):
                rows.append({"time": f"{pd.Timestamp(stamp):%Y-%m-%d %H:%M}", key: _round(float(value), digits)})
            x_key, y_keys = "time", [key]
        else:
            days = series.groupby("time.date")
            for day, values in days:
                rows.append(
                    {
                        "date": f"{day}",
                        f"mean_{key}": _round(float(values.mean()), digits),
                        f"max_{key}": _round(float(values.max()), digits),
                        "hours": int(values.count()),
                    }
                )
            x_key, y_keys = "date", [f"mean_{key}"]
        peak = series.idxmax("time")
        low = series.idxmin("time")
        preposition = "Across" if int(mask.sum()) > 1 else "At"
        summary = (
            f"{preposition} {place}, the CAMS forecast {variable.label} for {period} averages "
            f"{_amount(float(series.mean()), variable)}, peaking at "
            f"{_amount(float(series.sel(time=peak)), variable)} at "
            f"{pd.Timestamp(peak.values):%a %d %b %H:%M} IST and lowest at "
            f"{_amount(float(series.sel(time=low)), variable)} at "
            f"{pd.Timestamp(low.values):%a %d %b %H:%M} IST."
        )
        standard = DAILY_STANDARDS.get(args.pollutant)
        if standard:
            daily = series.groupby("time.date")
            full = [(day, float(v.mean())) for day, v in daily if int(v.count()) == 24]
            above = [f"{day:%d %b}" for day, mean in full if mean > standard]
            if full:
                summary += (
                    f" Of the {len(full)} full IST days in the window, {len(above)} have a forecast "
                    f"daily mean above the CPCB 24-hour standard of {standard:g} µg/m³"
                    + (f" ({', '.join(above)})." if above else ".")
                )
        summary += f" Forecast issued {_run_label(domain)}."
        partial = [row for row in rows if row.get("hours", 24) < 24]
        method = (
            f"Read {origin} from the CAMS {variable.label} forecast ({int(series.count())} hourly steps)"
            + (", then took each IST day's mean and maximum" if args.frequency == "daily" else "")
            + "."
            + (
                f" {len(partial)} day(s) at the edge of the forecast window are partial; their "
                "'hours' column says how many hourly steps they contain."
                if partial
                else ""
            )
            + self._source_note(domain)
        )
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="line",
                title=f"CAMS {variable.label} forecast at {place}, {period}",
                x_key=x_key,
                y_keys=y_keys,
            ),
            columns=list(rows[0]) if rows else [],
            rows=rows,
            grid=None,
        )

    def _threshold(self, args: CamsThresholdArgs) -> GriddedOutput:
        region = self._region(args.state, args.city)
        if isinstance(region, str):
            return _refusal(region)
        cut = self._cube(args, region)
        if isinstance(cut, str):
            return _refusal(cut)
        cube, period, variable = cut
        domain = region.domain
        mask = region.mask.sel(lat=cube["lat"], lon=cube["lon"])
        region = replace(region, mask=mask)
        above = cube > args.threshold
        hours_above = above.sum("time").astype("float32")
        cells = int(mask.sum())
        share = above.where(mask).sum(["lat", "lon"]) / cells * 100
        rows = []
        for day, shares in share.groupby("time.date"):
            peak = shares.idxmax("time")
            rows.append(
                {
                    "date": f"{day}",
                    "hours": int(shares.count()),
                    "mean_area_above_pct": _round(float(shares.mean())),
                    "peak_area_above_pct": _round(float(shares.max())),
                    "peak_time": f"{pd.Timestamp(peak.values):%H:%M}",
                }
            )
        counts = hours_above.where(mask)
        most = counts.where(counts == counts.max(), drop=True)
        lat, lon = float(most["lat"].values[0]), float(most["lon"].values[0])
        city, distance = self._nearest_city(lat, lon)
        unit = variable.unit
        condition = f"{variable.label} above {args.threshold:g}" + (f" {unit}" if unit else "")
        summary = (
            f"For {period} ({cube.sizes['time']} forecast hours), "
            f"{float((counts > 0).sum() / counts.count()) * 100:.0f}% of {region.label} is forecast "
            f"to have at least one hour with {condition}. A grid cell averages "
            f"{float(counts.mean()):.1f} such hours; the most is {float(counts.max()):.0f} hours at "
            f"{lat:.2f}°N, {lon:.2f}°E, {distance:.0f} km from {city}. Forecast issued {_run_label(domain)}."
        )
        method = (
            f"Compared every hourly value of the CAMS {variable.label} forecast cube "
            f"({cube.sizes['time']} hours × {cube.sizes['lat']} × {cube.sizes['lon']} cells) with "
            f"{args.threshold:g}" + (f" {unit}" if unit else "")
            + ", then counted hours above it per cell (the map) and the share of "
            f"{region.note} above it per hour, summarised by IST day (the table). Hourly values are "
            "compared, so this is not a 24-hour-standard exceedance."
            + self._source_note(domain)
        )
        grid = self._grid_payload(
            [(period, hours_above)],
            region,
            value_label=f"Forecast hours with {condition}",
            unit="hours",
            scale="hours",
            scale_max=float(max(1.0, float(counts.max()))),
        )
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="grid_heatmap",
                title=f"Forecast hours with {condition}, {region.label}, {period}",
                x_key="date",
                y_keys=["mean_area_above_pct"],
            ),
            columns=list(rows[0]) if rows else [],
            rows=rows,
            grid=grid,
        )

    def _rank(self, args: CamsRankArgs) -> GriddedOutput:
        cut = self._cube(args)
        if isinstance(cut, str):
            return _refusal(cut)
        cube, period, variable = cut
        domain = self.base
        field = self._reduce(cube, args.statistic)
        key = f"{args.statistic}_{args.pollutant}"
        digits = 1 if variable.unit else 2
        entries = []
        if args.level == "state":
            index = domain.state_index().rename("state_index")
            by_state = field.groupby(index).mean()
            for number, value in zip(by_state["state_index"].values, by_state.values):
                if number >= 0:
                    entries.append({"state": domain.states[int(number)], key: _round(float(value), digits)})
        else:
            cities = places.cities()
            lats = xr.DataArray([c[1].y for c in cities], dims="city")
            lons = xr.DataArray([c[1].x for c in cities], dims="city")
            at_cities = field.sel(lat=lats, lon=lons, method="nearest")
            for (city, centre, _), value in zip(cities, at_cities.values):
                if domain.extent.contains(centre):
                    entries.append({"city": city, key: _round(float(value), digits)})
        entries = [e for e in entries if e[key] is not None]
        entries.sort(key=lambda e: e[key], reverse=args.order == "highest")
        rows = [{"rank": n + 1, **e} for n, e in enumerate(entries[: args.limit])]
        label_key = "state" if args.level == "state" else "city"
        words = ("mean" if args.statistic == "mean" else "peak hourly") + f" {variable.label}"
        listed = "; ".join(f"{r[label_key]} {_amount(r[key], variable)}" for r in rows[:5])
        summary = (
            f"Ranked by CAMS forecast {words} for {period}, the {args.order} "
            f"{'states and union territories' if args.level == 'state' else 'cities'} are: {listed}. "
            f"Forecast issued {_run_label(domain)}."
        )
        how = (
            "grouped the cells by the state containing their centres and took each group's mean"
            if args.level == "state"
            else f"read the cell nearest each station-registry city's centre ({len(entries)} cities)"
        )
        method = (
            f"Took the {'mean' if args.statistic == 'mean' else 'maximum'} of the CAMS {variable.label} "
            f"forecast over {cube.sizes['time']} hourly steps per cell, then {how}."
            + self._source_note(domain)
        )
        return GriddedOutput(
            plan=QueryPlan(
                in_scope=True,
                summary_template=summary,
                method_note=method,
                visualization="bar",
                title=f"{label_key.capitalize()}s by forecast {words}, {period}",
                x_key=label_key,
                y_keys=[key],
            ),
            columns=list(rows[0]) if rows else [],
            rows=rows,
            grid=None,
        )


cams_forecast = CamsForecast()
