# Progniños

Local weather forecasts, checked against what happened, for GringoPost's cities: Cuenca, Quito, Ajijic and Da Nang.

Runs entirely on free data sources that allow commercial use with attribution. Monthly cost: $0.

## What runs every day

GitHub Actions runs `progninos.py` once a day at 12:00 UTC (07:00 in Ecuador, 06:00 in Ajijic, 19:00 in Da Nang; `.github/workflows/daily.yml`). For each city it:

1. Saves today's MET Norway forecast to `archive/<city>/forecast_log.csv`. This is our own forecast record, and it grows every day.
2. Compares the forecasts we saved a day in advance with real airport observations (METAR) for the last 7 days.
3. Compares the last 12 months of rain with the 1991–2020 normal (CHIRPS).
4. Writes the report to `output/<city>/<date>/`: `report.html`, `precip_chart.svg`, `monthly_chart.svg`, `lookback.csv`, `checks.json`, and `site.html` (the version for the GringoPost page).
5. Commits the new files back to this repository.
6. If every check passed, copies `site.html` to `output/latest/<city>.html`, which the GringoPost page loads. See [SETUP-WORDPRESS.md](SETUP-WORDPRESS.md).

If any automatic check fails, the report is marked "Needs human review".

To run it by hand: **Actions → Daily Progniños run → Run workflow**.

## Running it locally

Python 3.9+ with no extra packages.

```
python progninos.py                      # all cities, today (also saves today's forecasts)
python progninos.py --city quito         # one city
python progninos.py --date 2026-11-10    # an earlier date (does not touch the archive)
```

## Data sources and attribution

| Data | Source | Attribution |
|---|---|---|
| Forecast | MET Norway Locationforecast | "Data from MET Norway", CC BY 4.0 |
| Past forecasts | Our own archive | — |
| Temperature, rain / no rain | Airport METAR (SECU, SEQM, MMGL, VVDN) via Iowa Environmental Mesonet | — |
| El Niño status | NOAA Climate Prediction Center | public domain |
| Monthly rain and normals | CHIRPS v2.0 via IRI Data Library | UCSB Climate Hazards Center |

MET Norway asks for an identifying User-Agent (set in `MET_UA`) and no more than 20 requests per second; we make one request a day.

## Adding a city

Add one entry to `LOCATIONS` at the top of `progninos.py` (coordinates, time zone, nearest airport METAR code, plausible temperature and rain ranges, and a short El Niño note), run it once locally so the CHIRPS years are cached, and commit `cache/<city>/`. The city's page shows "not available" until its own forecast archive covers a full week.

## Known limits

- No chance-of-rain figure: MET Norway does not provide one outside the Nordic region.
- Forecast rain amounts run about twice too high in Cuenca, so readers only see rain described in words.
- Airport METAR gives temperature and rain / no-rain codes only, in whole degrees. Cuenca's airport reports only about 06:00–22:00. Quito's airport is 18 km from the city and about 450 m lower; Ajijic uses Guadalajara airport, 25 km away.
- There is no free daily rain-gauge measurement. CHIRPS is reliable for months, not single days, and arrives 3–6 weeks after the month ends.
- The IRI server is slow, so complete CHIRPS years are cached in `cache/`.
- `archive/forecast_log.csv` also contains a few Open-Meteo rows from the first test runs; the script ignores them.
