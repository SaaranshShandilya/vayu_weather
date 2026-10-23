"""VayuChat answers only from real data: no synthetic samples, no forecasts."""

import json
import re
from pathlib import Path

from backend.analysis_tools import TOOL_BY_NAME
from backend.catalog import DATA_SOURCES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_WORDS = re.compile(r"\b(?:synthetic|simulated sample|dummy|placeholder|fake)\b", re.IGNORECASE)


def test_catalog_lists_no_synthetic_source():
    for source in DATA_SOURCES:
        text = json.dumps(source, ensure_ascii=False)
        assert not SYNTHETIC_WORDS.search(text), source["id"]
        assert "sample" not in source["id"], source["id"]


def test_forecast_functions_are_backed_by_real_cams_runs():
    """The only forecasts are CAMS model runs, credited to Copernicus."""
    import xarray as xr

    forecast_tools = {name for name in TOOL_BY_NAME if "forecast" in name}
    assert forecast_tools and all(name.startswith("cams_") for name in forecast_tools)
    for path in (PROJECT_ROOT / "data" / "cams").glob("cams_forecast_*.nc"):
        with xr.open_dataset(path) as dataset:
            assert dataset.attrs["model_short"] == "CAMS"
            assert "Copernicus" in dataset.attrs["licence"]
            assert not SYNTHETIC_WORDS.search(dataset.attrs["provenance"])


def test_only_the_real_weather_grids_are_bundled():
    import xarray as xr

    gridded = sorted(path.name for path in (PROJECT_ROOT / "data" / "gridded").iterdir())
    assert gridded and all(re.fullmatch(r"wrf_d0[1-4]_daily\.nc", name) for name in gridded), gridded
    for name in gridded:
        with xr.open_dataset(PROJECT_ROOT / "data" / "gridded" / name) as dataset:
            assert dataset.attrs["source_files"] > 5_000
            assert dataset.attrs["domain"] == name[4:7]
            assert not SYNTHETIC_WORDS.search(dataset.attrs["provenance"])


def test_sidebar_prompts_ask_about_real_data_only():
    source = (PROJECT_ROOT / "src" / "prompts.ts").read_text(encoding="utf-8")
    prompts = re.findall(r'^\s+"([^"]+)",?$', source, flags=re.MULTILINE)
    assert len(prompts) > 50
    forecasts = source[source.index('name: "cams_forecast"') : source.index('name: "methods"')]
    forecast_prompts = set(re.findall(r'^\s+"([^"]+)",?$', forecasts, flags=re.MULTILINE))
    for prompt in prompts:
        assert not re.search(r"\bsample\b", prompt, re.IGNORECASE), prompt
        # Future-looking questions belong only in the CAMS forecast group.
        if prompt not in forecast_prompts:
            assert not re.search(r"\b(?:forecast|tomorrow|next \d+ days)\b", prompt, re.IGNORECASE), prompt
