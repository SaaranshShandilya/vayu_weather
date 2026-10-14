---
title: VayuChat
emoji: 🌬️
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
fullWidth: true
header: mini
pinned: false
short_description: Gemini-powered analysis of Indian air-quality and NCAP data
---

# VayuChat

VayuChat is a password-protected air-quality analysis workspace from the
Sustainability Lab at IIT Gandhinagar. The interface is built with React and
Vite; a FastAPI backend keeps credentials private and uses Gemini 3.5
Flash-Lite in two bounded stages:

**NLQ → Gemini 3.5 Flash-Lite selects a typed function → VayuChat validates
and runs read-only DuckDB SQL → answer, table, or chart.**

1. Gemini selects one typed analysis function—or up to three for a compound
   question—and supplies validated arguments.
2. The backend builds and runs read-only DuckDB SQL.
3. Gemini composes only the verified result sets, then suggests follow-up questions.

The function library uses reusable analytical primitives rather than one
function per question. It covers city and state summaries, rankings,
thresholds, trends, seasons, weekday/weekend and measured-threshold
comparisons, monitoring coverage, and NCAP funding joins. Gemini can compose
up to three primitives, but every query and join remains server-owned; Gemini
does not write or repair SQL.

The bundled source catalog is exposed in the app and through the
`data_catalog` function:

- CPCB station-day pollution and meteorology: 2017–2025, 858,604 rows.
- Monitoring locations: 535 geocoded stations in 277 cities.
- Historical NCAP city releases: FY 2019–20 through FY 2021–22, with a June
  2022 utilisation snapshot.
- National NCAP/XV-FC finance and physical progress: PRANA reporting snapshots
  dated 14–18 August 2025.
- Census state/UT population, density, and decadal growth: Census 2011.
- CPCB National Ambient Air Quality Standards: 2009 notification, with
  averaging periods retained separately.

## Gridded forecast data

Model and emulator forecasts (WRF-Chem, ML emulators of it, CAMS) are not
station readings. Every latitude × longitude cell has a value for every
forecast hour. VayuChat keeps such output as one `[time, latitude, longitude]`
array per pollutant in `data/gridded/delhi_ncr_forecast.npz` and answers
questions with array operations in `backend/gridded.py`. It does not flatten
the grid into SQL rows: the bundled 72 × 61 × 61 grid would be 267,912 rows
per pollutant, and a 400 m emulator domain would be tens of millions.

- `forecast_grid_map` gives a mean, peak, or hour-by-hour heatmap over Delhi
  NCR, or over one NCR city, plus the hotspot cells.
- `forecast_point_series` gives the hourly forecast at a city, a monitoring
  station, or a coordinate.
- `forecast_exceedance_map` counts the hours each cell is above a stated
  threshold, and the share of area above it each day.

Only summary rows and one downsampled grid (at most 120 cells per axis and
24 animation frames) reach the browser. The `grid_heatmap` chart uses CPCB AQI
colours.

**The bundled grid is a synthetic sample.** It is labelled as synthetic in
the app and in the answers. To load real emulator or WRF-Chem output on a
regular lat/lon grid, convert it to the same layout (this step needs
`xarray`/`netCDF4`):

```bash
python scripts/build_gridded_forecast.py --from-netcdf emulator.nc     --var pm25=PM2_5_DRY --var pm10=PM10 --source "IITM emulator"
python scripts/build_gridded_forecast.py   # regenerate the synthetic sample
```

Maps are rendered from the station registry and a simplified India state
boundary layer. Every source keeps its own grain and vintage; VayuChat does
not label Census 2011 population as current or partial city-finance evidence
as complete. The boundary file is only a visual layer; choropleth values and
rankings always come from the separately versioned analytical tables.

For example, a city mean is fully specified as:

```text
city_average(
  city="Mumbai",
  pollutant="pm25",
  start_year=2017,
  end_year=2025,
  months=[],
  statistic="mean",
  minimum_station_days=30,
  station_weighting="equal_station"
)
```

This calculates each station's mean across valid daily observations in the
selected window, requires at least 30 days per station, then takes the mean of
the qualifying station means. It does not silently weight stations with denser
reporting more heavily.

Year-to-year questions use the generic `pollution_change` primitive. By
default, it retains only the same stations in both years with at least 180
valid days spanning nine months in each year. An optional `ncap_funded` scope
joins the resulting city changes to the bundled funding fields. The NCAP
release data end in FY 2021–22 and utilisation is dated June 2022, so joined
results are reported as associations and context—not causal effects.

## Required Hugging Face secrets

Add both values under **Space settings → Variables and secrets → Secrets**:

- `GEMINI_API_KEYS`: one or more Google Gemini API keys, separated by commas
  or newlines. `GEMINI_API_KEY` is still accepted for a single key.
- `APP_PASSWORD`: the shared password required by the sign-in screen

Optionally add `GEMINI_MODEL` as a public variable to override the default
`gemini-3.5-flash-lite`.

Secrets are read only by the Python backend. They are never embedded in the
Vite build or returned to the browser.

## Local development

```bash
npm install
npm run build

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export GEMINI_API_KEYS="key-one,key-two"
export APP_PASSWORD="..."
uvicorn backend.app:app --host 0.0.0.0 --port 7860
```

For frontend hot reload, run `npm run dev` in a second terminal. Vite proxies
`/api` requests to port 7860.

## The Gemini key pool

One user turn costs two to four API calls: the router, up to two repair
attempts, and the presenter. Under concurrent load that arrives as a burst,
so the unit of scarcity is a key with headroom right now. Every outbound call
leases one from a shared pool:

```python
key = await pool.acquire()
try:
    response = await call(client_for(key))
except Exception as exc:
    pool.release(key, error=exc)
    raise
pool.release(key)
```

`backend/keypool.py` holds the whole mechanism and imports nothing from the
app, so it ports to another service by supplying two predicates: is this error
a quota signal, and is it worth retrying.

- **Selection.** `acquire` picks the ready key with the fewest in-flight
  requests, then the least of its minute spent, then the least recently used,
  so load spreads instead of hammering the first key.
- **Waiting.** Cooldown, per-minute, and per-day limits collapse into one
  timestamp per key, so a caller with no ready key sleeps exactly until the
  first one frees rather than polling.
- **Admission control.** A request is recorded against its key at lease time,
  not at completion, so a paced pool makes the caller that would overrun the
  limit wait rather than discover it as a 429.
- **Learning from failures.** `release` parks a rate-limited key for the delay
  Google names in its own error (capped at 120s), parks a key that reports a
  daily quota until the next Pacific midnight, and parks nothing at all for a
  dropped socket: a broken pipe is not a quota signal.
- **Failover and a turn budget.** A retriable failure re-sends the request on a
  different key, up to four distinct keys; a malformed 400 raises at once
  rather than burning four keys on a call that is wrong on all of them. One
  wall-clock budget covers the whole turn (`GEMINI_TURN_BUDGET_SECONDS`), so
  per-call waits cannot stack into a minutes-long spinner. Past it the user
  gets an honest "request limit reached" card.

Local pacing is off unless configured (`GEMINI_KEY_RPM=0`), because Google no
longer publishes per-model free-tier numbers and a hardcoded guess is either
wasteful or useless. Do not guess: the 429 message names the real limit
(`limit: 15, model: gemini-3.5-flash-lite`), so pace just below it. Measured
against 40 concurrent users on ten keys, pacing at 20 produced 17 rate limits
per 100 calls; pacing at 12 produced 2, and finished faster. Keys are never
logged or returned — `/api/keys` and the pool's logs identify them by
fingerprint (`AQ.A...GhhA`) only.

Configuration comes from the environment, or from a `.env` in the project root
that is loaded at startup; a real environment variable always wins over the
file.

The free tier meters **per Google Cloud project, not per API key**, so ten keys
minted in one project share one quota and the pool buys nothing. Prove your
keys are independent before relying on them:

```bash
python scripts/check_keys.py           # one probe per key
python scripts/check_keys.py --burst 40  # saturate key one, then probe the rest
```

`--burst` drives the first key until Google rate limits it, then immediately
probes every other key. A key that still answers is in a different project.

Measured on ten keys, one project each: key one rate limited after 15 of 40
requests and all nine others still answered. Forty concurrent chat turns then
completed 40/40 in 26s over 86 API calls (2.1 per turn), with two 429s absorbed
by failover and no user-visible error.

## Security notes

- Authentication uses a signed, HTTP-only, same-site session cookie.
- API keys never leave the backend. Status output shows fingerprints only.
- Login attempts are rate-limited in memory.
- All analytical SQL is built by typed, server-owned functions. The model can
  select functions and arguments but cannot submit code or SQL.
- Analyses use equal-station weighting, explicit
  coverage columns, and aligned city-day pairs for correlations.
- Generated server queries still pass a read-only allow-list validator before
  execution as defense in depth.
- City names are checked against the actual datasets and close unmatched names
  receive a "Did you mean …?" suggestion.
- The Gemini API key and shared password must be Hugging Face secrets, not
  repository files or public variables.

## Verification

```bash
uv run --with-requirements requirements.txt --with pytest pytest -q
npm run build
```

The test suite exercises every function against the real bundled data,
statistical weighting, coverage thresholds, relationship alignment, typed
argument validation, composition, city-name correction, capability-backed
follow-up suggestions, and hostile SQL inputs.

The practical-question release suite is separate from the unit tests. It
contains 96 reviewed cases / 105 conversational turns covering single analyses,
station-grain rankings, ten compound questions, follow-ups, context resets,
clarification, unsupported scope, causal wording, typos, maps, funding,
standards, and prompt injection.
Every expected executable plan is first run against the bundled database.

```bash
# Validate the reviewed corpus and execute its expected plans locally
.venv/bin/python scripts/run_practical_evals.py

# Score the live Gemini planner once
.venv/bin/python scripts/run_practical_evals.py --live

# Stability gate: 98 turns repeated three times
.venv/bin/python scripts/run_practical_evals.py --live --repetitions 3
```

The 25 July 2026 release-gate run passed all 294/294 turns with a median planner
time of 1.54 seconds. Fifteen turns used the bounded semantic repair path.
The 27 July station-grain extension additionally passed 25/25 live station
ranking routes and 24/24 live multi-turn context-reset routes against
`gemini-3.5-flash-lite`, without repair.
Machine-readable and Markdown reports are written under
`outputs/evals/practical/`. The 180 independently Gemini-generated discovery
questions in `evals/generated/` remain explicitly unreviewed; they are inputs
for expanding the reviewed corpus, not self-certified ground truth.

Application password protection controls access to the running app. If the
Space repository itself must also be hidden, make the Space private in Hugging
Face settings.
