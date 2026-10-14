from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.analysis_tools import (
    TOOL_BY_NAME,
    build_analysis,
    tool_declarations,
)
from backend.app import (
    ExecutedAnalysis,
    capability_suggested_questions,
    clarification_options,
    corrected_question,
    requested_city_names,
    routing_alignment_issue,
)
from backend.database import AirQualityDatabase, database as app_database
from backend.gemini_service import GeminiService, RoutedToolCall


@pytest.fixture(scope="module")
def database():
    instance = AirQualityDatabase()
    instance.initialize()
    return instance


@pytest.mark.parametrize(
    ("name", "arguments", "expected_columns"),
    [
        (
            "rank_cities",
            {
                "pollutant": "pm25",
                "start_year": 2023,
                "end_year": 2023,
                "limit": 10,
            },
            {
                "city",
                "mean_pm25",
                "station_count",
                "min_observation_days",
                "total_station_days",
            },
        ),
        (
            "city_average",
            {
                "pollutant": "pm25",
                "city": "Mumbai",
                "start_year": 2017,
                "end_year": 2024,
            },
            {
                "city",
                "mean_pm25",
                "station_count",
                "min_observation_days",
                "total_station_days",
            },
        ),
        (
            "threshold_cities",
            {
                "pollutant": "pm25",
                "threshold": 60,
                "start_year": 2023,
                "end_year": 2023,
            },
            {
                "city",
                "mean_pm25",
                "station_count",
                "min_observation_days",
                "matching_city_count",
            },
        ),
        (
            "compare_cities",
            {
                "pollutant": "pm25",
                "cities": ["delhi", "MUMBAI"],
                "start_year": 2023,
                "end_year": 2023,
            },
            {"city", "mean_pm25", "station_count", "min_observation_days"},
        ),
        (
            "time_trend",
            {
                "pollutant": "pm25",
                "city": "delhi",
                "start_year": 2023,
                "end_year": 2023,
                "interval": "monthly",
            },
            {"period", "mean_pm25", "station_count", "min_observation_days"},
        ),
        (
            "relationship",
            {
                "x_metric": "rainfall",
                "y_metric": "pm25",
                "city": "delhi",
                "months": [9, 6, 8, 7, 7],
            },
            {
                "city",
                "date",
                "rainfall",
                "pm25",
                "station_pair_count",
                "pearson_r",
                "paired_days",
            },
        ),
        (
            "strongest_weather_relationship",
            {"pollutant": "pm25", "city": "Delhi"},
            {
                "metric",
                "pearson_r",
                "paired_days",
                "city_count",
                "min_station_pairs",
            },
        ),
        (
            "seasonal_profile",
            {"pollutant": "pm25", "city": "Delhi"},
            {
                "season",
                "average_pm25",
                "city_count",
                "station_count",
                "min_observation_days",
            },
        ),
        (
            "funding_lookup",
            {"cities": ["Delhi", "Mumbai"]},
            {
                "city",
                "state",
                "total_fund_released",
                "utilisation_june_2022",
            },
        ),
        (
            "station_coverage",
            {
                "metric": "pm25",
                "cities": ["Mumbai", "Delhi"],
            },
            {
                "city",
                "station_count",
                "min_observation_days",
                "total_station_days",
            },
        ),
        (
            "rank_stations",
            {
                "metric": "pm25",
                "city": "Delhi",
                "start_year": 2023,
                "end_year": 2023,
            },
            {
                "station",
                "site_id",
                "city",
                "state",
                "mean_pm25",
                "observation_days",
                "first_observation_date",
                "last_observation_date",
            },
        ),
        (
            "weekday_weekend_profile",
            {"pollutant": "pm25", "city": "Delhi", "start_year": 2023, "end_year": 2023},
            {"day_type", "mean_pm25", "station_count", "city_count"},
        ),
        (
            "condition_comparison",
            {
                "pollutant": "pm25",
                "condition_metric": "wind_speed",
                "threshold": 3,
                "city": "Delhi",
            },
            {"condition_group", "mean_pm25", "station_count", "city_count"},
        ),
        (
            "rank_states",
            {"pollutant": "pm25", "start_year": 2023, "end_year": 2023},
            {"state", "mean_pm25", "city_count", "station_count"},
        ),
        (
            "coverage_trend",
            {"metric": "pm25"},
            {"year", "station_count", "city_count", "total_station_days"},
        ),
        (
            "funding_rank",
            {"limit": 10},
            {"city", "state", "total_fund_released"},
        ),
        (
            "ncap_threshold_cities",
            {"pollutant": "pm25", "threshold": 60},
            {"city", "mean_pm25", "total_fund_released", "station_count"},
        ),
        (
            "ncap_funding_groups",
            {"pollutant": "pm25"},
            {"funding_group", "mean_pm25", "city_count"},
        ),
        (
            "context_relationship",
            {
                "pollutant": "pm25",
                "context_metric": "total_fund_released",
                "pollution_measure": "level",
                "start_year": 2017,
                "end_year": 2024,
            },
            {"city", "total_fund_released", "mean_pm25", "pearson_r", "paired_cities"},
        ),
        (
            "pollution_change",
            {
                "pollutant": "pm25",
                "start_year": 2022,
                "end_year": 2023,
                "scope": "ncap_funded",
                "change_filter": "reductions_only",
            },
            {
                "city",
                "mean_pm25_2022",
                "mean_pm25_2023",
                "absolute_change_pm25",
                "percent_change",
                "matched_station_count",
                "total_fund_released",
            },
        ),
        (
            "context_relationship",
            {
                "pollutant": "pm25",
                "start_year": 2022,
                "end_year": 2023,
                "pollution_measure": "change",
                "context_metric": "total_fund_released",
            },
            {
                "city",
                "total_fund_released",
                "absolute_change_pm25",
                "pearson_r",
                "paired_cities",
            },
        ),
        (
            "threshold_frequency",
            {
                "pollutant": "pm25",
                "threshold": 60,
                "start_year": 2023,
                "end_year": 2023,
            },
            {
                "city",
                "threshold_day_count",
                "observed_day_count",
                "threshold_day_share_pct",
            },
        ),
        (
            "context_relationship",
            {
                "pollutant": "pm25",
                "context_metric": "population_density",
                "pollution_measure": "level",
                "start_year": 2023,
                "end_year": 2023,
            },
            {
                "state",
                "mean_pm25",
                "population_density",
                "pearson_r",
                "paired_states",
            },
        ),
    ],
)
def test_prebuilt_analysis_executes_safely(
    database,
    name,
    arguments,
    expected_columns,
):
    analysis = build_analysis(name, arguments)
    assert analysis.name == name
    safe_sql = database.validate_sql(analysis.plan.sql)
    columns, rows, truncated = database.execute(safe_sql)
    assert expected_columns.issubset(columns)
    assert rows
    assert truncated is False


def test_rank_cities_uses_equal_station_weighting_and_known_baseline(database):
    analysis = build_analysis(
        "rank_cities",
        {
            "pollutant": "pm25",
            "start_year": 2023,
            "end_year": 2023,
            "limit": 10,
        },
    )
    _, rows, _ = database.execute(analysis.plan.sql)
    assert rows[0] == {
        "city": "Byrnihat",
        "mean_pm25": 151.51,
        "station_count": 1,
        "min_observation_days": 351,
        "total_station_days": 351,
    }
    assert all(row["min_observation_days"] >= 30 for row in rows)


def test_single_city_average_and_station_coverage_have_known_baselines(database):
    average = build_analysis(
        "city_average",
        {"pollutant": "pm25", "city": "Mumbai"},
    )
    _, average_rows, _ = database.execute(average.plan.sql)
    assert len(average_rows) == 1
    assert average_rows[0]["city"] == "Mumbai"
    assert average_rows[0]["station_count"] == 30
    assert average_rows[0]["mean_pm25"] > 0

    coverage = build_analysis(
        "station_coverage",
        {"metric": "pm25", "cities": ["Mumbai", "Delhi"]},
    )
    _, coverage_rows, _ = database.execute(coverage.plan.sql)
    assert [(row["city"], row["station_count"]) for row in coverage_rows] == [
        ("Delhi", 38),
        ("Mumbai", 30),
    ]


def test_rank_stations_uses_station_grain_and_known_delhi_baseline(database):
    analysis = build_analysis(
        "rank_stations",
        {
            "metric": "pm25",
            "city": "Delhi",
            "start_year": 2023,
            "end_year": 2023,
            "order": "highest",
            "limit": 10,
        },
    )
    _, rows, _ = database.execute(analysis.plan.sql)
    assert rows[0] == {
        "station": "Anand Vihar, Delhi - DPCC",
        "site_id": "site_301",
        "city": "Delhi",
        "state": "Delhi",
        "mean_pm25": 127.87,
        "observation_days": 355,
        "first_observation_date": "2023-01-01",
        "last_observation_date": "2023-12-31",
    }
    assert len({row["site_id"] for row in rows}) == len(rows)
    assert all(row["city"] == "Delhi" for row in rows)


def test_rank_city_followups_are_capability_backed_and_station_aware(database):
    analysis = build_analysis(
        "rank_cities",
        {
            "pollutant": "pm25",
            "start_year": 2023,
            "end_year": 2023,
            "limit": 10,
        },
    )
    columns, rows, truncated = database.execute(analysis.plan.sql)
    result = ExecutedAnalysis(
        name=analysis.name,
        arguments=analysis.arguments,
        plan=analysis.plan,
        columns=columns,
        rows=rows,
        truncated=truncated,
        city_corrections={},
    )
    suggestions = capability_suggested_questions([result])
    assert suggestions == [
        "What was the mean PM2.5 for Byrnihat in 2023?",
        (
            "Rank monitoring stations in Byrnihat by highest mean "
            "PM2.5 in 2023."
        ),
        "Show the monthly PM2.5 trend for Byrnihat in 2023.",
    ]
    station_suggestion = suggestions[1]
    assert (
        routing_alignment_issue(
            station_suggestion,
            [
                RoutedToolCall(
                    name="rank_stations",
                    arguments={
                        "metric": "pm25",
                        "city": "Byrnihat",
                        "start_year": 2023,
                        "end_year": 2023,
                        "statistic": "mean",
                        "order": "highest",
                    },
                )
            ],
        )
        is None
    )


def test_relationship_uses_all_pairs_for_statistic_before_chart_limit(database):
    analysis = build_analysis(
        "relationship",
        {
            "x_metric": "rainfall",
            "y_metric": "pm25",
            "months": [6, 7, 8, 9],
        },
    )
    _, rows, _ = database.execute(analysis.plan.sql)
    assert len(rows) == 100
    assert rows[0]["paired_days"] > len(rows)
    assert -1 <= rows[0]["pearson_r"] <= 1
    assert all(row["paired_days"] == rows[0]["paired_days"] for row in rows)
    rainfall_values = [row["rainfall"] for row in rows]
    assert rainfall_values == sorted(rainfall_values)
    assert min(rainfall_values) == 0
    assert max(rainfall_values) > 1
    assert "stratified across the x-axis distribution" in analysis.plan.method_note


def test_seasonal_relationship_returns_one_verified_coefficient_per_season(
    database,
):
    analysis = build_analysis(
        "relationship",
        {
            "x_metric": "rainfall",
            "y_metric": "pm25",
            "city": "Jaipur",
            "start_year": 2017,
            "end_year": 2025,
            "group_by": "season",
        },
    )
    columns, rows, truncated = database.execute(analysis.plan.sql)

    assert analysis.plan.visualization == "bar"
    assert analysis.plan.x_key == "season"
    assert analysis.plan.y_keys == ["pearson_r"]
    assert columns == ["season", "pearson_r", "paired_days", "city_count"]
    assert [row["season"] for row in rows] == [
        "Winter",
        "Pre-monsoon",
        "Monsoon",
        "Post-monsoon",
    ]
    assert all(-1 <= row["pearson_r"] <= 1 for row in rows)
    assert all(row["paired_days"] >= 10 for row in rows)
    assert truncated is False


def test_question_to_plan_alignment_preserves_named_cities_and_grouping():
    app_database.initialize()
    correct = [
        RoutedToolCall(
            name="compare_cities",
            arguments={
                "pollutant": "pm25",
                "cities": ["Ahmedabad", "Surat"],
            },
        )
    ]
    wrong_cities = [
        RoutedToolCall(
            name="compare_cities",
            arguments={
                "pollutant": "pm25",
                "cities": ["Delhi", "Mumbai"],
            },
        )
    ]
    pooled = [
        RoutedToolCall(
            name="relationship",
            arguments={
                "x_metric": "rainfall",
                "y_metric": "pm25",
                "city": "Jaipur",
                "group_by": "none",
            },
        )
    ]
    seasonal = [
        RoutedToolCall(
            name="relationship",
            arguments={
                "x_metric": "rainfall",
                "y_metric": "pm25",
                "city": "Jaipur",
                "group_by": "season",
            },
        )
    ]

    assert (
        routing_alignment_issue(
            "Compare Ahmedabad and Surat PM2.5.",
            correct,
        )
        is None
    )
    assert "preserve exactly" in (
        routing_alignment_issue(
            "Compare Ahmedabad and Surat PM2.5.",
            wrong_cities,
        )
        or ""
    )
    assert 'group_by="season"' in (
        routing_alignment_issue(
            "Show the seasonal relationship between rainfall and PM2.5 "
            "for Jaipur.",
            pooled,
        )
        or ""
    )
    assert (
        routing_alignment_issue(
            "Show the seasonal relationship between rainfall and PM2.5 "
            "for Jaipur.",
            seasonal,
        )
        is None
    )


def test_question_to_plan_alignment_rejects_invented_scope_and_missing_clauses():
    app_database.initialize()
    invented_pollutant = [
        RoutedToolCall(
            name="compare_cities",
            arguments={
                "pollutant": "pm25",
                "cities": ["Delhi", "Mumbai"],
            },
        )
    ]
    invented_threshold = [
        RoutedToolCall(
            name="threshold_frequency",
            arguments={
                "pollutant": "pm25",
                "threshold": 60,
                "rank_by": "count",
            },
        )
    ]
    missing_standard = [
        RoutedToolCall(
            name="city_average",
            arguments={
                "pollutant": "pm10",
                "city": "Jaipur",
                "start_year": 2025,
                "end_year": 2025,
            },
        )
    ]
    widened_coverage = [
        RoutedToolCall(
            name="station_coverage",
            arguments={
                "metric": "pm25",
                "cities": ["Delhi", "Mumbai"],
                "start_year": 2017,
                "end_year": 2025,
            },
        )
    ]
    wrong_station_grain = [
        RoutedToolCall(
            name="city_average",
            arguments={
                "pollutant": "pm25",
                "city": "Delhi",
                "start_year": 2023,
                "end_year": 2023,
            },
        )
    ]
    station_ranking = [
        RoutedToolCall(
            name="rank_stations",
            arguments={
                "metric": "pm25",
                "city": "Delhi",
                "start_year": 2023,
                "end_year": 2023,
                "order": "highest",
            },
        )
    ]

    assert "did not name a supported pollutant" in (
        routing_alignment_issue(
            "Compare pollution levels between Delhi and Mumbai.",
            invented_pollutant,
        )
        or ""
    )
    assert "did not state a threshold" in (
        routing_alignment_issue(
            "Which cities had the most polluted days?",
            invented_threshold,
        )
        or ""
    )
    assert "regulatory standard" in (
        routing_alignment_issue(
            "Does Jaipur's 2025 average PM10 exceed the CPCB annual standard?",
            missing_standard,
        )
        or ""
    )
    assert "2022–2023" in (
        routing_alignment_issue(
            "Compare Delhi and Mumbai PM2.5 station coverage from 2022 to 2023.",
            widened_coverage,
        )
        or ""
    )
    assert "station-level value ranking" in (
        routing_alignment_issue(
            "Which station had the highest PM2.5 in Delhi during 2023?",
            wrong_station_grain,
        )
        or ""
    )
    assert (
        routing_alignment_issue(
            "Which station had the highest PM2.5 in Delhi during 2023?",
            station_ranking,
        )
        is None
    )


def test_question_to_plan_alignment_allows_bounded_follow_up_city_context():
    app_database.initialize()
    calls = [
        RoutedToolCall(
            name="funding_lookup",
            arguments={"cities": ["Agra", "Lucknow"]},
        )
    ]
    history = [
        SimpleNamespace(role="user", content="How much NCAP funding did Agra receive?"),
        SimpleNamespace(role="assistant", content="Agra funding was returned."),
    ]
    assert (
        routing_alignment_issue(
            "How does that compare with Lucknow?",
            calls,
            history,
        )
        is None
    )


def test_question_to_plan_alignment_accepts_terminal_clarification():
    app_database.initialize()
    clarification = [
        RoutedToolCall(
            name="clarify_question",
            arguments={"question": "Which pollutant should I compare?"},
        )
    ]
    assert (
        routing_alignment_issue(
            "Compare pollution levels between Delhi and Mumbai.",
            clarification,
        )
        is None
    )


def test_question_to_plan_alignment_rejects_an_invented_default_pollutant():
    calls = [
        RoutedToolCall(
            name="compare_cities",
            arguments={
                "pollutant": "pm25",
                "cities": ["Delhi", "Mumbai"],
                "start_year": 2017,
                "end_year": 2025,
                "min_observation_days": 30,
            },
        )
    ]
    issue = routing_alignment_issue(
        "Compare Delhi and Mumbai using only stations with at least 30 days.",
        calls,
    )
    assert issue is not None
    assert "did not name a supported pollutant" in issue


def test_clarification_options_are_bounded_and_pollutant_safe():
    analysis = build_analysis(
        "clarify_question",
        {
            "question": "Which pollutant would you like to compare?",
            "options": ["PM2.5", "PM10", "CO2", "PM2.5", ""],
        },
    )
    executed = ExecutedAnalysis(
        name=analysis.name,
        arguments=analysis.arguments,
        plan=analysis.plan,
        columns=[],
        rows=[],
        truncated=False,
        city_corrections={},
    )
    assert clarification_options(executed) == [
        "PM2.5",
        "PM10",
        "NO₂",
        "ozone",
    ]


def test_pollutant_clarification_has_clickable_fallback_choices():
    analysis = build_analysis(
        "clarify_question",
        {"question": "Which pollutant would you like to compare?"},
    )
    executed = ExecutedAnalysis(
        name=analysis.name,
        arguments=analysis.arguments,
        plan=analysis.plan,
        columns=[],
        rows=[],
        truncated=False,
        city_corrections={},
    )
    assert clarification_options(executed) == [
        "PM2.5",
        "PM10",
        "NO₂",
        "ozone",
    ]


def test_question_to_plan_alignment_never_substitutes_an_unsupported_measure():
    app_database.initialize()
    substituted = [
        RoutedToolCall(
            name="rank_cities",
            arguments={
                "pollutant": "co",
                "start_year": 2024,
                "end_year": 2024,
            },
        )
    ]
    issue = routing_alignment_issue(
        "Rank Indian cities by average CO2 in 2024.",
        substituted,
    )
    assert issue is not None
    assert "not in the loaded analytical schema" in issue
    assert "never substitute" in issue


def test_strongest_weather_relationship_evaluates_all_factors(database):
    analysis = build_analysis(
        "strongest_weather_relationship",
        {"pollutant": "pm25", "start_year": 2023, "end_year": 2023},
    )
    _, rows, _ = database.execute(analysis.plan.sql)
    assert {row["metric"] for row in rows} == {
        "temperature",
        "humidity",
        "wind_speed",
        "rainfall",
        "solar_radiation",
        "pressure",
    }
    absolute_correlations = [abs(row["pearson_r"]) for row in rows]
    assert absolute_correlations == sorted(absolute_correlations, reverse=True)
    assert all(row["paired_days"] >= 30 for row in rows)


def test_ncap_change_uses_same_stations_and_reports_funding_context(database):
    analysis = build_analysis(
        "pollution_change",
        {
            "pollutant": "pm25",
            "start_year": 2022,
            "end_year": 2023,
            "scope": "ncap_funded",
            "change_filter": "reductions_only",
            "order": "largest_reduction",
            "limit": 10,
        },
    )
    _, rows, _ = database.execute(analysis.plan.sql)
    assert rows
    assert all(row["absolute_change_pm25"] < 0 for row in rows)
    assert all(row["matched_station_count"] >= 1 for row in rows)
    assert all(row["min_start_observation_days"] >= 180 for row in rows)
    assert all(row["min_end_observation_days"] >= 180 for row in rows)
    assert all(row["min_start_observation_months"] >= 9 for row in rows)
    assert all(row["min_end_observation_months"] >= 9 for row in rows)
    assert rows[0]["city"] == "Gaya"
    assert rows[0]["absolute_change_pm25"] == -11.37
    assert "only the same stations" in analysis.plan.method_note
    assert "does not attribute" in analysis.plan.method_note


def test_named_change_comparison_keeps_both_increase_and_reduction(database):
    analysis = build_analysis(
        "pollution_change",
        {
            "pollutant": "pm25",
            "start_year": 2022,
            "end_year": 2023,
            "cities": ["Delhi", "Mumbai"],
            "scope": "all_cities",
            "change_filter": "all",
            "order": "largest_reduction",
        },
    )
    _, rows, _ = database.execute(analysis.plan.sql)
    assert {row["city"] for row in rows} == {"Delhi", "Mumbai"}
    assert any(row["absolute_change_pm25"] < 0 for row in rows)
    assert any(row["absolute_change_pm25"] > 0 for row in rows)


def test_change_relationship_preserves_city_and_state_level_granularity(database):
    funding = build_analysis(
        "context_relationship",
        {
            "pollutant": "pm25",
            "start_year": 2022,
            "end_year": 2023,
            "pollution_measure": "change",
            "context_metric": "total_fund_released",
        },
    )
    _, funding_rows, _ = database.execute(funding.plan.sql)
    assert funding_rows[0]["paired_cities"] == len(funding_rows)
    assert all("city" in row for row in funding_rows)
    assert -1 <= funding_rows[0]["pearson_r"] <= 1

    utilisation = build_analysis(
        "context_relationship",
        {
            "pollutant": "pm25",
            "start_year": 2022,
            "end_year": 2023,
            "pollution_measure": "change",
            "context_metric": "utilisation_june_2022",
        },
    )
    _, utilisation_rows, _ = database.execute(utilisation.plan.sql)
    assert utilisation_rows[0]["paired_states"] == len(utilisation_rows)
    assert all("state" in row and "city" not in row for row in utilisation_rows)
    assert "each state was included once" in utilisation.plan.method_note


def test_exceedance_frequency_does_not_treat_missing_days_as_clean(database):
    analysis = build_analysis(
        "threshold_frequency",
        {
            "pollutant": "pm25",
            "threshold": 60,
            "start_year": 2023,
            "end_year": 2023,
            "rank_by": "share",
            "order": "most",
            "limit": 10,
        },
    )
    _, rows, _ = database.execute(analysis.plan.sql)
    assert rows
    assert all(row["observed_day_count"] >= 30 for row in rows)
    assert all(
        0 <= row["threshold_day_count"] <= row["observed_day_count"]
        for row in rows
    )
    assert all(0 <= row["threshold_day_share_pct"] <= 100 for row in rows)
    assert "Missing days were not treated" in analysis.plan.method_note


def test_utilisation_ranking_is_state_level_not_repeated_by_city(database):
    analysis = build_analysis(
        "funding_rank",
        {
            "metric": "utilisation_june_2022",
            "order": "highest",
            "limit": 10,
        },
    )
    _, rows, _ = database.execute(analysis.plan.sql)
    assert rows
    assert all("state" in row and "city" not in row for row in rows)
    assert len({row["state"] for row in rows}) == len(rows)
    assert "Ranked states, not cities" in analysis.plan.method_note


def test_city_typo_suggestions_come_from_real_dataset_names(database):
    assert database.suggest_city_names(["Dheli"]) == {"Dheli": "Delhi"}
    assert database.suggest_city_names(["Delhi"]) == {}
    assert database.suggest_city_names(["not a real place at all"]) == {}


def test_requested_city_extraction_and_corrected_follow_up():
    assert requested_city_names("time_trend", {"city": "Dheli"}) == ["Dheli"]
    assert requested_city_names(
        "compare_cities",
        {"cities": ["Dheli", "Mumbai"]},
    ) == ["Dheli", "Mumbai"]
    assert requested_city_names("rank_cities", {"limit": 10}) == []
    assert requested_city_names(
        "pollution_change",
        {"cities": ["Dheli"], "scope": "ncap_funded"},
    ) == ["Dheli"]
    assert corrected_question(
        "Show monthly PM2.5 for Dheli.",
        "Dheli",
        "Delhi",
    ) == "Show monthly PM2.5 for Delhi."


def test_threshold_reports_full_match_count_even_when_output_is_limited(database):
    analysis = build_analysis(
        "threshold_cities",
        {
            "pollutant": "pm25",
            "threshold": 20,
            "limit": 5,
        },
    )
    _, rows, _ = database.execute(analysis.plan.sql)
    assert len(rows) == 5
    assert rows[0]["matching_city_count"] > len(rows)


def test_city_values_are_sql_escaped_and_cannot_change_the_query(database):
    analysis = build_analysis(
        "time_trend",
        {
            "pollutant": "pm25",
            "city": "Delhi' OR 1=1",
            "start_year": 2023,
            "end_year": 2023,
        },
    )
    safe_sql = database.validate_sql(analysis.plan.sql)
    _, rows, _ = database.execute(safe_sql)
    assert rows == []


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        (
            "rank_cities",
            {"pollutant": "pm25", "start_year": 2024, "end_year": 2023},
        ),
        (
            "threshold_cities",
            {"pollutant": "pm25", "threshold": -1},
        ),
        (
            "compare_cities",
            {"pollutant": "pm25", "cities": ["Delhi", "delhi"]},
        ),
        (
            "relationship",
            {"x_metric": "pm25", "y_metric": "pm25"},
        ),
        (
            "time_trend",
            {"pollutant": "invalid", "city": "Delhi"},
        ),
    ],
)
def test_invalid_function_arguments_are_rejected(name, arguments):
    with pytest.raises(ValidationError):
        build_analysis(name, arguments)


def test_out_of_scope_can_return_a_helpful_explanation():
    definition = TOOL_BY_NAME["out_of_scope"]
    validated = definition.arguments_model.model_validate({"reason": "x" * 600})
    assert len(validated.reason) == 600

    with pytest.raises(ValidationError):
        definition.arguments_model.model_validate({"reason": "x" * 601})


@pytest.mark.parametrize(
    ("name", "arguments", "expected_visualization", "expected_columns"),
    [
        (
            "station_map",
            {"metric": "pm25", "start_year": 2025, "end_year": 2025},
            "map_points",
            {"state", "city", "station", "latitude", "longitude", "mean_pm25"},
        ),
        (
            "state_choropleth",
            {"value": "station_count", "start_year": 2025, "end_year": 2025},
            "choropleth",
            {"state", "station_count", "city_count", "observation_days"},
        ),
        (
            "data_catalog",
            {"topic": "funding"},
            "none",
            {"title", "provider", "date_start", "date_end", "quality"},
        ),
        (
            "census_state_rank",
            {"metric": "population_density_2011_per_km2", "limit": 10},
            "bar",
            {"state", "category", "population_density_2011_per_km2"},
        ),
        (
            "air_quality_standard",
            {"pollutant": "pm25", "averaging_period": "24-hour"},
            "none",
            {"pollutant", "averaging_period", "standard_limit", "unit"},
        ),
        (
            "programme_finance_trend",
            {"program": "NCAP + XVFC", "metric": "both"},
            "bar",
            {"fiscal_year", "released_crore", "utilised_crore", "reporting_date"},
        ),
        (
            "physical_progress",
            {"programme_scope": "ncap_plus_xvfc"},
            "none",
            {"activity", "unit", "reported_value", "reporting_date"},
        ),
    ],
)
def test_enrichment_functions_execute_with_explicit_scope(
    database,
    name,
    arguments,
    expected_visualization,
    expected_columns,
):
    analysis = build_analysis(name, arguments)
    columns, rows, _ = database.execute(analysis.plan.sql, max_rows=600)
    assert analysis.plan.visualization == expected_visualization
    assert expected_columns.issubset(columns)
    assert rows


def test_recent_data_and_source_ranges_are_exposed(database):
    stats = database.stats()
    assert stats["first_date"] == "2017-01-01"
    assert stats["last_date"] == "2025-12-31"
    assert stats["records"] == 858_604
    assert stats["cities"] == 277
    assert stats["stations"] == 535
    source_ids = {source["id"] for source in stats["sources"]}
    assert {
        "cpcb_2025",
        "census_state_context",
        "cpcb_naaqs",
        "ncap_program_finance_2025",
        "ncap_physical_progress_2025",
    }.issubset(source_ids)


def test_cpcb_pm25_daily_standard_is_not_confused_with_annual(database):
    daily = build_analysis(
        "air_quality_standard",
        {"pollutant": "pm25", "averaging_period": "24-hour"},
    )
    _, rows, _ = database.execute(daily.plan.sql)
    assert rows == [
        {
            "pollutant": "PM2.5",
            "averaging_period": "24-hour",
            "standard_limit": 60.0,
            "unit": "µg/m³",
            "area_scope": "all notified areas",
            "notification_year": 2009,
        }
    ]


def test_city_average_contract_makes_temporal_and_weighting_choices_explicit():
    analysis = build_analysis(
        "city_average",
        {
            "city": "Mumbai",
            "pollutant": "pm25",
            "start_year": 2020,
            "end_year": 2023,
            "months": [12, 1, 2],
            "statistic": "median",
            "minimum_station_days": 45,
            "station_weighting": "equal_station",
        },
    )
    assert "MEDIAN(pm25)" in analysis.plan.sql
    assert "year BETWEEN 2020 AND 2023" in analysis.plan.sql
    assert "IN (1, 2, 12)" in analysis.plan.sql
    assert ">= 45" in analysis.plan.sql
    assert "equal-station weighting" in analysis.plan.method_note
    assert "custom_sql_analysis" not in TOOL_BY_NAME


def test_tool_declarations_and_router_extraction_are_closed_over_known_tools():
    declarations = tool_declarations()
    assert {item["name"] for item in declarations} == set(TOOL_BY_NAME)
    assert all(item["type"] == "function" for item in declarations)
    assert all(item["parameters"]["additionalProperties"] is False for item in declarations)

    response = SimpleNamespace(
        steps=[
            SimpleNamespace(
                type="function_call",
                name="rank_cities",
                arguments={"pollutant": "pm25", "limit": 10},
            )
        ]
    )
    call = GeminiService._extract_tool_call(response)
    assert call.name == "rank_cities"
    assert call.arguments["limit"] == 10


def test_router_extraction_rejects_text_or_multiple_calls():
    with pytest.raises(ValueError, match="between one and three"):
        GeminiService._extract_tool_call(SimpleNamespace(steps=[]))
    with pytest.raises(ValueError, match="exactly one"):
        GeminiService._extract_tool_call(
            SimpleNamespace(
                steps=[
                    SimpleNamespace(
                        type="function_call",
                        name="rank_cities",
                        arguments={},
                    ),
                    SimpleNamespace(
                        type="function_call",
                        name="threshold_cities",
                        arguments={},
                    ),
                ]
            )
        )


def test_router_accepts_composition_of_up_to_three_typed_functions():
    response = SimpleNamespace(
        steps=[
            SimpleNamespace(
                type="function_call",
                name="city_average",
                arguments={"city": "Mumbai", "pollutant": "pm25"},
            ),
            SimpleNamespace(
                type="function_call",
                name="station_coverage",
                arguments={"cities": ["Mumbai"], "metric": "pm25"},
            ),
        ]
    )
    routed = GeminiService._extract_tool_calls(response)
    assert [call.name for call in routed.calls] == [
        "city_average",
        "station_coverage",
    ]
    assert routed.name == "composition"

    with pytest.raises(ValueError, match="cannot be composed"):
        GeminiService._extract_tool_calls(
            SimpleNamespace(
                steps=[
                    *response.steps,
                    SimpleNamespace(
                        type="function_call",
                        name="out_of_scope",
                        arguments={},
                    ),
                ]
            )
        )


def test_malformed_tool_call_errors_are_retryable_but_auth_errors_are_not():
    malformed = RuntimeError(
        "Model generated invalid JSON syntax: malformed_tool_call"
    )
    assert GeminiService._is_malformed_tool_call_error(malformed)
    assert not GeminiService._is_malformed_tool_call_error(
        RuntimeError("401 invalid API key")
    )
