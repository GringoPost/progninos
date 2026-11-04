# Progniños

Cuenca weather forecasts, checked against what happened. A feasibility prototype for GringoPost.

## What runs every day

GitHub Actions runs `progninos.py` every morning at 07:00 Cuenca time (`.github/workflows/daily.yml`). Each run:

1. Saves today's forecast (Open-Meteo and MET Norway) to `archive/forecast_log.csv`. This is our own forecast record, and it grows every day.
2. Compares the last 7 days of forecasts with past weather and with real airport observations (METAR, Mariscal Lamar).
3. Compares the last full month of rain with the 1991–2020 normal (CHIRPS).
4. Writes the report to `output/<date>/`: `report.html`, `precip_chart.svg`, `lookback.csv` and `checks.json`.
5. Commits the new files back to this repository.

To run it by hand: **Actions → Daily Progniños run → Run workflow**.

## Running it locally

Python 3.9+ with no extra packages.

```
python progninos.py                      # today
python progninos.py --date 2026-10-20    # any earlier date
```

## Licensing

- The Open-Meteo free API is for **non-commercial use only**. Before anything is published on GringoPost, add a commercial key as the repository secret `OPEN_METEO_API_KEY` (Settings → Secrets and variables → Actions). The script then switches to the `customer-` endpoints automatically.
- Attribution required on every page that shows the data: "Weather data by Open-Meteo.com", "Data from MET Norway", Copernicus (ERA5) and CHIRPS (UCSB Climate Hazards Center).

## Known limits

- Rain amounts from the weather models are about twice the real rainfall in Cuenca. Use CHIRPS for monthly totals; treat daily and weekly model rain as relative.
- Airport METAR gives temperature and rain / no-rain codes only, about 06:00–22:00.
- CHIRPS arrives 3–6 weeks after the month ends; the IRI server is slow, so past years are cached in `cache/`.
