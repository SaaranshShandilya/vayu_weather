"""Build one daily weather dataset per WRF domain from raw WRF output.

Reads the 3-hourly ``wrfout_<domain>_*`` files, reduces them to one value per
IST calendar day and grid cell, and interpolates the result to a regular
latitude/longitude grid. Each domain is written as a self-describing NetCDF
file, ``data/gridded/wrf_<domain>_daily.nc``, holding one ``(time, lat, lon)``
variable per weather field and tagged with its domain in the attributes.
VayuChat opens these with xarray and answers weather questions with labelled
array operations on them.

    python scripts/build_wrf_weather.py --domain d01
    python scripts/build_wrf_weather.py --domain d02 --workers 12

Domains (all Mercator, 3-hourly, 2023-2024):

    d01  all India           27 km  ->  0.25° grid
    d02  North India          9 km  ->  0.08° grid
    d03  South India          9 km  ->  0.08° grid
    d04  East India           9 km  ->  0.08° grid

Needs ``netCDF4`` and ``scipy`` (for ``.interp``), which the app itself does
not need. About 20 minutes per domain over NFS with 12 workers; more workers
saturate the NAS.

Day boundaries. WRF writes 00, 03, ..., 21 UTC. IST is UTC+05:30, so the IST
day D is sampled by 21 UTC on D-1 and 00-18 UTC on D: eight instants from
02:30 to 23:30 IST. Daily maxima and minima are taken over those eight
instants, so they can miss a peak that falls between them.

Rainfall. RAINC + RAINNC accumulate from the start of each WRF run. The runs
are re-initialised roughly every ten days, so 3-hour totals are differences
within one run. The interval ending at a re-initialisation (XTIME = 0) is not
recorded by either run and is counted as missing rather than zero.

Damaged input. Empty or truncated files are skipped and listed in the
attributes. A day with fewer than MIN_SAMPLES readable instants is stored as
missing (NaN) rather than estimated.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import xarray as xr


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "gridded"
RAW_WRF = Path("/mnt/nas-sustain/gfs_data/raw_wrf")

DOMAINS = {
    "d01": {"step": 0.25, "region": "all India"},
    "d02": {"step": 0.08, "region": "North India"},
    "d03": {"step": 0.08, "region": "South India"},
    "d04": {"step": 0.08, "region": "East India"},
}
IST_OFFSET = dt.timedelta(hours=5, minutes=30)
MIN_SAMPLES = 4

# Stored as int16 with this scale factor; xarray decodes to float, NaN for gaps.
VARIABLES = {
    "t2_mean": ("Mean 2 m air temperature", "°C", 0.1),
    "t2_max": ("Maximum 2 m air temperature", "°C", 0.1),
    "t2_min": ("Minimum 2 m air temperature", "°C", 0.1),
    "rh_mean": ("Mean 2 m relative humidity", "%", 0.1),
    "wind_mean": ("Mean 10 m wind speed", "m/s", 0.01),
    "wind_max": ("Maximum 10 m wind speed", "m/s", 0.01),
    "u10_mean": ("Mean 10 m eastward wind", "m/s", 0.01),
    "v10_mean": ("Mean 10 m northward wind", "m/s", 0.01),
    "rain": ("Daily rainfall total", "mm", 0.1),
    "pblh_mean": ("Mean planetary boundary layer height", "m", 1.0),
    "pblh_max": ("Maximum planetary boundary layer height", "m", 1.0),
    "swdown_mean": ("Mean downward shortwave radiation", "W/m²", 0.1),
    "psfc_mean": ("Mean surface pressure", "hPa", 0.1),
}
FIELDS = ["T2", "Q2", "PSFC", "U10", "V10", "RAINC", "RAINNC", "PBLH", "SWDOWN"]
NAME_PATTERN = re.compile(r"wrfout_d0\d_(\d{4}-\d{2}-\d{2}_\d{2}):\d{2}:\d{2}$")


def _valid_time(path: Path) -> dt.datetime:
    match = NAME_PATTERN.search(path.name)
    if not match:
        raise ValueError(f"Not a wrfout file name: {path.name}")
    return dt.datetime.strptime(match.group(1), "%Y-%m-%d_%H")


def _ist_day(valid_utc: dt.datetime) -> dt.date:
    return (valid_utc + IST_OFFSET).date()


def _read(path: Path) -> xr.Dataset | None:
    """One file's fields as an (y, x) Dataset, or None when it is damaged."""
    try:
        with xr.open_dataset(path, engine="netcdf4", decode_times=False) as handle:
            sample = handle[FIELDS].isel(Time=0).load()
            sample.attrs = {
                "xtime": float(handle["XTIME"].values[0]),
                "run_start": handle.attrs["SIMULATION_START_DATE"],
                "valid": _valid_time(path).isoformat(),
            }
    except (OSError, IndexError, KeyError, ValueError, RuntimeError):
        return None
    return sample


def _relative_humidity(t2_k: xr.DataArray, q2: xr.DataArray, psfc_pa: xr.DataArray) -> xr.DataArray:
    vapour_hpa = q2 * (psfc_pa / 100.0) / (0.622 + 0.378 * q2)
    temp_c = t2_k - 273.15
    saturation_hpa = 6.112 * np.exp(17.67 * temp_c / (temp_c + 243.5))
    return (100.0 * vapour_hpa / saturation_hpa).clip(0.0, 100.0)


def _daily_rain(samples: list[xr.Dataset], previous: xr.Dataset | None) -> tuple[xr.DataArray, int]:
    """Sum 3-hour rain within each WRF run; count intervals no run recorded."""
    rain = xr.zeros_like(samples[0]["T2"])
    missing = 0
    before = previous
    for sample in samples:
        accumulated = sample["RAINC"] + sample["RAINNC"]
        if sample.attrs["xtime"] == 0 or before is None:
            missing += 1
        elif before.attrs["run_start"] == sample.attrs["run_start"]:
            # Differences across a skipped file still cover the gap.
            rain = rain + (accumulated - (before["RAINC"] + before["RAINNC"])).clip(min=0)
        elif sample.attrs["run_start"].replace("_", "T") >= before.attrs["valid"]:
            # A run that started after the previous file: its whole
            # accumulation falls inside this interval.
            rain = rain + accumulated.clip(min=0)
        else:
            missing += 1
        before = sample
    return rain, missing


def reduce_day(task: tuple[str, list[str], str | None, list[float], list[float], list[float], list[float]]) -> dict:
    """Reduce one IST day's files to daily fields on the regular target grid."""
    day, paths, previous_path, source_lat, source_lon, target_lat, target_lon = task
    read = [(path, _read(Path(path))) for path in paths]
    samples = [sample for _, sample in read if sample is not None]
    unreadable = [Path(path).name for path, sample in read if sample is None]
    result = {"day": day, "samples": len(samples), "unreadable": unreadable}
    if len(samples) < MIN_SAMPLES:
        return {**result, "missing_rain_intervals": len(paths), "fields": None}
    previous = _read(Path(previous_path)) if previous_path else None

    stacked = xr.concat(samples, dim="sample", combine_attrs="drop")
    t2 = stacked["T2"] - 273.15
    speed = np.hypot(stacked["U10"], stacked["V10"])
    rh = _relative_humidity(stacked["T2"], stacked["Q2"], stacked["PSFC"])
    rain, missing_rain = _daily_rain(samples, previous)
    daily = xr.Dataset(
        {
            "t2_mean": t2.mean("sample"),
            "t2_max": t2.max("sample"),
            "t2_min": t2.min("sample"),
            "rh_mean": rh.mean("sample"),
            "wind_mean": speed.mean("sample"),
            "wind_max": speed.max("sample"),
            "u10_mean": stacked["U10"].mean("sample"),
            "v10_mean": stacked["V10"].mean("sample"),
            "rain": rain,
            "pblh_mean": stacked["PBLH"].mean("sample"),
            "pblh_max": stacked["PBLH"].max("sample"),
            "swdown_mean": stacked["SWDOWN"].mean("sample"),
            "psfc_mean": stacked["PSFC"].mean("sample") / 100.0,
        }
    )
    # The Mercator grid has 1-D latitude and longitude, so the native grid is
    # labelled with them and interpolated bilinearly to the regular grid.
    daily = daily.rename({"south_north": "lat", "west_east": "lon"}).assign_coords(
        lat=source_lat, lon=source_lon
    )
    regridded = daily.interp(lat=target_lat, lon=target_lon).astype("float32")
    return {**result, "missing_rain_intervals": missing_rain, "fields": regridded}


def _target_axis(native: np.ndarray, step: float) -> np.ndarray:
    """A regular axis inside the native grid, half a step in from its edges."""
    start = math.ceil((native.min() + step / 2) / step) * step
    stop = math.floor((native.max() - step / 2) / step) * step
    return np.round(np.arange(start, stop + step / 2, step), 4)


def state_index(lat: np.ndarray, lon: np.ndarray) -> tuple[xr.DataArray, list[str]]:
    """Each cell centre's state or union territory index, or -1 outside India."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from backend.places import places

    index, names = places.state_index_grid(lat, lon)
    return xr.DataArray(index, coords={"lat": lat, "lon": lon}, dims=("lat", "lon")), names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", choices=sorted(DOMAINS), required=True)
    parser.add_argument("--wrf-dir", type=Path, help="Defaults to raw_wrf/<domain> on the NAS.")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit-days", type=int, default=0, help="Process only the first N days (testing).")
    args = parser.parse_args()
    domain = args.domain
    step = DOMAINS[domain]["step"]
    wrf_dir = args.wrf_dir or RAW_WRF / domain
    output_path = OUTPUT_DIR / f"wrf_{domain}_daily.nc"

    files = sorted(wrf_dir.glob(f"wrfout_{domain}_*"), key=_valid_time)
    if not files:
        raise SystemExit(f"No wrfout_{domain} files in {wrf_dir}")
    by_day: dict[dt.date, list[Path]] = defaultdict(list)
    for path in files:
        by_day[_ist_day(_valid_time(path))].append(path)
    first_valid = _valid_time(files[0])
    last_valid = _valid_time(files[-1])
    # Keep whole IST days only: day D ends with the 18 UTC output on D, so a
    # trailing day the files stop short of (e.g. only 21 and 00 UTC) is dropped.
    days = sorted(
        day
        for day in by_day
        if dt.datetime.combine(day, dt.time(18)) <= last_valid
    )
    if args.limit_days:
        days = days[: args.limit_days]

    with xr.open_dataset(files[0], engine="netcdf4", decode_times=False) as handle:
        source_lat = handle["XLAT"].isel(Time=0).values[:, 0].astype("float64")
        source_lon = handle["XLONG"].isel(Time=0).values[0, :].astype("float64")
        model = {
            "title": handle.attrs["TITLE"].strip(),
            "dx_km": float(handle.attrs["DX"]) / 1000,
            "map_proj": int(handle.attrs["MAP_PROJ"]),
        }
    target_lat = _target_axis(source_lat, step)
    target_lon = _target_axis(source_lon, step)

    position = {path: number for number, path in enumerate(files)}
    tasks = []
    for day in days:
        paths = by_day[day]
        first = position[paths[0]]
        previous = str(files[first - 1]) if first > 0 else None
        tasks.append(
            (
                day.isoformat(),
                [str(path) for path in paths],
                previous,
                source_lat.tolist(),
                source_lon.tolist(),
                target_lat.tolist(),
                target_lon.tolist(),
            )
        )

    empty = xr.Dataset(
        {name: (("lat", "lon"), np.full((len(target_lat), len(target_lon)), np.nan, "float32")) for name in VARIABLES},
        coords={"lat": target_lat, "lon": target_lon},
    )
    daily_fields, samples, missing_rain, missing_days, unreadable = [], [], [], [], []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for number, result in enumerate(pool.map(reduce_day, tasks, chunksize=2)):
            unreadable.extend(result["unreadable"])
            fields = result["fields"]
            if fields is None:
                fields = empty
                missing_days.append(result["day"])
            daily_fields.append(fields.expand_dims(time=[np.datetime64(result["day"], "ns")]))
            samples.append(result["samples"])
            missing_rain.append(result["missing_rain_intervals"])
            if number % 25 == 0:
                print(f"{domain} {result['day']}: {number + 1}/{len(tasks)} days", flush=True)

    dataset = xr.concat(daily_fields, dim="time", data_vars="all", coords="minimal", compat="override")
    states, state_names = state_index(target_lat, target_lon)
    dataset["state_index"] = states
    dataset["samples_per_day"] = ("time", np.asarray(samples, dtype="int8"))
    for name, (label, unit, _) in VARIABLES.items():
        dataset[name].attrs = {"long_name": label, "units": unit}
    dataset["lat"].attrs = {"units": "degrees_north"}
    dataset["lon"].attrs = {"units": "degrees_east"}

    times = [task[0] for task in tasks]
    incomplete = [day for day, count in zip(times, samples) if MIN_SAMPLES <= count < 8]
    dataset.attrs = {
        "domain": domain,
        "region": DOMAINS[domain]["region"],
        "title": f"Daily weather from WRF ({domain}, {DOMAINS[domain]['region']}, {model['dx_km']:g} km)",
        "provider": "WRF v4.7.1 simulation, Sustainability Lab, IIT Gandhinagar",
        "model": model["title"],
        "native_dx_km": model["dx_km"],
        "native_projection": "Mercator" if model["map_proj"] == 3 else f"MAP_PROJ {model['map_proj']}",
        "resolution_degrees": step,
        "source_directory": str(wrf_dir),
        "source_files": len(files),
        "source_valid_range_utc": f"{first_valid.isoformat()} / {last_valid.isoformat()}",
        "timezone": "Asia/Kolkata (IST, UTC+05:30)",
        "day_definition": (
            "IST calendar day sampled at the eight 3-hourly WRF outputs from "
            "02:30 to 23:30 IST (21 UTC the previous day to 18 UTC)."
        ),
        "time_start": times[0],
        "time_end": times[-1],
        "states": json.dumps(state_names),
        "missing_days": json.dumps(missing_days),
        "incomplete_days": json.dumps(incomplete),
        "unreadable_files": json.dumps(sorted(unreadable)),
        "missing_rain_intervals": int(sum(missing_rain)),
        "provenance": (
            f"Built by scripts/build_wrf_weather.py from {len(files):,} "
            f"3-hourly wrfout_{domain} files. Daily statistics are taken on the "
            f"native {model['dx_km']:g} km Mercator grid, then bilinearly "
            f"interpolated to a regular {step}° grid. Days with fewer than "
            f"{MIN_SAMPLES} readable outputs are stored as missing "
            f"({len(missing_days)} days); {len(set(unreadable))} empty or "
            "truncated source files were skipped. Relative humidity is derived "
            "from Q2, T2 and PSFC (Bolton saturation vapour pressure). Rainfall "
            "is RAINC + RAINNC differenced within each WRF run; the 3-hour "
            "interval ending at each run re-initialisation is not recorded "
            f"({int(sum(missing_rain))} intervals in total). Daily maxima and "
            "minima come from 3-hourly instants and can miss intermediate "
            "peaks. This is model simulation output, not station observation."
        ),
    }
    encoding = {
        name: {"dtype": "int16", "scale_factor": scale, "_FillValue": np.int16(-32768), "zlib": True, "complevel": 4}
        for name, (_, _, scale) in VARIABLES.items()
    }
    encoding["state_index"] = {"zlib": True}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dataset.to_netcdf(output_path, encoding=encoding)
    size = output_path.stat().st_size / 1e6
    print(
        f"Wrote {output_path.relative_to(PROJECT_ROOT)} ({size:.1f} MB): "
        f"{dataset.sizes['time']} days × {dataset.sizes['lat']} × {dataset.sizes['lon']}, "
        f"{len(missing_days)} missing days"
    )


if __name__ == "__main__":
    main()
