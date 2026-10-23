"""Download one CAMS global forecast run over India.

Source: Copernicus Atmosphere Monitoring Service (CAMS) global atmospheric
composition forecasts, from the Copernicus Atmosphere Data Store (ADS),
CC-BY-4.0. Each run (00 or 12 UTC) forecasts 0-120 hours ahead, hourly.

Two requests per run:

* surface fields: PM2.5, PM10, dust and total aerosol optical depth at
  550 nm, surface pressure and 10 m wind;
* the lowest model level (137, ~10 m above ground): NO2, O3, SO2, CO as mass
  mixing ratios, plus temperature there, so the gases can be converted to
  µg/m³ with the air density at that level.

Credentials: the ADS personal access token as ADS_API_KEY in the project
.env (or the environment). If it is absent, ~/.cdsapirc is used instead.

Usage:

    python scripts/fetch_cams_forecast.py --test           # one lead hour, prints the grid
    python scripts/fetch_cams_forecast.py                  # latest 00 UTC run, 0-120 h
    python scripts/fetch_cams_forecast.py --date 2026-10-22 --run 12
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from backend.config import load_env_file  # noqa: E402

ADS_URL = "https://ads.atmosphere.copernicus.eu/api"
RAW_DIR = PROJECT_ROOT / "data" / "cams" / "raw"
DATASET = "cams-global-atmospheric-composition-forecasts"
# North, West, South, East: India with a margin for neighbouring sources.
AREA = [38, 66, 5, 100]

SURFACE_VARIABLES = [
    "particulate_matter_2.5um",
    "particulate_matter_10um",
    "dust_aerosol_optical_depth_550nm",
    "total_aerosol_optical_depth_550nm",
    "surface_pressure",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
]
LOWEST_LEVEL_VARIABLES = [
    "nitrogen_dioxide",
    "ozone",
    "sulphur_dioxide",
    "carbon_monoxide",
    "temperature",
]


def _request(variables: list[str], date: str, run: int, lead_hours: list[int], model_level: bool) -> dict:
    request = {
        "variable": variables,
        "date": [f"{date}/{date}"],
        "time": [f"{run:02d}:00"],
        "leadtime_hour": [str(hour) for hour in lead_hours],
        "type": ["forecast"],
        "area": AREA,
        "data_format": "netcdf_zip",
    }
    if model_level:
        request["model_level"] = ["137"]
    return request


def _unzip(archive: Path, target: Path) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        names = [name for name in bundle.namelist() if name.endswith(".nc")]
        for name in names:
            # Flatten: keep only the file name, never a path from the archive.
            (target / Path(name).name).write_bytes(bundle.read(name))
    archive.unlink()
    return [target / Path(name).name for name in names]


def fetch(date: str, run: int, lead_hours: list[int]) -> list[Path]:
    import cdsapi

    load_env_file()
    key = os.environ.get("ADS_API_KEY", "").strip()
    # The key never appears in logs: cdsapi is quiet about credentials.
    client = cdsapi.Client(url=ADS_URL, key=key) if key else cdsapi.Client()
    folder = RAW_DIR / f"{date}T{run:02d}"
    files = []
    for label, variables, model_level in (
        ("surface", SURFACE_VARIABLES, False),
        ("level137", LOWEST_LEVEL_VARIABLES, True),
    ):
        archive = folder / f"{label}.zip"
        folder.mkdir(parents=True, exist_ok=True)
        client.retrieve(DATASET, _request(variables, date, run, lead_hours, model_level)).download(str(archive))
        files += _unzip(archive, folder / label)
    return files


def describe(files: list[Path]) -> None:
    import xarray as xr

    for path in files:
        with xr.open_dataset(path) as dataset:
            lat = next(name for name in ("latitude", "lat") if name in dataset.coords)
            lon = next(name for name in ("longitude", "lon") if name in dataset.coords)
            step_lat = abs(float(dataset[lat][1] - dataset[lat][0]))
            step_lon = abs(float(dataset[lon][1] - dataset[lon][0]))
            print(f"{path.relative_to(PROJECT_ROOT)}")
            print(f"  grid: {dataset.sizes[lat]} × {dataset.sizes[lon]} at {step_lat:g}° × {step_lon:g}°")
            print(f"  dims: {dict(dataset.sizes)}")
            for name, variable in dataset.data_vars.items():
                print(f"  {name}: {variable.attrs.get('long_name', '')} [{variable.attrs.get('units', '')}]")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    yesterday = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
    parser.add_argument("--date", default=yesterday, help="Run date (UTC), default yesterday")
    parser.add_argument("--run", type=int, choices=(0, 12), default=0, help="Run time, UTC")
    parser.add_argument("--test", action="store_true", help="Fetch one lead hour only")
    args = parser.parse_args()
    lead_hours = [0] if args.test else list(range(0, 121))
    files = fetch(args.date, args.run, lead_hours)
    describe(files)


if __name__ == "__main__":
    main()
