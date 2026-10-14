from __future__ import annotations

import datetime as dt
import decimal
import difflib
import math
import re
import threading
from pathlib import Path
from typing import Any

import duckdb
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from .catalog import DATA_SOURCES

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ALLOWED_TABLES = {
    "air_quality",
    "stations",
    "states",
    "census_states",
    "air_quality_standards",
    "ncap_funding",
    "ncap_program_finance",
    "ncap_physical_progress",
    "data_sources",
}

DISALLOWED_SQL = re.compile(
    r"\b("
    r"attach|copy|export|import|install|load|pragma|call|set|create|drop|alter|"
    r"insert|update|delete|merge|truncate|grant|revoke|vacuum|checkpoint|"
    r"read_csv|read_json|read_parquet|read_text|glob|httpfs|sqlite_scan|"
    r"postgres_scan|mysql_scan|getenv|current_setting"
    r")\b",
    re.IGNORECASE,
)
DISALLOWED_FUNCTION = re.compile(
    r"\b(?:"
    r"read|scan|glob|sniff|query_table|duckdb|pragma|current_setting|"
    r"getenv|secret|shell|system|parquet|csv|json|sqlite|postgres|mysql|"
    r"generate_series|range|unnest|repeat"
    r")\w*\s*\(",
    re.IGNORECASE,
)
class AirQualityDatabase:
    def __init__(self) -> None:
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.Lock()
        self._air_quality_cities: list[str] = []
        self._funding_cities: list[str] = []

    def initialize(self) -> None:
        required = [
            DATA_DIR / "AQ_met_data.csv",
            DATA_DIR / "AQ_met_data_2025.csv",
            DATA_DIR / "stations.csv",
            DATA_DIR / "states_data.csv",
            DATA_DIR / "ncap_funding_data.csv",
            DATA_DIR / "census_state_context.csv",
            DATA_DIR / "air_quality_standards.csv",
            DATA_DIR / "ncap_program_finance_2025.csv",
            DATA_DIR / "ncap_physical_progress_2025.csv",
            DATA_DIR / "data_catalog.json",
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError(f"Missing data files: {', '.join(missing)}")

        connection = duckdb.connect(database=":memory:")
        connection.execute("SET threads = 2")
        connection.execute(
            """
            CREATE TABLE air_quality AS
            SELECT
                TRY_CAST("Timestamp" AS DATE) AS timestamp,
                "State" AS state,
                "City" AS city,
                "Station" AS station,
                "site_id" AS site_id,
                TRY_CAST("Year" AS INTEGER) AS year,
                TRY_CAST("PM2.5 (µg/m³)" AS DOUBLE) AS pm25,
                TRY_CAST("PM10 (µg/m³)" AS DOUBLE) AS pm10,
                TRY_CAST("NO (µg/m³)" AS DOUBLE) AS no,
                TRY_CAST("NO2 (µg/m³)" AS DOUBLE) AS no2,
                TRY_CAST("NOx (ppb)" AS DOUBLE) AS nox,
                TRY_CAST("NH3 (µg/m³)" AS DOUBLE) AS nh3,
                TRY_CAST("SO2 (µg/m³)" AS DOUBLE) AS so2,
                TRY_CAST("CO (mg/m³)" AS DOUBLE) AS co,
                TRY_CAST("Ozone (µg/m³)" AS DOUBLE) AS ozone,
                TRY_CAST("AT (°C)" AS DOUBLE) AS temperature,
                TRY_CAST("RH (%)" AS DOUBLE) AS humidity,
                TRY_CAST("WS (m/s)" AS DOUBLE) AS wind_speed,
                TRY_CAST("WD (deg)" AS DOUBLE) AS wind_direction,
                TRY_CAST("RF (mm)" AS DOUBLE) AS rainfall,
                TRY_CAST("TOT-RF (mm)" AS DOUBLE) AS total_rainfall,
                TRY_CAST("SR (W/mt2)" AS DOUBLE) AS solar_radiation,
                TRY_CAST("BP (mmHg)" AS DOUBLE) AS pressure,
                TRY_CAST("VWS (m/s)" AS DOUBLE) AS vertical_wind_speed
            FROM read_csv_auto(
                ?,
                header = true,
                ignore_errors = true,
                union_by_name = true
            )
            """,
            [str(DATA_DIR / "AQ_met_data*.csv")],
        )
        connection.execute(
            """
            CREATE TABLE stations AS
            SELECT
                site_id,
                state,
                city,
                station,
                address,
                TRY_CAST(latitude AS DOUBLE) AS latitude,
                TRY_CAST(longitude AS DOUBLE) AS longitude
            FROM read_csv_auto(?, header = true, ignore_errors = true)
            """,
            [str(DATA_DIR / "stations.csv")],
        )
        connection.execute(
            """
            CREATE TABLE states AS
            SELECT
                state,
                TRY_CAST(population AS BIGINT) AS population,
                TRY_CAST("area (km2)" AS DOUBLE) AS area_km2,
                TRY_CAST(isUnionTerritory AS BOOLEAN) AS is_union_territory
            FROM read_csv_auto(?, header = true, ignore_errors = true)
            """,
            [str(required[3])],
        )
        connection.execute(
            """
            CREATE TABLE ncap_funding AS
            SELECT
                state,
                city,
                TRY_CAST("Amount released during FY 2019-20" AS DOUBLE) AS fy_2019_20,
                TRY_CAST("Amount released during FY 2020-21" AS DOUBLE) AS fy_2020_21,
                TRY_CAST("Amount released during FY 2021-22" AS DOUBLE) AS fy_2021_22,
                TRY_CAST("Total fund released" AS DOUBLE) AS total_fund_released,
                TRY_CAST("Utilisation as on June 2022" AS DOUBLE) AS utilisation_june_2022
            FROM read_csv_auto(?, header = true, ignore_errors = true)
            """,
            [str(required[4])],
        )
        connection.execute(
            """
            CREATE TABLE census_states AS
            SELECT
                category,
                state,
                TRY_CAST(population_2011 AS BIGINT) AS population_2011,
                TRY_CAST(decadal_growth_2001_2011_pct AS DOUBLE)
                    AS decadal_growth_2001_2011_pct,
                TRY_CAST(population_density_2011_per_km2 AS DOUBLE)
                    AS population_density_2011_per_km2
            FROM read_csv_auto(?, header = true, ignore_errors = true)
            """,
            [str(required[5])],
        )
        connection.execute(
            """
            CREATE TABLE air_quality_standards AS
            SELECT
                pollutant,
                averaging_period,
                TRY_CAST(standard_limit AS DOUBLE) AS standard_limit,
                unit,
                area_scope,
                standard_name,
                TRY_CAST(notification_year AS INTEGER) AS notification_year
            FROM read_csv_auto(?, header = true, ignore_errors = true)
            """,
            [str(required[6])],
        )
        connection.execute(
            """
            CREATE TABLE ncap_program_finance AS
            SELECT
                program,
                fiscal_year,
                TRY_CAST(released_crore AS DOUBLE) AS released_crore,
                TRY_CAST(utilised_crore AS DOUBLE) AS utilised_crore,
                TRY_CAST(utilisation_pct AS DOUBLE) AS utilisation_pct,
                TRY_CAST(reporting_date AS DATE) AS reporting_date
            FROM read_csv_auto(?, header = true, ignore_errors = true)
            """,
            [str(required[7])],
        )
        connection.execute(
            """
            CREATE TABLE ncap_physical_progress AS
            SELECT
                activity,
                unit,
                TRY_CAST(ncap AS DOUBLE) AS ncap,
                TRY_CAST(xvfc AS DOUBLE) AS xvfc,
                TRY_CAST(ncap_plus_xvfc AS DOUBLE) AS ncap_plus_xvfc,
                TRY_CAST(other_scheme_convergence AS DOUBLE)
                    AS other_scheme_convergence,
                TRY_CAST(reporting_date AS DATE) AS reporting_date
            FROM read_csv_auto(?, header = true, ignore_errors = true)
            """,
            [str(required[8])],
        )
        connection.execute(
            """
            CREATE TABLE data_sources (
                id VARCHAR,
                title VARCHAR,
                provider VARCHAR,
                date_start VARCHAR,
                date_end VARCHAR,
                temporal_granularity VARCHAR,
                geographic_granularity VARCHAR,
                records BIGINT,
                quality VARCHAR,
                provenance VARCHAR
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO data_sources
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    source.get("id"),
                    source.get("title"),
                    source.get("provider"),
                    source.get("date_start"),
                    source.get("date_end"),
                    source.get("temporal_granularity"),
                    source.get("geographic_granularity"),
                    source.get("records"),
                    source.get("quality"),
                    source.get("provenance"),
                )
                for source in DATA_SOURCES
            ],
        )
        connection.execute("ANALYZE")
        self._air_quality_cities = [
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT city
                FROM air_quality
                WHERE city IS NOT NULL AND trim(city) <> ''
                ORDER BY city
                """
            ).fetchall()
        ]
        self._funding_cities = [
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT city
                FROM ncap_funding
                WHERE city IS NOT NULL AND trim(city) <> ''
                ORDER BY city
                """
            ).fetchall()
        ]
        self._connection = connection

    @staticmethod
    def validate_sql(sql: str) -> str:
        cleaned = sql.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.rstrip(";").strip()

        if not cleaned or not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
            raise ValueError("Only read-only SELECT queries are allowed.")
        if ";" in cleaned or "--" in cleaned or "/*" in cleaned or "*/" in cleaned:
            raise ValueError("Multiple statements and SQL comments are not allowed.")
        if DISALLOWED_SQL.search(cleaned):
            raise ValueError("The generated query contains a blocked operation.")
        if DISALLOWED_FUNCTION.search(cleaned):
            raise ValueError("The generated query contains a blocked function.")

        try:
            expression = parse_one(cleaned, read="duckdb")
        except ParseError as exc:
            raise ValueError("The generated query is not valid DuckDB SQL.") from exc
        if not isinstance(expression, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise ValueError("Only read-only SELECT queries are allowed.")
        if sum(1 for _ in expression.walk()) > 600:
            raise ValueError("The generated query is too complex.")

        with_expression = expression.args.get("with_")
        if with_expression is not None and with_expression.args.get("recursive"):
            raise ValueError("Recursive queries are not allowed.")

        for join in expression.find_all(exp.Join):
            kind = str(join.args.get("kind") or "").upper()
            if kind == "CROSS" or (
                join.args.get("on") is None
                and join.args.get("using") is None
            ):
                raise ValueError("Cross joins are not allowed.")

        ctes = {
            cte.alias_or_name.lower()
            for cte in expression.find_all(exp.CTE)
            if cte.alias_or_name
        }
        referenced = {
            table.name.lower()
            for table in expression.find_all(exp.Table)
            if table.name
        }
        invalid = referenced - ALLOWED_TABLES - ctes
        if invalid:
            raise ValueError(
                f"Query referenced an unavailable table: {', '.join(sorted(invalid))}."
            )
        if not referenced.intersection(ALLOWED_TABLES):
            raise ValueError("Query must use one of the VayuChat data tables.")
        physical_references = [
            table.name.lower()
            for table in expression.find_all(exp.Table)
            if table.name and table.name.lower() in ALLOWED_TABLES
        ]
        if len(physical_references) > 4:
            raise ValueError("The generated query references too many data tables.")
        return cleaned

    def execute(self, sql: str, max_rows: int = 200) -> tuple[list[str], list[dict], bool]:
        if self._connection is None:
            raise RuntimeError("Database has not been initialized.")
        safe_sql = self.validate_sql(sql)
        wrapped = f"SELECT * FROM ({safe_sql}) AS vayuchat_result LIMIT {max_rows + 1}"
        with self._lock:
            cursor = self._connection.execute(wrapped)
            columns = [column[0] for column in cursor.description]
            values = cursor.fetchall()
        truncated = len(values) > max_rows
        rows = [
            {
                column: self._json_safe(value)
                for column, value in zip(columns, row, strict=True)
            }
            for row in values[:max_rows]
        ]
        return columns, rows, truncated

    def stats(self) -> dict[str, Any]:
        if self._connection is None:
            return {"ready": False}
        with self._lock:
            row = self._connection.execute(
                """
                SELECT
                    MIN(timestamp),
                    MAX(timestamp),
                    COUNT(*),
                    COUNT(DISTINCT city),
                    COUNT(DISTINCT state),
                    COUNT(DISTINCT station)
                FROM air_quality
                """
            ).fetchone()
        return {
            "ready": True,
            "first_date": self._json_safe(row[0]),
            "last_date": self._json_safe(row[1]),
            "records": row[2],
            "cities": row[3],
            "states": row[4],
            "stations": row[5],
            "sources": DATA_SOURCES,
        }

    def suggest_city_names(
        self,
        requested: list[str],
        *,
        funding: bool = False,
    ) -> dict[str, str]:
        candidates = self._funding_cities if funding else self._air_quality_cities
        normalized_candidates = {
            candidate.strip().casefold(): candidate
            for candidate in candidates
        }
        candidate_keys = list(normalized_candidates)
        suggestions: dict[str, str] = {}
        for value in requested:
            normalized = value.strip().casefold()
            if not normalized or normalized in normalized_candidates:
                continue
            matches = difflib.get_close_matches(
                normalized,
                candidate_keys,
                n=1,
                cutoff=0.68,
            )
            if matches:
                suggestions[value] = normalized_candidates[matches[0]]
        return suggestions

    def city_names_in_text(self, text: str) -> list[str]:
        """Return exact dataset city names mentioned in text, in text order."""
        normalized_text = text.casefold()
        matches: list[tuple[int, str]] = []
        for city in self._air_quality_cities:
            normalized_city = city.strip().casefold()
            if not normalized_city:
                continue
            pattern = (
                r"(?<!\w)"
                + re.escape(normalized_city).replace(r"\ ", r"\s+")
                + r"(?!\w)"
            )
            match = re.search(pattern, normalized_text)
            if match:
                matches.append((match.start(), city))
        return [city for _, city in sorted(matches)]

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dt.date, dt.datetime, dt.time)):
            return value.isoformat()
        if isinstance(value, decimal.Decimal):
            return float(value)
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value


database = AirQualityDatabase()
