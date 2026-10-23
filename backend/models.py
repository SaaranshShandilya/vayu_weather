from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8_000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=2, max_length=2_000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=12)


class QueryPlan(BaseModel):
    in_scope: bool
    refusal: str = ""
    sql: str = ""
    summary_template: str = Field(default="", max_length=1_200)
    method_note: str = Field(default="", max_length=1_200)
    visualization: Literal[
        "none",
        "bar",
        "line",
        "scatter",
        "map_points",
        "choropleth",
        "grid_heatmap",
    ] = "none"
    title: str = ""
    x_key: str = ""
    y_keys: list[str] = Field(default_factory=list)


class AnswerNarrative(BaseModel):
    answer: str = Field(min_length=1, max_length=900)
    key_points: list[
        Annotated[str, Field(min_length=1, max_length=260)]
    ] = Field(default_factory=list, max_length=15)
    follow_up_questions: list[
        Annotated[str, Field(min_length=2, max_length=240)]
    ] = Field(default_factory=list, min_length=2, max_length=3)


class GridFrame(BaseModel):
    label: str
    values: list[float | None]


class GridLandmark(BaseModel):
    name: str
    latitude: float
    longitude: float


class GridPayload(BaseModel):
    """A gridded field for the heatmap: row-major values, south to north."""

    latitudes: list[float]
    longitudes: list[float]
    frames: list[GridFrame] = Field(max_length=24)
    value_label: str
    unit: str
    scale: Literal[
        "days",
        "hours",
        "pm25",
        "pm10",
        "pollutant",
        "aod",
        "temperature",
        "humidity",
        "wind",
        "rainfall",
        "pblh",
        "radiation",
        "pressure",
    ]
    scale_max: float | None = None
    landmarks: list[GridLandmark] = Field(default_factory=list)
    source_shape: list[int]
    resolution_degrees: float
    # Draw state outlines over the grid (all-India and state maps).
    boundaries: bool = False
    source_label: str = "WRF d01 daily weather"
    # The step of the time axis the grid was reduced over.
    time_unit: Literal["days", "hours"] = "days"
    # The WRF domain the values come from, e.g. "d02" and its description.
    domain: str = ""
    domain_label: str = ""
    # Boundary of the place asked about, as [lon, lat] rings, and its name.
    outline: list[list[list[float]]] = Field(default_factory=list)
    outline_label: str = ""


class AnalysisStep(BaseModel):
    function_name: str
    arguments: dict


class AnalysisResultSet(BaseModel):
    analysis_function: str
    columns: list[str]
    rows: list[dict]
    visualization: Literal[
        "none",
        "bar",
        "line",
        "scatter",
        "map_points",
        "choropleth",
        "grid_heatmap",
    ]
    title: str
    x_key: str
    y_keys: list[str]
    truncated: bool = False
    grid: GridPayload | None = None


class ChatResponse(BaseModel):
    answer: str
    key_points: list[str]
    method_note: str
    analysis_steps: list[AnalysisStep] = Field(default_factory=list, max_length=3)
    columns: list[str]
    rows: list[dict]
    visualization: Literal[
        "none",
        "bar",
        "line",
        "scatter",
        "map_points",
        "choropleth",
        "grid_heatmap",
    ]
    title: str
    x_key: str
    y_keys: list[str]
    truncated: bool = False
    analysis_type: str = ""
    elapsed_ms: int = 0
    suggested_questions: list[str] = Field(default_factory=list, max_length=3)
    clarification_options: list[str] = Field(default_factory=list, max_length=6)
    result_sets: list[AnalysisResultSet] = Field(default_factory=list, max_length=3)
    grid: GridPayload | None = None
