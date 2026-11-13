"""station_days: measured station data for yesterday, one date, the last N days."""

from __future__ import annotations

import asyncio
import datetime as dt
import os

import pytest
from pydantic import ValidationError

from backend import oaq
from backend.analysis_tools import TOOL_BY_NAME, station_data_end, tool_declarations
from backend.app import execute_analysis, routing_alignment_issue
from backend.config import get_settings
from backend.database import database as app_database
from backend.gemini_service import RoutedToolCall, client_for

needs_cache = pytest.mark.skipif(not oaq.STATION_DAY_PATH.exists(), reason="Needs the OAQ station cache")


@pytest.fixture(scope="module", autouse=True)
def initialized_database():
    if oaq.STATION_DAY_PATH.exists():
        app_database.initialize()


def run(**arguments):
    return execute_analysis(RoutedToolCall(name="station_days", arguments=arguments))


def latest() -> str:
    return station_data_end().isoformat()


def days_before(days: int) -> str:
    return (station_data_end() - dt.timedelta(days=days)).isoformat()


@needs_cache
def test_station_data_end_is_the_last_synced_day():
    assert latest() == oaq.provenance()["date_end"]
    _, rows, _ = app_database.execute("SELECT MAX(timestamp) AS last FROM air_quality")
    assert str(rows[0]["last"]) == latest()


@needs_cache
def test_one_day_city_value_is_the_mean_of_reporting_stations():
    result = run(pollutant="pm25", cities=["Delhi"], start_date=latest())
    assert result.plan.in_scope and result.plan.visualization == "none"
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["date"] == latest() and row["station_count"] > 10
    assert row["lowest_station_pm25"] <= row["mean_pm25"] <= row["highest_station_pm25"]
    _, check, _ = app_database.execute(
        "SELECT ROUND(AVG(v), 2) AS mean FROM (SELECT station, AVG(pm25) AS v FROM air_quality "
        f"WHERE city = 'Delhi' AND timestamp = DATE '{latest()}' AND pm25 IS NOT NULL "
        "AND isfinite(pm25) AND pm25 >= 0 GROUP BY station)"
    )
    assert check[0]["mean"] == row["mean_pm25"]


@needs_cache
def test_a_week_is_a_daily_line_in_date_order():
    result = run(pollutant="pm25", cities=["Delhi"], start_date=days_before(6), end_date=latest())
    assert result.plan.visualization == "line" and result.plan.x_key == "date"
    dates = [row["date"] for row in result.rows]
    assert dates == sorted(dates) and len(dates) == 7


@needs_cache
def test_dates_after_the_data_say_which_day_is_shown():
    future = (station_data_end() + dt.timedelta(days=1)).isoformat()
    shown = run(pollutant="pm25", cities=["Delhi"], start_date=future)
    assert [row["date"] for row in shown.rows] == [latest()]
    assert "latest complete day" in shown.plan.summary_template
    clipped = run(pollutant="pm25", cities=["Delhi"], start_date=days_before(2), end_date=future)
    assert max(row["date"] for row in clipped.rows) == latest()
    assert "window ends there" in clipped.plan.method_note


@needs_cache
def test_one_station_by_name():
    result = run(pollutant="pm25", cities=["Delhi"], station="Jahangirpuri", start_date=days_before(2), end_date=latest())
    assert result.rows and all("Jahangirpuri" in row["station"] for row in result.rows)


@needs_cache
def test_station_rainfall_reads_the_daily_total():
    result = run(pollutant="rainfall", cities=["Mumbai"], start_date="2025-07-01", end_date="2025-07-05")
    assert result.arguments["pollutant"] == "total_rainfall"
    assert "mean_total_rainfall" in result.rows[0]


@pytest.mark.parametrize(
    "arguments",
    [
        {"pollutant": "pm25", "cities": ["Delhi"], "start_date": "2026-01-01", "end_date": "2026-06-30"},  # > 92 days
        {"view": "rank", "pollutant": "pm25", "start_date": "2025-01-01", "end_date": "2026-06-30"},  # > 366 days
        {"pollutant": "pm25", "start_date": "2026-11-01"},  # daily view without a place
        {"pollutant": "pm25", "cities": ["Delhi", "Mumbai"], "start_date": "2026-11-01"},  # daily view, two cities
        {"pollutant": "pm25", "cities": ["Delhi"], "start_date": "2026-11-05", "end_date": "2026-11-01"},
        {"pollutant": "pm25", "cities": ["Delhi"], "start_date": "2026-02-30"},
        {"pollutant": "pm25", "cities": ["Delhi"], "start_date": "12/11/2026"},
    ],
)
def test_invalid_windows_are_rejected(arguments):
    with pytest.raises((ValidationError, ValueError)):
        TOOL_BY_NAME["station_days"].arguments_model.model_validate(arguments)


@needs_cache
@pytest.mark.parametrize("group_by", ["city", "state", "station"])
def test_rankings_are_sorted(group_by):
    result = run(view="rank", pollutant="pm25", group_by=group_by, start_date=latest())
    values = [row["mean_pm25"] for row in result.rows]
    assert values and values == sorted(values, reverse=True)
    assert result.plan.x_key == group_by


@needs_cache
def test_station_ranking_respects_city_and_coverage():
    result = run(
        view="rank", pollutant="pm25", group_by="station", cities=["Delhi"],
        start_date=days_before(6), end_date=latest(), minimum_coverage_pct=100,
    )
    assert result.rows and all(row["city"] == "Delhi" for row in result.rows)
    assert all(row["observation_days"] == 7 for row in result.rows)


@needs_cache
def test_named_cities_are_compared_on_the_same_day():
    result = run(view="rank", pollutant="pm25", cities=["Delhi", "Mumbai"], start_date=latest())
    assert {row["city"] for row in result.rows} == {"Delhi", "Mumbai"}


@needs_cache
def test_the_national_station_map_is_not_cut_at_200_rows():
    result = run(view="map", pollutant="pm25", start_date=latest())
    assert result.plan.visualization == "map_points"
    assert len(result.rows) > 200 and not result.truncated
    assert all(row["latitude"] is not None and row["longitude"] is not None for row in result.rows)


@needs_cache
@pytest.mark.parametrize(
    ("question", "arguments"),
    [
        ("What was the PM2.5 for Delhi yesterday?", {"cities": ["Delhi"]}),
        ("What's the air like in Delhi right now?", {"cities": ["Delhi"]}),
        ("Which city had the highest PM2.5 yesterday?", {"view": "rank"}),
        (
            "Which Delhi monitoring stations had the highest PM2.5 last week?",
            {"view": "rank", "group_by": "station", "cities": ["Delhi"]},
        ),
        ("Map PM2.5 at Delhi's monitoring stations yesterday.", {"view": "map", "cities": ["Delhi"]}),
    ],
)
def test_day_window_plans_pass_validation(question, arguments):
    call = RoutedToolCall("station_days", {"pollutant": "pm25", "start_date": latest(), **arguments})
    assert routing_alignment_issue(question, [call]) is None


@needs_cache
def test_plans_must_keep_the_named_city_year_and_capability():
    wrong_city = RoutedToolCall("station_days", {"pollutant": "pm25", "cities": ["Mumbai"], "start_date": latest()})
    assert routing_alignment_issue("What was the PM2.5 for Delhi yesterday?", [wrong_city])
    wrong_year = RoutedToolCall("station_days", {"pollutant": "pm25", "cities": ["Delhi"], "start_date": "2025-11-01"})
    issue = routing_alignment_issue("What was Delhi's PM2.5 on 1 November 2024?", [wrong_year])
    assert issue and "year" in issue
    series_for_a_map = RoutedToolCall("station_days", {"pollutant": "pm25", "cities": ["Delhi"], "start_date": latest()})
    issue = routing_alignment_issue("Map PM2.5 at Delhi's monitoring stations yesterday.", [series_for_a_map])
    assert issue and "map" in issue


@needs_cache
def test_unnamed_pollutant_defaults_to_pm25_only_for_general_air_wording():
    call = RoutedToolCall("station_days", {"pollutant": "pm10", "cities": ["Delhi"], "start_date": latest()})
    assert routing_alignment_issue("What's the air like in Delhi right now?", [call])


@pytest.mark.skipif(
    not get_settings().gemini_api_keys or os.environ.get("VAYUCHAT_OFFLINE"),
    reason="Needs a Gemini key (set VAYUCHAT_OFFLINE=1 to skip)",
)
def test_gemini_accepts_the_whole_function_set():
    # Gemini refuses every request once the function schemas pass a size limit
    # ("too many states for serving"); three day-window functions crossed it.
    settings = get_settings()
    declarations = tool_declarations()

    async def route():
        return await client_for(settings.gemini_api_keys[0]).aio.interactions.create(
            model=settings.gemini_model,
            input="What was the PM2.5 for Delhi yesterday?",
            system_instruction="Call one function.",
            tools=declarations,
            generation_config={
                "max_output_tokens": 300,
                "thinking_level": "minimal",
                "tool_choice": {"allowed_tools": {"mode": "any", "tools": [d["name"] for d in declarations]}},
            },
            store=False,
        )

    try:
        asyncio.run(route())
    except Exception as error:  # noqa: BLE001
        if "too many states" in str(error):
            pytest.fail("Gemini rejected the function schemas as too large")
        pytest.skip(f"Gemini unavailable: {str(error)[:80]}")


def test_router_context_spells_out_yesterday_and_last_week():
    from backend.analysis_tools import IST, station_data_context

    text = station_data_context()
    yesterday = dt.datetime.now(IST).date() - dt.timedelta(days=1)
    assert f"Yesterday was {yesterday:%Y-%m-%d}" in text
    assert f"{days_before(6)} to {latest()}" in text


def test_one_ranked_city_reads_as_singular():
    from backend.app import clean_model_text

    assert clean_model_text("The table ranks 1 cities.") == "The table ranks 1 city."
    assert clean_model_text("The table ranks 1 stations.") == "The table ranks 1 station."


@needs_cache
@pytest.mark.parametrize(
    ("question", "call"),
    [
        ("Which Delhi monitoring stations had the highest PM2.5 last week?",
         ("rank_stations", {"pollutant": "pm25", "city": "Delhi", "start_year": 2026, "end_year": 2026})),
        ("What was the PM2.5 for Delhi yesterday?",
         ("city_average", {"pollutant": "pm25", "city": "Delhi", "start_year": 2026, "end_year": 2026})),
        ("What was Delhi's PM2.5 on 1 November 2026?",
         ("city_average", {"pollutant": "pm25", "city": "Delhi", "start_year": 2026, "end_year": 2026})),
    ],
)
def test_particular_days_are_not_answered_with_whole_years(question, call):
    issue = routing_alignment_issue(question, [RoutedToolCall(*call)])
    assert issue and "station_days" in issue


def test_whole_years_and_fire_windows_are_unaffected():
    year_plan = RoutedToolCall("city_average", {"pollutant": "pm25", "city": "Delhi", "start_year": 2024, "end_year": 2024})
    assert routing_alignment_issue("What was Delhi's average PM2.5 in 2024?", [year_plan]) is None
    fires = RoutedToolCall("fire_map", {"state": "Punjab"})
    issue = routing_alignment_issue("Map fire detections across Punjab over the last 7 days.", [fires])
    assert not issue or "station_days" not in issue


@needs_cache
def test_now_and_today_ask_for_today_so_the_answer_says_no_live_readings():
    from backend.analysis_tools import IST
    from backend.app import now_as_today

    today = dt.datetime.now(IST).date().isoformat()
    call = RoutedToolCall("station_days", {"view": "daily", "pollutant": "pm25", "cities": ["Delhi"],
                                           "start_date": latest(), "end_date": latest()})
    (rewritten,) = now_as_today([call], "What's the air like in Delhi right now?")
    assert rewritten.arguments["start_date"] == rewritten.arguments["end_date"] == today
    result = execute_analysis(rewritten)
    assert [row["date"] for row in result.rows] == [latest()]
    assert "no live readings" in result.plan.summary_template
    # Yesterday, past windows and forecasts are left alone.
    assert now_as_today([call], "What was the PM2.5 for Delhi yesterday?") == [call]
    week = RoutedToolCall("station_days", {"pollutant": "pm25", "cities": ["Delhi"],
                                           "start_date": days_before(6), "end_date": latest()})
    assert now_as_today([week], "How has Delhi's PM2.5 changed this week until today?") == [week]
    assert now_as_today([call], "What is today's PM2.5 forecast for Delhi?") == [call]
