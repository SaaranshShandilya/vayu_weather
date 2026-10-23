"""The CAMS forecast engine, checked against independent xarray reads."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from backend.analysis_tools import TOOL_BY_NAME
from backend.app import execute_analysis, routing_alignment_issue
from backend.cams_forecast import CAMS_TOOL_NAMES, latest, routing_context
from backend.database import database as app_database
from backend.gemini_service import RoutedToolCall
from backend.places import places


DOMAIN = latest.domain()
pytestmark = pytest.mark.skipif(DOMAIN is None, reason="Fetch a run with scripts/update_cams_forecast.py")


@pytest.fixture(scope="module", autouse=True)
def initialized_database():
    app_database.initialize()


@pytest.fixture(scope="module")
def run():
    return xr.open_dataset(DOMAIN.path)


def masks(dataset):
    """State index from the detailed GeoJSON (the source of truth for masks)."""
    index, names = places.state_index_grid(dataset["lat"].values, dataset["lon"].values)
    return xr.DataArray(index, coords={"lat": dataset["lat"], "lon": dataset["lon"]}, dims=("lat", "lon")), names


def ask(name: str, **arguments):
    return execute_analysis(RoutedToolCall(name=name, arguments=arguments))


def full_day(run) -> str:
    """An IST day the run covers completely (24 hourly steps)."""
    counts = run["time"].to_index().normalize().value_counts()
    return str(counts[counts == 24].sort_index().index[0].date())


def test_tools_are_declared_and_router_gets_the_window(run):
    assert CAMS_TOOL_NAMES <= set(TOOL_BY_NAME)
    context = routing_context()
    assert "Today is" in context and DOMAIN.attrs["time_start"][:10] in context


def test_processed_run_has_cpcb_units_and_ist_times(run):
    assert run["pm25"].attrs["units"] == "µg/m³" and run["co"].attrs["units"] == "mg/m³"
    times = run["time"].to_index()
    assert len(times) == 121 and (times[1:] - times[:-1] == pd.Timedelta(hours=1)).all()
    run_utc = pd.Timestamp(run.attrs["run_utc"])
    assert times[0] == run_utc + pd.Timedelta(hours=5, minutes=30)  # IST valid times
    assert bool((run["pm25"] >= 0).all())
    # PM10 contains PM2.5; CAMS can dip below by rounding at near-zero values.
    assert bool((run["pm10"] >= run["pm25"] - 0.05).all())


def test_india_map_matches_an_independent_mean(run):
    day = full_day(run)
    result = ask("cams_forecast_map", pollutant="pm25", start_date=day, end_date=day)
    expected = run["pm25"].sel(time=day).mean("time")
    india = (masks(run)[0] >= 0).values
    grid = result.grid
    values = np.array([np.nan if v is None else v for v in grid["frames"][0]["values"]]).reshape(expected.shape)
    np.testing.assert_allclose(values[india], expected.values[india], atol=0.06)
    assert grid["scale"] == "pm25" and grid["time_unit"] == "hours" and grid["domain"] == "cams"
    assert "Forecast issued" in result.plan.summary_template
    assert "Copernicus" in result.plan.method_note and "not measurement" in result.plan.method_note


def test_city_series_daily_rows_flag_partial_days(run):
    result = ask("cams_forecast_series", pollutant="pm25", city="Lucknow", frequency="daily")
    hours = [row["hours"] for row in result.rows]
    assert sum(hours) == 121 and hours[0] < 24 and hours[-1] < 24
    assert "partial" in result.plan.method_note
    assert "CPCB 24-hour standard of 60" in result.plan.summary_template


def test_animation_uses_whole_hour_steps_within_24_frames():
    result = ask("cams_forecast_map", pollutant="pm25", city="Delhi", statistic="sequence")
    assert 1 < len(result.grid["frames"]) <= 24
    assert result.grid["outline_label"] == "Delhi"
    assert len(result.grid["latitudes"]) >= 3  # context around the 0.4° cell


def test_threshold_counts_hours(run):
    result = ask("cams_forecast_threshold_map", pollutant="pm25", threshold=60, state="Uttar Pradesh")
    index, names = masks(run)
    up = (index == names.index("Uttar Pradesh")).values
    expected = (run["pm25"] > 60).sum("time").values
    values = result.grid["frames"][0]["values"]
    grid = np.array([np.nan if v is None else v for v in values]).reshape(
        len(result.grid["latitudes"]), len(result.grid["longitudes"])
    )
    assert np.nanmax(grid) == expected[up].max()
    assert result.grid["scale"] == "hours"


def test_rank_and_optical_depth_formatting():
    day = full_day(xr.open_dataset(DOMAIN.path))
    ranked = ask("cams_forecast_rank", pollutant="pm25", start_date=day, end_date=day, limit=5)
    values = [row["mean_pm25"] for row in ranked.rows]
    assert values == sorted(values, reverse=True)
    dust = ask("cams_forecast_map", pollutant="dust_aod", state="Rajasthan")
    assert " ." not in dust.plan.summary_template and dust.grid["unit"] == ""


def test_dates_outside_the_run_are_refused():
    result = ask("cams_forecast_map", pollutant="pm25", start_date="2099-01-01", end_date="2099-01-02")
    assert not result.plan.in_scope and "covers" in result.plan.refusal


def test_forecast_plans_pass_validation():
    call = RoutedToolCall("cams_forecast_series", {"pollutant": "pm25", "city": "Lucknow"})
    assert routing_alignment_issue("What will PM2.5 be in Lucknow on Friday?", [call]) is None
    ozone = RoutedToolCall("cams_forecast_map", {"pollutant": "ozone", "city": "Kolkata"})
    assert routing_alignment_issue("Map the ozone forecast around Kolkata tomorrow.", [ozone]) is None
    threshold = RoutedToolCall("cams_forecast_threshold_map", {"pollutant": "pm25", "threshold": 60})
    issue = routing_alignment_issue("Map the hours of bad PM2.5 forecast tomorrow.", [threshold])
    assert issue and "threshold" in issue
