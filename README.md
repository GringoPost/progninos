# Progniños

Cuenca weather forecasts, checked against what happened. A feasibility prototype for GringoPost.

Runs entirely on free data sources that allow commercial use with attribution. Monthly cost: $0.

## What runs every day

GitHub Actions runs `progninos.py` every morning at 07:00 Cuenca time (`.github/workflows/daily.yml`). Each run:

1. Saves today's MET Norway forecast to `archive/forecast_log.csv`. This is our own forecast record, and it grows every day.
2. Compares the forecasts we saved a day in advance with real airport observations (METAR, Mariscal Lamar) for the last 7 days.
3. Compares the last 12 months of rain with the 1991–2020 normal (CHIRPS).
4. Writes the report to `output/<date>/`: `report.html`, `precip_chart.svg`, `monthly_chart.svg`, `lookback.csv` and `checks.json`.
5. Commits the new files back to this repository.

If any automatic check fails, the report is marked "Needs human review".

To run it by hand: **Actions → Daily Progniños run → Run workflow**.

## Running it locally

Python 3.9+ with no extra packages.

```
python progninos.py                      # today (also saves today's forecast to the archive)
python progninos.py --date 2026-11-10    # an earlier date (does not touch the archive)
```

## Data sources and attribution

| Data | Source | Attribution |
|---|---|---|
| Forecast | MET Norway Locationforecast | "Data from MET Norway", CC BY 4.0 |
| Past forecasts | Our own archive | — |
| Temperature, rain / no rain | METAR from SECU via Iowa Environmental Mesonet | — |
| Monthly rain and normals | CHIRPS v2.0 via IRI Data Library | UCSB Climate Hazards Center |

MET Norway asks for an identifying User-Agent (set in `MET_UA`) and no more than 20 requests per second; we make one request a day.

## Known limits

- No chance-of-rain figure: MET Norway does not provide one for Ecuador.
- Forecast rain amounts run high in Cuenca, so the report describes them in broad terms.
- Airport METAR gives temperature and rain / no-rain codes only, about 06:00–22:00, in whole degrees.
- There is no free daily rain-gauge measurement. CHIRPS is reliable for months, not single days, and arrives 3–6 weeks after the month ends.
- The IRI server is slow, so complete CHIRPS years are cached in `cache/`.
- `archive/forecast_log.csv` also contains a few Open-Meteo rows from the first test runs; the script ignores them.
