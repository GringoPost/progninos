"""Progniños: Cuenca forecasts, checked against what happened. Free data sources only.

Standard library only (Python 3.9+). Usage:
    python progninos.py                     # report for today (look-back ends yesterday)
    python progninos.py --date 2026-11-10   # rerun for an earlier date

Sources, all free and open to commercial use with attribution:
    Forecast      MET Norway Locationforecast (CC BY 4.0)
    Past forecast our own archive of MET Norway forecasts (archive/forecast_log.csv)
    Observations  Mariscal Lamar airport METAR via Iowa Environmental Mesonet
    Rainfall      CHIRPS v2.0 monthly (satellite + rain gauges) via the IRI Data Library

Outputs go to ./output/<date>/ : report.html, precip_chart.svg, monthly_chart.svg, lookback.csv, checks.json
"""
import argparse
import calendar
import csv
import datetime as dt
import gzip
import json
import statistics
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

# ---- Fixed location (Cuenca, Parque Calderón area) --------------------------
LAT, LON, TZ = -2.9001, -79.0059, "America/Guayaquil"   # MET Norway accepts at most 4 decimals
ALTITUDE = 2560
STATION = "SECU"            # Mariscal Lamar airport, ~2 km NE of the point above
NORMAL_YEARS = (1991, 2020)    # WMO standard reference period
RAIN_DAY_MM = 1.0              # threshold for calling a day "rainy"
LOCAL = dt.timezone(dt.timedelta(hours=-5))   # Ecuador has no daylight saving time

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
ARCHIVE = HERE / "archive"
LOG = ARCHIVE / "forecast_log.csv"

MET_UA = "progninos/1.0 https://gringopost.com"  # MET Norway requires an identifying User-Agent
CHIRPS = "https://iridl.ldeo.columbia.edu/SOURCES/.UCSB/.CHIRPS/.v2p0/.monthly/.global/.precipitation"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---- HTTP helper ------------------------------------------------------------
def get_text(url, headers=None, retries=3):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return body.decode("utf-8")
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


# ---- Data sources -----------------------------------------------------------
def fetch_met_norway():
    """Daily forecast from MET Norway Locationforecast. No rain probability is given for Ecuador."""
    url = f"https://api.met.no/weatherapi/locationforecast/2.0/complete?lat={LAT}&lon={LON}&altitude={ALTITUDE}"
    ts = json.loads(get_text(url, {"User-Agent": MET_UA}))["properties"]["timeseries"]
    out = defaultdict(lambda: {"temps": [], "precip": 0.0})
    for i, t in enumerate(ts):
        u = dt.datetime.fromisoformat(t["time"].replace("Z", "+00:00"))
        day = out[u.astimezone(LOCAL).date().isoformat()]
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
            by_month[m + 1].append(v)
    out = {}
    for m, vs in by_month.items():
        q = statistics.quantiles(vs, n=4)
        out[m] = {"mean": round(statistics.mean(vs), 1), "p25": round(q[0], 1), "p75": round(q[2], 1),
                  "years": len(vs), "all": sorted(vs)}
    return out


def chirps_recent(report, n=12):
    """The last n months CHIRPS has published, as [(year, month, mm)], oldest first.

    CHIRPS arrives roughly 3-6 weeks after the month ends, so look back from last month until data appears.
    """
    y, m = report.year, report.month - 1
    current = []
    for _ in range(4):
        if m == 0:
            y, m = y - 1, 12
        try:
            vals = fetch_chirps_year(y, m)
        except Exception:
            vals = []
        if len(vals) >= m:
            current = vals[:m]
            break
        m -= 1
    if not current:
        return []
    months = [(y, i + 1, round(v, 1)) for i, v in enumerate(current)]
    py = y - 1
    while len(months) < n:
        prev = fetch_chirps_year(py)
        months = [(py, i + 1, round(v, 1)) for i, v in enumerate(prev)] + months
        py -= 1
    return months[-n:]


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


# ---- Forecast archive -------------------------------------------------------
def archive_forecast(met, run_time, today):
    """Our own permanent forecast record, appended on every run."""
    ARCHIVE.mkdir(exist_ok=True)
    new = not LOG.exists()
    with LOG.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["retrieved_at_utc", "model", "target_date", "lead_days", "tmax", "tmin",
                        "precip_mm", "precip_prob_max"])
        for t, v in sorted(met.items()):
            w.writerow([run_time, "met_norway", t, (dt.date.fromisoformat(t) - today).days,
                        v["tmax"], v["tmin"], v["precip"], ""])


def archived_forecasts(days, lead=1):
    """For each day, the MET Norway forecast we saved `lead` days earlier (latest save wins)."""
    out, first = {}, None
    if not LOG.exists():
        return out, first
    with LOG.open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r["model"] != "met_norway":
                continue
            saved = r["retrieved_at_utc"][:10]
            first = saved if first is None else min(first, saved)
            if r["target_date"] in days and int(r["lead_days"]) == lead:
                out[r["target_date"]] = {"tmax": float(r["tmax"]), "tmin": float(r["tmin"]),
                                         "precip": float(r["precip_mm"])}
    return out, first


# ---- Processing -------------------------------------------------------------
def mean_or_none(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 1) if xs else None


def accuracy(rows):
    def diffs(a, b):
        return [r[a] - r[b] for r in rows if r.get(a) is not None and r.get(b) is not None]
    res = {}
    for key, fc, obs in [("tmax_vs_station", "fc_tmax", "stn_tmax"), ("tmin_vs_station", "fc_tmin", "stn_tmin")]:
        d = diffs(fc, obs)
        res[key] = {"n": len(d), "mae": mean_or_none([abs(x) for x in d]), "bias": mean_or_none(d)}
    hits = [(r["fc_precip"] >= RAIN_DAY_MM) == r["stn_rain"] for r in rows
            if r.get("fc_precip") is not None and r.get("stn_rain") is not None]
    res["rain_yes_no_vs_station"] = {"n": len(hits), "correct": sum(hits)}
    res["forecast_rain_total"] = round(sum(r["fc_precip"] for r in rows if r.get("fc_precip") is not None), 1)
    return res


def quality_checks(rows, met, recent, archive_start):
    """Automatic sanity checks. Any 'fail' means the report should go to human review."""
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "status": "pass" if ok else "fail", "detail": detail})

    add("forecast_available", len(met) >= 3, f"MET Norway forecast for {len(met)} days")
    # Days before the archive started cannot have a saved forecast; only later gaps are a problem.
    missing = [r["date"] for r in rows if r["fc_precip"] is None]
    gaps = [d for d in missing if archive_start and d > archive_start]
    add("archive_complete", not gaps,
        f"missing saved forecasts: {gaps or 'none'}"
        + (f"; {len(missing) - len(gaps)} day(s) before the archive started on {archive_start}" if len(missing) > len(gaps) else ""))
    odd = [r["date"] for r in rows if r["fc_precip"] is not None and not (0 <= r["fc_precip"] <= 80)]
    odd += [d for d, v in met.items() if not (0 <= v["precip"] <= 80)]
    add("precip_plausible", not odd, "daily rain between 0 and 80 mm" + (f"; outside: {odd}" if odd else ""))
    odd = [d for d, v in met.items() if not (-2 <= v["tmin"] <= v["tmax"] <= 32)]
    odd += [r["date"] for r in rows for k in ("stn_tmax", "stn_tmin") if r[k] is not None and not (-2 <= r[k] <= 32)]
    add("temp_plausible", not odd, "temperatures between -2 and 32 °C" + (f"; outside: {odd}" if odd else ""))
    stn_days = sum(1 for r in rows if r.get("stn_tmax") is not None)
    add("station_available", stn_days >= 5, f"airport METAR data on {stn_days}/{len(rows)} days")
    add("chirps_available", bool(recent),
        f"CHIRPS latest month: {MONTHS[recent[-1][1] - 1]} {recent[-1][0]}" if recent else "no CHIRPS data")
    return checks


# ---- Output -----------------------------------------------------------------
INK, GRID, MUTED = "#222", "#e5e5e5", "#555"
C_FC, C_NORM, C_OBS = "#2f6fb2", "#9a9a9a", "#e08a2c"


def svg_frame(W, H, L, T, ph, top, step, label):
    y = lambda v: T + ph - v / top * ph
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="system-ui, sans-serif" '
           f'font-size="12" role="img" aria-label="{label}">', f'<rect width="{W}" height="{H}" fill="#fff"/>']
    for v in range(0, int(top) + 1, step):
        out.append(f'<line x1="{L}" x2="{W - 16}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{GRID}"/>'
                   f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end" fill="{MUTED}">{v}</text>')
    out.append(f'<text x="8" y="{T - 12}" fill="{INK}">mm</text>')
    return out, y


def legend(out, items, x, y):
    for label, c in items:
        out.append(f'<rect x="{x}" y="{y - 10}" width="12" height="12" fill="{c}"/>'
                   f'<text x="{x + 16}" y="{y}" fill="{INK}">{label}</text>')
        x += 16 + len(label) * 6.6 + 18


def svg_daily_chart(rows):
    """Per day: forecast rain (saved a day ahead), daily share of the CHIRPS monthly normal, airport rain or dry."""
    W, H, L, T, B = 720, 320, 48, 36, 70
    ph = H - T - B
    top = max([r[k] or 0 for r in rows for k in ("fc_precip", "normal_precip")] + [5])
    top = (int(top / 5) + 1) * 5
    out, y = svg_frame(W, H, L, T, ph, top, 5, "Daily rain: forecast vs normal, with airport rain reports")
    gw = (W - L - 16) / len(rows)
    bw = gw * 0.8 / 2
    for i, r in enumerate(rows):
        x0, cx = L + i * gw + gw * 0.1, L + i * gw + gw / 2
        for j, (k, c) in enumerate([("fc_precip", C_FC), ("normal_precip", C_NORM)]):
            v = r.get(k)
            if v is not None:
                out.append(f'<rect x="{x0 + j * bw:.1f}" y="{y(v):.1f}" width="{bw - 2:.1f}" '
                           f'height="{max(y(0) - y(v), 0.5):.1f}" fill="{c}"><title>{v} mm</title></rect>')
        if r["fc_precip"] is None:
            out.append(f'<text x="{x0 + bw / 2:.1f}" y="{y(0) - 4:.1f}" text-anchor="middle" fill="{MUTED}" font-size="10">n/a</text>')
        out.append(f'<text x="{cx:.1f}" y="{H - B + 16}" text-anchor="middle" fill="{INK}">'
                   f'{dt.date.fromisoformat(r["date"]).strftime("%a %d")}</text>')
        rain = r.get("stn_rain")
        mark = "● rain" if rain else "○ dry" if rain is not None else "–"
        out.append(f'<text x="{cx:.1f}" y="{H - B + 32}" text-anchor="middle" fill="{C_OBS if rain else MUTED}" '
                   f'font-size="11">{mark}</text>')
    legend(out, [("Forecast, 1 day ahead", C_FC), ("Normal (CHIRPS 1991–2020)", C_NORM)], L, H - 10)
    out.append(f'<text x="{W - 16}" y="{H - 10}" text-anchor="end" fill="{MUTED}" font-size="11">airport: ● rain ○ dry</text>')
    out.append("</svg>")
    return "\n".join(out)


def svg_monthly_chart(recent, normals):
    """Last 12 months of CHIRPS rainfall against the 1991-2020 normal and its middle-half range."""
    W, H, L, T, B = 720, 300, 48, 36, 54
    ph = H - T - B
    top = max([v for _, _, v in recent] + [normals[m]["p75"] for _, m, _ in recent] + [50])
    top = (int(top / 50) + 1) * 50
    out, y = svg_frame(W, H, L, T, ph, top, 50, "Monthly rain in Cuenca compared with the 1991-2020 normal")
    gw = (W - L - 16) / len(recent)
    for i, (yr, m, v) in enumerate(recent):
        nm = normals[m]
        x0, cx = L + i * gw + gw * 0.18, L + i * gw + gw / 2
        out.append(f'<rect x="{L + i * gw + 3:.1f}" y="{y(nm["p75"]):.1f}" width="{gw - 6:.1f}" '
                   f'height="{y(nm["p25"]) - y(nm["p75"]):.1f}" fill="{GRID}"/>')
        out.append(f'<rect x="{x0:.1f}" y="{y(v):.1f}" width="{gw * 0.64:.1f}" height="{max(y(0) - y(v), 0.5):.1f}" '
                   f'fill="{C_OBS}"><title>{MONTHS[m - 1]} {yr}: {v:.0f} mm (normal {nm["mean"]:.0f})</title></rect>')
        out.append(f'<line x1="{L + i * gw + 3:.1f}" x2="{L + (i + 1) * gw - 3:.1f}" y1="{y(nm["mean"]):.1f}" '
                   f'y2="{y(nm["mean"]):.1f}" stroke="{INK}" stroke-width="2"/>')
        out.append(f'<text x="{cx:.1f}" y="{H - B + 16}" text-anchor="middle" fill="{INK}">{MONTHS[m - 1]}</text>')
        if m == 1 or i == 0:
            out.append(f'<text x="{cx:.1f}" y="{H - B + 30}" text-anchor="middle" fill="{MUTED}" font-size="10">{yr}</text>')
    legend(out, [("Measured rain (CHIRPS)", C_OBS), ("Normal range (middle half of years)", GRID)], L, H - 6)
    out.append(f'<text x="{W - 16}" y="{H - 6}" text-anchor="end" fill="{INK}" font-size="11">— average</text>')
    out.append("</svg>")
    return "\n".join(out)


def fmt(v, unit="", digits=1):
    return "n/a" if v is None else f"{v:.{digits}f}{unit}"


def describe_day(rain):
    """Plain wording. Model rain amounts run high in Cuenca, so describe them in broad, calm terms."""
    if rain < 1:
        return "Mostly dry."
    if rain < 10:
        return "Some rain likely."
    return "A wet day is likely."


def compare_phrase(total, nm):
    if total < nm["p25"]:
        return "drier than usual"
    if total > nm["p75"]:
        return "wetter than usual"
    return "within the normal range"


def month_html(recent, normals):
    if not recent:
        return "<p>CHIRPS monthly data was not available for this run.</p>"
    y, m, total = recent[-1]
    nm = normals[m]
    below = sum(1 for v in nm["all"] if v < total)
    return (f"<p>Rain in Cuenca in <b>{MONTHS[m - 1]} {y}</b>: <b>{total:.0f} mm</b>. The 1991–2020 average for "
            f"{MONTHS[m - 1]} is <b>{nm['mean']:.0f} mm</b> ({round(100 * total / nm['mean'])}% of normal); the middle half "
            f"of years fell between {nm['p25']:.0f} and {nm['p75']:.0f} mm. That makes it "
            f"<b>{compare_phrase(total, nm)}</b>, wetter than {below} of {nm['years']} reference years.</p>")


def report_parts(report, met, rows, acc, checks, daily_svg, monthly_svg, recent, normals, archive_start):
    """Report sections A-E, the review flag and the internal checks table, as separate HTML pieces."""
    ahead = [(d, met[d]) for d in sorted(met) if d > report.isoformat()][:3]
    tn, tl, rn = acc["tmax_vs_station"], acc["tmin_vs_station"], acc["rain_yes_no_vs_station"]
    failed = [c for c in checks if c["status"] == "fail"]

    def tr(cells, head=False):
        tag = "th" if head else "td"
        return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"

    ahead_rows = "".join(tr([dt.date.fromisoformat(d).strftime("%a %b %d"), fmt(v["tmax"], "°C"),
                             fmt(v["tmin"], "°C"), fmt(v["precip"], " mm"), describe_day(v["precip"])])
                         for d, v in ahead)
    back_rows = "".join(tr([
        dt.date.fromisoformat(r["date"]).strftime("%a %b %d"),
        fmt(r["fc_precip"], " mm"), fmt(r["normal_precip"], " mm"),
        "yes" if r["stn_rain"] else ("no" if r["stn_rain"] is not None else "n/a"),
        f'{fmt(r["fc_tmax"], "°", 0)} / {fmt(r["stn_tmax"], "°", 0)}',
        f'{fmt(r["fc_tmin"], "°", 0)} / {fmt(r["stn_tmin"], "°", 0)}',
    ]) for r in rows)
    check_rows = "".join(tr([c["check"], c["status"].upper(), c["detail"]]) for c in checks)
    bias = tn["bias"] or 0
    acc_html = (f"""<ul>
<li>Daytime highs: off by {fmt(tn['mae'], ' °C')} on average compared with the airport
({fmt(abs(bias), ' °C')} {'too cool' if bias < 0 else 'too warm'}; {tn['n']} days).</li>
<li>Night-time lows: off by {fmt(tl['mae'], ' °C')} on average ({tl['n']} days).</li>
<li>Rain or no rain (≥{RAIN_DAY_MM:.0f} mm): right on {rn['correct']} of {rn['n']} days, compared with airport reports.</li>
</ul>""" if tn["n"] else
               f"<p>No saved forecasts yet for these days. Our forecast archive started on {archive_start}; "
               f"scores appear once it covers the last week.</p>")

    flag = ('<p class="flag"><b>Needs human review:</b> ' + ', '.join(c['check'] for c in failed) + '</p>'
            if failed else '')
    checks_html = f"""<h2>Automatic quality checks (internal)</h2>
<div class="wrap"><table>{tr(["Check", "Status", "Detail"], True)}{check_rows}</table></div>"""
    body = f"""<p class="sub">Cuenca weather forecasts, checked against what happened. Report for {report.strftime('%A, %B %d, %Y')}.</p>

<h2>A. Looking ahead – next 3 days</h2>
<div class="wrap"><table>{tr(["Day", "High", "Low", "Forecast rain", "Summary"], True)}{ahead_rows}</table></div>
<p class="note">Forecast models tend to overstate rain amounts in Cuenca, so treat the millimetres as a rough guide.</p>

<h2>B. Looking back – last {len(rows)} days</h2>
<div class="chart">{daily_svg}</div>
<div class="wrap"><table>{tr(["Day", "Forecast rain (1 day ahead)", "Normal for the day*", "Airport reported rain?", "High °C: forecast / airport", "Low °C: forecast / airport"], True)}{back_rows}</table></div>
<p class="note">Forecasts are the ones we saved a day in advance. Airport = real observations from Mariscal Lamar airport (METAR),
reported about 06:00–22:00 only, in whole degrees; they say whether it rained, not how much.
* The month's 1991–2020 CHIRPS average spread evenly across its days.</p>

<h2>C. Compared with normal</h2>
{month_html(recent, normals)}
<div class="chart">{monthly_svg}</div>
<p class="note">Source: CHIRPS, which combines satellite data with rain-gauge records (about 5 km). Its long-term average,
about 890 mm a year, matches published figures for Cuenca. It is published 3–6 weeks after each month ends.</p>

<h2>D. Forecast accuracy</h2>
{acc_html}
<p class="note">A week is too short to judge a forecast. These scores become meaningful after several months.</p>

<h2>E. Data sources</h2>
<ul>
<li>Forecasts: MET Norway Locationforecast. Data from MET Norway (<a href="https://api.met.no/">api.met.no</a>), CC BY 4.0.</li>
<li>Past forecasts: our own daily archive of those forecasts, kept since {archive_start}.</li>
<li>Airport observations: METAR reports from Mariscal Lamar (SECU) via the Iowa Environmental Mesonet.</li>
<li>Monthly rainfall and normals: CHIRPS v2.0, UC Santa Barbara Climate Hazards Center, via the IRI Data Library.</li>
<li>Location: {LAT}, {LON} (central Cuenca), time zone {TZ}. Weather can differ a lot across the city.</li>
</ul>"""
    return body, flag, checks_html


REPORT_CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#222;line-height:1.5}
h1{margin-bottom:0} .sub{color:#666;margin-top:4px} table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}
th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top} th{background:#f4f4f4}
.wrap{overflow-x:auto} .note{font-size:13px;color:#555} .flag{background:#fff4e5;border-left:4px solid #e08a2c;padding:8px 12px}
svg{width:100%;height:auto}
"""

# For the GringoPost page. Every rule is scoped to .progninos so it cannot change the rest of the site;
# fonts and text colours come from the theme.
SITE_CSS = """
.progninos{max-width:860px;margin:0 auto;padding:0 16px;box-sizing:border-box;line-height:1.5}
.progninos h2{margin:28px 0 8px}
.progninos .sub{opacity:.75;margin-top:4px}
.progninos table{border-collapse:collapse;width:100%;font-size:15px;margin:8px 0}
.progninos th,.progninos td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}
.progninos th{background:#f4f4f4;color:#222}
.progninos .wrap{overflow-x:auto}
.progninos .note{font-size:14px;opacity:.8}
.progninos .chart{background:#fff;margin:12px 0;overflow-x:auto}
.progninos svg{display:block;width:100%;height:auto;min-width:560px}
"""


def html_report(report, met, rows, acc, checks, daily_svg, monthly_svg, recent, normals, archive_start):
    """Stand-alone page, including the internal quality checks."""
    body, flag, checks_html = report_parts(report, met, rows, acc, checks, daily_svg, monthly_svg,
                                           recent, normals, archive_start)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Progniños – Cuenca – {report}</title>
<style>{REPORT_CSS}</style></head><body>
<h1>Progniños</h1>
{flag}
{body}
{checks_html}
</body></html>"""


def site_fragment(report, run_utc, met, rows, acc, checks, daily_svg, monthly_svg, recent, normals, archive_start):
    """Content for the GringoPost page: one Custom HTML block, without the page title (WordPress shows it)
    and without the internal checks."""
    body, _, _ = report_parts(report, met, rows, acc, checks, daily_svg, monthly_svg, recent, normals, archive_start)
    stamp = run_utc.astimezone(LOCAL).strftime("%B %d, %Y at %H:%M")
    return f"""<!-- wp:html -->
<div class="progninos">
<style>{SITE_CSS}</style>
{body}
<p class="note">Updated automatically every morning. Last update: {stamp} (Cuenca time).</p>
</div>
<!-- /wp:html -->
"""


# ---- Main -------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="report date YYYY-MM-DD (default: today in Cuenca)")
    ap.add_argument("--days", type=int, default=7, help="look-back length")
    a = ap.parse_args()

    now_utc = dt.datetime.now(dt.timezone.utc)
    today = now_utc.astimezone(LOCAL).date()
    report = dt.date.fromisoformat(a.date) if a.date else today
    end = report - dt.timedelta(days=1)
    start = end - dt.timedelta(days=a.days - 1)
    days = [(start + dt.timedelta(days=i)).isoformat() for i in range(a.days)]
    print(f"Report {report} | look-back {days[0]} .. {days[-1]}")

    try:
        met = fetch_met_norway()
    except Exception as exc:
        print(f"MET Norway unavailable: {exc}")
        met = {}
    if report == today and met:
        archive_forecast(met, now_utc.strftime("%Y-%m-%dT%H:%MZ"), today)
    fc, archive_start = archived_forecasts(set(days))
    stn = fetch_station(days[0], days[-1])
    normals = chirps_normals()
    try:
        recent = chirps_recent(report)
    except Exception as exc:
        print(f"CHIRPS unavailable: {exc}")
        recent = []

    rows = []
    for d in days:
        f, st = fc.get(d, {}), stn.get(d, {})
        month = int(d[5:7])
        rows.append({
            "date": d, "fc_precip": f.get("precip"),
            "normal_precip": round(normals[month]["mean"] / calendar.monthrange(int(d[:4]), month)[1], 1),
            "fc_tmax": f.get("tmax"), "stn_tmax": st.get("tmax"),
            "fc_tmin": f.get("tmin"), "stn_tmin": st.get("tmin"),
            "stn_rain": st.get("rain_reported"), "stn_codes": " ".join(st.get("codes", [])),
        })
    acc = accuracy(rows)
    checks = quality_checks(rows, met, recent, archive_start)

    out = HERE / "output" / report.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    daily_svg = svg_daily_chart(rows)
    monthly_svg = svg_monthly_chart(recent, normals) if recent else ""
    (out / "precip_chart.svg").write_text(daily_svg, encoding="utf-8")
    if monthly_svg:
        (out / "monthly_chart.svg").write_text(monthly_svg, encoding="utf-8")
    with (out / "lookback.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "checks.json").write_text(json.dumps(
        {"accuracy": acc, "checks": checks, "chirps_recent": recent}, indent=2), encoding="utf-8")
    (out / "report.html").write_text(html_report(report, met, rows, acc, checks, daily_svg, monthly_svg,
                                                 recent, normals, archive_start), encoding="utf-8")
    (out / "site.html").write_text(site_fragment(report, now_utc, met, rows, acc, checks, daily_svg, monthly_svg,
                                                 recent, normals, archive_start), encoding="utf-8")

    print(json.dumps(acc, indent=1))
    for c in checks:
        print(f"[{c['status']}] {c['check']}: {c['detail']}")
    print(f"Written to {out}")


if __name__ == "__main__":
    main()
