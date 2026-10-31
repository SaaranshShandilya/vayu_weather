"""NASA FIRMS fire detections: storage, places and analyses."""

from __future__ import annotations

import duckdb
import pytest

from backend.analysis_tools import TOOL_BY_NAME
from backend.app import execute_analysis, routing_alignment_issue
from backend.database import database as app_database
from backend.firms_fires import FIRE_TOOL_NAMES, TABLE, fires
from backend.gemini_service import RoutedToolCall

pytestmark = pytest.mark.skipif(not TABLE.exists(), reason="Fetch detections with scripts/update_firms.py")


@pytest.fixture(scope="module", autouse=True)
def initialized_database():
    app_database.initialize()


def ask(name: str, **arguments):
    return execute_analysis(RoutedToolCall(name=name, arguments=arguments))


def sql(query: str):
    return duckdb.sql(query.replace("FROM fires", f"FROM read_parquet('{TABLE}')")).fetchall()


def test_table_is_india_only_deduplicated_and_tagged():
    assert FIRE_TOOL_NAMES <= set(TOOL_BY_NAME)
    assert sql("SELECT count(*) FROM fires WHERE state IS NULL OR district IS NULL") == [(0,)]
    duplicates = sql(
        "SELECT count(*) FROM (SELECT product, latitude, longitude, time_utc, count(*) AS n "
        "FROM fires GROUP BY ALL HAVING n > 1)"
    )
    assert duplicates == [(0,)]
    offsets = sql("SELECT DISTINCT time_ist - time_utc FROM fires")
    assert len(offsets) == 1 and str(offsets[0][0]) == "5:30:00"


def test_state_series_matches_a_direct_count():
    first, last = fires.coverage()
    result = ask("fire_series", state="Punjab", start_date=first, end_date=last)
    expected = sql(
        f"SELECT count(*) FROM fires WHERE state = 'Punjab' AND confidence <> 'low' "
        f"AND date_ist BETWEEN '{first}' AND '{last}'"
    )[0][0]
    assert sum(row["detections"] for row in result.rows) == expected
    assert "not burned area" in result.plan.method_note


def test_map_bins_detections_and_lists_districts():
    result = ask("fire_map", state="Punjab")
    grid = result.grid
    assert grid["scale"] == "fires" and grid["domain"] == "firms" and grid["outline_label"] == "Punjab"
    assert grid["resolution_degrees"] <= 0.1
    shown = sum(v for v in grid["frames"][0]["values"] if v is not None)
    assert shown >= sum(row["detections"] for row in result.rows)  # the map also shows neighbours
    india = ask("fire_map", statistic="sequence")
    assert india.grid["resolution_degrees"] == 0.25 and 1 <= len(india.grid["frames"]) <= 7


def test_rank_within_state_and_out_of_range_refusal():
    ranked = ask("fire_rank", state="Punjab", limit=5)
    counts = [row["detections"] for row in ranked.rows]
    assert counts == sorted(counts, reverse=True) and {row["state"] for row in ranked.rows} <= {"Punjab"}
    old = ask("fire_map", state="Punjab", start_date="2020-11-01", end_date="2020-11-05")
    assert not old.plan.in_scope and "MAP_KEY" in old.plan.refusal


def test_fire_plans_pass_validation():
    call = RoutedToolCall("fire_map", {"state": "Punjab"})
    assert routing_alignment_issue("Map stubble burning fires in Punjab this week.", [call]) is None
    rank = RoutedToolCall("fire_rank", {"level": "district"})
    assert routing_alignment_issue("Which districts had the most farm fires?", [rank]) is None


def test_presenter_instruction_echo_is_removed():
    from backend.app import drop_instruction_echo

    echoed = (
        "The top districts are Dhanbad 713 and Anugul 635. Note that counts are detections "
        "and name the dates. If an industrial district ranks high, say it may be a heat source."
    )
    assert drop_instruction_echo(echoed) == "The top districts are Dhanbad 713 and Anugul 635."
    plain = "Delhi had 104 µg/m³ in 2024. The values come from 38 stations."
    assert drop_instruction_echo(plain) == plain
