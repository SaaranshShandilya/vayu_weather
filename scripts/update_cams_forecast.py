"""Fetch and process the newest CAMS forecast run for VayuChat.

Looks for the newest CAMS global forecast run (00 or 12 UTC) that has not
been processed yet, downloads it over India with fetch_cams_forecast.py, and
writes one tidy NetCDF file per run:

    data/cams/cams_forecast_<YYYYMMDD>T<HH>.nc

Each file is a (time, lat, lon) Dataset on the 0.4° CAMS grid with:

* time in IST (the valid time of each hourly step, 0-120 h after the run);
* pm25, pm10, no2, o3, so2 in µg/m³ and co in mg/m³ (CPCB's units). PM comes
  as kg/m³; the gases come as mass mixing ratios at the lowest model level
  (~10 m), converted with the air density there, p / (R_d T), where
  p ≈ 0.9975 × surface pressure (the L137 hybrid coefficients of that level);
* aod550 and duaod550 (total and dust aerosol optical depth, unitless) and
  10 m wind;
* state_index: each cell centre's state, as for the WRF grids.

Raw downloads are deleted after processing; processed runs older than
--keep-days are removed. Run it from a timer every few hours: it does nothing
when no newer run is available.

    python scripts/update_cams_forecast.py
    python scripts/update_cams_forecast.py --date 2026-10-22 --run 0   # one specific run
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from build_wrf_weather import state_index  # noqa: E402
from fetch_cams_forecast import PROJECT_ROOT, RAW_DIR, fetch  # noqa: E402

CAMS_DIR = PROJECT_ROOT / "data" / "cams"
IST = pd.Timedelta(hours=5, minutes=30)
R_DRY = 287.05  # J kg-1 K-1
# Full-level pressure of model level 137 as a fraction of surface pressure.
LEVEL_137_PRESSURE = 0.9975
VARIABLES = {
    "pm25": ("PM2.5", "µg/m³"),
    "pm10": ("PM10", "µg/m³"),
    "no2": ("Nitrogen dioxide (NO2)", "µg/m³"),
    "o3": ("Ozone (O3)", "µg/m³"),
    "so2": ("Sulphur dioxide (SO2)", "µg/m³"),
    "co": ("Carbon monoxide (CO)", "mg/m³"),
    "aod550": ("Total aerosol optical depth at 550 nm", "1"),
    "duaod550": ("Dust aerosol optical depth at 550 nm", "1"),
    "wind_speed": ("10 m wind speed", "m/s"),
    "u10": ("10 m eastward wind", "m/s"),
    "v10": ("10 m northward wind", "m/s"),
}


def output_path(run: pd.Timestamp) -> Path:
    return CAMS_DIR / f"cams_forecast_{run:%Y%m%dT%H}.nc"


def process(folder: Path) -> Path:
    """Turn one downloaded run into the tidy VayuChat file."""
    surface = xr.open_dataset(next((folder / "surface").glob("*.nc")))
    level = xr.open_dataset(next((folder / "level137").glob("*.nc")))
    surface = surface.squeeze(drop=False)
    level = level.squeeze(drop=False)
    run = pd.Timestamp(surface["forecast_reference_time"].values.item())
    valid_ist = run + pd.to_timedelta(surface["forecast_period"].values) + IST

    density = LEVEL_137_PRESSURE * surface["sp"] / (R_DRY * level["t"])  # kg/m³
    fields = {
        "pm25": surface["pm2p5"] * 1e9,
        "pm10": surface["pm10"] * 1e9,
        "no2": level["no2"] * density * 1e9,
        "o3": level["go3"] * density * 1e9,
        "so2": level["so2"] * density * 1e9,
        "co": level["co"] * density * 1e6,
        "aod550": surface["aod550"],
        "duaod550": surface["duaod550"],
        "wind_speed": np.hypot(surface["u10"], surface["v10"]),
        "u10": surface["u10"],
        "v10": surface["v10"],
    }
    dataset = xr.Dataset(
        {
            name: (
                ("time", "lat", "lon"),
                field.transpose("forecast_period", "latitude", "longitude").values.astype("float32"),
            )
            for name, field in fields.items()
        },
        coords={
            "time": valid_ist.values,
            "lat": surface["latitude"].values.astype("float64"),
            "lon": surface["longitude"].values.astype("float64"),
        },
    ).sortby("lat")
    for name, (label, unit) in VARIABLES.items():
        dataset[name].attrs = {"long_name": label, "units": unit}
    dataset["time"].attrs = {"long_name": "valid time, IST (UTC+05:30)"}

    lat = dataset["lat"].values
    lon = dataset["lon"].values
    states, names = state_index(lat, lon)
    dataset["state_index"] = states
    step = round(float(abs(lat[1] - lat[0])), 4)
    run_ist = run + IST
    dataset.attrs = {
        "domain": "cams",
        "region": "India",
        "display_label": f"CAMS global forecast ({step}°, ~{step * 111:.0f} km)",
        "model_short": "CAMS",
        "native_dx_km": round(step * 111, 0),
        "resolution_degrees": step,
        "run_utc": run.isoformat(),
        "run_ist": run_ist.isoformat(),
        "time_start": str(pd.Timestamp(valid_ist[0]).isoformat()),
        "time_end": str(pd.Timestamp(valid_ist[-1]).isoformat()),
        "states": json.dumps(names),
        "missing_days": "[]",
        "source": "Copernicus Atmosphere Monitoring Service (CAMS) global atmospheric composition forecasts",
        "licence": "CC-BY-4.0. Contains modified Copernicus Atmosphere Monitoring Service information.",
        "provenance": (
            f"CAMS global forecast run {run:%Y-%m-%d %H} UTC from the Copernicus Atmosphere Data "
            "Store, cut to 5–38°N, 66–100°E, processed by scripts/update_cams_forecast.py. PM2.5 "
            "and PM10 are surface concentrations; NO2, O3, SO2 and CO are mass mixing ratios at the "
            "lowest model level (~10 m) converted with the air density there. This is model "
            "forecast output, not observation."
        ),
    }
    path = output_path(run)
    CAMS_DIR.mkdir(parents=True, exist_ok=True)
    encoding = {name: {"zlib": True, "complevel": 4} for name in dataset.data_vars}
    dataset.to_netcdf(path, encoding=encoding)
    surface.close()
    level.close()
    return path


def candidate_runs(now: dt.datetime, lookback_runs: int = 6) -> list[pd.Timestamp]:
    """Recent 00/12 UTC runs, newest first."""
    latest = pd.Timestamp(now).tz_convert("UTC").tz_localize(None).floor("12h")
    return [latest - pd.Timedelta(hours=12 * k) for k in range(lookback_runs)]


def prune(keep_days: int) -> None:
    cutoff = pd.Timestamp.now("UTC").tz_localize(None) - pd.Timedelta(days=keep_days)
    for path in CAMS_DIR.glob("cams_forecast_*.nc"):
        run = pd.Timestamp(dt.datetime.strptime(path.stem.split("_")[-1], "%Y%m%dT%H"))
        if run < cutoff:
            path.unlink()
            print(f"removed {path.name} (older than {keep_days} days)")


def update(runs: list[pd.Timestamp]) -> Path | None:
    """Fetch and process the newest run in `runs` that is available and new."""
    for run in runs:
        target = output_path(run)
        if target.exists():
            print(f"{target.name} is already processed; nothing newer is available yet.")
            return None
        folder = RAW_DIR / f"{run:%Y-%m-%d}T{run:%H}"
        try:
            fetch(f"{run:%Y-%m-%d}", run.hour, list(range(0, 121)))
        except Exception as error:  # noqa: BLE001 - an unpublished run is a normal case
            print(f"run {run:%Y-%m-%d %H} UTC not available: {str(error).splitlines()[0][:160]}")
            shutil.rmtree(folder, ignore_errors=True)
            continue
        path = process(folder)
        shutil.rmtree(folder, ignore_errors=True)
        print(f"wrote {path.relative_to(PROJECT_ROOT)} ({path.stat().st_size / 1e6:.1f} MB)")
        return path
    print("no CAMS run in the look-back window was available.")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="Process one specific run date (UTC) instead of the newest")
    parser.add_argument("--run", type=int, choices=(0, 12), default=0)
    parser.add_argument("--keep-days", type=int, default=30)
    parser.add_argument("--reprocess", action="store_true", help="Process an existing raw download only")
    args = parser.parse_args()
    if args.reprocess and args.date:
        folder = RAW_DIR / f"{args.date}T{args.run:02d}"
        print("wrote", process(folder))
        return
    if args.date:
        runs = [pd.Timestamp(f"{args.date} {args.run:02d}:00")]
    else:
        runs = candidate_runs(dt.datetime.now(dt.timezone.utc))
    update(runs)
    prune(args.keep_days)


if __name__ == "__main__":
    main()
