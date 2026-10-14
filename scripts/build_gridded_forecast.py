"""Build the gridded emulator forecast bundled with VayuChat.

The output is one compressed NumPy archive holding each variable as a
``[time, latitude, longitude]`` cube, plus a small JSON sidecar that describes
it. VayuChat answers gridded questions with array operations on that cube; it
never flattens the grid into a table of ``time × lat × lon`` rows.

Two modes:

* ``--synthetic`` (default) writes a deterministic, clearly labelled synthetic
  sample over Delhi NCR so the feature can be developed and demonstrated
  before real emulator output is available.
* ``--from-netcdf PATH`` converts a real WRF-Chem or emulator NetCDF file
  into the same layout. It needs ``xarray`` and ``netCDF4``, which the app
  itself does not require.

    python scripts/build_gridded_forecast.py
    python scripts/build_gridded_forecast.py --from-netcdf emulator.nc \\
        --var pm25=PM2_5_DRY --var pm10=PM10 --source "IITM emulator v1"
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "gridded"
ARRAY_PATH = OUTPUT_DIR / "delhi_ncr_forecast.npz"
METADATA_PATH = OUTPUT_DIR / "delhi_ncr_forecast.json"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

LAT_MIN, LAT_MAX = 28.0, 29.2
LON_MIN, LON_MAX = 76.6, 77.8
STEP = 0.02
HOURS = 72
ISSUED = dt.datetime(2026, 10, 7, 0, 0, tzinfo=IST)

# Road corridors out of Delhi, as (lat, lon) polylines.
CORRIDORS = [
    [(28.63, 77.21), (28.84, 77.10), (28.99, 77.02)],  # NH-44 to Sonipat
    [(28.60, 77.15), (28.46, 77.03), (28.36, 76.93)],  # NH-48 to Gurugram, Manesar
    [(28.55, 77.27), (28.41, 77.31), (28.14, 77.33)],  # NH-19 to Faridabad, Palwal
    [(28.63, 77.30), (28.67, 77.44), (28.73, 77.78)],  # NH-9 to Ghaziabad, Hapur
    [(28.68, 77.11), (28.69, 76.93), (28.70, 76.75)],  # NH-9 west to Bahadurgarh
]
# Industrial clusters and waste sites with elevated local emissions.
HOTSPOTS = [
    (28.647, 77.316, 1.0),  # Anand Vihar
    (28.801, 77.051, 0.9),  # Bawana
    (28.852, 77.092, 0.8),  # Narela
    (28.683, 77.033, 0.7),  # Mundka
    (28.744, 77.319, 0.7),  # Loni, Ghaziabad
    (28.395, 77.311, 0.6),  # Faridabad industrial area
    (28.209, 76.860, 0.5),  # Bhiwadi
    (28.625, 77.328, 0.6),  # Ghazipur landfill
]


def _gaussian(lat2d, lon2d, lat, lon, sigma):
    return np.exp(-(((lat2d - lat) ** 2 + (lon2d - lon) ** 2) / (2 * sigma**2)))


def _corridor(lat2d, lon2d, polyline, sigma):
    field = np.zeros_like(lat2d)
    for (lat_a, lon_a), (lat_b, lon_b) in zip(polyline, polyline[1:]):
        for fraction in np.linspace(0, 1, 25):
            lat = lat_a + (lat_b - lat_a) * fraction
            lon = lon_a + (lon_b - lon_a) * fraction
            field = np.maximum(field, _gaussian(lat2d, lon2d, lat, lon, sigma))
    return field


def _station_points():
    points = []
    with (PROJECT_ROOT / "data" / "stations.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (TypeError, ValueError):
                continue
            if LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX:
                points.append((lat, lon))
    return points


def _advect(field, shift_rows, shift_cols, steps=6):
    """Smear a field downwind to mimic transport of urban emissions."""
    smeared = field.copy()
    for step in range(1, steps + 1):
        weight = 0.75**step
        moved = np.roll(
            field,
            (round(shift_rows * step), round(shift_cols * step)),
            axis=(0, 1),
        )
        smeared = np.maximum(smeared, moved * weight)
    return smeared


def build_synthetic() -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, list[str], dict]:
    rng = np.random.default_rng(20261007)
    latitudes = np.round(np.arange(LAT_MIN, LAT_MAX + STEP / 2, STEP), 4)
    longitudes = np.round(np.arange(LON_MIN, LON_MAX + STEP / 2, STEP), 4)
    lat2d, lon2d = np.meshgrid(latitudes, longitudes, indexing="ij")

    urban = 1.2 * _gaussian(lat2d, lon2d, 28.63, 77.19, 0.11)
    urban += 0.5 * _gaussian(lat2d, lon2d, 28.46, 77.03, 0.05)  # Gurugram
    urban += 0.5 * _gaussian(lat2d, lon2d, 28.67, 77.42, 0.05)  # Ghaziabad
    urban += 0.4 * _gaussian(lat2d, lon2d, 28.40, 77.31, 0.05)  # Faridabad
    urban += 0.35 * _gaussian(lat2d, lon2d, 28.98, 77.70, 0.05)  # Meerut
    for lat, lon in _station_points():
        urban += 0.12 * _gaussian(lat2d, lon2d, lat, lon, 0.025)
    for polyline in CORRIDORS:
        urban += 0.35 * _corridor(lat2d, lon2d, polyline, 0.018)
    for lat, lon, weight in HOTSPOTS:
        urban += 0.9 * weight * _gaussian(lat2d, lon2d, lat, lon, 0.018)

    # Smoke from crop-residue burning arrives from the north-west.
    northwest = np.clip(
        ((lat2d - LAT_MIN) / (LAT_MAX - LAT_MIN) + (LON_MAX - lon2d) / (LON_MAX - LON_MIN)) / 2,
        0,
        1,
    )
    # Dust is coarser and comes from the Rajasthan side (south-west).
    southwest = np.clip(
        ((LAT_MAX - lat2d) / (LAT_MAX - LAT_MIN) + (LON_MAX - lon2d) / (LON_MAX - LON_MIN)) / 2,
        0,
        1,
    )

    def smooth_noise():
        noise = np.zeros_like(lat2d)
        for _ in range(6):
            noise += rng.normal(0, 0.5) * _gaussian(
                lat2d,
                lon2d,
                rng.uniform(LAT_MIN, LAT_MAX),
                rng.uniform(LON_MIN, LON_MAX),
                rng.uniform(0.12, 0.3),
            )
        return noise

    noise_a, noise_b = smooth_noise(), smooth_noise()
    pm25 = np.empty((HOURS, *lat2d.shape), dtype=np.float32)
    pm10 = np.empty_like(pm25)
    times = []
    for hour in range(HOURS):
        moment = ISSUED + dt.timedelta(hours=hour)
        times.append(moment.isoformat(timespec="minutes"))
        local_hour = moment.hour
        # Shallow night-time boundary layer traps pollution; afternoon mixing
        # dilutes it. Small traffic peaks at 09:00 and 20:00.
        mixing = 1.0 + 0.42 * np.cos(2 * np.pi * (local_hour - 3) / 24)
        traffic = 1.0 + 0.12 * np.exp(-((local_hour - 9) ** 2) / 4) + 0.15 * np.exp(
            -((local_hour - 20) ** 2) / 5
        )
        # The episode builds: north-westerlies weaken to near calm by day three.
        episode = 1.0 + 0.32 * hour / (HOURS - 1)
        wind_turn = hour / (HOURS - 1)
        shift_rows = -1.6 * (1 - 0.7 * wind_turn)
        shift_cols = 1.4 * (1 - 0.7 * wind_turn) - 0.5 * wind_turn
        plume = _advect(urban, shift_rows, shift_cols)
        smoke = 40 * northwest * (0.6 + 0.6 * np.sin(np.pi * hour / HOURS))
        blend = 0.5 + 0.5 * np.sin(2 * np.pi * hour / 36)
        texture = 1.0 + 0.12 * (blend * noise_a + (1 - blend) * noise_b)

        fine = (32 + smoke + 62 * plume * traffic) * mixing * episode * texture
        fine += rng.normal(0, 2.0, size=fine.shape)
        coarse_ratio = 1.55 + 0.45 * southwest + 0.25 * np.clip(plume, 0, 1)
        dust = 30 * southwest * (1.2 - 0.4 * mixing)
        pm25[hour] = np.clip(fine, 8, None)
        pm10[hour] = np.clip(fine * coarse_ratio + dust, 15, None)

    metadata = {
        "id": "gridded_forecast_sample",
        "title": "Delhi NCR gridded PM forecast (synthetic emulator sample)",
        "provider": "Synthetic sample generated by VayuChat for development",
        "synthetic": True,
        "model": "Synthetic stand-in for WRF-Chem / ML-emulator output",
        "issued": ISSUED.isoformat(timespec="minutes"),
        "timezone": "Asia/Kolkata (IST, UTC+05:30)",
        "provenance": (
            "Generated by scripts/build_gridded_forecast.py from station "
            "locations, road corridors, industrial hotspots, a diurnal "
            "boundary-layer cycle, north-west smoke transport and seeded "
            "smooth noise. It has the structure of emulator output but is "
            "not a model forecast and must not be used for decisions."
        ),
    }
    return {"pm25": pm25, "pm10": pm10}, latitudes, longitudes, times, metadata


def build_from_netcdf(path: Path, variables: dict[str, str], source: str):
    import xarray as xr  # Optional: only needed to convert real model output.

    dataset = xr.open_dataset(path)
    lat_name = next(name for name in ("latitude", "lat", "XLAT") if name in dataset.coords or name in dataset)
    lon_name = next(name for name in ("longitude", "lon", "XLONG") if name in dataset.coords or name in dataset)
    time_name = next(name for name in ("time", "Time", "valid_time") if name in dataset.dims or name in dataset)
    latitudes = np.asarray(dataset[lat_name].values, dtype=np.float64)
    longitudes = np.asarray(dataset[lon_name].values, dtype=np.float64)
    if latitudes.ndim != 1 or longitudes.ndim != 1:
        raise SystemExit(
            "Only regular latitude/longitude grids are supported. Regrid "
            "curvilinear WRF output (XLAT/XLONG 2-D) to a regular grid first."
        )
    cubes = {}
    for key, name in variables.items():
        cube = np.asarray(dataset[name].transpose(time_name, lat_name, lon_name).values, dtype=np.float32)
        if latitudes[0] > latitudes[-1]:
            cube = cube[:, ::-1, :]
        cubes[key] = cube
    if latitudes[0] > latitudes[-1]:
        latitudes = latitudes[::-1]
    stamps = np.asarray(dataset[time_name].values).astype("datetime64[m]")
    times = [
        (dt.datetime.fromisoformat(str(stamp)).replace(tzinfo=dt.timezone.utc).astimezone(IST)).isoformat(timespec="minutes")
        for stamp in stamps
    ]
    metadata = {
        "id": "gridded_forecast",
        "title": f"Delhi NCR gridded PM forecast ({source})",
        "provider": source,
        "synthetic": False,
        "model": source,
        "issued": times[0],
        "timezone": "Asia/Kolkata (IST, UTC+05:30)",
        "provenance": f"Converted from {path.name} by scripts/build_gridded_forecast.py.",
    }
    return cubes, latitudes, longitudes, times, metadata


def write(cubes, latitudes, longitudes, times, metadata) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rounded = {key: np.round(cube, 1).astype(np.float32) for key, cube in cubes.items()}
    np.savez_compressed(
        ARRAY_PATH,
        latitude=np.asarray(latitudes, dtype=np.float64),
        longitude=np.asarray(longitudes, dtype=np.float64),
        time=np.asarray(times),
        **rounded,
    )
    first = next(iter(rounded.values()))
    metadata = {
        **metadata,
        "variables": sorted(rounded),
        "dimensions": ["time", "latitude", "longitude"],
        "shape": list(first.shape),
        "latitude_range": [float(latitudes[0]), float(latitudes[-1])],
        "longitude_range": [float(longitudes[0]), float(longitudes[-1])],
        "resolution_degrees": round(float(latitudes[1] - latitudes[0]), 4),
        "time_start": times[0],
        "time_end": times[-1],
        "time_step_hours": 1,
        "values_per_variable": int(first.size),
        "units": {key: "µg/m³" for key in rounded},
    }
    METADATA_PATH.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {ARRAY_PATH.relative_to(PROJECT_ROOT)} ({ARRAY_PATH.stat().st_size / 1e6:.2f} MB)")
    print(f"Shape per variable: {first.shape} = {first.size:,} values")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-netcdf", type=Path)
    parser.add_argument("--var", action="append", default=[], help="key=netcdf_name, e.g. pm25=PM2_5_DRY")
    parser.add_argument("--source", default="Emulator output")
    args = parser.parse_args()
    if args.from_netcdf:
        variables = dict(item.split("=", 1) for item in args.var) or {"pm25": "pm25", "pm10": "pm10"}
        write(*build_from_netcdf(args.from_netcdf, variables, args.source))
    else:
        write(*build_synthetic())


if __name__ == "__main__":
    main()
