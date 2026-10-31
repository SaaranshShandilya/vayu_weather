"""State comparisons across datasets: fires, CAMS and CPCB stations."""

from __future__ import annotations

import math

import pytest

from backend.app import execute_analysis, routing_alignment_issue
from backend.cams_forecast import latest
from backend.database import database as app_database
from backend.firms_fires import TABLE
from backend.gemini_service import RoutedToolCall

pytestmark = pytest.mark.skipif(
    not TABLE.exists() or latest.domain() is None, reason="Needs FIRMS detections and a CAMS run"
)


@pytest.fixture(scope="module", autouse=True)
def initialized_database():
    app_database.initialize()


def ask(**arguments):
    return execute_analysis(RoutedToolCall(name="state_compare", arguments=arguments))


def test_ratio_ranking_is_sorted_and_skips_small_denominators():
    result = ask(x_measure="cams_pm25", y_measure="fire_detections")
    key = "cams_pm25_per_fire_detections"
    ratios = [row[key] for row in result.rows]
    assert ratios == sorted(ratios, reverse=True)
    for row in result.rows:
        assert row["fire_detections"] > 10
        assert math.isclose(row[key], row["cams_pm25"] / row["fire_detections"], rel_tol=1e-2)
    assert "Pearson correlation" in result.plan.summary_template
    assert "not measurements" in result.plan.method_note and "FIRMS" in result.plan.method_note


def test_non_overlapping_datasets_are_refused_with_the_reason():
    result = ask(x_measure="station_pm25", y_measure="fire_detections")
    assert not result.plan.in_scope
    assert "no days in common" in result.plan.refusal and "CAMS PM2.5" in result.plan.refusal


def test_the_screenshot_question_plan_passes_validation():
    call = RoutedToolCall(
        "state_compare", {"x_measure": "cams_pm25", "y_measure": "fire_detections", "rank_by": "ratio"}
    )
    assert routing_alignment_issue("which states have highest PM2.5/number of fires ratio?", [call]) is None


def test_gemini_overload_is_reported_as_busy_not_as_a_bad_question():
    import asyncio

    from backend.app import busy_response, model_service_busy

    class TemporaryError(Exception):
        pass

    try:
        try:
            raise TemporaryError("<Response [429 Too Many Requests]>")
        except TemporaryError as inner:
            raise asyncio.TimeoutError() from inner
    except asyncio.TimeoutError as outer:
        assert model_service_busy(outer)
    assert not model_service_busy(ValueError("x_measure and y_measure must differ"))
    response = busy_response(0.0, "test")
    assert response.analysis_type == "capacity_unavailable" and "try again" in response.answer
