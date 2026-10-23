from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .catalog import AIR_QUALITY_END_YEAR, AIR_QUALITY_START_YEAR
from .cams_forecast import (
    CAMS_TOOL_NAMES,
    TOOL_GUIDE as CAMS_TOOL_GUIDE,
    CamsMapArgs,
    CamsRankArgs,
    CamsSeriesArgs,
    CamsThresholdArgs,
    cams_forecast,
)
from .models import QueryPlan
from .weather_grid import (
    WEATHER_TOOL_NAMES,
    WINDOW_DESCRIPTION as WEATHER_WINDOW_DESCRIPTION,
    WeatherGridMapArgs,
    WeatherPointSeriesArgs,
    WeatherRegionRankArgs,
    WeatherThresholdArgs,
    weather_grid,
)


Pollutant = Literal[
    "pm25",
    "pm10",
    "no",
    "no2",
    "nox",
    "nh3",
    "so2",
    "co",
    "ozone",
]
Metric = Literal[
    "pm25",
    "pm10",
    "no",
    "no2",
    "nox",
    "nh3",
    "so2",
    "co",
    "ozone",
    "temperature",
    "humidity",
    "wind_speed",
    "wind_direction",
    "rainfall",
    "total_rainfall",
    "solar_radiation",
    "pressure",
    "vertical_wind_speed",
]

METRIC_LABELS = {
    "pm25": "PM2.5",
    "pm10": "PM10",
    "no": "NO",
    "no2": "NO₂",
    "nox": "NOx",
    "nh3": "NH₃",
    "so2": "SO₂",
    "co": "CO",
    "ozone": "ozone",
    "temperature": "temperature",
    "humidity": "relative humidity",
    "wind_speed": "wind speed",
    "wind_direction": "wind direction",
    "rainfall": "rainfall",
    "total_rainfall": "total rainfall",
    "solar_radiation": "solar radiation",
    "pressure": "pressure",
    "vertical_wind_speed": "vertical wind speed",
}

METRIC_UNITS = {
    "pm25": "µg/m³",
    "pm10": "µg/m³",
    "no": "µg/m³",
    "no2": "µg/m³",
    "nox": "ppb",
    "nh3": "µg/m³",
    "so2": "µg/m³",
    "co": "mg/m³",
    "ozone": "µg/m³",
    "temperature": "°C",
    "humidity": "%",
    "wind_speed": "m/s",
    "wind_direction": "degrees",
    "rainfall": "mm",
    "total_rainfall": "mm",
    "solar_radiation": "W/m²",
    "pressure": "mmHg",
    "vertical_wind_speed": "m/s",
}

FUNDING_METRIC_LABELS = {
    "fy_2019_20": "funds released during FY 2019–20",
    "fy_2020_21": "funds released during FY 2020–21",
    "fy_2021_22": "funds released during FY 2021–22",
    "total_fund_released": "total funds released",
    "utilisation_june_2022": "utilisation as of June 2022",
}


class StrictArgs(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}


class YearRangeArgs(StrictArgs):
    start_year: int = Field(
        default=AIR_QUALITY_START_YEAR,
        ge=AIR_QUALITY_START_YEAR,
        le=AIR_QUALITY_END_YEAR,
    )
    end_year: int = Field(
        default=AIR_QUALITY_END_YEAR,
        ge=AIR_QUALITY_START_YEAR,
        le=AIR_QUALITY_END_YEAR,
    )

    @model_validator(mode="after")
    def validate_year_order(self):
        if self.start_year > self.end_year:
            raise ValueError("start_year must not be later than end_year")
        return self


class StationWeightedArgs(YearRangeArgs):
    months: list[int] = Field(default_factory=list, max_length=12)
    statistic: Literal["mean", "median"] = "mean"
    minimum_station_days: int = Field(default=30, ge=1, le=2_922)
    station_weighting: Literal["equal_station"] = "equal_station"

    @model_validator(mode="after")
    def validate_months(self):
        if any(month < 1 or month > 12 for month in self.months):
            raise ValueError("months must be integers from 1 through 12")
        self.months = sorted(set(self.months))
        return self


class RankCitiesArgs(StationWeightedArgs):
    pollutant: Pollutant
    limit: int = Field(default=10, ge=1, le=50)
    order: Literal["highest", "lowest"] = "highest"


class CityAverageArgs(StationWeightedArgs):
    pollutant: Pollutant
    city: str = Field(min_length=1, max_length=120)


class ThresholdCitiesArgs(StationWeightedArgs):
    pollutant: Pollutant
    threshold: float = Field(ge=0, le=10_000)
    comparison: Literal["above", "below"] = "above"
    limit: int = Field(default=100, ge=1, le=100)


class CompareCitiesArgs(StationWeightedArgs):
    pollutant: Pollutant
    cities: list[str] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def validate_cities(self):
        normalized = {city.casefold() for city in self.cities if city}
        if len(normalized) < 2:
            raise ValueError("at least two distinct non-empty cities are required")
        return self


class TimeTrendArgs(YearRangeArgs):
    pollutant: Pollutant
    city: str = Field(min_length=1, max_length=120)
    interval: Literal["monthly", "yearly"] = "monthly"
    statistic: Literal["mean", "median"] = "mean"
    minimum_station_period_days: int | None = Field(
        default=None,
        ge=1,
        le=366,
    )
    station_weighting: Literal["equal_station"] = "equal_station"


class RelationshipArgs(YearRangeArgs):
    x_metric: Metric
    y_metric: Metric
    city: str | None = Field(default=None, max_length=120)
    months: list[int] = Field(default_factory=list, max_length=12)
    group_by: Literal["none", "season"] = "none"

    @model_validator(mode="after")
    def validate_relationship(self):
        if self.x_metric == self.y_metric:
            raise ValueError("x_metric and y_metric must differ")
        if any(month < 1 or month > 12 for month in self.months):
            raise ValueError("months must be integers from 1 through 12")
        self.months = sorted(set(self.months))
        return self


class StrongestWeatherRelationshipArgs(YearRangeArgs):
    pollutant: Pollutant
    city: str | None = Field(default=None, max_length=120)
    months: list[int] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_months(self):
        if any(month < 1 or month > 12 for month in self.months):
            raise ValueError("months must be integers from 1 through 12")
        self.months = sorted(set(self.months))
        return self


class SeasonalProfileArgs(YearRangeArgs):
    pollutant: Pollutant
    city: str | None = Field(default=None, max_length=120)
    minimum_station_season_days: int = Field(default=15, ge=1, le=276)
    station_weighting: Literal["equal_station"] = "equal_station"


class FundingLookupArgs(StrictArgs):
    cities: list[str] = Field(min_length=1, max_length=20)


class StationCoverageArgs(YearRangeArgs):
    metric: Metric = "pm25"
    cities: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=20, ge=1, le=100)
    order: Literal["highest", "lowest"] = "highest"
    minimum_station_days: int = Field(default=30, ge=1, le=2_922)


class RankStationsArgs(YearRangeArgs):
    metric: Metric = "pm25"
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    months: list[int] = Field(default_factory=list, max_length=12)
    statistic: Literal["mean", "median"] = "mean"
    limit: int = Field(default=10, ge=1, le=100)
    order: Literal["highest", "lowest"] = "highest"
    minimum_observation_days: int = Field(default=30, ge=1, le=2_922)

    @model_validator(mode="after")
    def validate_months(self):
        if any(month < 1 or month > 12 for month in self.months):
            raise ValueError("months must be integers from 1 through 12")
        self.months = sorted(set(self.months))
        return self


class WeekdayWeekendArgs(YearRangeArgs):
    pollutant: Pollutant
    city: str | None = Field(default=None, max_length=120)
    minimum_station_group_days: int = Field(default=30, ge=1, le=2_088)
    station_weighting: Literal["equal_station"] = "equal_station"


class ConditionComparisonArgs(YearRangeArgs):
    pollutant: Pollutant
    condition_metric: Metric
    threshold: float = Field(ge=-100_000, le=100_000)
    city: str | None = Field(default=None, max_length=120)
    minimum_station_group_days: int = Field(default=15, ge=1, le=2_922)
    station_weighting: Literal["equal_station"] = "equal_station"


class RankStatesArgs(StationWeightedArgs):
    pollutant: Pollutant
    limit: int = Field(default=20, ge=1, le=50)
    order: Literal["highest", "lowest"] = "highest"


class CoverageTrendArgs(YearRangeArgs):
    metric: Metric = "pm25"
    cities: list[str] = Field(default_factory=list, max_length=20)
    minimum_station_year_days: int = Field(default=30, ge=1, le=366)


class FundingRankArgs(StrictArgs):
    limit: int = Field(default=20, ge=1, le=100)
    order: Literal["highest", "lowest"] = "highest"
    metric: Literal[
        "fy_2019_20",
        "fy_2020_21",
        "fy_2021_22",
        "total_fund_released",
        "utilisation_june_2022",
    ] = "total_fund_released"


class NCAPPollutionArgs(StationWeightedArgs):
    pollutant: Pollutant = "pm25"


class NCAPFundingPollutionArgs(NCAPPollutionArgs):
    funding_metric: Literal[
        "fy_2019_20",
        "fy_2020_21",
        "fy_2021_22",
        "total_fund_released",
    ] = "total_fund_released"


class NCAPThresholdArgs(NCAPPollutionArgs):
    threshold: float = Field(ge=0, le=10_000)
    comparison: Literal["above", "below"] = "above"
    limit: int = Field(default=100, ge=1, le=100)


class PollutionChangeArgs(StrictArgs):
    pollutant: Pollutant = "pm25"
    start_year: int = Field(
        ge=AIR_QUALITY_START_YEAR,
        le=AIR_QUALITY_END_YEAR,
    )
    end_year: int = Field(
        ge=AIR_QUALITY_START_YEAR,
        le=AIR_QUALITY_END_YEAR,
    )
    months: list[int] = Field(default_factory=list, max_length=12)
    cities: list[str] = Field(default_factory=list, max_length=20)
    scope: Literal["all_cities", "ncap_funded"] = "all_cities"
    change_filter: Literal[
        "all",
        "reductions_only",
        "increases_only",
    ]
    order: Literal["largest_reduction", "largest_increase"] = "largest_reduction"
    limit: int = Field(default=10, ge=1, le=100)
    minimum_station_days_per_year: int | None = Field(
        default=None,
        ge=1,
        le=366,
    )
    minimum_station_months_per_year: int | None = Field(
        default=None,
        ge=1,
        le=12,
    )
    station_matching: Literal["same_stations"] = "same_stations"

    @model_validator(mode="after")
    def validate_change_window(self):
        if self.start_year >= self.end_year:
            raise ValueError("start_year must be earlier than end_year")
        if any(month < 1 or month > 12 for month in self.months):
            raise ValueError("months must be integers from 1 through 12")
        self.months = sorted(set(self.months))
        available_months = len(self.months) if self.months else 12
        if self.minimum_station_days_per_year is None:
            self.minimum_station_days_per_year = (
                180 if not self.months else 15 * available_months
            )
        if self.minimum_station_months_per_year is None:
            self.minimum_station_months_per_year = (
                9
                if not self.months
                else max(1, (3 * available_months + 3) // 4)
            )
        if self.minimum_station_months_per_year > available_months:
            raise ValueError(
                "minimum_station_months_per_year exceeds the selected months"
            )
        normalized = {city.casefold() for city in self.cities if city}
        if len(normalized) != len(self.cities):
            raise ValueError("cities must be distinct and non-empty")
        return self


class ContextRelationshipArgs(StrictArgs):
    pollutant: Pollutant = "pm25"
    start_year: int = Field(
        ge=AIR_QUALITY_START_YEAR,
        le=AIR_QUALITY_END_YEAR,
    )
    end_year: int = Field(
        ge=AIR_QUALITY_START_YEAR,
        le=AIR_QUALITY_END_YEAR,
    )
    months: list[int] = Field(default_factory=list, max_length=12)
    context_metric: Literal[
        "fy_2019_20",
        "fy_2020_21",
        "fy_2021_22",
        "total_fund_released",
        "utilisation_june_2022",
        "population",
        "area_km2",
        "population_density",
    ] = "total_fund_released"
    pollution_measure: Literal["level", "change"] = "level"
    statistic: Literal["mean", "median"] = "mean"
    minimum_station_days: int = Field(default=30, ge=1, le=2_922)
    minimum_station_days_per_year: int | None = Field(
        default=None,
        ge=1,
        le=366,
    )
    minimum_station_months_per_year: int | None = Field(
        default=None,
        ge=1,
        le=12,
    )
    station_matching: Literal["same_stations"] = "same_stations"

    @model_validator(mode="after")
    def validate_relationship_window(self):
        if self.start_year > self.end_year:
            raise ValueError("start_year must not be later than end_year")
        if self.pollution_measure == "change" and self.start_year == self.end_year:
            raise ValueError(
                "change relationships require start_year earlier than end_year"
            )
        if any(month < 1 or month > 12 for month in self.months):
            raise ValueError("months must be integers from 1 through 12")
        self.months = sorted(set(self.months))
        available_months = len(self.months) if self.months else 12
        if self.minimum_station_days_per_year is None:
            self.minimum_station_days_per_year = (
                180 if not self.months else 15 * available_months
            )
        if self.minimum_station_months_per_year is None:
            self.minimum_station_months_per_year = (
                9
                if not self.months
                else max(1, (3 * available_months + 3) // 4)
            )
        if self.minimum_station_months_per_year > available_months:
            raise ValueError(
                "minimum_station_months_per_year exceeds the selected months"
            )
        return self


class ThresholdFrequencyArgs(YearRangeArgs):
    pollutant: Pollutant = "pm25"
    threshold: float = Field(ge=0, le=10_000)
    comparison: Literal["above", "below"] = "above"
    cities: list[str] = Field(default_factory=list, max_length=20)
    months: list[int] = Field(default_factory=list, max_length=12)
    order: Literal["most", "fewest"] = "most"
    rank_by: Literal["share", "count"] = "share"
    limit: int = Field(default=20, ge=1, le=100)
    minimum_city_days: int = Field(default=30, ge=1, le=2_922)

    @model_validator(mode="after")
    def validate_filters(self):
        if any(month < 1 or month > 12 for month in self.months):
            raise ValueError("months must be integers from 1 through 12")
        self.months = sorted(set(self.months))
        normalized = {city.casefold() for city in self.cities if city}
        if len(normalized) != len(self.cities):
            raise ValueError("cities must be distinct and non-empty")
        return self


class StationMapArgs(YearRangeArgs):
    metric: Metric | None = None
    states: list[str] = Field(default_factory=list, max_length=12)
    cities: list[str] = Field(default_factory=list, max_length=30)
    minimum_station_days: int = Field(default=30, ge=1, le=2_922)


class StateChoroplethArgs(YearRangeArgs):
    value: Literal[
        "station_count",
        "pm25",
        "pm10",
        "no",
        "no2",
        "nox",
        "nh3",
        "so2",
        "co",
        "ozone",
        "temperature",
        "humidity",
        "wind_speed",
        "rainfall",
    ] = "station_count"
    minimum_station_days: int = Field(default=30, ge=1, le=2_922)


class DataCatalogArgs(StrictArgs):
    topic: Literal[
        "all",
        "air_quality",
        "funding",
        "population",
        "standards",
        "stations",
        "weather",
        "forecast",
    ] = "all"


class CensusStateRankArgs(StrictArgs):
    metric: Literal[
        "population_2011",
        "population_density_2011_per_km2",
        "decadal_growth_2001_2011_pct",
    ] = "population_2011"
    order: Literal["highest", "lowest"] = "highest"
    limit: int = Field(default=15, ge=1, le=36)


class StandardsLookupArgs(StrictArgs):
    pollutant: Literal[
        "all",
        "pm25",
        "pm10",
        "no2",
        "so2",
        "co",
        "ozone",
        "nh3",
    ] = "all"
    averaging_period: Literal[
        "all",
        "annual",
        "24-hour",
        "8-hour",
        "1-hour",
    ] = "all"


class ProgramFinanceArgs(StrictArgs):
    program: Literal["NCAP", "XVFC", "NCAP + XVFC"] = "NCAP + XVFC"
    metric: Literal[
        "released_crore",
        "utilised_crore",
        "both",
    ] = "both"


class PhysicalProgressArgs(StrictArgs):
    programme_scope: Literal[
        "ncap",
        "xvfc",
        "ncap_plus_xvfc",
        "other_scheme_convergence",
    ] = "ncap_plus_xvfc"


class OutOfScopeArgs(StrictArgs):
    reason: str = Field(default="", max_length=600)


class ClarifyQuestionArgs(StrictArgs):
    question: str = Field(min_length=2, max_length=300)
    reason: str = Field(default="", max_length=600)
    options: list[str] = Field(default_factory=list, max_length=6)


# Gemini rejects a request whose tool schemas, taken together, carry too many
# constraints ("Request contains an invalid argument"); the 33 original tools
# sat just under that ceiling. Bounds are therefore declared to the model as
# text, and Pydantic still enforces every one when the plan is validated.
SCHEMA_BOUND_WORDS = {
    "minimum": "at least {}",
    "exclusiveMinimum": "greater than {}",
    "maximum": "at most {}",
    "exclusiveMaximum": "less than {}",
    "minLength": "at least {} characters",
    "maxLength": "at most {} characters",
    "minItems": "at least {} items",
    "maxItems": "at most {} items",
}


def _bounds_as_text(node: Any) -> Any:
    if isinstance(node, list):
        return [_bounds_as_text(item) for item in node]
    if not isinstance(node, dict):
        return node
    compact = {
        key: _bounds_as_text(value)
        for key, value in node.items()
        if key not in SCHEMA_BOUND_WORDS and key != "title"
    }
    bounds = [
        words.format(node[key])
        for key, words in SCHEMA_BOUND_WORDS.items()
        if key in node
    ]
    if bounds:
        note = "Must be " + ", ".join(bounds) + "."
        compact["description"] = f"{compact['description']} {note}" if compact.get("description") else note
    return compact


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    arguments_model: type[StrictArgs]

    def declaration(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": _bounds_as_text(self.arguments_model.model_json_schema()),
        }


@dataclass(frozen=True)
class ToolAnalysis:
    name: str
    plan: QueryPlan
    arguments: dict[str, Any]


TOOL_DEFINITIONS = (
    ToolDefinition(
        "rank_cities",
        (
            "Rank Indian cities by a pollutant over a year range, including a "
            "single highest/lowest city. Use for top, bottom, highest, lowest, "
            "or ranked city questions."
        ),
        RankCitiesArgs,
    ),
    ToolDefinition(
        "city_average",
        (
            "Calculate one city's mean or median pollutant level over an explicit "
            "year/month window. First summarizes daily values within each "
            "qualifying station, then gives every station equal weight."
        ),
        CityAverageArgs,
    ),
    ToolDefinition(
        "threshold_cities",
        (
            "Find cities whose rigorously averaged pollutant concentration is "
            "above or below a numeric threshold."
        ),
        ThresholdCitiesArgs,
    ),
    ToolDefinition(
        "compare_cities",
        (
            "Compare one pollutant across two or more explicitly named cities "
            "over a year range."
        ),
        CompareCitiesArgs,
    ),
    ToolDefinition(
        "time_trend",
        (
            "Return a monthly or yearly pollutant trend for one named city. "
            "Use monthly unless the user explicitly asks for annual/yearly."
        ),
        TimeTrendArgs,
    ),
    ToolDefinition(
        "relationship",
        (
            "Measure the Pearson association between two pollutant or weather "
            "metrics using aligned city-day values. For Indian monsoon months, "
            "use months [6, 7, 8, 9]."
        ),
        RelationshipArgs,
    ),
    ToolDefinition(
        "strongest_weather_relationship",
        (
            "Rank all supported meteorological factors by the absolute Pearson "
            "association with one pollutant. Use whenever the user asks which "
            "weather or meteorological factor is most/strongest correlated."
        ),
        StrongestWeatherRelationshipArgs,
    ),
    ToolDefinition(
        "seasonal_profile",
        (
            "Compare winter, pre-monsoon, monsoon, and post-monsoon pollutant "
            "levels for one city or across all cities."
        ),
        SeasonalProfileArgs,
    ),
    ToolDefinition(
        "funding_lookup",
        "Compare recorded NCAP funding and utilisation for explicitly named cities.",
        FundingLookupArgs,
    ),
    ToolDefinition(
        "station_coverage",
        (
            "Count and compare qualifying monitoring stations for a pollutant "
            "or weather metric. Use for station counts, most/least stations, "
            "and monitoring or observation coverage by city."
        ),
        StationCoverageArgs,
    ),
    ToolDefinition(
        "rank_stations",
        (
            "Rank individual monitoring stations by their mean or median "
            "pollutant or weather measurement over a year/month window. Use "
            "when the user asks which station recorded the highest or lowest "
            "value, or asks to rank stations within a city or state. This ranks "
            "measured values, not station counts or monitoring coverage."
        ),
        RankStationsArgs,
    ),
    ToolDefinition(
        "weekday_weekend_profile",
        (
            "Compare a pollutant between weekdays and weekends for one city or "
            "all cities using equal-station and, when national, equal-city weights."
        ),
        WeekdayWeekendArgs,
    ),
    ToolDefinition(
        "condition_comparison",
        (
            "Compare pollutant levels when another measured metric is above "
            "versus at-or-below a numeric threshold, such as PM2.5 when wind "
            "speed is above 3 m/s."
        ),
        ConditionComparisonArgs,
    ),
    ToolDefinition(
        "rank_states",
        (
            "Rank Indian states by a pollutant. Station means are weighted "
            "equally within cities, then qualifying cities equally within states."
        ),
        RankStatesArgs,
    ),
    ToolDefinition(
        "coverage_trend",
        (
            "Compare monitoring coverage across calendar years for a metric, "
            "including qualifying stations, cities, and station-days."
        ),
        CoverageTrendArgs,
    ),
    ToolDefinition(
        "funding_rank",
        (
            "Rank NCAP cities by a recorded annual funding field or total funds "
            "released. When metric='utilisation_june_2022', rank states because "
            "the source records one utilisation value per state."
        ),
        FundingRankArgs,
    ),
    ToolDefinition(
        "ncap_threshold_cities",
        (
            "Find NCAP-funded cities whose station-weighted pollutant average "
            "is above or below a threshold."
        ),
        NCAPThresholdArgs,
    ),
    ToolDefinition(
        "ncap_funding_groups",
        (
            "Compare pollution between NCAP cities below versus at-or-above the "
            "median of a selected recorded funding-release field, weighting "
            "cities equally."
        ),
        NCAPFundingPollutionArgs,
    ),
    ToolDefinition(
        "pollution_change",
        (
            "Rank or compare cities by pollutant reduction or increase between "
            "two explicit years. Uses only stations with sufficient data in both "
            "years. Set scope='ncap_funded' when the question mentions NCAP, "
            "funding, funded cities, or asks for funding context. Set "
            "change_filter='all' for named comparisons; use reductions_only or "
            "increases_only only when that direction is explicitly requested."
        ),
        PollutionChangeArgs,
    ),
    ToolDefinition(
        "context_relationship",
        (
            "Relate a reusable context metric—NCAP funding, state utilisation, "
            "population, area, or population density—to either a pollutant level "
            "or a pollutant change between two years. Granularity is handled by "
            "the server and every result is descriptive, never causal."
        ),
        ContextRelationshipArgs,
    ),
    ToolDefinition(
        "threshold_frequency",
        (
            "Count and rank observed city-days above or below a pollutant "
            "threshold. Use for how many days, share of days, exceedance days, "
            "polluted days, clean days, or most/fewest threshold-day questions."
        ),
        ThresholdFrequencyArgs,
    ),
    ToolDefinition(
        "station_map",
        (
            "Map monitoring-station coordinates across India. Optionally colour "
            "stations by a measured pollutant or weather metric over a year range. "
            "Use for station maps, monitoring-location maps, or geographic point maps."
        ),
        StationMapArgs,
    ),
    ToolDefinition(
        "state_choropleth",
        (
            "Create an India state choropleth for active station count or an "
            "equal-city pollutant/weather estimate over a year range."
        ),
        StateChoroplethArgs,
    ),
    ToolDefinition(
        "data_catalog",
        (
            "List the available VayuChat data sources, their exact date ranges, "
            "granularity, record counts, quality notes, and provenance."
        ),
        DataCatalogArgs,
    ),
    ToolDefinition(
        "census_state_rank",
        (
            "Rank Census 2011 states and union territories by population, "
            "population density, or 2001–2011 decadal population growth."
        ),
        CensusStateRankArgs,
    ),
    ToolDefinition(
        "air_quality_standard",
        (
            "Look up CPCB National Ambient Air Quality Standards by pollutant "
            "and averaging period. Keep annual, 24-hour, 8-hour, and 1-hour "
            "standards distinct."
        ),
        StandardsLookupArgs,
    ),
    ToolDefinition(
        "programme_finance_trend",
        (
            "Show programme-wide NCAP or XV-FC funds released and utilised by "
            "fiscal year through the 18 August 2025 PRANA reporting snapshot. "
            "This is national programme data, not city funding."
        ),
        ProgramFinanceArgs,
    ),
    ToolDefinition(
        "physical_progress",
        (
            "Show cumulative NCAP/XV-FC physical outputs reported by PRANA as "
            "of 14 August 2025, such as road paving, green area, electric buses, "
            "charging stations, and waste remediation."
        ),
        PhysicalProgressArgs,
    ),
    ToolDefinition(
        "weather_grid_map",
        (
            "Map a WRF model weather variable across India, one state, or "
            "the surroundings of a city as a heatmap, with the most extreme "
            "cells listed. statistic='mean' averages the days in the window, "
            "'total' sums rainfall, 'max'/'min' take the most extreme day, and "
            "'sequence' animates day by day (or in blocks for long windows). "
            "order='lowest' lists the coldest/driest cells. Use for weather "
            "maps, spatial patterns, and where it was hottest, wettest, or "
            "windiest. "
            + WEATHER_WINDOW_DESCRIPTION
        ),
        WeatherGridMapArgs,
    ),
    ToolDefinition(
        "weather_point_series",
        (
            "Daily, weekly, or monthly WRF model weather at one place: a city "
            "(grid cell at the centre of its monitors), a whole state (area "
            "mean), or a latitude/longitude. Rainfall periods are totals; other "
            "variables are means. Wind adds direction. Use for 'how hot was "
            "Delhi in May 2024', monsoon rainfall at a city, and weather time "
            "series. "
            + WEATHER_WINDOW_DESCRIPTION
        ),
        WeatherPointSeriesArgs,
    ),
    ToolDefinition(
        "weather_threshold_map",
        (
            "Count, for every grid cell, the days a WRF weather variable is "
            "above or below a numeric threshold, and the share of area meeting "
            "it by month. Requires an explicit threshold in the variable's "
            "unit, for example max_temperature above 40 °C for heat days or "
            "rainfall above 64.5 mm for heavy-rain days. "
            + WEATHER_WINDOW_DESCRIPTION
        ),
        WeatherThresholdArgs,
    ),
    ToolDefinition(
        "weather_region_rank",
        (
            "Rank Indian states (area mean over the grid) or station-registry "
            "cities (nearest grid cell) by a WRF weather variable over a "
            "window. Use for 'which state was hottest in May 2024', 'wettest "
            "cities in the 2023 monsoon', and state or city weather rankings. "
            + WEATHER_WINDOW_DESCRIPTION
        ),
        WeatherRegionRankArgs,
    ),
    ToolDefinition(
        "cams_forecast_map",
        (
            "Map the CAMS air-quality forecast over India, a state, or a city or "
            "district with its surroundings, with the most extreme cells listed. "
            "statistic='mean' averages the forecast hours, 'max'/'min' take the "
            "most extreme hour, 'sequence' animates hour by hour (or in blocks). "
            "Use for forecast maps and where pollution will be highest. "
            + CAMS_TOOL_GUIDE
        ),
        CamsMapArgs,
    ),
    ToolDefinition(
        "cams_forecast_series",
        (
            "Hourly or IST-daily CAMS air-quality forecast at a city or district, "
            "a state average, or a coordinate. Use for 'what will PM2.5 be in "
            "Lucknow on Friday', forecast peaks, and forecast time series. "
            + CAMS_TOOL_GUIDE
        ),
        CamsSeriesArgs,
    ),
    ToolDefinition(
        "cams_forecast_threshold_map",
        (
            "Count, for every grid cell, the forecast hours a CAMS pollutant is "
            "above a numeric threshold, and the share of area above it by IST "
            "day. Requires an explicit threshold in the pollutant's unit. "
            + CAMS_TOOL_GUIDE
        ),
        CamsThresholdArgs,
    ),
    ToolDefinition(
        "cams_forecast_rank",
        (
            "Rank Indian states (area mean) or station-registry cities (nearest "
            "cell) by CAMS forecast mean or peak hourly value over a window. Use "
            "for 'which states will have the worst PM2.5 tomorrow'. "
            + CAMS_TOOL_GUIDE
        ),
        CamsRankArgs,
    ),
    ToolDefinition(
        "clarify_question",
        (
            "Ask one concise clarification only when an essential analytical "
            "dimension is missing or ambiguous and no documented default can "
            "resolve it safely. Examples include an unspecified pollutant in a "
            "city comparison, an unspecified threshold in a polluted-day "
            "question, or an unclear geography for a map. When the answer comes "
            "from a small supported set, include 2–6 concise user-selectable "
            "answers in options. For pollutants, use only supported pollutant "
            "labels: PM2.5, PM10, NO, NO2, NOx, NH3, SO2, CO, or ozone. Leave "
            "options empty when the user must enter a number, place, or other "
            "free-form value."
        ),
        ClarifyQuestionArgs,
    ),
    ToolDefinition(
        "out_of_scope",
        (
            "Use only for questions unrelated to Indian air pollution, "
            "meteorology, monitoring, standards, Census context, or NCAP funding."
        ),
        OutOfScopeArgs,
    ),
)

TOOL_BY_NAME = {definition.name: definition for definition in TOOL_DEFINITIONS}


def tool_declarations() -> list[dict[str, Any]]:
    return [definition.declaration() for definition in TOOL_DEFINITIONS]


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _valid_metric(metric: str, prefix: str = "") -> str:
    column = f"{prefix}{metric}"
    conditions = [f"{column} IS NOT NULL", f"isfinite({column})"]
    if metric == "humidity":
        conditions.append(f"{column} BETWEEN 0 AND 100")
    elif metric == "wind_direction":
        conditions.append(f"{column} BETWEEN 0 AND 360")
    elif metric != "temperature":
        conditions.append(f"{column} >= 0")
    return " AND ".join(conditions)


def _period_label(start_year: int, end_year: int) -> str:
    return str(start_year) if start_year == end_year else f"{start_year}–{end_year}"


def _aggregate_sql(statistic: Literal["mean", "median"], value: str) -> str:
    return f"AVG({value})" if statistic == "mean" else f"MEDIAN({value})"


def _window_label(
    start_year: int,
    end_year: int,
    months: list[int],
) -> str:
    period = _period_label(start_year, end_year)
    if not months:
        return period
    return f"{period}, months {', '.join(str(month) for month in months)}"


def _pollutant_station_cte(
    pollutant: str,
    start_year: int,
    end_year: int,
    *,
    months: list[int] | None = None,
    statistic: Literal["mean", "median"] = "mean",
    minimum_station_days: int = 30,
    geography: Literal["city", "state"] = "city",
    extra_where: str = "",
) -> str:
    where = [
        f"year BETWEEN {start_year} AND {end_year}",
        _valid_metric(pollutant),
    ]
    if months:
        where.append(
            "date_part('month', timestamp) IN "
            f"({', '.join(str(month) for month in months)})"
        )
    if extra_where:
        where.append(extra_where)
    station_statistic = _aggregate_sql(statistic, pollutant)
    geography_columns = "state, city" if geography == "state" else "city"
    city_statistic = _aggregate_sql(statistic, "station_average")
    return f"""
        station_estimates AS (
            SELECT
                {geography_columns},
                station,
                {station_statistic} AS station_average,
                COUNT(DISTINCT timestamp) AS observation_days
            FROM air_quality
            WHERE {" AND ".join(where)}
            GROUP BY {geography_columns}, station
            HAVING COUNT(DISTINCT timestamp) >= {minimum_station_days}
        ),
        city_estimates AS (
            SELECT
                {geography_columns},
                {city_statistic} AS average_value,
                COUNT(*) AS station_count,
                MIN(observation_days) AS min_observation_days,
                SUM(observation_days) AS total_station_days
            FROM station_estimates
            GROUP BY {geography_columns}
        )
    """.strip()


def _plan(
    *,
    sql: str,
    summary: str,
    method: str,
    visualization: Literal[
        "none",
        "bar",
        "line",
        "scatter",
        "map_points",
        "choropleth",
    ],
    title: str,
    x_key: str,
    y_keys: list[str],
) -> QueryPlan:
    return QueryPlan(
        in_scope=True,
        sql=sql,
        summary_template=summary,
        method_note=method,
        visualization=visualization,
        title=title,
        x_key=x_key,
        y_keys=y_keys,
    )


def _rank_cities(args: RankCitiesArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _window_label(args.start_year, args.end_year, args.months)
    value_key = f"{args.statistic}_{args.pollutant}"
    direction = "DESC" if args.order == "highest" else "ASC"
    superlative = "highest" if args.order == "highest" else "lowest"
    sql = f"""
        WITH {_pollutant_station_cte(
            args.pollutant,
            args.start_year,
            args.end_year,
            months=args.months,
            statistic=args.statistic,
            minimum_station_days=args.minimum_station_days,
        )}
        SELECT
            city,
            ROUND(average_value, 2) AS {value_key},
            station_count,
            min_observation_days,
            total_station_days
        FROM city_estimates
        ORDER BY average_value {direction}, city
        LIMIT {args.limit}
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{city}}}} had the {superlative} qualifying {args.statistic} "
            f"{label} in {period}: {{{{{value_key}}}}} {unit}, based on "
            "{{station_count}} stations. The table contains "
            "{{result_count}} ranked cities."
        ),
        method=(
            f"Used valid, non-negative daily {label} observations from {period}. "
            f"Calculated each station's full-window {args.statistic} after requiring "
            f"at least {args.minimum_station_days} distinct observation days, then "
            f"took the {args.statistic} across qualifying stations so each station "
            "had equal weight. No missing values were imputed."
        ),
        visualization="bar",
        title=f"City {args.statistic} {label}, {period}",
        x_key="city",
        y_keys=[value_key],
    )


def _city_average(args: CityAverageArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _window_label(args.start_year, args.end_year, args.months)
    value_key = f"{args.statistic}_{args.pollutant}"
    city_filter = (
        "lower(trim(city)) = "
        f"lower(trim({_quoted(args.city)}))"
    )
    sql = f"""
        WITH {_pollutant_station_cte(
            args.pollutant,
            args.start_year,
            args.end_year,
            months=args.months,
            statistic=args.statistic,
            minimum_station_days=args.minimum_station_days,
            extra_where=city_filter,
        )}
        SELECT
            city,
            ROUND(average_value, 2) AS {value_key},
            station_count,
            min_observation_days,
            total_station_days
        FROM city_estimates
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{city}}}} had a {args.statistic} {label} of "
            f"{{{{{value_key}}}}} {unit} during {period}, based on "
            "{{station_count}} "
            "qualifying stations."
        ),
        method=(
            f"Used valid, non-negative daily {label} observations for "
            f"{args.city} during {period}. Calculated each station's full-window "
            f"{args.statistic} after requiring at least "
            f"{args.minimum_station_days} distinct days, then took the "
            f"{args.statistic} across qualifying stations (equal-station "
            "weighting). Missing values were not imputed."
        ),
        visualization="none",
        title=f"{args.statistic.title()} {label} in {args.city}, {period}",
        x_key="city",
        y_keys=[value_key],
    )


def _threshold_cities(args: ThresholdCitiesArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _window_label(args.start_year, args.end_year, args.months)
    value_key = f"{args.statistic}_{args.pollutant}"
    operator = ">" if args.comparison == "above" else "<"
    direction = "DESC" if args.comparison == "above" else "ASC"
    sql = f"""
        WITH {_pollutant_station_cte(
            args.pollutant,
            args.start_year,
            args.end_year,
            months=args.months,
            statistic=args.statistic,
            minimum_station_days=args.minimum_station_days,
        )}
        SELECT
            city,
            ROUND(average_value, 2) AS {value_key},
            station_count,
            min_observation_days,
            total_station_days,
            COUNT(*) OVER () AS matching_city_count
        FROM city_estimates
        WHERE average_value {operator} {args.threshold}
        ORDER BY average_value {direction}, city
        LIMIT {args.limit}
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{matching_city_count}}}} cities had {args.statistic} {label} "
            f"{args.comparison} "
            f"{args.threshold:g} {unit} in {period}. The first listed city is "
            f"{{{{city}}}} at {{{{{value_key}}}}} {unit}."
        ),
        method=(
            f"Applied the {args.threshold:g} {unit} threshold to city estimates "
            f"for {period}. Each station required at least "
            f"{args.minimum_station_days} days; its full-window "
            f"{args.statistic} was calculated first, then qualifying stations "
            "were equally weighted within each city. "
            "Invalid and missing concentrations were excluded without imputation."
        ),
        visualization="bar",
        title=f"Cities {args.comparison} {args.threshold:g} {unit} {label}",
        x_key="city",
        y_keys=[value_key],
    )


def _compare_cities(args: CompareCitiesArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _window_label(args.start_year, args.end_year, args.months)
    value_key = f"{args.statistic}_{args.pollutant}"
    cities = ", ".join(
        f"lower(trim({_quoted(city)}))"
        for city in args.cities
    )
    sql = f"""
        WITH {_pollutant_station_cte(
            args.pollutant,
            args.start_year,
            args.end_year,
            months=args.months,
            statistic=args.statistic,
            minimum_station_days=args.minimum_station_days,
            extra_where=f"lower(trim(city)) IN ({cities})",
        )}
        SELECT
            city,
            ROUND(average_value, 2) AS {value_key},
            station_count,
            min_observation_days,
            total_station_days
        FROM city_estimates
        ORDER BY average_value DESC, city
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{city}}}} had the highest qualifying {args.statistic} {label} "
            f"among the requested cities in {period}: "
            f"{{{{{value_key}}}}} {unit}. "
            "{{result_count}} cities had sufficient coverage."
        ),
        method=(
            f"Compared the requested cities over {period}. Each station required "
            f"at least {args.minimum_station_days} valid days; station full-window "
            f"{args.statistic}s were then given equal weight. "
            "coverage is shown and missing values were not imputed."
        ),
        visualization="bar",
        title=f"{label} comparison, {period}",
        x_key="city",
        y_keys=[value_key],
    )


def _time_trend(args: TimeTrendArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _period_label(args.start_year, args.end_year)
    city = _quoted(args.city)
    value_key = f"{args.statistic}_{args.pollutant}"
    if args.interval == "monthly":
        bucket = "date_trunc('month', timestamp)"
        output = "strftime(period, '%Y-%m')"
        default_minimum_days = 7
    else:
        bucket = "date_trunc('year', timestamp)"
        output = "strftime(period, '%Y')"
        default_minimum_days = 30
    minimum_days = args.minimum_station_period_days or default_minimum_days
    station_statistic = _aggregate_sql(args.statistic, args.pollutant)
    period_statistic = _aggregate_sql(args.statistic, "station_value")
    sql = f"""
        WITH station_period AS (
            SELECT
                city,
                station,
                {bucket} AS period,
                {station_statistic} AS station_value,
                COUNT(DISTINCT timestamp) AS observation_days
            FROM air_quality
            WHERE
                lower(trim(city)) = lower(trim({city}))
                AND year BETWEEN {args.start_year} AND {args.end_year}
                AND {_valid_metric(args.pollutant)}
            GROUP BY city, station, period
            HAVING COUNT(DISTINCT timestamp) >= {minimum_days}
        )
        SELECT
            {output} AS period,
            ROUND({period_statistic}, 2) AS {value_key},
            COUNT(*) AS station_count,
            MIN(observation_days) AS min_observation_days
        FROM station_period
        GROUP BY period
        ORDER BY period
        LIMIT 100
    """
    return _plan(
        sql=sql,
        summary=(
            f"The {args.interval} {args.statistic} {label} series for {args.city} "
            "contains "
            f"{{{{result_count}}}} comparable periods from {period}; the first "
            f"value is {{{{{value_key}}}}} {unit} in {{{{period}}}}."
        ),
        method=(
            f"Computed {args.interval} station {args.statistic}s for {args.city} during "
            f"{period}, requiring at least {minimum_days} valid days per station-"
            f"period, then took the {args.statistic} across qualifying stations "
            "(equal-station weighting). Missing values "
            "were excluded without imputation."
        ),
        visualization="line",
        title=f"{args.city} {args.interval} {label}",
        x_key="period",
        y_keys=[value_key],
    )


def _relationship(args: RelationshipArgs) -> QueryPlan:
    x_label = METRIC_LABELS[args.x_metric]
    y_label = METRIC_LABELS[args.y_metric]
    period = _period_label(args.start_year, args.end_year)
    filters = [
        f"year BETWEEN {args.start_year} AND {args.end_year}",
        _valid_metric(args.x_metric),
        _valid_metric(args.y_metric),
    ]
    if args.city:
        filters.append(
            f"lower(trim(city)) = lower(trim({_quoted(args.city)}))"
        )
    if args.months:
        filters.append(
            "date_part('month', timestamp) IN "
            f"({', '.join(str(month) for month in args.months)})"
        )
    scope = args.city or "all available cities"
    month_note = (
        f" and calendar months {', '.join(str(month) for month in args.months)}"
        if args.months
        else ""
    )
    if args.group_by == "season":
        sql = f"""
            WITH station_daily AS (
                SELECT
                    city,
                    station,
                    timestamp,
                    CASE
                        WHEN date_part('month', timestamp) IN (12, 1, 2)
                            THEN 'Winter'
                        WHEN date_part('month', timestamp) IN (3, 4, 5)
                            THEN 'Pre-monsoon'
                        WHEN date_part('month', timestamp) IN (6, 7, 8, 9)
                            THEN 'Monsoon'
                        ELSE 'Post-monsoon'
                    END AS season,
                    AVG({args.x_metric}) AS station_x,
                    AVG({args.y_metric}) AS station_y
                FROM air_quality
                WHERE {" AND ".join(filters)}
                GROUP BY city, station, timestamp, season
            ),
            city_daily AS (
                SELECT
                    city,
                    timestamp,
                    season,
                    AVG(station_x) AS x_value,
                    AVG(station_y) AS y_value
                FROM station_daily
                GROUP BY city, timestamp, season
            )
            SELECT
                season,
                ROUND(CORR(x_value, y_value), 3) AS pearson_r,
                COUNT(*) AS paired_days,
                COUNT(DISTINCT city) AS city_count
            FROM city_daily
            GROUP BY season
            HAVING COUNT(*) >= 10
            ORDER BY CASE season
                WHEN 'Winter' THEN 1
                WHEN 'Pre-monsoon' THEN 2
                WHEN 'Monsoon' THEN 3
                ELSE 4
            END
        """
        return _plan(
            sql=sql,
            summary=(
                f"The seasonal {x_label}–{y_label} analysis for {scope} "
                "returned {{result_count}} season-specific Pearson "
                "associations. Each coefficient uses its own paired-day count."
            ),
            method=(
                f"Grouped valid paired {x_label} and {y_label} observations "
                f"for {scope} during {period}{month_note} into fixed Indian "
                "season definitions. Values were first averaged within "
                "station-day, then stations were weighted equally within each "
                "city-day. Pearson's r was calculated separately inside each "
                "season; no values were imputed and association does not "
                "establish causation."
            ),
            visualization="bar",
            title=f"Seasonal {x_label} and {y_label} association, {scope}",
            x_key="season",
            y_keys=["pearson_r"],
        )
    sql = f"""
        WITH station_daily AS (
            SELECT
                city,
                station,
                timestamp,
                AVG({args.x_metric}) AS station_x,
                AVG({args.y_metric}) AS station_y
            FROM air_quality
            WHERE {" AND ".join(filters)}
            GROUP BY city, station, timestamp
        ),
        city_daily AS (
            SELECT
                city,
                timestamp,
                AVG(station_x) AS x_value,
                AVG(station_y) AS y_value,
                COUNT(*) AS station_pair_count
            FROM station_daily
            GROUP BY city, timestamp
        ),
        paired AS (
            SELECT
                city,
                timestamp,
                x_value,
                y_value,
                station_pair_count,
                CORR(x_value, y_value) OVER () AS pearson_r,
                COUNT(*) OVER () AS paired_days
            FROM city_daily
        ),
        quantile_sample AS (
            SELECT
                *,
                NTILE(50) OVER (ORDER BY x_value, city, timestamp) AS x_bin
            FROM paired
        ),
        ranked_sample AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY x_bin
                    ORDER BY hash(city, timestamp)
                ) AS sample_rank
            FROM quantile_sample
        )
        SELECT
            city,
            timestamp AS date,
            ROUND(x_value, 3) AS {args.x_metric},
            ROUND(y_value, 3) AS {args.y_metric},
            station_pair_count,
            ROUND(pearson_r, 3) AS pearson_r,
            paired_days
        FROM ranked_sample
        WHERE sample_rank <= 2
        ORDER BY x_value, date, city
    """
    return _plan(
        sql=sql,
        summary=(
            f"Across {{{{paired_days}}}} aligned city-day observations, the "
            f"Pearson association between {x_label} and {y_label} was "
            "{{pearson_r}}. This is an association, not evidence of causation."
        ),
        method=(
            f"Used valid paired {x_label} and {y_label} observations for {scope} "
            f"during {period}{month_note}. Values were first averaged within "
            "station-day, then stations were weighted equally within each "
            "city-day. Pearson's r was calculated across aligned city-day pairs; "
            "no values were imputed. The coefficient uses every valid pair; "
            "the chart displays a deterministic sample of up to 100 points "
            "stratified across the x-axis distribution."
        ),
        visualization="scatter",
        title=f"{x_label} and {y_label}, {scope}",
        x_key=args.x_metric,
        y_keys=[args.y_metric],
    )


def _strongest_weather_relationship(
    args: StrongestWeatherRelationshipArgs,
) -> QueryPlan:
    pollutant_label = METRIC_LABELS[args.pollutant]
    period = _period_label(args.start_year, args.end_year)
    weather_metrics = (
        "temperature",
        "humidity",
        "wind_speed",
        "rainfall",
        "solar_radiation",
        "pressure",
    )
    filters = [f"year BETWEEN {args.start_year} AND {args.end_year}"]
    if args.city:
        filters.append(
            f"lower(trim(city)) = lower(trim({_quoted(args.city)}))"
        )
    if args.months:
        filters.append(
            "date_part('month', timestamp) IN "
            f"({', '.join(str(month) for month in args.months)})"
        )
    where_scope = " AND ".join(filters)
    branches = [
        f"""
        SELECT
            city,
            station,
            timestamp,
            '{metric}' AS metric,
            station_pollutant,
            {metric} AS station_metric
        FROM station_daily
        WHERE {metric} IS NOT NULL
        """
        for metric in weather_metrics
    ]
    union_sql = "\nUNION ALL\n".join(branches)
    scope = args.city or "all available cities"
    month_note = (
        f" during months {', '.join(str(month) for month in args.months)}"
        if args.months
        else ""
    )
    sql = f"""
        WITH station_daily AS (
            SELECT
                city,
                station,
                timestamp,
                AVG({args.pollutant}) AS station_pollutant,
                {", ".join(
                    f"AVG({metric}) FILTER (WHERE {_valid_metric(metric)}) AS {metric}"
                    for metric in weather_metrics
                )}
            FROM air_quality
            WHERE
                {where_scope}
                AND {_valid_metric(args.pollutant)}
            GROUP BY city, station, timestamp
        ),
        station_pairs AS (
            {union_sql}
        ),
        city_daily_pairs AS (
            SELECT
                city,
                timestamp,
                metric,
                AVG(station_pollutant) AS pollutant_value,
                AVG(station_metric) AS metric_value,
                COUNT(*) AS station_pair_count
            FROM station_pairs
            GROUP BY city, timestamp, metric
        )
        SELECT
            metric,
            ROUND(CORR(pollutant_value, metric_value), 3) AS pearson_r,
            COUNT(*) AS paired_days,
            COUNT(DISTINCT city) AS city_count,
            MIN(station_pair_count) AS min_station_pairs
        FROM city_daily_pairs
        GROUP BY metric
        HAVING COUNT(*) >= 30
        ORDER BY ABS(pearson_r) DESC, metric
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{metric}}}} had the strongest absolute Pearson association "
            f"with {pollutant_label}: r = {{{{pearson_r}}}} across "
            "{{paired_days}} aligned city-day pairs."
        ),
        method=(
            f"Compared temperature, humidity, wind speed, rainfall, solar "
            f"radiation, and pressure with {pollutant_label} for {scope} over "
            f"{period}{month_note}. Each factor used its own valid paired "
            "station-day observations; stations were then weighted equally "
            "within city-day. Factors were ranked by absolute Pearson's r. "
            "No values were imputed, and association does not establish causation."
        ),
        visualization="bar",
        title=f"Weather associations with {pollutant_label}, {scope}",
        x_key="metric",
        y_keys=["pearson_r"],
    )


def _seasonal_profile(args: SeasonalProfileArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _period_label(args.start_year, args.end_year)
    city_filter = (
        "AND lower(trim(city)) = "
        f"lower(trim({_quoted(args.city)}))"
        if args.city
        else ""
    )
    if args.city:
        city_layer = """
            SELECT
                season,
                AVG(station_average) AS season_average,
                COUNT(*) AS station_count,
                MIN(observation_days) AS min_observation_days,
                1 AS city_count
            FROM station_season
            GROUP BY season
        """
        scope = args.city
    else:
        city_layer = """
            SELECT
                season,
                AVG(city_average) AS season_average,
                SUM(station_count) AS station_count,
                MIN(min_observation_days) AS min_observation_days,
                COUNT(*) AS city_count
            FROM (
                SELECT
                    city,
                    season,
                    AVG(station_average) AS city_average,
                    COUNT(*) AS station_count,
                    MIN(observation_days) AS min_observation_days
                FROM station_season
                GROUP BY city, season
            ) AS city_season
            GROUP BY season
        """
        scope = "all qualifying cities"
    sql = f"""
        WITH station_season AS (
            SELECT
                city,
                station,
                CASE
                    WHEN date_part('month', timestamp) IN (12, 1, 2) THEN 'Winter'
                    WHEN date_part('month', timestamp) IN (3, 4, 5) THEN 'Pre-monsoon'
                    WHEN date_part('month', timestamp) IN (6, 7, 8, 9) THEN 'Monsoon'
                    ELSE 'Post-monsoon'
                END AS season,
                AVG({args.pollutant}) AS station_average,
                COUNT(DISTINCT timestamp) AS observation_days
            FROM air_quality
            WHERE
                year BETWEEN {args.start_year} AND {args.end_year}
                AND {_valid_metric(args.pollutant)}
                {city_filter}
            GROUP BY city, station, season
            HAVING COUNT(DISTINCT timestamp) >= {args.minimum_station_season_days}
        ),
        season_estimates AS (
            {city_layer}
        )
        SELECT
            season,
            ROUND(season_average, 2) AS average_{args.pollutant},
            city_count,
            station_count,
            min_observation_days
        FROM season_estimates
        ORDER BY CASE season
            WHEN 'Winter' THEN 1
            WHEN 'Pre-monsoon' THEN 2
            WHEN 'Monsoon' THEN 3
            ELSE 4
        END
    """
    return _plan(
        sql=sql,
        summary=(
            f"The seasonal {label} profile for {scope} contains "
            f"{{{{result_count}}}} comparable seasons in {period}. Winter's "
            f"estimate is {{{{average_{args.pollutant}}}}} {unit}."
        ),
        method=(
            f"Used fixed Indian seasonal month groups over {period}. Station-"
            "season means required at least "
            f"{args.minimum_station_season_days} valid days. Stations were weighted "
            + (
                "equally within the city."
                if args.city
                else "equally within cities, then cities were weighted equally."
            )
            + " Missing values were not imputed."
        ),
        visualization="bar",
        title=f"Seasonal {label}, {scope}",
        x_key="season",
        y_keys=[f"average_{args.pollutant}"],
    )


def _funding_lookup(args: FundingLookupArgs) -> QueryPlan:
    sql = f"""
        SELECT
            city,
            state,
            ROUND(fy_2019_20, 2) AS fy_2019_20,
            ROUND(fy_2020_21, 2) AS fy_2020_21,
            ROUND(fy_2021_22, 2) AS fy_2021_22,
            ROUND(total_fund_released, 2) AS total_fund_released,
            ROUND(utilisation_june_2022, 2) AS utilisation_june_2022
        FROM ncap_funding
        WHERE lower(trim(city)) IN (
            {", ".join(f"lower(trim({_quoted(city)}))" for city in args.cities)}
        )
        ORDER BY total_fund_released DESC NULLS LAST, city
    """
    return _plan(
        sql=sql,
        summary=(
            "{{city}} has the largest recorded total among the matched cities: "
            "{{total_fund_released}}. {{result_count}} funding records matched."
        ),
        method=(
            "Matched requested city names case-insensitively against the bundled "
            "NCAP funding table. Values are reported as recorded; no air-quality "
            "rows were joined, preventing duplicated funding amounts."
        ),
        visualization="bar",
        title="Recorded NCAP funding by city",
        x_key="city",
        y_keys=["total_fund_released", "utilisation_june_2022"],
    )


def _station_coverage(args: StationCoverageArgs) -> QueryPlan:
    label = METRIC_LABELS[args.metric]
    period = _period_label(args.start_year, args.end_year)
    filters = [
        f"year BETWEEN {args.start_year} AND {args.end_year}",
        _valid_metric(args.metric),
    ]
    if args.cities:
        city_values = ", ".join(
            f"lower(trim({_quoted(city)}))"
            for city in args.cities
        )
        filters.append(f"lower(trim(city)) IN ({city_values})")
    direction = "DESC" if args.order == "highest" else "ASC"
    sql = f"""
        WITH qualifying_stations AS (
            SELECT
                city,
                station,
                COUNT(DISTINCT timestamp) AS observation_days
            FROM air_quality
            WHERE {" AND ".join(filters)}
            GROUP BY city, station
            HAVING COUNT(DISTINCT timestamp) >= {args.minimum_station_days}
        )
        SELECT
            city,
            COUNT(*) AS station_count,
            MIN(observation_days) AS min_observation_days,
            SUM(observation_days) AS total_station_days
        FROM qualifying_stations
        GROUP BY city
        ORDER BY station_count {direction}, city
        LIMIT {args.limit}
    """
    scope = (
        ", ".join(args.cities)
        if args.cities
        else "all qualifying cities"
    )
    ranking_word = "most" if args.order == "highest" else "fewest"
    return _plan(
        sql=sql,
        summary=(
            f"{{{{city}}}} had the {ranking_word} qualifying {label} monitoring "
            "stations in the requested comparison: {{station_count}}. "
            "{{result_count}} cities are shown."
        ),
        method=(
            f"Counted distinct stations with at least "
            f"{args.minimum_station_days} valid {label} "
            f"observation days during {period} for {scope}. Coverage columns "
            "report the minimum station-day count and total station-days. "
            "Missing measurements were excluded without imputation."
        ),
        visualization="bar",
        title=f"{label} monitoring-station coverage, {period}",
        x_key="city",
        y_keys=["station_count"],
    )


def _rank_stations(args: RankStationsArgs) -> QueryPlan:
    label = METRIC_LABELS[args.metric]
    unit = METRIC_UNITS[args.metric]
    period = _window_label(args.start_year, args.end_year, args.months)
    value_key = f"{args.statistic}_{args.metric}"
    filters = [
        f"year BETWEEN {args.start_year} AND {args.end_year}",
        _valid_metric(args.metric),
    ]
    if args.city:
        filters.append(
            f"lower(trim(city)) = lower(trim({_quoted(args.city)}))"
        )
    if args.state:
        filters.append(
            f"lower(trim(state)) = lower(trim({_quoted(args.state)}))"
        )
    if args.months:
        filters.append(
            "date_part('month', timestamp) IN "
            f"({', '.join(str(month) for month in args.months)})"
        )
    direction = "DESC" if args.order == "highest" else "ASC"
    superlative = "highest" if args.order == "highest" else "lowest"
    aggregate = _aggregate_sql(args.statistic, args.metric)
    scope_parts = [
        part
        for part in (args.city, args.state)
        if part
    ]
    scope = ", ".join(scope_parts) if scope_parts else "India"
    sql = f"""
        SELECT
            station,
            site_id,
            city,
            state,
            ROUND({aggregate}, 2) AS {value_key},
            COUNT(DISTINCT timestamp) AS observation_days,
            MIN(timestamp) AS first_observation_date,
            MAX(timestamp) AS last_observation_date
        FROM air_quality
        WHERE {" AND ".join(filters)}
        GROUP BY station, site_id, city, state
        HAVING COUNT(DISTINCT timestamp) >= {args.minimum_observation_days}
        ORDER BY {value_key} {direction}, station, site_id
        LIMIT {args.limit}
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{station}}}} in {{{{city}}}} had the {superlative} "
            f"qualifying station {args.statistic} {label} during {period}: "
            f"{{{{{value_key}}}}} {unit}, based on {{{{observation_days}}}} "
            "observation days."
        ),
        method=(
            f"Filtered the station-grain table to {scope} and {period}, excluded "
            f"missing or invalid {label} values, required at least "
            f"{args.minimum_observation_days} distinct observation days per "
            f"station, then ranked each station's {args.statistic}. No values "
            "were imputed. This is a station ranking, not a city average."
        ),
        visualization="bar",
        title=f"Monitoring-station {args.statistic} {label}, {scope}, {period}",
        x_key="station",
        y_keys=[value_key],
    )


def _weekday_weekend_profile(args: WeekdayWeekendArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _period_label(args.start_year, args.end_year)
    city_filter = (
        "AND lower(trim(city)) = "
        f"lower(trim({_quoted(args.city)}))"
        if args.city
        else ""
    )
    if args.city:
        result_cte = """
            SELECT
                day_type,
                AVG(station_mean) AS group_mean,
                COUNT(*) AS station_count,
                1 AS city_count,
                MIN(observation_days) AS min_observation_days
            FROM station_group
            GROUP BY day_type
        """
        scope = args.city
    else:
        result_cte = """
            SELECT
                day_type,
                AVG(city_mean) AS group_mean,
                SUM(station_count) AS station_count,
                COUNT(*) AS city_count,
                MIN(min_observation_days) AS min_observation_days
            FROM (
                SELECT
                    city,
                    day_type,
                    AVG(station_mean) AS city_mean,
                    COUNT(*) AS station_count,
                    MIN(observation_days) AS min_observation_days
                FROM station_group
                GROUP BY city, day_type
            ) AS city_group
            GROUP BY day_type
        """
        scope = "all qualifying cities"
    sql = f"""
        WITH station_group AS (
            SELECT
                city,
                station,
                CASE
                    WHEN date_part('dayofweek', timestamp) IN (0, 6)
                    THEN 'Weekend'
                    ELSE 'Weekday'
                END AS day_type,
                AVG({args.pollutant}) AS station_mean,
                COUNT(DISTINCT timestamp) AS observation_days
            FROM air_quality
            WHERE
                year BETWEEN {args.start_year} AND {args.end_year}
                AND {_valid_metric(args.pollutant)}
                {city_filter}
            GROUP BY city, station, day_type
            HAVING COUNT(DISTINCT timestamp) >= {args.minimum_station_group_days}
        ),
        result AS (
            {result_cte}
        )
        SELECT
            day_type,
            ROUND(group_mean, 2) AS mean_{args.pollutant},
            city_count,
            station_count,
            min_observation_days
        FROM result
        ORDER BY CASE day_type WHEN 'Weekday' THEN 1 ELSE 2 END
    """
    return _plan(
        sql=sql,
        summary=(
            f"Weekday mean {label} for {scope} was "
            f"{{{{mean_{args.pollutant}}}}} {unit}; the table compares it with "
            "the weekend estimate."
        ),
        method=(
            f"Classified daily observations in {period} as weekday or weekend. "
            f"Each station-group required {args.minimum_station_group_days} valid "
            "days. Stations were weighted equally within cities"
            + (
                "."
                if args.city
                else ", then qualifying cities were weighted equally nationally."
            )
            + " Missing values were not imputed."
        ),
        visualization="bar",
        title=f"Weekday vs weekend {label}, {scope}",
        x_key="day_type",
        y_keys=[f"mean_{args.pollutant}"],
    )


def _condition_comparison(args: ConditionComparisonArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    condition_label = METRIC_LABELS[args.condition_metric]
    condition_unit = METRIC_UNITS[args.condition_metric]
    unit = METRIC_UNITS[args.pollutant]
    period = _period_label(args.start_year, args.end_year)
    city_filter = (
        "AND lower(trim(city)) = "
        f"lower(trim({_quoted(args.city)}))"
        if args.city
        else ""
    )
    if args.city:
        result_cte = """
            SELECT
                condition_group,
                AVG(station_mean) AS group_mean,
                COUNT(*) AS station_count,
                1 AS city_count,
                MIN(observation_days) AS min_observation_days
            FROM station_group
            GROUP BY condition_group
        """
        scope = args.city
    else:
        result_cte = """
            SELECT
                condition_group,
                AVG(city_mean) AS group_mean,
                SUM(station_count) AS station_count,
                COUNT(*) AS city_count,
                MIN(min_observation_days) AS min_observation_days
            FROM (
                SELECT
                    city,
                    condition_group,
                    AVG(station_mean) AS city_mean,
                    COUNT(*) AS station_count,
                    MIN(observation_days) AS min_observation_days
                FROM station_group
                GROUP BY city, condition_group
            ) AS city_group
            GROUP BY condition_group
        """
        scope = "all qualifying cities"
    sql = f"""
        WITH station_group AS (
            SELECT
                city,
                station,
                CASE
                    WHEN {args.condition_metric} > {args.threshold}
                    THEN 'Above {args.threshold:g}'
                    ELSE 'At or below {args.threshold:g}'
                END AS condition_group,
                AVG({args.pollutant}) AS station_mean,
                COUNT(DISTINCT timestamp) AS observation_days
            FROM air_quality
            WHERE
                year BETWEEN {args.start_year} AND {args.end_year}
                AND {_valid_metric(args.pollutant)}
                AND {_valid_metric(args.condition_metric)}
                {city_filter}
            GROUP BY city, station, condition_group
            HAVING COUNT(DISTINCT timestamp) >= {args.minimum_station_group_days}
        ),
        result AS (
            {result_cte}
        )
        SELECT
            condition_group,
            ROUND(group_mean, 2) AS mean_{args.pollutant},
            city_count,
            station_count,
            min_observation_days
        FROM result
        ORDER BY condition_group
    """
    return _plan(
        sql=sql,
        summary=(
            f"The table compares mean {label} when {condition_label} is above "
            f"versus at-or-below {args.threshold:g} {condition_unit} for {scope}; "
            f"the first estimate is {{{{mean_{args.pollutant}}}}} {unit}."
        ),
        method=(
            f"Split valid daily observations from {period} at "
            f"{condition_label} = {args.threshold:g} {condition_unit}. Each "
            f"station-condition group required {args.minimum_station_group_days} "
            "days, and stations were weighted equally within cities"
            + (
                "."
                if args.city
                else ", then cities were weighted equally."
            )
            + " This is a descriptive comparison, not a causal estimate."
        ),
        visualization="bar",
        title=f"{label} by {condition_label} threshold, {scope}",
        x_key="condition_group",
        y_keys=[f"mean_{args.pollutant}"],
    )


def _rank_states(args: RankStatesArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _window_label(args.start_year, args.end_year, args.months)
    value_key = f"{args.statistic}_{args.pollutant}"
    direction = "DESC" if args.order == "highest" else "ASC"
    sql = f"""
        WITH {_pollutant_station_cte(
            args.pollutant,
            args.start_year,
            args.end_year,
            months=args.months,
            statistic=args.statistic,
            minimum_station_days=args.minimum_station_days,
            geography="state",
        )},
        state_estimates AS (
            SELECT
                state,
                {_aggregate_sql(args.statistic, "average_value")} AS state_value,
                COUNT(*) AS city_count,
                SUM(station_count) AS station_count,
                MIN(min_observation_days) AS min_observation_days
            FROM city_estimates
            GROUP BY state
        )
        SELECT
            state,
            ROUND(state_value, 2) AS {value_key},
            city_count,
            station_count,
            min_observation_days
        FROM state_estimates
        ORDER BY state_value {direction}, state
        LIMIT {args.limit}
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{state}}}} ranks first by {args.statistic} {label} in {period}: "
            f"{{{{{value_key}}}}} {unit}, based on {{{{city_count}}}} cities."
        ),
        method=(
            f"Calculated station-level {args.statistic}s from {period}, requiring "
            f"{args.minimum_station_days} days per station. Stations were weighted "
            f"equally within cities, then city {args.statistic}s were weighted "
            "equally within states. Missing values were not imputed."
        ),
        visualization="bar",
        title=f"State {args.statistic} {label}, {period}",
        x_key="state",
        y_keys=[value_key],
    )


def _coverage_trend(args: CoverageTrendArgs) -> QueryPlan:
    label = METRIC_LABELS[args.metric]
    filters = [
        f"year BETWEEN {args.start_year} AND {args.end_year}",
        _valid_metric(args.metric),
    ]
    if args.cities:
        cities = ", ".join(
            f"lower(trim({_quoted(city)}))" for city in args.cities
        )
        filters.append(f"lower(trim(city)) IN ({cities})")
    sql = f"""
        WITH station_year AS (
            SELECT
                year,
                city,
                station,
                COUNT(DISTINCT timestamp) AS observation_days
            FROM air_quality
            WHERE {" AND ".join(filters)}
            GROUP BY year, city, station
            HAVING COUNT(DISTINCT timestamp) >= {args.minimum_station_year_days}
        )
        SELECT
            year,
            COUNT(*) AS station_count,
            COUNT(DISTINCT city) AS city_count,
            SUM(observation_days) AS total_station_days,
            MIN(observation_days) AS min_observation_days
        FROM station_year
        GROUP BY year
        ORDER BY total_station_days DESC, year
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{year}}}} had the strongest qualifying {label} coverage with "
            "{{total_station_days}} station-days across {{station_count}} stations."
        ),
        method=(
            f"Counted valid {label} station-days separately by calendar year from "
            f"{_period_label(args.start_year, args.end_year)}. A station-year "
            f"needed at least {args.minimum_station_year_days} days. Years are "
            "ranked by total station-days; missing values were not imputed."
        ),
        visualization="bar",
        title=f"Annual {label} observation coverage",
        x_key="year",
        y_keys=["total_station_days", "station_count"],
    )


def _funding_rank(args: FundingRankArgs) -> QueryPlan:
    direction = "DESC" if args.order == "highest" else "ASC"
    metric_label = FUNDING_METRIC_LABELS[args.metric]
    if args.metric == "utilisation_june_2022":
        sql = f"""
            SELECT
                state,
                ROUND(MAX(utilisation_june_2022), 2) AS utilisation_june_2022,
                COUNT(*) AS funded_city_count,
                ROUND(SUM(total_fund_released), 2) AS total_fund_released
            FROM ncap_funding
            WHERE utilisation_june_2022 IS NOT NULL
                AND isfinite(utilisation_june_2022)
                AND utilisation_june_2022 >= 0
            GROUP BY state
            ORDER BY utilisation_june_2022 {direction}, state
            LIMIT {args.limit}
        """
        return _plan(
            sql=sql,
            summary=(
                "{{state}} ranks first by recorded utilisation as of June 2022, "
                "with {{utilisation_june_2022}}. {{result_count}} states are shown."
            ),
            method=(
                "Ranked states, not cities, because the utilisation value is "
                "identical across every city row within a state in the bundled "
                "source. Retained that state-level value once with MAX and summed "
                "city-level total releases only for context. Null, non-finite, "
                "and negative utilisation values were excluded."
            ),
            visualization="bar",
            title="States by recorded NCAP utilisation as of June 2022",
            x_key="state",
            y_keys=["utilisation_june_2022"],
        )
    sql = f"""
        SELECT
            city,
            state,
            ROUND(fy_2019_20, 2) AS fy_2019_20,
            ROUND(fy_2020_21, 2) AS fy_2020_21,
            ROUND(fy_2021_22, 2) AS fy_2021_22,
            ROUND(total_fund_released, 2) AS total_fund_released,
            ROUND(utilisation_june_2022, 2) AS utilisation_june_2022
        FROM ncap_funding
        WHERE {args.metric} IS NOT NULL
            AND isfinite({args.metric})
            AND {args.metric} >= 0
        ORDER BY {args.metric} {direction}, city
        LIMIT {args.limit}
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{city}}}} ranks first by recorded {metric_label}, with "
            f"{{{{{args.metric}}}}}. {{{{result_count}}}} cities are shown."
        ),
        method=(
            f"Ranked the bundled NCAP city records directly by {metric_label}. "
            "Null, non-finite, and negative values were excluded; no air-quality "
            "join or imputation was used. The source contains releases only "
            "through FY 2021–22 and utilisation as of June 2022."
        ),
        visualization="bar",
        title=f"NCAP cities by {metric_label}",
        x_key="city",
        y_keys=[args.metric],
    )


def _ncap_city_estimates_cte(args: NCAPPollutionArgs) -> str:
    return f"""
        {_pollutant_station_cte(
            args.pollutant,
            args.start_year,
            args.end_year,
            months=args.months,
            statistic=args.statistic,
            minimum_station_days=args.minimum_station_days,
            geography="state",
        )},
        funded_city_estimates AS (
            SELECT
                estimates.city,
                funding.state,
                estimates.average_value,
                estimates.station_count,
                estimates.min_observation_days,
                funding.fy_2019_20,
                funding.fy_2020_21,
                funding.fy_2021_22,
                funding.total_fund_released,
                funding.utilisation_june_2022
            FROM city_estimates AS estimates
            INNER JOIN ncap_funding AS funding
                ON lower(trim(estimates.city)) = lower(trim(funding.city))
                AND lower(trim(estimates.state)) = lower(trim(funding.state))
            WHERE funding.total_fund_released IS NOT NULL
                AND isfinite(funding.total_fund_released)
                AND funding.total_fund_released >= 0
        )
    """.strip()


def _ncap_threshold_cities(args: NCAPThresholdArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _window_label(args.start_year, args.end_year, args.months)
    value_key = f"{args.statistic}_{args.pollutant}"
    operator = ">" if args.comparison == "above" else "<"
    direction = "DESC" if args.comparison == "above" else "ASC"
    sql = f"""
        WITH {_ncap_city_estimates_cte(args)}
        SELECT
            city,
            state,
            ROUND(average_value, 2) AS {value_key},
            ROUND(total_fund_released, 2) AS total_fund_released,
            station_count,
            min_observation_days,
            COUNT(*) OVER () AS matching_city_count
        FROM funded_city_estimates
        WHERE average_value {operator} {args.threshold}
        ORDER BY average_value {direction}, city
        LIMIT {args.limit}
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{matching_city_count}}}} NCAP-funded cities had "
            f"{args.statistic} {label} {args.comparison} {args.threshold:g} "
            f"{unit} in {period}; the first is {{{{city}}}} at "
            f"{{{{{value_key}}}}} {unit}."
        ),
        method=(
            f"Computed station-weighted city {args.statistic}s for {period}, "
            f"requiring {args.minimum_station_days} days per station, then joined "
            "one city estimate to each matched NCAP funding record. Missing "
            "measurements were not imputed."
        ),
        visualization="bar",
        title=f"NCAP cities {args.comparison} {args.threshold:g} {unit} {label}",
        x_key="city",
        y_keys=[value_key],
    )


def _ncap_funding_groups(args: NCAPFundingPollutionArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _window_label(args.start_year, args.end_year, args.months)
    funding_label = FUNDING_METRIC_LABELS[args.funding_metric]
    sql = f"""
        WITH {_ncap_city_estimates_cte(args)},
        benchmark AS (
            SELECT MEDIAN({args.funding_metric}) AS median_funding
            FROM funded_city_estimates
            WHERE {args.funding_metric} IS NOT NULL
                AND isfinite({args.funding_metric})
                AND {args.funding_metric} >= 0
        )
        SELECT
            CASE
                WHEN {args.funding_metric} >= median_funding
                THEN 'At or above median funding'
                ELSE 'Below median funding'
            END AS funding_group,
            ROUND(AVG(average_value), 2) AS mean_{args.pollutant},
            COUNT(*) AS city_count,
            ROUND(MIN(median_funding), 2) AS median_funding_cutoff,
            SUM(station_count) AS station_count
        FROM funded_city_estimates
        INNER JOIN benchmark ON TRUE
        WHERE {args.funding_metric} IS NOT NULL
            AND isfinite({args.funding_metric})
            AND {args.funding_metric} >= 0
        GROUP BY funding_group
        ORDER BY funding_group
    """
    return _plan(
        sql=sql,
        summary=(
            f"The first funding group had mean city {label} of "
            f"{{{{mean_{args.pollutant}}}}} {unit}; compare both groups in the "
            "table."
        ),
        method=(
            f"Matched NCAP cities to station-weighted {args.statistic} {label} "
            f"estimates for {period}. Cities were split at the observed median "
            f"{funding_label} and weighted equally within each group. Funding "
            "releases end in FY 2021–22. This is a descriptive comparison, not "
            "a causal estimate."
        ),
        visualization="bar",
        title=f"{label} by NCAP {funding_label} group",
        x_key="funding_group",
        y_keys=[f"mean_{args.pollutant}"],
    )


def _ncap_funding_relationship(args: NCAPFundingPollutionArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _window_label(args.start_year, args.end_year, args.months)
    funding_label = FUNDING_METRIC_LABELS[args.funding_metric]
    sql = f"""
        WITH {_ncap_city_estimates_cte(args)}
        SELECT
            city,
            ROUND({args.funding_metric}, 2) AS {args.funding_metric},
            ROUND(average_value, 2) AS {args.statistic}_{args.pollutant},
            station_count,
            ROUND(CORR({args.funding_metric}, average_value) OVER (), 3)
                AS pearson_r,
            COUNT(*) OVER () AS paired_cities
        FROM funded_city_estimates
        WHERE {args.funding_metric} IS NOT NULL
            AND isfinite({args.funding_metric})
            AND {args.funding_metric} >= 0
        ORDER BY {args.funding_metric}, city
        LIMIT 100
    """
    return _plan(
        sql=sql,
        summary=(
            f"Across {{{{paired_cities}}}} matched NCAP cities, {funding_label} "
            f"and {label} had a Pearson association of r = {{{{pearson_r}}}}."
        ),
        method=(
            f"Matched one {funding_label} value to each qualifying city {label} "
            f"{args.statistic} from {period}; each city estimate equally weights "
            f"stations with at least {args.minimum_station_days} days. Pearson's "
            "r was calculated across cities. Funding releases end in FY 2021–22. "
            "Association does not establish whether funding caused pollution."
        ),
        visualization="scatter",
        title=f"NCAP {funding_label} and city {label}",
        x_key=args.funding_metric,
        y_keys=[f"{args.statistic}_{args.pollutant}"],
    )


def _matched_station_change_cte(
    *,
    pollutant: str,
    start_year: int,
    end_year: int,
    months: list[int],
    minimum_station_days_per_year: int,
    minimum_station_months_per_year: int,
    cities: list[str] | None = None,
) -> str:
    filters = [
        f"year IN ({start_year}, {end_year})",
        _valid_metric(pollutant),
    ]
    if months:
        filters.append(
            "date_part('month', timestamp) IN "
            f"({', '.join(str(month) for month in months)})"
        )
    if cities:
        city_values = ", ".join(
            f"lower(trim({_quoted(city)}))"
            for city in cities
        )
        filters.append(f"lower(trim(city)) IN ({city_values})")
    return f"""
        station_year AS (
            SELECT
                state,
                city,
                station,
                year,
                AVG({pollutant}) AS station_mean,
                COUNT(DISTINCT timestamp) AS observation_days,
                COUNT(DISTINCT date_part('month', timestamp))
                    AS observation_months
            FROM air_quality
            WHERE {" AND ".join(filters)}
            GROUP BY state, city, station, year
            HAVING
                COUNT(DISTINCT timestamp) >= {minimum_station_days_per_year}
                AND COUNT(DISTINCT date_part('month', timestamp))
                    >= {minimum_station_months_per_year}
        ),
        matched_station_changes AS (
            SELECT
                baseline.state,
                baseline.city,
                baseline.station,
                baseline.station_mean AS start_station_mean,
                comparison.station_mean AS end_station_mean,
                baseline.observation_days AS start_observation_days,
                comparison.observation_days AS end_observation_days,
                baseline.observation_months AS start_observation_months,
                comparison.observation_months AS end_observation_months
            FROM station_year AS baseline
            INNER JOIN station_year AS comparison
                ON lower(trim(baseline.state)) = lower(trim(comparison.state))
                AND lower(trim(baseline.city)) = lower(trim(comparison.city))
                AND lower(trim(baseline.station)) = lower(trim(comparison.station))
                AND baseline.year = {start_year}
                AND comparison.year = {end_year}
        ),
        city_changes AS (
            SELECT
                state,
                city,
                AVG(start_station_mean) AS start_city_mean,
                AVG(end_station_mean) AS end_city_mean,
                AVG(end_station_mean) - AVG(start_station_mean) AS absolute_change,
                100.0 * (
                    AVG(end_station_mean) - AVG(start_station_mean)
                ) / NULLIF(AVG(start_station_mean), 0) AS percent_change,
                COUNT(*) AS matched_station_count,
                MIN(start_observation_days) AS min_start_observation_days,
                MIN(end_observation_days) AS min_end_observation_days,
                MIN(start_observation_months) AS min_start_observation_months,
                MIN(end_observation_months) AS min_end_observation_months
            FROM matched_station_changes
            GROUP BY state, city
        )
    """.strip()


def _pollution_change(args: PollutionChangeArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    value_key = f"absolute_change_{args.pollutant}"
    start_key = f"mean_{args.pollutant}_{args.start_year}"
    end_key = f"mean_{args.pollutant}_{args.end_year}"
    direction = "ASC" if args.order == "largest_reduction" else "DESC"
    change_filter = {
        "all": "TRUE",
        "reductions_only": "absolute_change < 0",
        "increases_only": "absolute_change > 0",
    }[args.change_filter]
    change_word = {
        "all": "change",
        "reductions_only": "reduction",
        "increases_only": "increase",
    }[args.change_filter]
    month_note = (
        f", months {', '.join(str(month) for month in args.months)}"
        if args.months
        else ""
    )
    if args.scope == "ncap_funded":
        result_source = """
            SELECT
                changes.*,
                funding.fy_2019_20,
                funding.fy_2020_21,
                funding.fy_2021_22,
                funding.total_fund_released,
                funding.utilisation_june_2022
            FROM city_changes AS changes
            INNER JOIN ncap_funding AS funding
                ON lower(trim(changes.city)) = lower(trim(funding.city))
                AND lower(trim(changes.state)) = lower(trim(funding.state))
        """
        funding_columns = """
            ROUND(fy_2019_20, 2) AS fy_2019_20,
            ROUND(fy_2020_21, 2) AS fy_2020_21,
            ROUND(fy_2021_22, 2) AS fy_2021_22,
            ROUND(total_fund_released, 2) AS total_fund_released,
            ROUND(utilisation_june_2022, 2) AS utilisation_june_2022,
        """
        scope_label = "matched NCAP-funded cities"
        funding_summary = (
            " Its recorded total NCAP funds released were "
            "{{total_fund_released}}."
        )
        funding_method = (
            " City and state names were matched to the NCAP table. Funding "
            "releases are available only through FY 2021–22 and utilisation "
            "through June 2022; the result is descriptive and does not attribute "
            "the pollution change to funding."
        )
    else:
        result_source = "SELECT * FROM city_changes"
        funding_columns = ""
        scope_label = (
            ", ".join(args.cities)
            if args.cities
            else "all qualifying cities"
        )
        funding_summary = ""
        funding_method = ""
    sql = f"""
        WITH {_matched_station_change_cte(
            pollutant=args.pollutant,
            start_year=args.start_year,
            end_year=args.end_year,
            months=args.months,
            minimum_station_days_per_year=args.minimum_station_days_per_year,
            minimum_station_months_per_year=args.minimum_station_months_per_year,
            cities=args.cities,
        )},
        scoped_changes AS (
            {result_source}
        )
        SELECT
            city,
            state,
            ROUND(start_city_mean, 2) AS {start_key},
            ROUND(end_city_mean, 2) AS {end_key},
            ROUND(absolute_change, 2) AS {value_key},
            ROUND(percent_change, 2) AS percent_change,
            matched_station_count,
            min_start_observation_days,
            min_end_observation_days,
            min_start_observation_months,
            min_end_observation_months,
            {funding_columns}
            COUNT(*) OVER () AS matching_city_count
        FROM scoped_changes
        WHERE {change_filter}
        ORDER BY absolute_change {direction}, city
        LIMIT {args.limit}
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{city}}}} had the largest qualifying {label} {change_word} "
            f"between {args.start_year} and {args.end_year}{month_note}: "
            f"{{{{{start_key}}}}} to {{{{{end_key}}}}} {unit}, a change of "
            f"{{{{{value_key}}}}} {unit} ({{{{percent_change}}}}%)."
            f"{funding_summary}"
        ),
        method=(
            f"Compared mean {label} in {args.start_year} and {args.end_year}"
            f"{month_note} for {scope_label}. Each station-year required at least "
            f"{args.minimum_station_days_per_year} valid days, and only the same "
            f"stations covering at least "
            f"{args.minimum_station_months_per_year} months in both years were "
            "retained. Matched stations were weighted equally within each city; "
            "missing values were not imputed."
            f"{funding_method}"
        ),
        visualization="bar",
        title=f"City {label} change, {args.start_year}–{args.end_year}",
        x_key="city",
        y_keys=[value_key],
    )


def _funding_change_relationship(args: ContextRelationshipArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    funding_label = FUNDING_METRIC_LABELS[args.context_metric]
    start_key = f"mean_{args.pollutant}_{args.start_year}"
    end_key = f"mean_{args.pollutant}_{args.end_year}"
    change_key = f"absolute_change_{args.pollutant}"
    month_note = (
        f" during months {', '.join(str(month) for month in args.months)}"
        if args.months
        else ""
    )
    if args.context_metric == "utilisation_june_2022":
        sql = f"""
            WITH {_matched_station_change_cte(
                pollutant=args.pollutant,
                start_year=args.start_year,
                end_year=args.end_year,
                months=args.months,
                minimum_station_days_per_year=args.minimum_station_days_per_year,
                minimum_station_months_per_year=(
                    args.minimum_station_months_per_year
                ),
            )},
            ncap_city_changes AS (
                SELECT changes.*
                FROM city_changes AS changes
                INNER JOIN ncap_funding AS funding
                    ON lower(trim(changes.city)) = lower(trim(funding.city))
                    AND lower(trim(changes.state)) = lower(trim(funding.state))
            ),
            state_changes AS (
                SELECT
                    state,
                    AVG(start_city_mean) AS start_state_mean,
                    AVG(end_city_mean) AS end_state_mean,
                    AVG(absolute_change) AS absolute_change,
                    AVG(percent_change) AS percent_change,
                    COUNT(*) AS matched_city_count,
                    SUM(matched_station_count) AS matched_station_count
                FROM ncap_city_changes
                GROUP BY state
            ),
            state_utilisation AS (
                SELECT
                    state,
                    MAX(utilisation_june_2022) AS utilisation_june_2022
                FROM ncap_funding
                WHERE utilisation_june_2022 IS NOT NULL
                    AND isfinite(utilisation_june_2022)
                    AND utilisation_june_2022 >= 0
                GROUP BY state
            ),
            paired_states AS (
                SELECT changes.*, funding.utilisation_june_2022
                FROM state_changes AS changes
                INNER JOIN state_utilisation AS funding
                    ON lower(trim(changes.state)) = lower(trim(funding.state))
            )
            SELECT
                state,
                ROUND(utilisation_june_2022, 2) AS utilisation_june_2022,
                ROUND(start_state_mean, 2) AS {start_key},
                ROUND(end_state_mean, 2) AS {end_key},
                ROUND(absolute_change, 2) AS {change_key},
                ROUND(percent_change, 2) AS percent_change,
                matched_city_count,
                matched_station_count,
                ROUND(
                    CORR(utilisation_june_2022, absolute_change) OVER (),
                    3
                ) AS pearson_r,
                COUNT(*) OVER () AS paired_states
            FROM paired_states
            ORDER BY utilisation_june_2022, state
        """
        return _plan(
            sql=sql,
            summary=(
                f"Across {{{{paired_states}}}} matched states, recorded "
                f"utilisation as of June 2022 and the {args.start_year}–"
                f"{args.end_year} {label} change had a Pearson association of "
                "r = {{pearson_r}}. Negative change values indicate reductions."
            ),
            method=(
                f"Estimated city {label} change from {args.start_year} to "
                f"{args.end_year}{month_note} using only stations with at least "
                f"{args.minimum_station_days_per_year} days and "
                f"{args.minimum_station_months_per_year} covered months in both "
                "years. "
                "Matched stations were weighted equally within cities and "
                "qualifying NCAP cities equally within states. The utilisation "
                "field is state-level in the source, so each state was included "
                "once rather than repeated for every city. Pearson's r is "
                "descriptive and does not establish causation."
            ),
            visualization="scatter",
            title=f"State NCAP utilisation and {label} change",
            x_key="utilisation_june_2022",
            y_keys=[change_key],
        )
    sql = f"""
        WITH {_matched_station_change_cte(
            pollutant=args.pollutant,
            start_year=args.start_year,
            end_year=args.end_year,
            months=args.months,
            minimum_station_days_per_year=args.minimum_station_days_per_year,
            minimum_station_months_per_year=args.minimum_station_months_per_year,
        )},
        paired_cities AS (
            SELECT
                changes.city,
                changes.state,
                changes.start_city_mean,
                changes.end_city_mean,
                changes.absolute_change,
                changes.percent_change,
                changes.matched_station_count,
                funding.{args.context_metric} AS funding_value
            FROM city_changes AS changes
            INNER JOIN ncap_funding AS funding
                ON lower(trim(changes.city)) = lower(trim(funding.city))
                AND lower(trim(changes.state)) = lower(trim(funding.state))
            WHERE funding.{args.context_metric} IS NOT NULL
                AND isfinite(funding.{args.context_metric})
                AND funding.{args.context_metric} >= 0
        )
        SELECT
            city,
            state,
            ROUND(funding_value, 2) AS {args.context_metric},
            ROUND(start_city_mean, 2) AS {start_key},
            ROUND(end_city_mean, 2) AS {end_key},
            ROUND(absolute_change, 2) AS {change_key},
            ROUND(percent_change, 2) AS percent_change,
            matched_station_count,
            ROUND(CORR(funding_value, absolute_change) OVER (), 3) AS pearson_r,
            COUNT(*) OVER () AS paired_cities
        FROM paired_cities
        ORDER BY funding_value, city
    """
    return _plan(
        sql=sql,
        summary=(
            f"Across {{{{paired_cities}}}} matched NCAP cities, recorded "
            f"{funding_label} and the {args.start_year}–{args.end_year} {label} "
            "change had a Pearson association of r = {{pearson_r}}. Negative "
            "change values indicate reductions."
        ),
        method=(
            f"Estimated city {label} change from {args.start_year} to "
            f"{args.end_year}{month_note} using only stations with at least "
            f"{args.minimum_station_days_per_year} valid days and "
            f"{args.minimum_station_months_per_year} covered months in both "
            "years. "
            "Matched stations were weighted equally within each city, then city "
            f"changes were paired with {funding_label}. Funding releases end in "
            "FY 2021–22 and utilisation is dated June 2022. Pearson's r describes "
            "association only and does not show that funding caused a change."
        ),
        visualization="scatter",
        title=f"NCAP {funding_label} and {label} change",
        x_key=args.context_metric,
        y_keys=[change_key],
    )


def _threshold_frequency(args: ThresholdFrequencyArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    operator = ">" if args.comparison == "above" else "<"
    direction = "DESC" if args.order == "most" else "ASC"
    period = _window_label(args.start_year, args.end_year, args.months)
    filters = [
        f"year BETWEEN {args.start_year} AND {args.end_year}",
        _valid_metric(args.pollutant),
    ]
    if args.months:
        filters.append(
            "date_part('month', timestamp) IN "
            f"({', '.join(str(month) for month in args.months)})"
        )
    if args.cities:
        city_values = ", ".join(
            f"lower(trim({_quoted(city)}))"
            for city in args.cities
        )
        filters.append(f"lower(trim(city)) IN ({city_values})")
    rank_column = (
        "threshold_day_share_pct"
        if args.rank_by == "share"
        else "threshold_day_count"
    )
    sql = f"""
        WITH station_daily AS (
            SELECT
                state,
                city,
                station,
                timestamp,
                AVG({args.pollutant}) AS station_daily_mean
            FROM air_quality
            WHERE {" AND ".join(filters)}
            GROUP BY state, city, station, timestamp
        ),
        city_daily AS (
            SELECT
                state,
                city,
                timestamp,
                AVG(station_daily_mean) AS city_daily_mean,
                COUNT(*) AS station_count
            FROM station_daily
            GROUP BY state, city, timestamp
        ),
        city_frequency AS (
            SELECT
                state,
                city,
                COUNT(*) FILTER (
                    WHERE city_daily_mean {operator} {args.threshold}
                ) AS threshold_day_count,
                COUNT(*) AS observed_day_count,
                100.0 * COUNT(*) FILTER (
                    WHERE city_daily_mean {operator} {args.threshold}
                ) / COUNT(*) AS threshold_day_share_pct,
                AVG(city_daily_mean) AS mean_daily_{args.pollutant},
                MIN(station_count) AS min_daily_station_count
            FROM city_daily
            GROUP BY state, city
            HAVING COUNT(*) >= {args.minimum_city_days}
        )
        SELECT
            city,
            state,
            threshold_day_count,
            observed_day_count,
            ROUND(threshold_day_share_pct, 2) AS threshold_day_share_pct,
            ROUND(mean_daily_{args.pollutant}, 2) AS mean_daily_{args.pollutant},
            min_daily_station_count
        FROM city_frequency
        ORDER BY {rank_column} {direction}, city
        LIMIT {args.limit}
    """
    ranking_label = "largest" if args.order == "most" else "smallest"
    return _plan(
        sql=sql,
        summary=(
            f"{{{{city}}}} had the {ranking_label} qualifying {args.comparison}-"
            f"threshold {'share' if args.rank_by == 'share' else 'count'}: "
            f"{{{{threshold_day_count}}}} of {{{{observed_day_count}}}} observed "
            f"days ({{{{threshold_day_share_pct}}}}%) were {args.comparison} "
            f"{args.threshold:g} {unit} {label} in {period}."
        ),
        method=(
            f"Calculated station-day mean {label}, then weighted available "
            "stations equally to form each city-day mean during "
            f"{period}. Counted city-days {args.comparison} {args.threshold:g} "
            f"{unit}; cities required at least {args.minimum_city_days} observed "
            f"days. Rankings use {args.rank_by}. Missing days were not treated "
            "as clean or polluted days, and no values were imputed."
        ),
        visualization="bar",
        title=f"City-days {args.comparison} {args.threshold:g} {unit} {label}",
        x_key="city",
        y_keys=[rank_column],
    )


def _state_level_context_relationship(args: ContextRelationshipArgs) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    unit = METRIC_UNITS[args.pollutant]
    period = _window_label(args.start_year, args.end_year, args.months)
    metric_labels = {
        "population": "recorded population",
        "area_km2": "area",
        "population_density": "recorded population density",
    }
    metric_label = metric_labels[args.context_metric]
    metric_expression = {
        "population": "CAST(metadata.population AS DOUBLE)",
        "area_km2": "metadata.area_km2",
        "population_density": (
            "CAST(metadata.population AS DOUBLE) / NULLIF(metadata.area_km2, 0)"
        ),
    }[args.context_metric]
    value_key = f"{args.statistic}_{args.pollutant}"
    sql = f"""
        WITH {_pollutant_station_cte(
            args.pollutant,
            args.start_year,
            args.end_year,
            months=args.months,
            statistic=args.statistic,
            minimum_station_days=args.minimum_station_days,
            geography="state",
        )},
        state_estimates AS (
            SELECT
                state,
                {_aggregate_sql(args.statistic, "average_value")} AS state_value,
                COUNT(*) AS city_count,
                SUM(station_count) AS station_count,
                MIN(min_observation_days) AS min_observation_days
            FROM city_estimates
            GROUP BY state
        ),
        paired_states AS (
            SELECT
                estimates.state,
                estimates.state_value,
                estimates.city_count,
                estimates.station_count,
                estimates.min_observation_days,
                metadata.population,
                metadata.area_km2,
                CAST(metadata.population AS DOUBLE)
                    / NULLIF(metadata.area_km2, 0) AS population_density,
                {metric_expression} AS context_value
            FROM state_estimates AS estimates
            INNER JOIN states AS metadata
                ON lower(trim(estimates.state)) = lower(trim(metadata.state))
            WHERE {metric_expression} IS NOT NULL
                AND isfinite({metric_expression})
                AND {metric_expression} >= 0
        )
        SELECT
            state,
            ROUND(state_value, 2) AS {value_key},
            ROUND(context_value, 2) AS {args.context_metric},
            population,
            ROUND(area_km2, 2) AS area_km2,
            ROUND(population_density, 2) AS population_density,
            city_count,
            station_count,
            min_observation_days,
            ROUND(CORR(context_value, state_value) OVER (), 3) AS pearson_r,
            COUNT(*) OVER () AS paired_states
        FROM paired_states
        ORDER BY context_value, state
    """
    return _plan(
        sql=sql,
        summary=(
            f"Across {{{{paired_states}}}} matched states, {metric_label} and "
            f"{args.statistic} {label} in {period} had a Pearson association of "
            "r = {{pearson_r}}."
        ),
        method=(
            f"Calculated station {args.statistic}s for {label} in {period}, "
            f"requiring {args.minimum_station_days} days per station. Stations "
            "were weighted equally within cities and cities equally within "
            f"states, then matched to the bundled {metric_label} metadata. "
            "Pearson's r is descriptive, does not establish causation, and the "
            "population metadata should be interpreted at its recorded vintage."
        ),
        visualization="scatter",
        title=f"State {metric_label} and {label}",
        x_key=args.context_metric,
        y_keys=[value_key],
    )


def _state_change_context_relationship(
    args: ContextRelationshipArgs,
) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    metric_labels = {
        "population": "recorded population",
        "area_km2": "area",
        "population_density": "recorded population density",
    }
    metric_label = metric_labels[args.context_metric]
    metric_expression = {
        "population": "CAST(metadata.population AS DOUBLE)",
        "area_km2": "metadata.area_km2",
        "population_density": (
            "CAST(metadata.population AS DOUBLE) / NULLIF(metadata.area_km2, 0)"
        ),
    }[args.context_metric]
    start_key = f"mean_{args.pollutant}_{args.start_year}"
    end_key = f"mean_{args.pollutant}_{args.end_year}"
    change_key = f"absolute_change_{args.pollutant}"
    sql = f"""
        WITH {_matched_station_change_cte(
            pollutant=args.pollutant,
            start_year=args.start_year,
            end_year=args.end_year,
            months=args.months,
            minimum_station_days_per_year=args.minimum_station_days_per_year,
            minimum_station_months_per_year=args.minimum_station_months_per_year,
        )},
        state_changes AS (
            SELECT
                state,
                AVG(start_city_mean) AS start_state_mean,
                AVG(end_city_mean) AS end_state_mean,
                AVG(absolute_change) AS absolute_change,
                AVG(percent_change) AS percent_change,
                COUNT(*) AS matched_city_count,
                SUM(matched_station_count) AS matched_station_count
            FROM city_changes
            GROUP BY state
        ),
        paired_states AS (
            SELECT
                changes.*,
                {metric_expression} AS context_value
            FROM state_changes AS changes
            INNER JOIN states AS metadata
                ON lower(trim(changes.state)) = lower(trim(metadata.state))
            WHERE {metric_expression} IS NOT NULL
                AND isfinite({metric_expression})
                AND {metric_expression} >= 0
        )
        SELECT
            state,
            ROUND(context_value, 2) AS {args.context_metric},
            ROUND(start_state_mean, 2) AS {start_key},
            ROUND(end_state_mean, 2) AS {end_key},
            ROUND(absolute_change, 2) AS {change_key},
            ROUND(percent_change, 2) AS percent_change,
            matched_city_count,
            matched_station_count,
            ROUND(CORR(context_value, absolute_change) OVER (), 3) AS pearson_r,
            COUNT(*) OVER () AS paired_states
        FROM paired_states
        ORDER BY context_value, state
    """
    return _plan(
        sql=sql,
        summary=(
            f"Across {{{{paired_states}}}} matched states, {metric_label} and "
            f"the {args.start_year}–{args.end_year} {label} change had a Pearson "
            "association of r = {{pearson_r}}. Negative change values indicate "
            "reductions."
        ),
        method=(
            f"Estimated city {label} change from {args.start_year} to "
            f"{args.end_year} using only stations with at least "
            f"{args.minimum_station_days_per_year} days and "
            f"{args.minimum_station_months_per_year} covered months in both "
            "years. Matched "
            "stations were weighted equally within cities and qualifying cities "
            f"equally within states, then paired with {metric_label}. Pearson's "
            "r is descriptive, does not establish causation, and population "
            "metadata should be interpreted at its recorded vintage."
        ),
        visualization="scatter",
        title=f"State {metric_label} and {label} change",
        x_key=args.context_metric,
        y_keys=[change_key],
    )


def _state_utilisation_level_relationship(
    args: ContextRelationshipArgs,
) -> QueryPlan:
    label = METRIC_LABELS[args.pollutant]
    period = _window_label(args.start_year, args.end_year, args.months)
    value_key = f"{args.statistic}_{args.pollutant}"
    sql = f"""
        WITH {_pollutant_station_cte(
            args.pollutant,
            args.start_year,
            args.end_year,
            months=args.months,
            statistic=args.statistic,
            minimum_station_days=args.minimum_station_days,
            geography="state",
        )},
        state_pollution AS (
            SELECT
                state,
                {_aggregate_sql(args.statistic, "average_value")} AS state_value,
                COUNT(*) AS city_count,
                SUM(station_count) AS station_count
            FROM city_estimates
            GROUP BY state
        ),
        state_utilisation AS (
            SELECT
                state,
                MAX(utilisation_june_2022) AS utilisation_june_2022
            FROM ncap_funding
            WHERE utilisation_june_2022 IS NOT NULL
                AND isfinite(utilisation_june_2022)
                AND utilisation_june_2022 >= 0
            GROUP BY state
        ),
        paired_states AS (
            SELECT pollution.*, funding.utilisation_june_2022
            FROM state_pollution AS pollution
            INNER JOIN state_utilisation AS funding
                ON lower(trim(pollution.state)) = lower(trim(funding.state))
        )
        SELECT
            state,
            ROUND(utilisation_june_2022, 2) AS utilisation_june_2022,
            ROUND(state_value, 2) AS {value_key},
            city_count,
            station_count,
            ROUND(CORR(utilisation_june_2022, state_value) OVER (), 3)
                AS pearson_r,
            COUNT(*) OVER () AS paired_states
        FROM paired_states
        ORDER BY utilisation_june_2022, state
    """
    return _plan(
        sql=sql,
        summary=(
            f"Across {{{{paired_states}}}} matched states, recorded utilisation "
            f"as of June 2022 and {args.statistic} {label} in {period} had a "
            "Pearson association of r = {{pearson_r}}."
        ),
        method=(
            f"Calculated station {args.statistic}s for {label} in {period}, "
            f"requiring {args.minimum_station_days} days per station; stations "
            "were weighted equally within cities and cities equally within "
            "states. The utilisation field is state-level in the source, so each "
            "state was retained once. Pearson's r is descriptive and does not "
            "establish causation."
        ),
        visualization="scatter",
        title=f"State NCAP utilisation and {label}",
        x_key="utilisation_june_2022",
        y_keys=[value_key],
    )


def _context_relationship(args: ContextRelationshipArgs) -> QueryPlan:
    state_contexts = {"population", "area_km2", "population_density"}
    if args.pollution_measure == "change":
        if args.context_metric in state_contexts:
            return _state_change_context_relationship(args)
        return _funding_change_relationship(args)
    if args.context_metric in state_contexts:
        return _state_level_context_relationship(args)
    if args.context_metric == "utilisation_june_2022":
        return _state_utilisation_level_relationship(args)
    return _ncap_funding_relationship(
        NCAPFundingPollutionArgs(
            pollutant=args.pollutant,
            start_year=args.start_year,
            end_year=args.end_year,
            months=args.months,
            statistic=args.statistic,
            minimum_station_days=args.minimum_station_days,
            funding_metric=args.context_metric,
        )
    )


def _string_filter(column: str, values: list[str]) -> str:
    if not values:
        return ""
    normalized = ", ".join(
        f"lower(trim({_quoted(value)}))"
        for value in values
    )
    return f" AND lower(trim({column})) IN ({normalized})"


def _station_map(args: StationMapArgs) -> QueryPlan:
    state_filter = _string_filter("locations.state", args.states)
    city_filter = _string_filter("locations.city", args.cities)
    period = _period_label(args.start_year, args.end_year)
    if args.metric is None:
        sql = f"""
            SELECT
                locations.state,
                locations.city,
                locations.station,
                locations.site_id,
                locations.address,
                ROUND(locations.latitude, 5) AS latitude,
                ROUND(locations.longitude, 5) AS longitude
            FROM stations AS locations
            WHERE
                locations.latitude IS NOT NULL
                AND locations.longitude IS NOT NULL
                {state_filter}
                {city_filter}
            ORDER BY locations.state, locations.city, locations.station
            LIMIT 600
        """
        return _plan(
            sql=sql,
            summary=(
                "The map shows {{result_count}} monitoring-station locations "
                "with matched coordinates."
            ),
            method=(
                "Used the static CPCB station-location table. Coordinates are "
                "shown as recorded; no geographic positions were inferred."
            ),
            visualization="map_points",
            title="CPCB monitoring-station locations",
            x_key="longitude",
            y_keys=["latitude"],
        )

    label = METRIC_LABELS[args.metric]
    unit = METRIC_UNITS[args.metric]
    value_key = f"mean_{args.metric}"
    sql = f"""
        WITH station_values AS (
            SELECT
                site_id,
                station,
                AVG({args.metric}) AS average_value,
                COUNT(DISTINCT timestamp) AS observation_days
            FROM air_quality
            WHERE
                year BETWEEN {args.start_year} AND {args.end_year}
                AND {_valid_metric(args.metric)}
            GROUP BY site_id, station
            HAVING COUNT(DISTINCT timestamp) >= {args.minimum_station_days}
        )
        SELECT
            locations.state,
            locations.city,
            locations.station,
            locations.site_id,
            locations.address,
            ROUND(locations.latitude, 5) AS latitude,
            ROUND(locations.longitude, 5) AS longitude,
            ROUND(measured.average_value, 2) AS {value_key},
            measured.observation_days
        FROM stations AS locations
        INNER JOIN station_values AS measured
            ON (
                locations.site_id = measured.site_id
                OR (
                    measured.site_id IS NULL
                    AND locations.station = measured.station
                )
            )
        WHERE
            locations.latitude IS NOT NULL
            AND locations.longitude IS NOT NULL
            {state_filter}
            {city_filter}
        ORDER BY {value_key} DESC, locations.state, locations.city
        LIMIT 600
    """
    return _plan(
        sql=sql,
        summary=(
            f"The map shows {{{{result_count}}}} stations with qualifying {label} "
            f"coverage in {period}; the first listed station averaged "
            f"{{{{{value_key}}}}} {unit}."
        ),
        method=(
            f"Mapped station-level mean {label} for {period}, requiring at least "
            f"{args.minimum_station_days} valid station-days. Missing values were "
            "excluded and coordinates came from the separate station registry."
        ),
        visualization="map_points",
        title=f"Station mean {label}, {period}",
        x_key="longitude",
        y_keys=["latitude", value_key],
    )


def _state_choropleth(args: StateChoroplethArgs) -> QueryPlan:
    period = _period_label(args.start_year, args.end_year)
    if args.value == "station_count":
        sql = f"""
            SELECT
                state,
                COUNT(DISTINCT COALESCE(site_id, station)) AS station_count,
                COUNT(DISTINCT city) AS city_count,
                COUNT(DISTINCT timestamp) AS observation_days
            FROM air_quality
            WHERE year BETWEEN {args.start_year} AND {args.end_year}
            GROUP BY state
            ORDER BY station_count DESC, state
        """
        return _plan(
            sql=sql,
            summary=(
                f"{{{{state}}}} had the most active monitoring stations in "
                f"{period}: {{{{station_count}}}} across {{{{city_count}}}} cities."
            ),
            method=(
                f"Counted distinct station identifiers with observations during "
                f"{period}. This measures network coverage, not pollution burden."
            ),
            visualization="choropleth",
            title=f"Active monitoring stations by state, {period}",
            x_key="state",
            y_keys=["station_count"],
        )

    metric = args.value
    label = METRIC_LABELS[metric]
    unit = METRIC_UNITS[metric]
    value_key = f"mean_{metric}"
    sql = f"""
        WITH {_pollutant_station_cte(
            metric,
            args.start_year,
            args.end_year,
            minimum_station_days=args.minimum_station_days,
            geography="state",
        )}
        SELECT
            state,
            ROUND(AVG(average_value), 2) AS {value_key},
            COUNT(*) AS city_count,
            SUM(station_count) AS station_count
        FROM city_estimates
        GROUP BY state
        ORDER BY {value_key} DESC, state
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{state}}}} had the highest qualifying state estimate for {label} "
            f"in {period}: {{{{{value_key}}}}} {unit} across "
            "{{city_count}} cities."
        ),
        method=(
            f"Calculated station means for {label} in {period}, gave qualifying "
            "stations equal weight within cities, then gave cities equal weight "
            "within states. Missing values were not imputed."
        ),
        visualization="choropleth",
        title=f"State {label}, {period}",
        x_key="state",
        y_keys=[value_key],
    )


def _data_catalog(args: DataCatalogArgs) -> QueryPlan:
    topic_ids = {
        "air_quality": ["cpcb_historical", "cpcb_2025"],
        "funding": [
            "ncap_funding",
            "ncap_program_finance_2025",
            "ncap_physical_progress_2025",
        ],
        "population": ["census_state_context", "state_metadata"],
        "standards": ["cpcb_naaqs"],
        "stations": ["station_locations"],
        "forecast": ["cams_global_forecast"],
        "weather": ["wrf_d01_daily", "wrf_d02_daily", "wrf_d03_daily", "wrf_d04_daily", "district_boundaries"],
    }
    where = ""
    if args.topic != "all":
        identifiers = ", ".join(_quoted(value) for value in topic_ids[args.topic])
        where = f"WHERE id IN ({identifiers})"
    sql = f"""
        SELECT
            title,
            provider,
            date_start,
            date_end,
            temporal_granularity,
            geographic_granularity,
            records,
            quality
        FROM data_sources
        {where}
        ORDER BY COALESCE(date_end, '9999-12-31') DESC, title
    """
    return _plan(
        sql=sql,
        summary=(
            "VayuChat has {{result_count}} matching, explicitly versioned data "
            "sources. Their ranges and quality notes are shown in the table."
        ),
        method=(
            "Read the bundled source catalog. Dates describe source coverage or "
            "snapshot dates; blank endpoints indicate static reference metadata."
        ),
        visualization="none",
        title="VayuChat data sources",
        x_key="title",
        y_keys=[],
    )


def _census_state_rank(args: CensusStateRankArgs) -> QueryPlan:
    labels = {
        "population_2011": "Census 2011 population",
        "population_density_2011_per_km2": "Census 2011 population density",
        "decadal_growth_2001_2011_pct": "2001–2011 population growth",
    }
    units = {
        "population_2011": "people",
        "population_density_2011_per_km2": "people/km²",
        "decadal_growth_2001_2011_pct": "%",
    }
    direction = "DESC" if args.order == "highest" else "ASC"
    sql = f"""
        SELECT
            state,
            category,
            {args.metric}
        FROM census_states
        WHERE category <> 'India' AND {args.metric} IS NOT NULL
        ORDER BY {args.metric} {direction}, state
        LIMIT {args.limit}
    """
    return _plan(
        sql=sql,
        summary=(
            f"{{{{state}}}} had the {args.order} listed {labels[args.metric]}: "
            f"{{{{{args.metric}}}}} {units[args.metric]}. "
            "{{result_count}} states/UTs are shown."
        ),
        method=(
            "Used the Census of India 2011 state/UT table. These are historical "
            "census values using the source's 2011 boundaries, not current estimates."
        ),
        visualization="bar",
        title=labels[args.metric],
        x_key="state",
        y_keys=[args.metric],
    )


def _air_quality_standard(args: StandardsLookupArgs) -> QueryPlan:
    where = []
    pollutant_names = {
        "pm25": "PM2.5",
        "pm10": "PM10",
        "no2": "NO2",
        "so2": "SO2",
        "co": "CO",
        "ozone": "Ozone",
        "nh3": "NH3",
    }
    if args.pollutant != "all":
        where.append(f"pollutant = {_quoted(pollutant_names[args.pollutant])}")
    if args.averaging_period != "all":
        where.append(f"averaging_period = {_quoted(args.averaging_period)}")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""
        SELECT
            pollutant,
            averaging_period,
            standard_limit,
            unit,
            area_scope,
            notification_year
        FROM air_quality_standards
        {clause}
        ORDER BY pollutant, averaging_period, area_scope
    """
    return _plan(
        sql=sql,
        summary=(
            "The first matching CPCB limit is {{standard_limit}} {{unit}} for "
            "{{pollutant}} over the {{averaging_period}} averaging period. "
            "{{result_count}} applicable standard rows are shown."
        ),
        method=(
            "Looked up the CPCB Revised National Ambient Air Quality Standards "
            "(2009). Averaging periods and area categories were not combined."
        ),
        visualization="none",
        title="CPCB National Ambient Air Quality Standards",
        x_key="pollutant",
        y_keys=["standard_limit"],
    )


def _programme_finance_trend(args: ProgramFinanceArgs) -> QueryPlan:
    y_keys = (
        ["released_crore", "utilised_crore"]
        if args.metric == "both"
        else [args.metric]
    )
    sql = f"""
        SELECT
            fiscal_year,
            released_crore,
            utilised_crore,
            utilisation_pct,
            reporting_date
        FROM ncap_program_finance
        WHERE program = {_quoted(args.program)}
        ORDER BY fiscal_year
    """
    return _plan(
        sql=sql,
        summary=(
            f"The {args.program} programme finance table contains "
            "{{result_count}} fiscal years through the 18 August 2025 snapshot."
        ),
        method=(
            "Used national programme totals reported by PRANA. Utilisation in a "
            "fiscal-year row can draw on earlier balances, so percentages above "
            "100% are not treated as errors or same-year causal rates."
        ),
        visualization="bar",
        title=f"{args.program} programme finance by fiscal year",
        x_key="fiscal_year",
        y_keys=y_keys,
    )


def _physical_progress(args: PhysicalProgressArgs) -> QueryPlan:
    labels = {
        "ncap": "NCAP",
        "xvfc": "XV-FC",
        "ncap_plus_xvfc": "NCAP + XV-FC",
        "other_scheme_convergence": "other-scheme convergence",
    }
    sql = f"""
        SELECT
            activity,
            unit,
            {args.programme_scope} AS reported_value,
            reporting_date
        FROM ncap_physical_progress
        WHERE {args.programme_scope} IS NOT NULL
        ORDER BY activity
    """
    return _plan(
        sql=sql,
        summary=(
            f"The {labels[args.programme_scope]} progress snapshot reports "
            "{{result_count}} implementation outputs as of {{reporting_date}}."
        ),
        method=(
            "Used cumulative physical outputs reported in the 18th NCAP "
            "Implementation Committee material. Units differ by activity, so "
            "the table is authoritative; bar lengths should not be compared "
            "across unlike units."
        ),
        visualization="none",
        title=f"{labels[args.programme_scope]} physical progress",
        x_key="activity",
        y_keys=["reported_value"],
    )


def build_analysis(name: str, raw_arguments: dict[str, Any]) -> ToolAnalysis:
    definition = TOOL_BY_NAME.get(name)
    if definition is None:
        raise ValueError(f"Unknown analysis function: {name}")
    arguments = definition.arguments_model.model_validate(raw_arguments)

    if name == "rank_cities":
        plan = _rank_cities(arguments)
    elif name == "city_average":
        plan = _city_average(arguments)
    elif name == "threshold_cities":
        plan = _threshold_cities(arguments)
    elif name == "compare_cities":
        plan = _compare_cities(arguments)
    elif name == "time_trend":
        plan = _time_trend(arguments)
    elif name == "relationship":
        plan = _relationship(arguments)
    elif name == "strongest_weather_relationship":
        plan = _strongest_weather_relationship(arguments)
    elif name == "seasonal_profile":
        plan = _seasonal_profile(arguments)
    elif name == "funding_lookup":
        plan = _funding_lookup(arguments)
    elif name == "station_coverage":
        plan = _station_coverage(arguments)
    elif name == "rank_stations":
        plan = _rank_stations(arguments)
    elif name == "weekday_weekend_profile":
        plan = _weekday_weekend_profile(arguments)
    elif name == "condition_comparison":
        plan = _condition_comparison(arguments)
    elif name == "rank_states":
        plan = _rank_states(arguments)
    elif name == "coverage_trend":
        plan = _coverage_trend(arguments)
    elif name == "funding_rank":
        plan = _funding_rank(arguments)
    elif name == "ncap_threshold_cities":
        plan = _ncap_threshold_cities(arguments)
    elif name == "ncap_funding_groups":
        plan = _ncap_funding_groups(arguments)
    elif name == "pollution_change":
        plan = _pollution_change(arguments)
    elif name == "context_relationship":
        plan = _context_relationship(arguments)
    elif name == "threshold_frequency":
        plan = _threshold_frequency(arguments)
    elif name == "station_map":
        plan = _station_map(arguments)
    elif name == "state_choropleth":
        plan = _state_choropleth(arguments)
    elif name == "data_catalog":
        plan = _data_catalog(arguments)
    elif name == "census_state_rank":
        plan = _census_state_rank(arguments)
    elif name == "air_quality_standard":
        plan = _air_quality_standard(arguments)
    elif name == "programme_finance_trend":
        plan = _programme_finance_trend(arguments)
    elif name == "physical_progress":
        plan = _physical_progress(arguments)
    elif name in WEATHER_TOOL_NAMES:
        plan = weather_grid.run(name, arguments).plan
    elif name in CAMS_TOOL_NAMES:
        plan = cams_forecast.run(name, arguments).plan
    elif name == "clarify_question":
        return ToolAnalysis(
            name=name,
            plan=QueryPlan(
                in_scope=False,
                refusal=arguments.question,
            ),
            arguments=arguments.model_dump(),
        )
    else:
        return ToolAnalysis(
            name=name,
            plan=QueryPlan(
                in_scope=False,
                refusal=(
                    "I can help with Indian air quality, meteorology, and "
                    "NCAP funding questions."
                ),
            ),
            arguments=arguments.model_dump(),
        )
    return ToolAnalysis(
        name=name,
        plan=plan,
        arguments=arguments.model_dump(),
    )
