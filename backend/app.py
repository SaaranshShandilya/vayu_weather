from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .analysis_tools import METRIC_LABELS, TOOL_BY_NAME, build_analysis
from .catalog import AIR_QUALITY_END_YEAR, AIR_QUALITY_START_YEAR
from .config import Settings, get_settings
from .database import database
from .gemini_service import GeminiService, RoutedToolCall, build_key_pool
from .gridded import GRIDDED_TOOL_NAMES, gridded_forecast
from .keypool import KeyPool, PoolExhausted
from .models import ChatRequest, ChatResponse, LoginRequest, QueryPlan
from .security import (
    LoginRateLimiter,
    client_key,
    create_session_token,
    passwords_match,
    require_same_origin,
    session_cookie_policy,
    verify_session_token,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vayuchat")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist"
rate_limiter = LoginRateLimiter()
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
LATEX_MICROGRAM_UNIT = re.compile(
    r"\$?\s*\\mu\s*(?:\\text\{)?g/m(?:\})?\s*\^\{?3\}?\s*\$?",
    re.IGNORECASE,
)
LATEX_MILLIGRAM_UNIT = re.compile(
    r"\$?\s*(?:\\text\{)?mg/m(?:\})?\s*\^\{?3\}?\s*\$?",
    re.IGNORECASE,
)
SUMMARY_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
SIMPLE_PLURAL = re.compile(
    r"\b1 (stations|days|observations|cities|records)\b",
    re.IGNORECASE,
)
POLLUTANT_CLARIFICATION_OPTIONS = ("PM2.5", "PM10", "NO₂", "ozone")
FUNDING_CLARIFICATION_OPTIONS = (
    "Historical city funding",
    "State fund utilisation",
    "National programme finance",
)


@dataclass(frozen=True)
class ExecutedAnalysis:
    name: str
    arguments: dict[str, Any]
    plan: QueryPlan
    columns: list[str]
    rows: list[dict]
    truncated: bool
    city_corrections: dict[str, str]
    grid: dict[str, Any] | None = None


def clean_model_text(value: str) -> str:
    cleaned = CONTROL_CHARACTERS.sub("", value)
    cleaned = LATEX_MICROGRAM_UNIT.sub("µg/m³", cleaned)
    cleaned = LATEX_MILLIGRAM_UNIT.sub("mg/m³", cleaned)
    for variant in ("Pcg/cm³", "μg/m³", "ug/m3", "µg/m3"):
        cleaned = cleaned.replace(variant, "µg/m³")
    cleaned = SIMPLE_PLURAL.sub(
        lambda match: f"1 {match.group(1)[:-1]}",
        cleaned,
    )
    return cleaned.strip()


def format_result_value(value: Any) -> str:
    if value is None:
        return "not available"
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def render_result_summary(
    template: str,
    columns: list[str],
    rows: list[dict],
) -> str:
    if not rows:
        return "The available data returned no matching observations."

    allowed_values = {
        column: format_result_value(rows[0].get(column))
        for column in columns
    }
    allowed_values["result_count"] = format_result_value(len(rows))
    placeholders = SUMMARY_PLACEHOLDER.findall(template)
    if template.strip() and placeholders and all(
        placeholder in allowed_values for placeholder in placeholders
    ):
        rendered = SUMMARY_PLACEHOLDER.sub(
            lambda match: allowed_values[match.group(1)],
            template,
        )
        return clean_model_text(rendered)

    first_row = "; ".join(
        f"{column.replace('_', ' ')}: {format_result_value(rows[0].get(column))}"
        for column in columns[:4]
    )
    if len(rows) == 1:
        return f"The analysis returned: {first_row}."
    return (
        f"The first of {len(rows):,} results is {first_row}. "
        "The complete result is shown in the table and chart."
    )


def requested_city_names(
    analysis_name: str,
    arguments: dict[str, Any],
) -> list[str]:
    if analysis_name in {
        "time_trend",
        "city_average",
        "relationship",
        "strongest_weather_relationship",
        "seasonal_profile",
        "weekday_weekend_profile",
        "condition_comparison",
        "rank_stations",
    }:
        city = arguments.get("city")
        return [city] if isinstance(city, str) and city.strip() else []
    if analysis_name in GRIDDED_TOOL_NAMES:
        city = arguments.get("city")
        return [city] if isinstance(city, str) and city.strip() else []
    if analysis_name in {
        "compare_cities",
        "funding_lookup",
        "station_coverage",
        "coverage_trend",
        "pollution_change",
        "threshold_frequency",
        "station_map",
    }:
        cities = arguments.get("cities")
        if isinstance(cities, list):
            return [
                city
                for city in cities
                if isinstance(city, str) and city.strip()
            ]
    return []


def corrected_question(
    question: str,
    original: str,
    suggestion: str,
) -> str:
    if original in question:
        return question.replace(original, suggestion, 1)
    return f"Repeat this analysis for {suggestion}."


def clarification_options(analysis: ExecutedAnalysis) -> list[str]:
    """Return concise, safe choices for a bounded clarification."""
    raw_options = analysis.arguments.get("options")
    candidates = raw_options if isinstance(raw_options, list) else []
    cleaned: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        option = clean_model_text(candidate)
        key = option.casefold()
        if not option or len(option) > 80 or key in seen:
            continue
        seen.add(key)
        cleaned.append(option)
        if len(cleaned) == 6:
            break

    clarification = clean_model_text(analysis.plan.refusal).casefold()
    if "pollutant" in clarification:
        pollutant_keys = (
            "pm25",
            "pm10",
            "no",
            "no2",
            "nox",
            "nh3",
            "so2",
            "co",
            "ozone",
        )
        canonical_by_input: dict[str, str] = {}
        for key in pollutant_keys:
            label = METRIC_LABELS[key]
            canonical_by_input[key.casefold()] = label
            canonical_by_input[label.casefold()] = label
        canonical_by_input.update(
            {
                "pm2.5": "PM2.5",
                "no2": "NO₂",
                "nox": "NOx",
                "nh3": "NH₃",
                "so2": "SO₂",
            }
        )
        normalized = [
            canonical_by_input[option.casefold()]
            for option in cleaned
            if option.casefold() in canonical_by_input
        ]
        prioritized = list(POLLUTANT_CLARIFICATION_OPTIONS) + normalized
        return list(dict.fromkeys(prioritized))[:6]

    if "funding" in clarification and not cleaned:
        return list(FUNDING_CLARIFICATION_OPTIONS)
    return cleaned


def capability_suggested_questions(
    results: list[ExecutedAnalysis],
) -> list[str]:
    """Return only follow-ups backed by known typed functions and result scope."""
    if not results:
        return []

    primary = results[0]
    arguments = primary.arguments
    if primary.name in GRIDDED_TOOL_NAMES:
        return gridded_suggested_questions(primary)
    first_row = primary.rows[0] if primary.rows else {}
    pollutant = arguments.get("pollutant")
    if not isinstance(pollutant, str):
        metric = arguments.get("metric")
        pollutant = metric if metric in {
            "pm25",
            "pm10",
            "no",
            "no2",
            "nox",
            "nh3",
            "so2",
            "co",
            "ozone",
        } else None
    city = arguments.get("city")
    if not isinstance(city, str) or not city.strip():
        city = first_row.get("city")
    if not isinstance(city, str) or not city.strip():
        cities = arguments.get("cities")
        city = (
            cities[0]
            if isinstance(cities, list) and cities
            and isinstance(cities[0], str)
            else None
        )

    start_year = arguments.get("start_year")
    end_year = arguments.get("end_year")
    if not isinstance(start_year, int):
        start_year = AIR_QUALITY_START_YEAR
    if not isinstance(end_year, int):
        end_year = AIR_QUALITY_END_YEAR
    period = (
        str(start_year)
        if start_year == end_year
        else f"{start_year} to {end_year}"
    )

    if pollutant and city:
        label = METRIC_LABELS[pollutant]
        return [
            f"What was the mean {label} for {city} in {period}?",
            (
                f"Rank monitoring stations in {city} by highest mean "
                f"{label} in {period}."
            ),
            f"Show the monthly {label} trend for {city} in {period}.",
        ]
    if pollutant:
        label = METRIC_LABELS[pollutant]
        return [
            f"Rank the 10 cities with the highest mean {label} in {period}.",
            f"Rank Indian states by mean {label} in {period}.",
            f"Show the seasonal {label} profile across India in {period}.",
        ]
    return [
        (
            "What air-quality data sources, date ranges, and granularities "
            "are available?"
        ),
        (
            f"Which city had the highest mean PM2.5 in "
            f"{AIR_QUALITY_END_YEAR}?"
        ),
        "What is the CPCB annual PM2.5 standard?",
    ]


def gridded_suggested_questions(primary: ExecutedAnalysis) -> list[str]:
    """Follow-ups for the gridded forecast, which has its own geography."""
    pollutant = primary.arguments.get("pollutant")
    label = METRIC_LABELS.get(pollutant, "PM2.5") if isinstance(pollutant, str) else "PM2.5"
    city = primary.arguments.get("city")
    place = city if isinstance(city, str) and city.strip() else "Delhi"
    if primary.name == "forecast_grid_map":
        return [
            f"Show the hourly {label} forecast for {place} tomorrow.",
            f"Map the hours above 60 µg/m³ {label} across Delhi NCR in the forecast.",
            f"Animate the {label} forecast hour by hour for tomorrow across Delhi NCR.",
        ]
    if primary.name == "forecast_point_series":
        return [
            f"Map where {label} is forecast to be highest across Delhi NCR tomorrow.",
            f"Map the hours above 60 µg/m³ {label} across Delhi NCR in the forecast.",
            f"Show the hourly {label} forecast for Gurugram over the next 3 days.",
        ]
    return [
        f"Map the mean {label} forecast across Delhi NCR for the next 3 days.",
        f"Show the hourly {label} forecast at Anand Vihar station.",
        f"Map the peak {label} forecast for {place} tomorrow.",
    ]


def execute_gridded_analysis(call: RoutedToolCall) -> ExecutedAnalysis:
    arguments = TOOL_BY_NAME[call.name].arguments_model.model_validate(
        call.arguments
    )
    output = gridded_forecast.run(call.name, arguments)
    corrections = database.suggest_city_names(
        requested_city_names(call.name, call.arguments)
    ) if not output.plan.in_scope else {}
    return ExecutedAnalysis(
        name=call.name,
        arguments=arguments.model_dump(),
        plan=output.plan,
        columns=output.columns,
        rows=output.rows,
        truncated=False,
        city_corrections=corrections,
        grid=output.grid,
    )


def execute_analysis(call: RoutedToolCall) -> ExecutedAnalysis:
    if call.name in GRIDDED_TOOL_NAMES:
        return execute_gridded_analysis(call)
    analysis = build_analysis(call.name, call.arguments)
    plan = analysis.plan
    if not plan.in_scope:
        return ExecutedAnalysis(
            name=analysis.name,
            arguments=analysis.arguments,
            plan=plan,
            columns=[],
            rows=[],
            truncated=False,
            city_corrections={},
        )
    safe_sql = database.validate_sql(plan.sql)
    max_rows = 600 if analysis.name == "station_map" else 200
    columns, rows, truncated = database.execute(safe_sql, max_rows=max_rows)
    corrections = database.suggest_city_names(
        requested_city_names(analysis.name, call.arguments),
        funding=analysis.name == "funding_lookup",
    )
    return ExecutedAnalysis(
        name=analysis.name,
        arguments=analysis.arguments,
        plan=plan,
        columns=columns,
        rows=rows,
        truncated=truncated,
        city_corrections=corrections,
    )


def display_result_set(result: ExecutedAnalysis) -> dict[str, Any]:
    columns = result.columns
    rows = result.rows
    x_key = result.plan.x_key if result.plan.x_key in columns else (
        columns[0] if columns else ""
    )
    numeric_candidates = [
        column
        for column in columns
        if any(
            isinstance(row.get(column), (int, float))
            for row in rows[:10]
        )
    ]
    y_keys = [
        key for key in result.plan.y_keys
        if key in numeric_candidates
    ]
    if not y_keys:
        y_keys = [
            key for key in numeric_candidates
            if key != x_key
        ][:4]
    return {
        "analysis_function": result.name,
        "columns": columns,
        "rows": rows,
        "visualization": result.plan.visualization if rows else "none",
        "title": result.plan.title,
        "x_key": x_key,
        "y_keys": y_keys,
        "truncated": result.truncated,
        "grid": result.grid,
    }


def routing_alignment_issue(
    question: str,
    calls: list[RoutedToolCall],
    history: list[Any] | None = None,
) -> str | None:
    if len(calls) == 1 and calls[0].name in {
        "clarify_question",
        "out_of_scope",
    }:
        return None

    issues: list[str] = []
    mentioned_cities = database.city_names_in_text(question)
    if re.search(r"\bncr\b", question.casefold()) and all(
        call.name in GRIDDED_TOOL_NAMES for call in calls
    ):
        # "Delhi NCR" names the whole forecast domain, not the city of Delhi.
        mentioned_cities = [
            city for city in mentioned_cities if city.casefold() != "delhi"
        ]
    if mentioned_cities:
        planned_cities = []
        for call in calls:
            planned_cities.extend(
                requested_city_names(call.name, call.arguments)
            )
        expected = {city.casefold() for city in mentioned_cities}
        actual = {city.strip().casefold() for city in planned_cities}
        follow_up_language = bool(
            re.search(
                r"\b(?:same|that|those|what about|how about|compare with|"
                r"do it for|now)\b",
                question.casefold(),
            )
        )
        prior_cities: set[str] = set()
        if follow_up_language:
            for message in reversed(history or []):
                role = (
                    message.get("role")
                    if isinstance(message, dict)
                    else getattr(message, "role", None)
                )
                if role != "user":
                    continue
                content = (
                    message.get("content", "")
                    if isinstance(message, dict)
                    else getattr(message, "content", "")
                )
                prior_cities = {
                    city.casefold()
                    for city in database.city_names_in_text(content)
                }
                break
        allowed = expected | prior_cities
        city_scope_is_valid = (
            expected <= actual <= allowed
            if follow_up_language and prior_cities
            else actual == expected
        )
        if not city_scope_is_valid:
            issues.append(
                "The function plan must preserve exactly the dataset cities "
                "named in a complete question, or only the current and "
                "immediately prior cities in an explicit follow-up. Current "
                f"cities: {', '.join(mentioned_cities)}."
            )

    normalized = question.casefold()
    if len(mentioned_cities) == 1:
        expected_city = mentioned_cities[0].casefold()
        for call in calls:
            if call.name in {
                "city_average",
                "time_trend",
                "relationship",
                "strongest_weather_relationship",
                "seasonal_profile",
                "weekday_weekend_profile",
                "condition_comparison",
                "rank_stations",
            }:
                planned_city = call.arguments.get("city")
                if not isinstance(planned_city, str) or (
                    planned_city.strip().casefold() != expected_city
                ):
                    issues.append(
                        f"Every city-scoped {call.name} call must preserve "
                        f"the named city {mentioned_cities[0]}."
                    )

    metric_patterns = {
        "pm25": r"\bpm\s*2(?:[.\s]?5)\b|particulate\s+matter\s*2(?:[.\s]?5)",
        "pm10": r"\bpm\s*10\b|particulate\s+matter\s*10",
        "no2": r"\bno\s*2\b|nitrogen\s+dioxide",
        "nox": r"\bnox\b|nitrogen\s+oxides",
        "nh3": r"\bnh\s*3\b|ammonia",
        "so2": r"\bso\s*2\b|sulphur\s+dioxide|sulfur\s+dioxide",
        "co": r"\bco\b(?!\s*2)|carbon\s+monoxide",
        "ozone": r"\bozone\b|\bo3\b",
        "temperature": r"\btemperature\b|\btemp\b",
        "humidity": r"\bhumidity\b",
        "wind_speed": r"\bwind\s+speed\b",
        "wind_direction": r"\bwind\s+direction\b",
        "rainfall": r"\brain(?:fall)?\b",
        "solar_radiation": r"\bsolar\s+radiation\b",
        "pressure": r"\bpressure\b",
    }
    mentioned_metrics = {
        metric
        for metric, pattern in metric_patterns.items()
        if re.search(pattern, normalized)
    }
    unsupported_metric_match = re.search(
        r"\b(?:co\s*2|carbon\s+dioxide|pm\s*1(?:\.0)?|pm\s*4|"
        r"aqi|air\s+quality\s+index|voc|volatile\s+organic\s+compounds?|"
        r"benzene|lead|pb)\b",
        normalized,
    )
    planned_metrics: set[str] = set()
    for call in calls:
        for key in (
            "pollutant",
            "metric",
            "condition_metric",
            "x_metric",
            "y_metric",
            "value",
        ):
            value = call.arguments.get(key)
            if isinstance(value, str) and value in METRIC_LABELS:
                planned_metrics.add(value)
    if mentioned_metrics - planned_metrics:
        issues.append(
            "The function plan omitted metrics named in the question: "
            + ", ".join(sorted(mentioned_metrics - planned_metrics))
            + "."
        )
    if unsupported_metric_match:
        issues.append(
            f"The requested measure '{unsupported_metric_match.group(0)}' is "
            "not in the loaded analytical schema. Use clarify_question for a "
            "possible supported alternative; never substitute a different "
            "measure silently."
        )
    if not mentioned_metrics and planned_metrics:
        issues.append(
            "The question did not name a supported pollutant or weather metric "
            "selected by the function plan. Use clarify_question instead of "
            "silently assuming one."
        )

    mentioned_years = sorted(
        {
            int(value)
            for value in re.findall(r"\b(?:19|20)\d{2}\b", normalized)
            if AIR_QUALITY_START_YEAR <= int(value) <= AIR_QUALITY_END_YEAR
        }
    )
    if 1 <= len(mentioned_years) <= 2:
        expected_start = mentioned_years[0]
        expected_end = mentioned_years[-1]
        for call in calls:
            if "start_year" not in call.arguments or "end_year" not in call.arguments:
                continue
            actual_start = call.arguments.get("start_year")
            actual_end = call.arguments.get("end_year")
            if actual_start != expected_start or actual_end != expected_end:
                issues.append(
                    f"Every temporal {call.name} call must preserve the "
                    f"explicit {expected_start}–{expected_end} window."
                )

    threshold_calls = {
        "threshold_cities",
        "ncap_threshold_cities",
        "threshold_frequency",
        "condition_comparison",
        "forecast_exceedance_map",
    }
    if any(call.name in threshold_calls for call in calls):
        numeric_values = [
            float(value)
            for value in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)", normalized)
        ]
        has_numeric_threshold = any(
            not (value.is_integer() and 1900 <= value <= 2100)
            for value in numeric_values
        )
        names_documented_limit = bool(
            re.search(r"\b(?:standard|limit|threshold)\b", normalized)
        )
        if not has_numeric_threshold and not names_documented_limit:
            issues.append(
                "The question did not state a threshold. Use clarify_question "
                "instead of inventing one."
            )

    requested_capabilities: list[tuple[bool, set[str], str]] = [
        (
            bool(re.search(r"\b(?:standard|regulatory limit)\b", normalized)),
            {"air_quality_standard"},
            "a requested regulatory standard",
        ),
        (
            bool(
                re.search(
                    r"\b(?:station|monitoring|observation)\s+coverage\b",
                    normalized,
                )
            ),
            {"station_coverage", "coverage_trend"},
            "requested monitoring coverage",
        ),
        (
            bool(
                re.search(r"\bstations?\b", normalized)
                and re.search(
                    r"\b(?:highest|lowest|top|bottom|rank|ranking|"
                    r"most polluted|cleanest|dirtiest)\b",
                    normalized,
                )
                and not re.search(
                    r"\b(?:coverage|station count|number of stations|"
                    r"most stations|fewest stations)\b",
                    normalized,
                )
            ),
            {"rank_stations"},
            "a requested station-level value ranking",
        ),
        (
            bool(
                re.search(r"\bstrongest\b", normalized)
                and re.search(
                    r"\b(?:weather|meteorological|correlat|association|relationship)\w*",
                    normalized,
                )
            ),
            {"strongest_weather_relationship"},
            "a requested strongest-weather-factor ranking",
        ),
        (
            bool(re.search(r"\b(?:map|choropleth)\b", normalized)),
            {
                "station_map",
                "state_choropleth",
                "forecast_grid_map",
                "forecast_exceedance_map",
            },
            "a requested map",
        ),
    ]
    planned_tools = {call.name for call in calls}
    for requested, acceptable_tools, label in requested_capabilities:
        if requested and not (planned_tools & acceptable_tools):
            issues.append(
                f"The function plan omitted {label}; include a distinct "
                "function call when another call cannot supply it."
            )

    if any(
        call.name == "context_relationship"
        and call.arguments.get("pollution_measure") == "change"
        for call in calls
    ):
        years = {
            int(value)
            for value in re.findall(r"\b(?:19|20)\d{2}\b", normalized)
        }
        if len(years) < 2:
            issues.append(
                "A pollution-change relationship needs two explicit endpoint "
                "years. Use clarify_question rather than inventing them."
            )

    if any(call.name == "programme_finance_trend" for call in calls):
        names_programme_scope = bool(
            re.search(r"\b(?:ncap|xv-?fc|programme|program|funding)\b", normalized)
        )
        if not names_programme_scope:
            issues.append(
                "The finance scope is unspecified. Use clarify_question to ask "
                "whether the user means city funding, state utilisation, or "
                "national programme finance."
            )

    asks_seasonal_relationship = (
        re.search(r"\b(?:seasonal|season[- ]?wise|by season)\b", normalized)
        and re.search(
            r"\b(?:relationship|relation|correlation|association)\b",
            normalized,
        )
    )
    if asks_seasonal_relationship:
        seasonal_relationship = any(
            call.name == "relationship"
            and call.arguments.get("group_by") == "season"
            for call in calls
        )
        if not seasonal_relationship:
            issues.append(
                "A seasonal relationship question requires relationship with "
                'group_by="season"; a pooled correlation is not equivalent.'
            )
    if not issues:
        return None
    return " ".join(dict.fromkeys(issues))


@asynccontextmanager
async def lifespan(instance: FastAPI):
    logger.info("Loading VayuChat datasets")
    database.initialize()
    logger.info("VayuChat datasets ready: %s", database.stats())
    # One pool per process, built once: keys and their quota windows are
    # process-wide state, not per-request state.
    settings = get_settings()
    instance.state.key_pool = build_key_pool(settings)
    # Bounds how many turns contend for the pool at once. The pool itself is
    # the real limiter; this just stops a flood from queueing inside the app.
    instance.state.chat_slots = (
        asyncio.Semaphore(settings.api_concurrency_limit)
        if settings.api_concurrency_limit > 0
        else None
    )
    if instance.state.key_pool is None:
        logger.warning("No Gemini API key configured; /api/chat will return 503")
    else:
        logger.info("Gemini key pool ready: %s", instance.state.key_pool.stats())
    yield


app = FastAPI(
    title="VayuChat",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'self' https://huggingface.co https://*.huggingface.co; "
        "base-uri 'self'; form-action 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def settings_dependency() -> Settings:
    return get_settings()


def require_auth(
    request: Request,
    settings: Settings = Depends(settings_dependency),
) -> None:
    if not settings.app_password.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="APP_PASSWORD has not been configured.",
        )
    token = request.cookies.get(settings.cookie_name, "")
    if not token or not verify_session_token(token, settings.app_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )


def key_pool(request: Request) -> KeyPool | None:
    return getattr(request.app.state, "key_pool", None)


@asynccontextmanager
async def chat_slot(request: Request):
    slots = getattr(request.app.state, "chat_slots", None)
    if slots is None:
        yield
        return
    async with slots:
        yield


@app.get("/api/session")
async def session_status(
    request: Request,
    settings: Settings = Depends(settings_dependency),
):
    token = request.cookies.get(settings.cookie_name, "")
    return {
        "authenticated": bool(
            token
            and settings.app_password
            and verify_session_token(token, settings.app_password)
        ),
        "configured": settings.is_configured,
    }


@app.post("/api/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(settings_dependency),
):
    require_same_origin(request)
    if not settings.app_password.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="APP_PASSWORD has not been configured.",
        )
    key = client_key(request)
    rate_limiter.check(key)
    if not passwords_match(body.password, settings.app_password):
        rate_limiter.record_failure(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That password is not correct.",
        )

    rate_limiter.clear(key)
    token = create_session_token(
        settings.app_password,
        settings.cookie_ttl_seconds,
    )
    secure_cookie, same_site = session_cookie_policy(request)
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.cookie_ttl_seconds,
        httponly=True,
        secure=secure_cookie,
        samesite=same_site,
        path="/",
    )
    return {"authenticated": True}


@app.post("/api/logout")
async def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(settings_dependency),
):
    require_same_origin(request)
    response.delete_cookie(settings.cookie_name, path="/")
    return {"authenticated": False}


@app.get("/api/data-info", dependencies=[Depends(require_auth)])
async def data_info():
    return database.stats()


@app.get("/api/keys", dependencies=[Depends(require_auth)])
async def key_status(request: Request):
    """Pool headroom, per key. Keys appear only as fingerprints."""
    pool = key_pool(request)
    if pool is None:
        return {"configured": False, "pool": {}, "keys": []}
    return {"configured": True, "pool": pool.stats(), "keys": pool.snapshot()}


@app.post(
    "/api/chat",
    response_model=ChatResponse,
    dependencies=[Depends(require_auth)],
)
async def chat(
    body: ChatRequest,
    request: Request,
    settings: Settings = Depends(settings_dependency),
):
    require_same_origin(request)
    pool = key_pool(request)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No Gemini API key is configured. Set GEMINI_API_KEYS.",
        )
    async with chat_slot(request):
        return await run_chat_turn(body, settings, pool)


async def run_chat_turn(
    body: ChatRequest,
    settings: Settings,
    pool: KeyPool,
) -> ChatResponse:
    # One service per turn, so its wall-clock budget covers every call the turn
    # makes rather than each call separately.
    service = GeminiService(
        model=settings.gemini_model,
        pool=pool,
        turn_budget_seconds=settings.turn_budget_seconds,
    )
    started_at = time.perf_counter()
    try:
        routed = await asyncio.wait_for(
            service.route(body.message, body.history),
            timeout=service.call_timeout(20),
        )
        alignment_issue = routing_alignment_issue(
            body.message,
            routed.calls,
            body.history,
        )
        for _ in range(2):
            if not alignment_issue:
                break
            logger.warning(
                "Rejected question-to-plan mismatch: %s",
                alignment_issue,
            )
            routed = await asyncio.wait_for(
                service.route(
                    body.message,
                    body.history,
                    validation_feedback=(
                        f"{alignment_issue} The rejected plan called: "
                        + ", ".join(call.name for call in routed.calls)
                        + ". Rebuild the complete plan for every requested "
                        "clause, retaining any call that was already correct."
                    ),
                ),
                timeout=service.call_timeout(20),
            )
            alignment_issue = routing_alignment_issue(
                body.message,
                routed.calls,
                body.history,
            )
        if alignment_issue:
            raise ValueError(alignment_issue)
        executed = [
            execute_analysis(call)
            for call in routed.calls
        ]
        if len(executed) == 1 and not executed[0].plan.in_scope:
            refusal_plan = executed[0].plan
            choices = (
                clarification_options(executed[0])
                if executed[0].name == "clarify_question"
                else []
            )
            return ChatResponse(
                answer=clean_model_text(refusal_plan.refusal)
                or (
                    "I can help with Indian air quality, pollution, "
                    "meteorology, and NCAP funding questions."
                ),
                key_points=[],
                method_note="",
                analysis_steps=[],
                columns=[],
                rows=[],
                visualization="none",
                title="",
                x_key="",
                y_keys=[],
                analysis_type=executed[0].name,
                elapsed_ms=round((time.perf_counter() - started_at) * 1_000),
                suggested_questions=[],
                clarification_options=choices,
            )

        primary = executed[0]
        plan = primary.plan
        primary_display = display_result_set(primary)
        columns = primary_display["columns"]
        rows = primary_display["rows"]
        truncated = primary_display["truncated"]
        result_sets = [
            display_result_set(result)
            for result in executed
        ]

        city_corrections: dict[str, str] = {}
        for result in executed:
            city_corrections.update(result.city_corrections)
        additional_result_sets = [
            {
                "analysis_function": result.name,
                "method": result.plan.method_note,
                "verified_result_template": result.plan.summary_template,
                "columns": result.columns,
                "result_count": len(result.rows),
                "results_truncated": result.truncated,
                "rows": result.rows[:25],
            }
            for result in executed[1:]
        ]
        analysis_type = "composition" if len(executed) > 1 else primary.name
        try:
            narrative = await asyncio.wait_for(
                service.present(
                    question=body.message,
                    analysis_name=analysis_type,
                    plan=plan,
                    columns=columns,
                    rows=rows,
                    truncated=truncated,
                    city_name_corrections=city_corrections,
                    additional_result_sets=additional_result_sets,
                ),
                timeout=service.call_timeout(15),
            )
            answer = clean_model_text(narrative.answer)
            key_points = []
            for point in narrative.key_points:
                cleaned_point = clean_model_text(point)
                if cleaned_point:
                    key_points.append(cleaned_point)
            suggested_questions = capability_suggested_questions(executed)
        except Exception:
            logger.exception("Presentation call failed; using verified template")
            answer = " ".join(
                render_result_summary(
                    result.plan.summary_template,
                    result.columns,
                    result.rows,
                )
                for result in executed
            )
            key_points = []
            suggested_questions = []

        correction_points = [
            f'No exact city match for "{original}". Did you mean {suggestion}?'
            for original, suggestion in city_corrections.items()
        ]
        key_points = correction_points + [
            point for point in key_points if point not in correction_points
        ]
        correction_questions = [
            corrected_question(body.message, original, suggestion)
            for original, suggestion in city_corrections.items()
        ]
        suggested_questions = (
            correction_questions
            + [
                question
                for question in suggested_questions
                if question not in correction_questions
            ]
        )[:3]
        if not any(result.rows for result in executed) and city_corrections:
            corrections = "; ".join(
                f'"{original}" → {suggestion}'
                for original, suggestion in city_corrections.items()
            )
            answer = (
                "No exact dataset match was found for the requested city name. "
                f"Did you mean {corrections}?"
            )
        if len(executed) == 1:
            method_note = clean_model_text(primary.plan.method_note)
        else:
            method_note = "\n\n".join(
                f"{index}. {result.name}: "
                f"{clean_model_text(result.plan.method_note)}"
                for index, result in enumerate(executed, start=1)
            )
        elapsed_ms = round((time.perf_counter() - started_at) * 1_000)
        total_rows = sum(len(result.rows) for result in executed)
        logger.info(
            "Completed %s function(s) in %.2fs with %s total result rows",
            len(executed),
            elapsed_ms / 1_000,
            total_rows,
        )

        return ChatResponse(
            answer=answer,
            key_points=key_points,
            method_note=method_note,
            analysis_steps=[
                {
                    "function_name": result.name,
                    "arguments": result.arguments,
                }
                for result in executed
            ],
            columns=columns,
            rows=rows,
            visualization=primary_display["visualization"],
            title=primary_display["title"],
            x_key=primary_display["x_key"],
            y_keys=primary_display["y_keys"],
            truncated=truncated,
            analysis_type=analysis_type,
            elapsed_ms=elapsed_ms,
            suggested_questions=suggested_questions,
            result_sets=result_sets,
            grid=primary_display["grid"],
        )
    except PoolExhausted:
        logger.warning("Gemini key pool exhausted: %s", pool.stats())
        return ChatResponse(
            answer=(
                "VayuChat is at its shared request limit right now, so this "
                "question was not sent to the model. Please try again in a "
                "minute."
            ),
            key_points=[
                "Every configured API key is currently rate limited or busy.",
                "No analysis was run, so nothing was answered from guesswork.",
            ],
            method_note=(
                "The request waited for an available API key and reached the "
                "time budget for this turn before one freed up."
            ),
            analysis_steps=[],
            columns=[],
            rows=[],
            visualization="none",
            title="Request limit reached",
            x_key="",
            y_keys=[],
            truncated=False,
            analysis_type="capacity_unavailable",
            elapsed_ms=round((time.perf_counter() - started_at) * 1_000),
            suggested_questions=[],
        )
    except Exception as exc:
        logger.exception("Chat request failed")
        return ChatResponse(
            answer=(
                "VayuChat could not produce a validated function plan after "
                "two attempts, so no data query was run. Please retry with an "
                "explicit pollutant and period, or use one of the supported "
                "questions below."
            ),
            key_points=[
                (
                    "Air-quality and weather observations cover "
                    f"{AIR_QUALITY_START_YEAR}–{AIR_QUALITY_END_YEAR}."
                ),
                (
                    "NCAP releases end at FY 2021–22; utilisation is dated "
                    "June 2022."
                ),
                "Unvalidated model output is never executed.",
            ],
            method_note=(
                "The router did not return arguments that satisfied a tested "
                "analysis function after one automatic repair attempt."
            ),
            analysis_steps=[],
            columns=[],
            rows=[],
            visualization="none",
            title="Analysis needs a clearer scope",
            x_key="",
            y_keys=[],
            truncated=False,
            analysis_type="analysis_unavailable",
            elapsed_ms=round((time.perf_counter() - started_at) * 1_000),
            suggested_questions=[
                (
                    "Which NCAP-funded cities reduced PM2.5 most from 2017 to "
                    "2025, and is total funding associated with that change?"
                ),
                (
                    "Which cities had the highest share of days above 60 µg/m³ "
                    "PM2.5 in 2023?"
                ),
                "Compare Delhi and Mumbai PM2.5 from 2022 to 2023.",
            ],
        )


if DIST_DIR.exists():
    assets_dir = DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def frontend(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    requested = DIST_DIR / full_path
    if full_path and requested.is_file() and DIST_DIR in requested.resolve().parents:
        return FileResponse(requested)
    index = DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Frontend build not found. Run npm run build.",
    )
