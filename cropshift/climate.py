"""Code written with AI assistance (Claude, Anthropic); see README, "Use of SI".

Climate inputs from NASA POWER and the FAO-56 water calculations built on them.

Two ways to get data:
  * a bundled snapshot for the two Konya sites (works offline, used for the demo), and
  * a live request to the NASA POWER monthly point API for any coordinate.

Both end up as the same table: one row per (year, month) with reference
evapotranspiration (ETo, mm/month), precipitation (mm/month), mean air
temperature (C) and root-zone soil wetness (0-1).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

POWER_URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"
POWER_PARAMS = "T2M,T2M_MAX,T2M_MIN,RH2M,WS2M,ALLSKY_SFC_SW_DWN,PRECTOTCORR,GWETROOT"

MID_MONTH_DOY = [15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349]


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    lat: float
    lon: float
    elevation_m: float
    note: str


SITES = {
    "konya_plain": Site(
        "konya_plain", "Konya Plain (central)", 37.87, 32.48, 1215.6,
        "Irrigated plain around Konya city; POWER grid cell average elevation 1216 m.",
    ),
    "karapinar": Site(
        "karapinar", "Karapınar / Ereğli", 37.72, 33.55, 1236.0,
        "Eastern basin, centre of the sinkhole cluster; POWER grid cell average elevation 1236 m.",
    ),
}


# ---------------------------------------------------------------------------
# FAO-56 Penman-Monteith reference evapotranspiration (monthly step)
# ---------------------------------------------------------------------------

def _sat_vp(t_c: float) -> float:
    """Saturation vapour pressure, kPa (FAO-56 eq. 11)."""
    return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))


def extraterrestrial_radiation(lat_deg: float, doy: int) -> float:
    """Ra in MJ m-2 day-1 (FAO-56 eq. 21)."""
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    dec = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(dec))))
    return 24 * 60 / math.pi * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.sin(ws)
    )


def eto_daily_mean(t, tmax, tmin, rh, u2, rs, lat, elev, month) -> float:
    """Mean daily ETo (mm/day) for one month from monthly-mean weather.

    Soil heat flux G is neglected (FAO-56 notes it is small at monthly step
    when month-to-month temperature changes are moderate).
    """
    pressure = 101.3 * ((293 - 0.0065 * elev) / 293) ** 5.26
    gamma = 0.000665 * pressure
    es = (_sat_vp(tmax) + _sat_vp(tmin)) / 2
    ea = rh / 100 * es
    delta = 4098 * _sat_vp(t) / (t + 237.3) ** 2
    ra = extraterrestrial_radiation(lat, MID_MONTH_DOY[month - 1])
    rso = (0.75 + 2e-5 * elev) * ra
    rns = 0.77 * rs
    sigma = 4.903e-9
    rnl = sigma * ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2 * (
        0.34 - 0.14 * math.sqrt(ea)
    ) * (1.35 * min(rs / rso, 1.0) - 0.35)
    rn = rns - rnl
    return (0.408 * delta * rn + gamma * 900 / (t + 273) * u2 * (es - ea)) / (
        delta + gamma * (1 + 0.34 * u2)
    )


def days_in_month(year: int, month: int) -> int:
    return pd.Period(f"{year}-{month:02d}").days_in_month


# ---------------------------------------------------------------------------
# Loading data
# ---------------------------------------------------------------------------

def load_snapshot(site_key: str) -> pd.DataFrame:
    """Read the bundled NASA POWER snapshot (2001-2025) for a Konya site.

    File format, one line per year:
    year;ETo x12 (mm);P x12 (mm);T2M x12 (C);GWETROOT x12 (x100)
    ETo was computed from POWER monthly means with the same FAO-56 function
    as in this module (see scripts/README in the repo).
    """
    path = DATA_DIR / f"power_{site_key}_compact.txt"
    rows = []
    for line in path.read_text().strip().splitlines():
        year, eto, p, t, gw = line.split(";")
        eto, p, t, gw = (s.split() for s in (eto, p, t, gw))
        for m in range(12):
            rows.append({
                "year": int(year), "month": m + 1,
                "eto_mm": float(eto[m]), "p_mm": float(p[m]),
                "t_c": float(t[m]), "gwetroot": float(gw[m]) / 100,
            })
    return pd.DataFrame(rows)


def fetch_power(lat: float, lon: float, start: int = 2001, end: int = 2025,
                timeout: int = 60) -> tuple[pd.DataFrame, float]:
    """Download NASA POWER monthly data for a point and compute ETo.

    Returns the monthly table and the grid-cell elevation used.
    """
    import requests

    r = requests.get(POWER_URL, params={
        "parameters": POWER_PARAMS, "community": "AG", "longitude": lon,
        "latitude": lat, "start": start, "end": end, "format": "JSON",
    }, timeout=timeout)
    r.raise_for_status()
    js = r.json()
    par = js["properties"]["parameter"]
    elev = float(js["geometry"]["coordinates"][2])
    fill = js.get("header", {}).get("fill_value", -999)
    rows = []
    for y in range(start, end + 1):
        for m in range(1, 13):
            k = f"{y}{m:02d}"
            vals = {p: par[p].get(k, fill) for p in par}
            if any(v == fill for v in vals.values()):
                continue
            nd = days_in_month(y, m)
            eto = eto_daily_mean(vals["T2M"], vals["T2M_MAX"], vals["T2M_MIN"],
                                 vals["RH2M"], vals["WS2M"], vals["ALLSKY_SFC_SW_DWN"],
                                 lat, elev, m) * nd
            rows.append({
                "year": y, "month": m, "eto_mm": round(eto),
                "p_mm": round(vals["PRECTOTCORR"] * nd),
                "t_c": round(vals["T2M"], 1), "gwetroot": round(vals["GWETROOT"], 2),
            })
    return pd.DataFrame(rows), elev


# ---------------------------------------------------------------------------
# Water balance helpers
# ---------------------------------------------------------------------------

def effective_rain(p_mm):
    """USDA-SCS monthly effective rainfall, as used in FAO CROPWAT (mm)."""
    p = np.asarray(p_mm, dtype=float)
    return np.where(p <= 250, p * (125 - 0.2 * p) / 125, 125 + 0.1 * p)


def as_matrix(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Pivot to a year x month table."""
    return df.pivot(index="year", columns="month", values=col).sort_index()


def linear_trend(years, values):
    """OLS slope per decade with a two-sided p-value."""
    from scipy import stats

    res = stats.linregress(np.asarray(years, float), np.asarray(values, float))
    return {"slope_per_decade": res.slope * 10, "p_value": res.pvalue,
            "intercept": res.intercept, "slope": res.slope}


def climate_trends(df: pd.DataFrame) -> pd.DataFrame:
    """Long-term trends that matter for rotation choice."""
    eto = as_matrix(df, "eto_mm")
    p = as_matrix(df, "p_mm")
    t = as_matrix(df, "t_c")
    gw = as_matrix(df, "gwetroot")
    years = eto.index.values
    series = {
        "Annual reference ET, ETo (mm)": eto.sum(axis=1),
        "Summer ETo, Jun-Aug (mm)": eto[[6, 7, 8]].sum(axis=1),
        "Annual precipitation (mm)": p.sum(axis=1),
        "Spring precipitation, Mar-May (mm)": p[[3, 4, 5]].sum(axis=1),
        "Mean air temperature (C)": t.mean(axis=1),
        "July air temperature (C)": t[7],
        "Root-zone soil wetness, Apr (0-1)": gw[4],
    }
    out = []
    for name, s in series.items():
        tr = linear_trend(years, s.values)
        out.append({"indicator": name, "mean": s.mean(),
                    "change_per_decade": tr["slope_per_decade"],
                    "p_value": tr["p_value"]})
    return pd.DataFrame(out)
