from __future__ import annotations

import numpy as np
import pytest
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
from backend.gridded import (
    ARRAY_PATH,
    GRIDDED_TOOL_NAMES,
    ForecastPointSeriesArgs,
    gridded_forecast,
)
from backend.models import ChatResponse


@pytest.fixture(scope="module", autouse=True)
def initialized_database():
    app_database.initialize()


@pytest.fixture(scope="module")
def cube():
    with np.load(ARRAY_PATH) as archive:
        return {
            "pm25": archive["pm25"],
            "latitude": archive["latitude"],
            "longitude": archive["longitude"],
        }


def run(name: str, **arguments):
    return execute_analysis(RoutedToolCall(name=name, arguments=arguments))


def test_gridded_tools_are_declared_to_the_router():
    assert GRIDDED_TOOL_NAMES <= set(TOOL_BY_NAME)
    for name in GRIDDED_TOOL_NAMES:
        assert "Delhi NCR" in TOOL_BY_NAME[name].description


def test_sample_cube_has_time_lat_lon_layout(cube):
    assert cube["pm25"].shape == (72, len(cube["latitude"]), len(cube["longitude"]))
    assert np.all(np.diff(cube["latitude"]) > 0)
    assert np.isfinite(cube["pm25"]).all()


def test_grid_map_mean_matches_array_reduction(cube):
    result = run("forecast_grid_map", pollutant="pm25", start_hour=24, end_hour=47)
    expected = cube["pm25"][24:48].mean(axis=0)
    grid = result.grid
    assert result.plan.visualization == "grid_heatmap"
    assert len(grid["frames"]) == 1
    values = np.array(grid["frames"][0]["values"], dtype=float).reshape(expected.shape)
    np.testing.assert_allclose(values, expected, atol=0.06)
    assert grid["source_shape"] == list(cube["pm25"].shape)
    assert result.rows[0]["mean_pm25"] == pytest.approx(float(expected.max()), abs=0.06)
    assert len(result.rows) == 10


def test_hotspots_are_spread_out():
    result = run("forecast_grid_map", pollutant="pm25")
    points = [(row["latitude"], row["longitude"]) for row in result.rows]
    for index, (lat_a, lon_a) in enumerate(points):
        for lat_b, lon_b in points[index + 1 :]:
            assert abs(lat_a - lat_b) + abs(lon_a - lon_b) > 0.04


def test_hourly_map_caps_frames_and_city_crops():
    result = run(
        "forecast_grid_map",
        pollutant="pm10",
        statistic="hourly",
        city="Gurugram",
    )
    grid = result.grid
    assert 1 < len(grid["frames"]) <= 24
    assert len(grid["latitudes"]) < 61 and len(grid["longitudes"]) < 61
    assert len(grid["frames"][0]["values"]) == (
        len(grid["latitudes"]) * len(grid["longitudes"])
    )
    assert any(landmark["name"] == "Gurugram" for landmark in grid["landmarks"])


def test_point_series_reads_nearest_cell(cube):
    result = run(
        "forecast_point_series",
        pollutant="pm25",
        latitude=28.63,
        longitude=77.2,
        start_hour=0,
        end_hour=23,
    )
    i = int(np.argmin(np.abs(cube["latitude"] - 28.63)))
    j = int(np.argmin(np.abs(cube["longitude"] - 77.2)))
    assert [row["pm25"] for row in result.rows] == pytest.approx(
        cube["pm25"][:24, i, j].tolist(), abs=0.06
    )
    assert result.plan.visualization == "line"
    assert result.grid is None


def test_point_series_finds_station_by_name():
    result = run("forecast_point_series", pollutant="pm25", station="Anand Vihar")
    assert result.plan.in_scope
    assert "Anand Vihar" in result.plan.title
    assert len(result.rows) == 72


def test_exceedance_counts_hours_per_cell(cube):
    result = run(
        "forecast_exceedance_map",
        pollutant="pm25",
        threshold=90,
        start_hour=10,
        end_hour=50,
    )
    window = cube["pm25"][10:51]
    expected = (window > 90).sum(axis=0)
    values = np.array(result.grid["frames"][0]["values"]).reshape(expected.shape)
    np.testing.assert_array_equal(values, expected)
    assert result.grid["scale"] == "hours"
    assert sum(row["hours"] for row in result.rows) == 41
    assert [row["forecast_day"] for row in result.rows] == [
        "07 Oct 2026",
        "08 Oct 2026",
        "09 Oct 2026",
    ]


def test_places_outside_the_domain_are_refused():
    result = run("forecast_point_series", pollutant="pm25", city="Mumbai")
    assert not result.plan.in_scope
    assert "outside" in result.plan.refusal or "inside" in result.plan.refusal
    outside = run(
        "forecast_point_series",
        pollutant="pm25",
        latitude=19.07,
        longitude=72.87,
    )
    assert not outside.plan.in_scope


@pytest.mark.parametrize(
    "arguments",
    [
        {"pollutant": "pm25"},
        {"pollutant": "pm25", "city": "Delhi", "station": "Anand Vihar"},
        {"pollutant": "pm25", "latitude": 28.6},
        {"pollutant": "pm25", "city": "Delhi", "start_hour": 30, "end_hour": 10},
        {"pollutant": "no2", "city": "Delhi"},
    ],
)
def test_point_series_arguments_are_strict(arguments):
    with pytest.raises(ValidationError):
        ForecastPointSeriesArgs.model_validate(arguments)


def test_exceedance_requires_a_threshold():
    with pytest.raises(ValidationError):
        build_analysis("forecast_exceedance_map", {"pollutant": "pm25"})


def test_grid_payload_survives_the_response_model():
    result = run("forecast_grid_map", pollutant="pm25", statistic="hourly")
    display = display_result_set(result)
    response = ChatResponse(
        answer="ok",
        key_points=[],
        method_note=result.plan.method_note,
        columns=display["columns"],
        rows=display["rows"],
        visualization=display["visualization"],
        title=display["title"],
        x_key=display["x_key"],
        y_keys=display["y_keys"],
        result_sets=[display],
        grid=display["grid"],
    )
    assert response.grid is not None
    assert response.result_sets[0].grid is not None
    assert "SYNTHETIC" in response.method_note


def test_routing_accepts_forecast_maps_and_delhi_ncr_wording():
    calls = [
        RoutedToolCall(
            "forecast_grid_map",
            {"pollutant": "pm25", "statistic": "mean", "start_hour": 24, "end_hour": 47},
        )
    ]
    assert routing_alignment_issue(
        "Map the PM2.5 forecast across Delhi NCR tomorrow.", calls
    ) is None


def test_routing_keeps_named_city_for_forecasts():
    wrong = [RoutedToolCall("forecast_grid_map", {"pollutant": "pm25"})]
    assert routing_alignment_issue(
        "Show the PM2.5 forecast for Gurugram.", wrong
    )
    right = [
        RoutedToolCall(
            "forecast_point_series", {"pollutant": "pm25", "city": "Gurugram"}
        )
    ]
    assert routing_alignment_issue(
        "Show the PM2.5 forecast for Gurugram.", right
    ) is None
    assert requested_city_names("forecast_point_series", {"city": "Gurugram"}) == [
        "Gurugram"
    ]


def test_forecast_exceedance_needs_a_stated_threshold():
    calls = [
        RoutedToolCall(
            "forecast_exceedance_map", {"pollutant": "pm25", "threshold": 60}
        )
    ]
    assert routing_alignment_issue(
        "How polluted will Delhi NCR be in the PM2.5 forecast?", calls
    )
    assert routing_alignment_issue(
        "Map hours above 60 µg/m³ PM2.5 across Delhi NCR in the forecast.", calls
    ) is None


def test_forecast_follow_ups_stay_in_forecast_scope():
    result = run("forecast_grid_map", pollutant="pm25")
    questions = capability_suggested_questions([result])
    assert len(questions) == 3
    assert all("forecast" in question for question in questions)


def test_engine_reports_missing_file(tmp_path):
    from backend.gridded import GriddedForecast, ForecastGridMapArgs

    engine = GriddedForecast(tmp_path / "missing.npz")
    output = engine.run("forecast_grid_map", ForecastGridMapArgs(pollutant="pm25"))
    assert not output.plan.in_scope
    assert "not installed" in output.plan.refusal
    assert gridded_forecast.available
