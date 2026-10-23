"""The WRF weather engine, checked against independent xarray reads of the files."""

from __future__ import annotations

import json

import numpy as np
import pytest
import shapely
import xarray as xr
from pydantic import ValidationError

from backend.analysis_tools import TOOL_BY_NAME, build_analysis
from backend.app import (
    capability_suggested_questions,
    display_result_set,
    execute_analysis,
    requested_city_names,
    routing_alignment_issue,
)
from backend.database import database as app_database
from backend.gemini_service import RoutedToolCall
from backend.models import ChatResponse
from backend.places import places
from backend.weather_grid import (
    BASE,
    DOMAINS,
    LAST_DAY,
    WEATHER_TOOL_NAMES,
    WeatherGridMapArgs,
    WeatherPointSeriesArgs,
    best_domain,
    finest_domain,
)


pytestmark = pytest.mark.skipif(BASE is None, reason="Build data with scripts/build_wrf_weather.py")
needs_nests = pytest.mark.skipif(
    not {"d02", "d03", "d04"} <= set(DOMAINS), reason="Build the 9 km nests d02-d04"
)


@pytest.fixture(scope="module", autouse=True)
def initialized_database():
    app_database.initialize()


def masks(dataset):
    """State index from the detailed GeoJSON (the source of truth for masks)."""
    index, names = places.state_index_grid(dataset["lat"].values, dataset["lon"].values)
    return xr.DataArray(index, coords={"lat": dataset["lat"], "lon": dataset["lon"]}, dims=("lat", "lon")), names


def open_domain(name: str) -> xr.Dataset:
    return xr.open_dataset(DOMAINS[name].path)


def run(name: str, **arguments):
    return execute_analysis(RoutedToolCall(name=name, arguments=arguments))


def grid_values(result) -> np.ndarray:
    grid = result.grid
    values = [np.nan if v is None else v for v in grid["frames"][0]["values"]]
    return np.array(values, dtype=float).reshape(len(grid["latitudes"]), len(grid["longitudes"]))


def test_weather_tools_are_declared_to_the_router():
    assert WEATHER_TOOL_NAMES <= set(TOOL_BY_NAME)
    for name in WEATHER_TOOL_NAMES:
        assert "WRF" in TOOL_BY_NAME[name].description
        assert LAST_DAY in TOOL_BY_NAME[name].description


def test_each_domain_file_is_tagged_and_daily():
    for name, domain in DOMAINS.items():
        with open_domain(name) as dataset:
            assert dataset.attrs["domain"] == name
            assert dataset["t2_max"].dims == ("time", "lat", "lon")
            days = dataset["time"].to_index()
            assert (days[1:] - days[:-1]).days.unique().tolist() == [1]
            assert str(days[0].date()) == "2023-01-01" and str(days[-1].date()) == LAST_DAY
            assert np.allclose(np.diff(dataset["lat"]), domain.step)
            present = dataset["t2_max"].notnull()
            assert bool((dataset["t2_max"] >= dataset["t2_min"]).where(present, True).all())
            assert bool((dataset["rain"] >= 0).where(dataset["rain"].notnull(), True).all())
            # Damaged days are NaN everywhere, never zero-filled.
            for day in json.loads(dataset.attrs["missing_days"]):
                assert bool(dataset["t2_max"].sel(time=day).isnull().all())
    assert DOMAINS["d01"].step == 0.25


@needs_nests
def test_finest_domain_covering_95_percent_is_chosen():
    def domain_for(name):
        return best_domain(places.find(name).geometry)[0].name

    assert domain_for("Delhi") == "d02"
    assert domain_for("Chennai") == "d03"
    assert domain_for("Kolkata") == "d04"
    assert domain_for("Mumbai") == "d01"  # outside every 9 km nest
    assert domain_for("Rajasthan") == "d01"  # d02 covers under half of it
    # Nearly covered states now use the 9 km nest.
    assert domain_for("Haryana") == "d02"
    assert domain_for("Karnataka") == "d03"
    assert domain_for("Uttar Pradesh") == "d01"  # d02 covers ~73%
    assert finest_domain((68, 7, 97, 35)).name == "d01"


@needs_nests
def test_part_left_out_of_a_nearly_covered_place_is_reported():
    haryana = run("weather_grid_map", variable="max_temperature", state="Haryana", start_date="2024-05-25", end_date="2024-05-31")
    assert haryana.grid["domain"] == "d02"
    summary = haryana.plan.summary_template
    assert "covers 99.8% of Haryana" in summary and "north of 30.84°N" in summary
    up = run("weather_grid_map", variable="max_temperature", state="Uttar Pradesh", start_date="2024-05-25", end_date="2024-05-31")
    assert up.grid["domain"] == "d01"
    assert "covers only 73%" in up.plan.summary_template and "95%" in up.plan.summary_template


@needs_nests
def test_named_region_maps_a_whole_nest():
    result = run(
        "weather_grid_map",
        variable="max_temperature",
        region="north_india",
        start_date="2024-05-25",
        end_date="2024-05-31",
    )
    with open_domain("d02") as dataset:
        india = (masks(dataset)[0] >= 0).values
        expected = dataset["t2_max"].sel(time=slice("2024-05-25", "2024-05-31")).mean("time")
        values = grid_values(result)
        assert values.shape == expected.shape  # the whole d02 grid
        np.testing.assert_allclose(values[india], expected.values[india], atol=0.06)
    assert result.grid["domain"] == "d02"
    assert "North India here means this domain's box" in result.plan.summary_template
    assert result.rows[0]["nearest_city"]
    # A more specific place wins over a region.
    assert WeatherGridMapArgs(variable="rainfall", region="south_india", city="Chennai").region is None
    series = run("weather_point_series", variable="rainfall", region="south_india", start_date="2023-12-01", end_date="2023-12-06")
    assert series.plan.in_scope and "South India (area average)" in series.plan.summary_template


def test_india_map_matches_an_independent_xarray_mean():
    result = run("weather_grid_map", variable="max_temperature", start_date="2024-05-01", end_date="2024-05-31")
    with open_domain("d01") as dataset:
        expected = dataset["t2_max"].sel(time=slice("2024-05-01", "2024-05-31")).mean("time")
        india = masks(dataset)[0] >= 0
        values = grid_values(result)
        np.testing.assert_allclose(values[india.values], expected.values[india.values], atol=0.06)
        assert np.isnan(values[~india.values]).all()
        assert result.rows[0]["mean_max_temperature"] == pytest.approx(
            float(expected.where(india).max()), abs=0.06
        )
    assert result.grid["domain"] == "d01" and "d01" in result.plan.summary_template
    ChatResponse.model_validate(
        {
            "answer": "x",
            "key_points": [],
            "method_note": result.plan.method_note,
            **{k: v for k, v in display_result_set(result).items() if k != "analysis_function"},
        }
    )


def test_place_statistics_use_the_geojson_boundary():
    """Delhi = the NCT polygon: its cells, its outline, on the finest domain."""
    result = run(
        "weather_point_series",
        variable="max_temperature",
        city="Delhi",
        start_date="2024-05-25",
        end_date="2024-05-31",
    )
    place = places.find("Delhi")
    domain = best_domain(place.geometry)[0]
    with open_domain(domain.name) as dataset:
        lon2d, lat2d = np.meshgrid(dataset["lon"], dataset["lat"])
        inside = xr.DataArray(
            shapely.contains_xy(place.geometry, lon2d, lat2d), coords=dataset["state_index"].coords
        )
        assert int(inside.sum()) >= 1
        expected = (
            dataset["t2_max"].sel(time=slice("2024-05-25", "2024-05-31")).where(inside).mean(["lat", "lon"])
        )
    by_day = {row["date"]: row["max_temperature"] for row in result.rows}
    assert by_day["2024-05-28"] == pytest.approx(float(expected.sel(time="2024-05-28")), abs=0.06)
    assert f"Domain used: {domain.label}" in result.plan.summary_template
    assert "Delhi state/UT boundary" in result.plan.method_note

    animated = run(
        "weather_grid_map",
        variable="max_temperature",
        city="Delhi",
        statistic="sequence",
        start_date="2024-12-25",
        end_date="2024-12-31",
    )
    assert len(animated.grid["frames"]) == 7
    assert animated.grid["outline"] and animated.grid["outline_label"] == "Delhi"
    assert animated.grid["domain"] == domain.name


def test_rainfall_total_sums_days_and_total_is_rainfall_only():
    result = run(
        "weather_grid_map",
        variable="rainfall",
        statistic="total",
        start_date="2023-06-01",
        end_date="2023-09-30",
        state="Kerala",
    )
    domain = DOMAINS[result.grid["domain"]]
    with open_domain(domain.name) as dataset:
        index, names = masks(dataset)
        kerala = index == names.index("Kerala")
        total = dataset["rain"].sel(time=slice("2023-06-01", "2023-09-30")).sum("time", min_count=1)
        assert result.rows[0]["total_rainfall"] == pytest.approx(float(total.where(kerala).max()), rel=1e-4)
    assert {row["state"] for row in result.rows} == {"Kerala"}
    with pytest.raises(ValidationError):
        WeatherGridMapArgs(variable="temperature", statistic="total")


def test_sequence_animation_caps_frames():
    long = run("weather_grid_map", variable="temperature", statistic="sequence")
    assert len(long.grid["frames"]) <= 24
    assert max(len(long.grid["latitudes"]), len(long.grid["longitudes"])) <= 90


def test_city_monthly_rainfall_uses_xarray_resample():
    result = run(
        "weather_point_series",
        variable="rainfall",
        city="Bombay",
        start_date="2023-01-01",
        end_date="2023-12-31",
        frequency="monthly",
    )
    assert [row["period"] for row in result.rows] == [f"2023-{m:02d}" for m in range(1, 13)]
    assert result.plan.visualization == "bar"
    # Mumbai district is smaller than a cell: the nearest cell is used, and said.
    assert "nearest" in result.plan.method_note
    monsoon = sum(row["rainfall"] for row in result.rows[5:9])
    assert monsoon > 0.7 * sum(row["rainfall"] for row in result.rows)


def test_wind_series_adds_direction():
    wind = run(
        "weather_point_series",
        variable="wind_speed",
        city="Chennai",
        start_date="2023-07-01",
        end_date="2023-07-31",
        frequency="weekly",
    )
    assert {"wind_direction", "wind_from", "days"} <= set(wind.columns)
    assert all(0 <= row["wind_direction"] < 360 for row in wind.rows)


def test_threshold_map_counts_days():
    result = run(
        "weather_threshold_map",
        variable="max_temperature",
        threshold=40,
        start_date="2024-05-01",
        end_date="2024-06-30",
    )
    with open_domain("d01") as dataset:
        india = (masks(dataset)[0] >= 0).values
        expected = (dataset["t2_max"].sel(time=slice("2024-05-01", "2024-06-30")) > 40).sum("time")
        values = grid_values(result)
        np.testing.assert_array_equal(values[india], expected.values[india])
    assert result.grid["scale"] == "days"
    assert [row["period"] for row in result.rows] == ["2024-05", "2024-06"]
    assert sum(row["days"] for row in result.rows) == 61


def test_state_rank_is_a_groupby_mean_on_d01():
    result = run(
        "weather_region_rank",
        variable="max_temperature",
        start_date="2024-05-01",
        end_date="2024-05-31",
        limit=36,
    )
    values = [row["mean_max_temperature"] for row in result.rows]
    assert values == sorted(values, reverse=True)
    with open_domain("d01") as dataset:
        field = dataset["t2_max"].sel(time=slice("2024-05-01", "2024-05-31")).mean("time")
        top = result.rows[0]
        index, names = masks(dataset)
        assert top["mean_max_temperature"] == pytest.approx(
            float(field.where(index == names.index(top["state"])).mean()), abs=0.06
        )
    assert "Lakshadweep" in result.plan.method_note
    assert "d01" in result.plan.summary_template


def test_windows_are_validated_and_clipped():
    outside = run("weather_point_series", variable="temperature", city="Delhi", start_date="2022-03-01", end_date="2022-03-31")
    assert not outside.plan.in_scope and "outside it" in outside.plan.refusal
    with pytest.raises(ValidationError):
        WeatherGridMapArgs(variable="temperature", start_date="2024-02-01", end_date="2024-01-01")
    clipped = WeatherGridMapArgs(variable="temperature", start_date="2024-12-01", end_date="2025-03-31")
    assert clipped.end_date == LAST_DAY


def test_unresolvable_places_are_refused_not_guessed():
    # A UT smaller than a cell uses the nearest cell, and says so.
    tiny = run("weather_grid_map", variable="temperature", state="Lakshadweep")
    assert tiny.plan.in_scope and "smaller than one" in tiny.plan.method_note
    unknown = run("weather_point_series", variable="temperature", city="Atlantis")
    assert not unknown.plan.in_scope and "boundary files" in unknown.plan.refusal
    outside = run("weather_point_series", variable="temperature", latitude=51.5, longitude=-0.1)
    assert not outside.plan.in_scope and "outside" in outside.plan.refusal


def test_places_come_from_geojson():
    delhi = places.find("New Delhi")
    assert delhi.kind == "state" and delhi.name == "Delhi"
    assert 28.4 < delhi.latitude < 28.9 and 76.8 < delhi.longitude < 77.4
    assert places.find("Bengaluru").name == "Bangalore"
    hyderabad = places.find("Hyderabad")
    assert hyderabad.kind == "station city" and "contains Hyderabad's monitoring stations" in hyderabad.note
    assert "2 districts share this name" in places.find("Aurangabad").note
    assert places.find("Aurangabad", state="Maharashtra").state == "Maharashtra"
    assert places.find("Atlantis") is None


def test_build_analysis_dispatches_weather_tools():
    plan = build_analysis(
        "weather_region_rank", {"variable": "humidity", "start_date": "2023-08-01", "end_date": "2023-08-31"}
    ).plan
    assert plan.in_scope and plan.visualization == "bar"


def test_routing_accepts_weather_plans():
    hottest = RoutedToolCall(
        "weather_region_rank",
        {"variable": "max_temperature", "start_date": "2024-05-01", "end_date": "2024-05-31"},
    )
    assert routing_alignment_issue("Which states were hottest in May 2024?", [hottest]) is None
    delhi_state = RoutedToolCall("weather_grid_map", {"variable": "temperature", "state": "Delhi"})
    assert routing_alignment_issue("Map the temperature across Delhi.", [delhi_state]) is None
    rain = RoutedToolCall("weather_point_series", {"variable": "rainfall", "city": "Mumbai"})
    assert routing_alignment_issue("Show the monthly rainfall for Mumbai.", [rain]) is None
    assert requested_city_names("weather_point_series", rain.arguments) == ["Mumbai"]


def test_routing_rejects_invented_weather_threshold():
    call = RoutedToolCall("weather_threshold_map", {"variable": "max_temperature", "threshold": 40})
    issue = routing_alignment_issue("Map the very hot days across India.", [call])
    assert issue and "threshold" in issue


def test_weather_follow_ups_are_capability_backed():
    result = run("weather_point_series", variable="rainfall", city="Mumbai", frequency="monthly")
    suggestions = capability_suggested_questions([result])
    assert len(suggestions) == 3
    assert any("Mumbai" in question for question in suggestions)


def test_missing_days_are_reported_not_invented():
    missing = BASE.missing_days
    if not missing:
        pytest.skip("d01 has no missing days")
    series = run(
        "weather_point_series", variable="temperature", city="Mumbai", start_date="2023-10-10", end_date="2023-10-15"
    )
    by_date = {row["date"]: row["temperature"] for row in series.rows}
    assert by_date[missing[0]] is None
    assert by_date["2023-10-10"] is not None
    assert missing[0] in series.plan.summary_template
    gap = run("weather_grid_map", variable="temperature", start_date=missing[0], end_date=missing[0])
    assert not gap.plan.in_scope and "missing" in gap.plan.refusal
    rank = run(
        "weather_region_rank", variable="rainfall", statistic="total", start_date="2023-10-10", end_date="2023-10-15"
    )
    assert all(row["total_rainfall"] is not None for row in rank.rows)


def test_declared_schemas_carry_bounds_as_text_not_constraints():
    """Gemini rejects the whole tool list once constraints exceed its budget."""
    from backend.analysis_tools import SCHEMA_BOUND_WORDS, TOOL_DEFINITIONS

    for definition in TOOL_DEFINITIONS:
        declared = definition.declaration()["parameters"]
        text = json.dumps(declared)
        assert not any(f'"{key}"' in text for key in SCHEMA_BOUND_WORDS), definition.name
        assert set(declared["properties"]) == set(definition.arguments_model.model_fields)
    limit = TOOL_BY_NAME["weather_region_rank"].declaration()["parameters"]["properties"]["limit"]
    assert limit["description"] == "Must be at least 1, at most 40."
    with pytest.raises(ValidationError):
        TOOL_BY_NAME["weather_region_rank"].arguments_model.model_validate({"variable": "rainfall", "limit": 99})


def test_point_series_takes_the_most_specific_location():
    both = WeatherPointSeriesArgs(variable="wind_speed", city="Chennai", state="Tamil Nadu")
    assert both.city == "Chennai" and both.state is None
    point = WeatherPointSeriesArgs(variable="humidity", city="Delhi", latitude=28.6, longitude=77.2)
    assert point.city is None
    with pytest.raises(ValidationError):
        WeatherPointSeriesArgs(variable="humidity")
    state = run("weather_point_series", variable="temperature", state="Rajasthan", start_date="2024-06-01", end_date="2024-06-01")
    assert state.plan.summary_template.startswith("Across Rajasthan (area average)")
    assert "highest day" not in state.plan.summary_template


def test_presenter_degree_glitches_are_repaired_without_false_positives():
    from backend.app import clean_model_text

    assert clean_model_text("-14.3 ƒC") == "-14.3 °C"
    assert clean_model_text("-14.3 \x84ƒC") == "-14.3 °C"
    assert clean_model_text("averages 37.4 ƒ over") == "averages 37.4 °C over"
    assert clean_model_text("a 30º angle") == "a 30º angle"
    assert clean_model_text("Srinagar was 3.5 C") == "Srinagar was 3.5 °C"
    assert clean_model_text("PM2.5 in Zone 2 C") == "PM2.5 in Zone 2 C"
    assert clean_model_text("Category C cities") == "Category C cities"


def test_plural_measure_words_are_recognised():
    """'max temperatures' names temperature; a correct plan must not be rejected."""
    nagpur = RoutedToolCall(
        "weather_grid_map",
        {
            "variable": "max_temperature",
            "city": "nagpur",
            "start_date": "2024-05-20",
            "end_date": "2024-06-05",
            "statistic": "sequence",
        },
    )
    question = "Animate the max temperatures for nagpur from 20 May to 5 June 2024"
    assert routing_alignment_issue(question, [nagpur]) is None
    rains = RoutedToolCall("weather_point_series", {"variable": "rainfall", "city": "Mumbai"})
    assert routing_alignment_issue("Show the monsoon rains in Mumbai in 2023.", [rains]) is None
    winds = RoutedToolCall("weather_point_series", {"variable": "wind_speed", "city": "Chennai"})
    assert routing_alignment_issue("Show daily wind speeds in Chennai in July 2023.", [winds]) is None
