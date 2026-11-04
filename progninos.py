"""Progniños feasibility script: Cuenca forecasts vs. past weather.

Standard library only (Python 3.9+). Usage:
    python progninos.py                     # report for "today" (look-back ends yesterday)
    python progninos.py --date 2026-10-30   # rerun for any date (look-back = 7 days before it)
    python progninos.py --apikey KEY        # use the commercial customer-* endpoints

Outputs go to ./output/<date>/ : report.html, precip_chart.svg, lookback.csv, checks.json
Every run also appends the live forecast to ./archive/forecast_log.csv (our own forecast record).
"""
import argparse
import csv
import datetime as dt
import json
import os
import statistics
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

# ---- Fixed location (Cuenca, Parque Calderón area) --------------------------
LAT, LON, TZ = -2.9001, -79.0059, "America/Guayaquil"
STATION = "SECU"            # Mariscal Lamar airport, ~2 km NE of the point above
FORECAST_MODEL = "best_match"  # what the public Open-Meteo forecast shows
NORMAL_YEARS = (1991, 2020)    # WMO standard reference period, from ERA5
RAIN_DAY_MM = 1.0              # threshold for calling a day "rainy"

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
ARCHIVE = HERE / "archive"


# ---- HTTP helpers -----------------------------------------------------------
def endpoint(host, path, apikey):
    return f"https://{'customer-' if apikey else ''}{host}{path}"


def get_json(url, params, apikey=None, retries=3):
    params = dict(params)
    if apikey:
        params["apikey"] = apikey
    full = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(full, timeout=60) as r:
                data = json.load(r)
            if data.get("error"):
                raise RuntimeError(f"API error: {data.get('reason')}")
            return data
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def get_text(url, retries=3):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read().decode("utf-8")
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


BASE = {"latitude": LAT, "longitude": LON, "timezone": TZ}


# ---- Data sources -----------------------------------------------------------
def fetch_outlook(apikey):
    """Next 3 days from the live Forecast API (includes rain probability)."""
    d = get_json(endpoint("api.open-meteo.com", "/v1/forecast", apikey), {
        **BASE, "models": FORECAST_MODEL, "forecast_days": 4,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "precipitation_probability_max",
    }, apikey)
    return d


def fetch_previous_day1(start, end, apikey):
    """Forecasts made ~24 h before each hour (Previous Runs API), aggregated to local days."""
    d = get_json(endpoint("previous-runs-api.open-meteo.com", "/v1/forecast", apikey), {
        **BASE, "models": FORECAST_MODEL, "start_date": start, "end_date": end,
        "hourly": "precipitation_previous_day1,temperature_2m_previous_day1,"
                  "precipitation_probability_previous_day1",
    }, apikey)
    return daily_from_hourly(d["hourly"], {
        "fc_precip": ("precipitation_previous_day1", "sum"),
        "fc_tmax": ("temperature_2m_previous_day1", "max"),
        "fc_tmin": ("temperature_2m_previous_day1", "min"),
        "fc_prob": ("precipitation_probability_previous_day1", "max"),
    }), d


def fetch_historical(start, end, model, apikey):
    d = get_json(endpoint("archive-api.open-meteo.com", "/v1/archive", apikey), {
        **BASE, "models": model, "start_date": start, "end_date": end,
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min",
    }, apikey)
    days = d["daily"]
    return {t: {"precip": p, "tmax": hi, "tmin": lo} for t, p, hi, lo in zip(
        days["time"], days["precipitation_sum"], days["temperature_2m_max"],
        days["temperature_2m_min"])}, d


def fetch_era5_normals_raw(apikey):
    """30 years of ERA5 daily precipitation. Cached: it never changes."""
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"era5_daily_{NORMAL_YEARS[0]}_{NORMAL_YEARS[1]}.json"
    if f.exists():
        return json.loads(f.read_text())
    d = get_json(endpoint("archive-api.open-meteo.com", "/v1/archive", apikey), {
        **BASE, "models": "era5",
        "start_date": f"{NORMAL_YEARS[0]}-01-01", "end_date": f"{NORMAL_YEARS[1]}-12-31",
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min",
    }, apikey)
    f.write_text(json.dumps(d["daily"]))
    return d["daily"]


MET_UA = "progninos-feasibility/0.1 https://gringopost.com"  # MET Norway requires an identifying User-Agent
CHIRPS = "https://iridl.ldeo.columbia.edu/SOURCES/.UCSB/.CHIRPS/.v2p0/.monthly/.global/.precipitation"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fetch_met_norway():
    """Free second-opinion forecast (MET Norway Locationforecast, CC BY 4.0). No rain probability for Ecuador."""
    req = urllib.request.Request(
        f"https://api.met.no/weatherapi/locationforecast/2.0/complete?lat={LAT}&lon={LON}&altitude=2560",
        headers={"User-Agent": MET_UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        ts = json.load(r)["properties"]["timeseries"]
    local = dt.timezone(dt.timedelta(hours=-5))
    out = defaultdict(lambda: {"temps": [], "precip": 0.0})
    for i, t in enumerate(ts):
        u = dt.datetime.fromisoformat(t["time"].replace("Z", "+00:00"))
        day = out[u.astimezone(local).date().isoformat()]
        day["temps"].append(t["data"]["instant"]["details"]["air_temperature"])
        # Hourly steps first, then 6-hourly: count each period once, using the block that matches the step.
        nxt = dt.datetime.fromisoformat(ts[i + 1]["time"].replace("Z", "+00:00")) if i + 1 < len(ts) else None
        step = (nxt - u).total_seconds() / 3600 if nxt else None
        blk = t["data"].get("next_1_hours") if step == 1 else t["data"].get("next_6_hours") if step == 6 else None
        if blk:
            day["precip"] += blk["details"].get("precipitation_amount", 0)
    # Skip the partial last day (fewer than four time steps).
    return {d: {"tmax": max(v["temps"]), "tmin": min(v["temps"]), "precip": round(v["precip"], 1)}
            for d, v in out.items() if len(v["temps"]) >= 4}


def fetch_chirps_year(year, last_month=12):
    """CHIRPS monthly rainfall (satellite + rain gauges, ~5 km) for one year; cached once complete.

    The IRI Data Library returns plain values in time order; long ranges time out, so ask per year.
    """
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"chirps_{year}.json"
    if f.exists():
        return json.loads(f.read_text())
    url = (f"{CHIRPS}/X/{LON}/VALUE/Y/{LAT}/VALUE/T/"
           f"(Jan%20{year})/({MONTHS[last_month - 1]}%20{year})/RANGE/data.tsv")
    vals = [float(x) for x in get_text(url).split()]
    if len(vals) == 12:
        f.write_text(json.dumps(vals))
    return vals


def chirps_normals():
    by_month = defaultdict(list)
    for y in range(NORMAL_YEARS[0], NORMAL_YEARS[1] + 1):
        for m, v in enumerate(fetch_chirps_year(y)):
            by_month[m].append(v)
    out = {}
    for m, vs in by_month.items():
        q = statistics.quantiles(vs, n=4)
        out[m] = {"mean": round(statistics.mean(vs), 1), "p25": round(q[0], 1), "p75": round(q[2], 1),
                  "years": len(vs), "all": sorted(vs)}
    return out


def chirps_latest_month(report):
    """Most recent month CHIRPS has published (it arrives roughly 3-6 weeks after month end)."""
    y, m = report.year, report.month - 1
    for _ in range(4):
        if m == 0:
            y, m = y - 1, 12
        try:
            vals = fetch_chirps_year(y, m)
        except Exception:
            vals = []
        if len(vals) == m:
            return y, m, round(vals[-1], 1)
        m -= 1
    return None


def fetch_station(start, end):
    """Real METAR observations from the airport via Iowa Environmental Mesonet.

    METARs give temperature (whole °C) and weather codes (RA, DZ, TS...) but NOT rain amounts,
    and the airport only reports roughly 06:00-22:00 local time.
    """
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end) + dt.timedelta(days=1)
    url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?" + urllib.parse.urlencode({
        "station": STATION, "data": ["tmpc", "wxcodes"], "tz": TZ, "format": "onlycomma",
        "missing": "M", "year1": s.year, "month1": s.month, "day1": s.day,
        "year2": e.year, "month2": e.month, "day2": e.day}, doseq=True))
    rows = list(csv.DictReader(get_text(url).splitlines()))
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["valid"][:10]].append(r)
    out = {}
    for day, rs in by_day.items():
        temps = [float(r["tmpc"]) for r in rs if r["tmpc"] not in ("M", "")]
        codes = {r["wxcodes"] for r in rs if r["wxcodes"] not in ("M", "")}
        rain = any(any(k in c.replace("VCSH", "") for k in ("RA", "DZ", "SH")) for c in codes)
        out[day] = {"tmax": max(temps) if temps else None, "tmin": min(temps) if temps else None,
                    "reports": len(rs), "rain_reported": rain, "codes": sorted(codes)}
    return out


# ---- Processing -------------------------------------------------------------
def daily_from_hourly(hourly, spec):
    """Aggregate local-time hourly series to calendar days. A day needs all 24 hours."""
    out = defaultdict(dict)
    times = hourly["time"]
    for name, (var, how) in spec.items():
        vals = hourly.get(var, [])
        groups = defaultdict(list)
        for t, v in zip(times, vals):
            groups[t[:10]].append(v)
        for day, vs in groups.items():
            ok = [v for v in vs if v is not None]
            if len(ok) < 24:
                out[day][name] = None
            elif how == "sum":
                out[day][name] = round(sum(ok), 1)
            else:
                out[day][name] = (max if how == "max" else min)(ok)
    return dict(out)


def normals_for(days, era5):
    """Climatology for the given calendar days from ERA5 1991-2020.

    Daily normal = mean precipitation over all years for a ±7-day window around the calendar day
    (smooths the very noisy single-day average). Weekly normal = distribution of the 7-day totals
    for the same calendar dates in each year (mean, 25th and 75th percentile).
    """
    by_md = defaultdict(list)
    series = dict(zip(era5["time"], era5["precipitation_sum"]))
    for t, p in series.items():
        if p is not None:
            by_md[t[5:]].append(p)

    def window_mean(day):
        d = dt.date.fromisoformat(day)
        vals = []
        for k in range(-7, 8):
            md = (d + dt.timedelta(days=k)).strftime("%m-%d")
            vals += by_md.get(md, [])
        return round(statistics.mean(vals), 1) if vals else None

    daily = {d: window_mean(d) for d in days}
    totals = []
    for y in range(NORMAL_YEARS[0], NORMAL_YEARS[1] + 1):
        vals = []
        for d in days:
            md = d[5:]
            if md == "02-29":
                continue
            vals.append(series.get(f"{y}-{md}"))
        if all(v is not None for v in vals):
            totals.append(sum(vals))
    totals.sort()
    q = statistics.quantiles(totals, n=4) if len(totals) >= 4 else [None] * 3
    rank = None
    return daily, {"mean": round(statistics.mean(totals), 1), "p25": round(q[0], 1),
                   "p75": round(q[2], 1), "years": len(totals), "all": totals}


def mean_or_none(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 1) if xs else None


def accuracy(rows):
    def diffs(a, b):
        return [r[a] - r[b] for r in rows if r.get(a) is not None and r.get(b) is not None]
    res = {}
    for key, fc, obs in [("tmax_vs_model", "fc_tmax", "hist_tmax"),
                         ("tmin_vs_model", "fc_tmin", "hist_tmin"),
                         ("tmax_vs_station", "fc_tmax", "stn_tmax"),
                         ("tmin_vs_station", "fc_tmin", "stn_tmin"),
                         ("precip_vs_model", "fc_precip", "hist_precip")]:
        d = diffs(fc, obs)
        res[key] = {"n": len(d), "mae": mean_or_none([abs(x) for x in d]), "bias": mean_or_none(d)}
    hits = [(r["fc_precip"] >= RAIN_DAY_MM) == r["stn_rain"] for r in rows
            if r.get("fc_precip") is not None and r.get("stn_rain") is not None]
    res["rain_yes_no_vs_station"] = {"n": len(hits), "correct": sum(hits)}
    hits = [(r["fc_precip"] >= RAIN_DAY_MM) == (r["hist_precip"] >= RAIN_DAY_MM) for r in rows
            if r.get("fc_precip") is not None and r.get("hist_precip") is not None]
    res["rain_yes_no_vs_model"] = {"n": len(hits), "correct": sum(hits)}
    fc = sum(r["fc_precip"] for r in rows if r.get("fc_precip") is not None)
    ob = sum(r["hist_precip"] for r in rows if r.get("hist_precip") is not None)
    res["precip_total"] = {"forecast": round(fc, 1), "past": round(ob, 1)}
    return res


def quality_checks(rows, outlook, hist_meta):
    """Automatic sanity checks. Any 'fail' means the report should go to human review."""
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "status": "pass" if ok else "fail", "detail": detail})

    missing = [r["date"] for r in rows if None in (r["fc_precip"], r["hist_precip"],
                                                    r["fc_tmax"], r["hist_tmax"])]
    add("complete_lookback", not missing, f"days with missing values: {missing or 'none'}")
    odd = [r["date"] for r in rows for k in ("fc_precip", "hist_precip")
           if r.get(k) is not None and not (0 <= r[k] <= 80)]
    add("precip_plausible", not odd, "daily rain between 0 and 80 mm" + (f"; outside: {odd}" if odd else ""))
    odd = [r["date"] for r in rows for k in ("fc_tmax", "hist_tmax", "fc_tmin", "hist_tmin")
           if r.get(k) is not None and not (-2 <= r[k] <= 32)]
    add("temp_plausible", not odd, "temperatures between -2 and 32 °C" + (f"; outside: {odd}" if odd else ""))
    dist = abs(hist_meta["latitude"] - LAT) + abs(hist_meta["longitude"] - LON)
    add("same_grid_point", dist < 0.1, f"historical grid point {hist_meta['latitude']}, "
        f"{hist_meta['longitude']} (elev. {hist_meta['elevation']} m)")
    add("timezone", hist_meta.get("timezone") == TZ and outlook.get("timezone") == TZ,
        "all requests in America/Guayaquil")
    stn_days = sum(1 for r in rows if r.get("stn_tmax") is not None)
    add("station_available", stn_days >= 5, f"airport METAR data on {stn_days}/{len(rows)} days")
    return checks


# ---- Output -----------------------------------------------------------------
def svg_chart(rows):
    """Grouped bars per day: forecast (24 h ahead), past weather (model), normal."""
    W, H, L, R, T, B = 720, 320, 48, 16, 40, 56
    series = [("fc_precip", "Forecast 24 h ahead", "#2f6fb2"),
              ("hist_precip", "Past weather (model-estimated)", "#e08a2c"),
              ("normal_precip", "Normal (ERA5 1991–2020)", "#9a9a9a")]
    top = max([r[k] or 0 for r in rows for k, _, _ in series] + [5])
    top = (int(top / 5) + 1) * 5
    pw, ph = W - L - R, H - T - B
    gw = pw / len(rows)
    bw = gw * 0.8 / 3
    y = lambda v: T + ph - v / top * ph
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
           f'font-family="system-ui, sans-serif" font-size="12" role="img" '
           f'aria-label="Daily rainfall: forecast vs past weather vs normal">',
           f'<rect width="{W}" height="{H}" fill="#fff"/>']
    for v in range(0, top + 1, 5):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#e5e5e5"/>')
        out.append(f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end" fill="#555">{v}</text>')
    out.append(f'<text x="12" y="{T - 14}" fill="#333">mm</text>')
    for i, r in enumerate(rows):
        x0 = L + i * gw + gw * 0.1
        for j, (k, _, c) in enumerate(series):
            v = r.get(k)
            if v is None:
                continue
            out.append(f'<rect x="{x0 + j * bw:.1f}" y="{y(v):.1f}" width="{bw - 2:.1f}" '
                       f'height="{max(y(0) - y(v), 0.5):.1f}" fill="{c}"><title>{v} mm</title></rect>')
        d = dt.date.fromisoformat(r["date"])
        out.append(f'<text x="{L + i * gw + gw / 2:.1f}" y="{H - B + 18}" text-anchor="middle" '
                   f'fill="#333">{d.strftime("%a %d")}</text>')
    lx = L
    for _, label, c in series:
        out.append(f'<rect x="{lx}" y="{H - 20}" width="12" height="12" fill="{c}"/>'
                   f'<text x="{lx + 16}" y="{H - 10}" fill="#333">{label}</text>')
        lx += 16 + len(label) * 6.6 + 18
    out.append("</svg>")
    return "\n".join(out)


def fmt(v, unit="", digits=1):
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}{unit}"


def signed(v, unit=""):
    return "n/a" if v is None else f"{v:+.1f}{unit}"


def describe_day(tmax, tmin, rain, prob):
    if prob is None:
        chance = ""
    elif prob >= 70:
        chance = "Rain is likely"
    elif prob >= 40:
        chance = "Showers are possible"
    else:
        chance = "Rain is unlikely"
    amount = ("little or no rain expected" if rain < 1 else
              "light rain amounts" if rain < 5 else
              "moderate rain amounts" if rain < 15 else "heavy rain amounts")
    return f"{chance} ({prob}% chance), {amount}." if chance else f"{amount.capitalize()}."


def compare_phrase(total, wk):
    if total < wk["p25"]:
        return "drier than usual"
    if total > wk["p75"]:
        return "wetter than usual"
    return "within the normal range"


def month_html(monthly):
    if not monthly:
        return "<p>CHIRPS monthly data was not available for this run.</p>"
    y, m, total, nm = monthly
    below = sum(1 for v in nm["all"] if v < total)
    return (f"<p>Rain in Cuenca in <b>{MONTHS[m - 1]} {y}</b>: <b>{total:.0f} mm</b>. The 1991–2020 average for "
            f"{MONTHS[m - 1]} is <b>{nm['mean']:.0f} mm</b> ({round(100 * total / nm['mean'])}% of normal); the middle half "
            f"of years fell between {nm['p25']:.0f} and {nm['p75']:.0f} mm. That makes it "
            f"<b>{compare_phrase(total, nm)}</b>, wetter than {below} of {nm['years']} reference years.</p>"
            f"<p class=\"note\">Source: CHIRPS, which combines satellite data with rain-gauge records (about 5 km). "
            f"Unlike the weather models, its long-term average matches the roughly 800–900 mm a year published for "
            f"Cuenca. It is published 3–6 weeks after the month ends and is not reliable for single days.</p>")


def html_report(report_date, outlook, rows, acc, wk, checks, chart_svg, sources_note, met=None, monthly=None):
    met = met or {}
    d0 = dt.date.fromisoformat(report_date)
    od = outlook["daily"]
    ahead = []
    for i, t in enumerate(od["time"]):
        if t <= report_date and i < len(od["time"]) - 3:
            continue
        if len(ahead) == 3:
            break
        ahead.append((t, od["temperature_2m_max"][i], od["temperature_2m_min"][i],
                      od["precipitation_sum"][i], od["precipitation_probability_max"][i]))
    total_past = acc["precip_total"]["past"]
    n_back = len(rows)
    pct = round(100 * total_past / wk["mean"]) if wk["mean"] else None
    rank_below = sum(1 for x in wk["all"] if x < total_past)
    tm, tn = acc["tmax_vs_model"], acc["tmax_vs_station"]
    rn = acc["rain_yes_no_vs_station"]
    failed = [c for c in checks if c["status"] == "fail"]

    def tr(cells, head=False):
        tag = "th" if head else "td"
        return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"

    ahead_rows = "".join(tr([dt.date.fromisoformat(t).strftime("%a %b %d"), fmt(hi, "°C"),
                             fmt(lo, "°C"), f"{p}%" if p is not None else "n/a", fmt(r, " mm"),
                             fmt(met.get(t, {}).get("precip"), " mm"),
                             describe_day(hi, lo, r, p)]) for t, hi, lo, r, p in ahead)
    back_rows = "".join(tr([
        dt.date.fromisoformat(r["date"]).strftime("%a %b %d"),
        fmt(r["fc_precip"], " mm"), fmt(r["hist_precip"], " mm"),
        signed(None if None in (r["fc_precip"], r["hist_precip"]) else r["hist_precip"] - r["fc_precip"], " mm"),
        fmt(r["normal_precip"], " mm"),
        "yes" if r["stn_rain"] else ("no" if r["stn_rain"] is not None else "n/a"),
        f'{fmt(r["fc_tmax"], "°", 0)} / {fmt(r["hist_tmax"], "°", 0)} / {fmt(r["stn_tmax"], "°", 0)}',
        f'{fmt(r["fc_tmin"], "°", 0)} / {fmt(r["hist_tmin"], "°", 0)} / {fmt(r["stn_tmin"], "°", 0)}',
    ]) for r in rows)
    check_rows = "".join(tr([c["check"], c["status"].upper(), c["detail"]]) for c in checks)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Progniños – Cuenca – {report_date}</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#222;line-height:1.5}}
h1{{margin-bottom:0}} .sub{{color:#666;margin-top:4px}} table{{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}}
th,td{{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}} th{{background:#f4f4f4}}
.wrap{{overflow-x:auto}} .note{{font-size:13px;color:#555}} .flag{{background:#fff4e5;border-left:4px solid #e08a2c;padding:8px 12px}}
svg{{width:100%;height:auto}}
</style></head><body>
<h1>Progniños</h1>
<p class="sub">Cuenca weather forecasts, checked against what happened. Report for {d0.strftime('%A, %B %d, %Y')}.</p>
{'<p class="flag"><b>Needs human review:</b> ' + ', '.join(c['check'] for c in failed) + '</p>' if failed else ''}

<h2>A. Looking ahead – next 3 days</h2>
<div class="wrap"><table>{tr(["Day", "High", "Low", "Chance of rain", "Expected rain", "Second opinion (MET Norway)", "Summary"], True)}{ahead_rows}</table></div>
<p class="note">"Chance of rain" is the probability of any rain at some point in the day. It is not an amount:
an 80% chance can still mean only 1–2 mm.</p>

<h2>B. Looking back – last {n_back} days</h2>
<div class="chart">{chart_svg}</div>
<div class="wrap"><table>{tr(["Day", "Forecast rain (24 h ahead)", "Past weather rain*", "Difference", "Normal for date**", "Airport reported rain?", "High °C: forecast / past* / airport", "Low °C: forecast / past* / airport"], True)}{back_rows}</table></div>
<p class="note">* "Past weather" is a model-based estimate (Open-Meteo Historical Weather API), not a rain gauge reading.
No public rain-gauge data for Cuenca was available automatically. ** ERA5 reanalysis average for 1991–2020, ±7-day window.
Airport = real observations from Mariscal Lamar airport (METAR), reported about 06:00–22:00 only, in whole degrees.</p>

<h2>C. Compared with normal</h2>
<p>Model-estimated rain over these {n_back} days: <b>{fmt(total_past, ' mm')}</b>. The 1991–2020 average for the same dates is
<b>{fmt(wk['mean'], ' mm')}</b> ({pct}% of normal), and the middle half of years fell between {fmt(wk['p25'], ' mm')} and
{fmt(wk['p75'], ' mm')}. That puts this week <b>{compare_phrase(total_past, wk)}</b>: wetter than {rank_below} of {wk['years']} years in the
reference period.</p>
<p class="note">Caution: both numbers come from weather models, and they come from two different models (IFS for this week,
ERA5 for the normal). For Cuenca both models give roughly 1,800–2,000 mm of rain a year, about twice the
800–900 mm usually published for the city. Read rain amounts as relative ("wetter or drier than the model's normal"), not as exact millimetres.</p>
<h3>Last full month</h3>
{month_html(monthly)}

<h2>D. Forecast accuracy</h2>
<ul>
<li>Daytime high, forecast vs airport: off by {fmt(tn['mae'], '°C')} on average (forecasts ran {fmt(abs(tn['bias'] or 0), '°C')} {'too cool' if (tn['bias'] or 0) < 0 else 'too warm'}; {tn['n']} days).</li>
<li>Daytime high, forecast vs model past weather: off by {fmt(tm['mae'], '°C')} on average ({tm['n']} days).</li>
<li>Rain or no rain (≥{RAIN_DAY_MM:.0f} mm), forecast vs airport reports: right on {rn['correct']} of {rn['n']} days.</li>
<li>Rain amount: forecast {fmt(acc['precip_total']['forecast'], ' mm')} in total vs {fmt(total_past, ' mm')} model-estimated
(average daily miss {fmt(acc['precip_vs_model']['mae'], ' mm')}).</li>
</ul>
<p class="note">Seven days is too short to judge any forecast. These scores become meaningful after several months.</p>

<h2>E. Data sources</h2>
<ul>
<li>Forecasts: Open-Meteo Forecast API and Previous Runs API (model: {FORECAST_MODEL}). "24 h ahead" means the value the model
predicted 24 hours before each hour, not one specific published forecast.</li>
<li>Past weather: Open-Meteo Historical Weather API. For recent days this is the ECMWF IFS model, about 9 km resolution.</li>
<li>Normals: ERA5 reanalysis (Copernicus Climate Change Service), 1991–2020, ~25 km resolution.</li>
<li>Second-opinion forecast: MET Norway Locationforecast (Data from MET Norway, CC BY 4.0).</li>
<li>Monthly rainfall: CHIRPS v2.0 (UC Santa Barbara Climate Hazards Center), via the IRI Data Library.</li>
<li>Airport observations: METAR reports from SECU via Iowa Environmental Mesonet.</li>
<li>Location: {LAT}, {LON} (central Cuenca), time zone {TZ}. Weather can differ a lot across the city.</li>
</ul>
<p class="note">{sources_note}</p>
<p class="note"><a href="https://open-meteo.com/">Weather data by Open-Meteo.com</a> (CC BY 4.0).
Contains modified Copernicus Climate Change Service information.</p>

<h2>Automatic quality checks (internal)</h2>
<div class="wrap"><table>{tr(["Check", "Status", "Detail"], True)}{check_rows}</table></div>
</body></html>"""


def archive_forecast(outlook, run_time, met=None):
    """Our own permanent forecast record, appended on every run."""
    ARCHIVE.mkdir(exist_ok=True)
    f = ARCHIVE / "forecast_log.csv"
    new = not f.exists()
    od = outlook["daily"]
    with f.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["retrieved_at_utc", "model", "target_date", "lead_days", "tmax", "tmin",
                        "precip_mm", "precip_prob_max"])
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date()
        for i, t in enumerate(od["time"]):
            w.writerow([run_time, FORECAST_MODEL, t, (dt.date.fromisoformat(t) - today).days,
                        od["temperature_2m_max"][i], od["temperature_2m_min"][i],
                        od["precipitation_sum"][i], od["precipitation_probability_max"][i]])
        for t, v in sorted((met or {}).items()):
            w.writerow([run_time, "met_norway", t, (dt.date.fromisoformat(t) - today).days,
                        v["tmax"], v["tmin"], v["precip"], ""])


# ---- Main -------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="report date YYYY-MM-DD (default: today in Cuenca)")
    ap.add_argument("--days", type=int, default=7, help="look-back length")
    ap.add_argument("--apikey", default=os.environ.get("OPEN_METEO_API_KEY") or None,
                    help="Open-Meteo commercial API key (default: $OPEN_METEO_API_KEY)")
    a = ap.parse_args()

    now_utc = dt.datetime.now(dt.timezone.utc)
    today = now_utc.astimezone(dt.timezone(dt.timedelta(hours=-5))).date()
    report = dt.date.fromisoformat(a.date) if a.date else today
    end = report - dt.timedelta(days=1)
    start = end - dt.timedelta(days=a.days - 1)
    s, e = start.isoformat(), end.isoformat()
    days = [(start + dt.timedelta(days=i)).isoformat() for i in range(a.days)]

    print(f"Report {report} | look-back {s} .. {e}")
    outlook = fetch_outlook(a.apikey)
    try:
        met = fetch_met_norway()
    except Exception as exc:
        print(f"MET Norway unavailable: {exc}")
        met = {}
    if report == today:
        archive_forecast(outlook, now_utc.strftime("%Y-%m-%dT%H:%MZ"), met)
    try:
        latest = chirps_latest_month(report)
        monthly = (*latest, chirps_normals()[latest[1] - 1]) if latest else None
    except Exception as exc:
        print(f"CHIRPS unavailable: {exc}")
        monthly = None
    fc, _ = fetch_previous_day1(s, e, a.apikey)
    hist, hist_meta = fetch_historical(s, e, "best_match", a.apikey)
    era5_recent, _ = fetch_historical(s, e, "era5", a.apikey)
    stn = fetch_station(s, e)
    normals, wk = normals_for(days, fetch_era5_normals_raw(a.apikey))

    rows = []
    for d in days:
        f, h, st = fc.get(d, {}), hist.get(d, {}), stn.get(d, {})
        rows.append({
            "date": d, "fc_precip": f.get("fc_precip"), "hist_precip": h.get("precip"),
            "era5_precip": era5_recent.get(d, {}).get("precip"), "normal_precip": normals[d],
            "fc_prob": f.get("fc_prob"),
            "fc_tmax": f.get("fc_tmax"), "hist_tmax": h.get("tmax"), "stn_tmax": st.get("tmax"),
            "fc_tmin": f.get("fc_tmin"), "hist_tmin": h.get("tmin"), "stn_tmin": st.get("tmin"),
            "stn_rain": st.get("rain_reported"), "stn_codes": " ".join(st.get("codes", [])),
        })
    acc = accuracy(rows)
    checks = quality_checks(rows, outlook, hist_meta)

    era5_pairs = [(r["hist_precip"], r["era5_precip"]) for r in rows
                  if r["era5_precip"] is not None and r["hist_precip"] is not None]
    note = ("ERA5 reanalysis arrives about 5–7 days late, so recent days use the IFS model. "
            f"On {len(era5_pairs)} overlapping days the two models' rain totals were "
            f"{sum(p[0] for p in era5_pairs):.1f} mm (IFS) vs {sum(p[1] for p in era5_pairs):.1f} mm (ERA5)."
            if era5_pairs else "ERA5 reanalysis arrives about 5–7 days late; recent days use the IFS model.")

    out = HERE / "output" / report.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    chart = svg_chart(rows)
    (out / "precip_chart.svg").write_text(chart, encoding="utf-8")
    with (out / "lookback.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "checks.json").write_text(json.dumps(
        {"accuracy": acc, "week_normal": {k: v for k, v in wk.items() if k != "all"},
         "checks": checks, "sources_note": note}, indent=2), encoding="utf-8")
    (out / "report.html").write_text(
        html_report(report.isoformat(), outlook, rows, acc, wk, checks, chart, note, met, monthly),
        encoding="utf-8")
    if monthly:
        y, m, total, nm = monthly
        print(f"CHIRPS {MONTHS[m - 1]} {y}: {total} mm vs normal {nm['mean']} mm ({nm['p25']}-{nm['p75']})")

    print(json.dumps(acc, indent=1))
    for c in checks:
        print(f"[{c['status']}] {c['check']}: {c['detail']}")
    print(f"Written to {out}")


if __name__ == "__main__":
    main()
