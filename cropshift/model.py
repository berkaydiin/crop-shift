"""Code written with AI assistance (Claude, Anthropic); see README, "Use of SI".

Crop water use, rotation candidates and the rotation optimizer.

Pipeline
--------
1. ``crop_water_by_year``  runs a monthly soil-water bucket for every crop and
   every climate year (FAO-56 Kc x ETo, USDA-SCS effective rain, NASA POWER
   root-zone wetness as the starting soil moisture). Irrigated crops get the
   water they lack; rainfed crops take a yield cut through the FAO-33 Ky
   response.
2. ``generate_rotations``  lists every 4-year cyclic rotation that passes the
   agronomic rules (return intervals, no back-to-back legumes, ...).
3. ``rotation_metrics``    scores each rotation on income, irrigation water,
   soil-health indicators and drought risk.
4. ``optimize``            picks the best mix of at most K rotations under a
   water quota (mixed-integer linear program, solved with HiGHS via SciPy).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .climate import as_matrix, effective_rain

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
YEARS_IN_ROTATION = 4
AWC_MM_PER_M = 160  # available water capacity measured at Konya Alakova station


# ---------------------------------------------------------------------------
# Crops
# ---------------------------------------------------------------------------

def load_crops(path: Path | None = None) -> pd.DataFrame:
    df = pd.read_csv(path or DATA_DIR / "crops.csv", dtype={"rain_window": str})
    df["kc"] = df["kc_profile"].apply(
        lambda s: [(int(a), float(b)) for a, b in (x.split(":") for x in s.split())]
    )
    return df.set_index("code")


def _season_calendar(kc):
    """Map a crop's Kc profile to (year_offset, month, Kc) tuples.

    Winter crops are written Oct..Jul; months before the wrap belong to the
    previous calendar year (offset -1).
    """
    months = [m for m, _ in kc]
    wraps = months[0] > months[-1]
    out = []
    for m, k in kc:
        off = -1 if (wraps and m >= months[0]) else 0
        out.append((off, m, k))
    return out


@dataclass
class WaterSettings:
    irrigation_efficiency: float = 0.70   # sprinkler/furrow mix
    awc_mm_per_m: float = AWC_MM_PER_M
    mad: float = 0.5                      # management allowed depletion (irrigated)


def crop_water_by_year(climate: pd.DataFrame, crops: pd.DataFrame,
                       ws: WaterSettings | None = None) -> pd.DataFrame:
    """Per crop and harvest year: ETc, net/gross irrigation, rainfed yield factor."""
    ws = ws or WaterSettings()
    eto = as_matrix(climate, "eto_mm")
    peff = as_matrix(climate, "p_mm").apply(effective_rain)
    gw = as_matrix(climate, "gwetroot")
    years = list(eto.index)
    rows = []
    for code, c in crops.iterrows():
        cal = _season_calendar(c["kc"])
        taw = c["root_depth_m"] * ws.awc_mm_per_m
        for y in years:
            if (y + min(o for o, _, _ in cal)) not in eto.index:
                continue  # season starts before the record
            first_off, first_m, _ = cal[0]
            s = taw * float(gw.loc[y + first_off, first_m])  # NASA root-zone wetness as start
            etc = eta = nir = 0.0
            for off, m, k in cal:
                yy = y + off
                demand = k * float(eto.loc[yy, m])
                s = min(s + float(peff.loc[yy, m]), taw)
                etc += demand
                if c["irrigated"]:
                    floor = (1 - ws.mad) * taw
                    if s - demand >= floor:
                        s -= demand
                    else:
                        irr = demand - (s - floor)
                        nir += irr
                        s = floor
                    eta += demand
                else:
                    use = min(demand, max(s, 0.0))
                    s -= use
                    eta += use
            rows.append({
                "code": code, "year": y, "etc_mm": etc, "eta_mm": eta,
                "nir_mm": nir, "gross_irr_m3_da": nir / ws.irrigation_efficiency,
                "rel_et": eta / etc if etc else 1.0,
            })
    out = pd.DataFrame(rows)
    # FAO-33 yield response: 1 - Ya/Ym = Ky (1 - ETa/ETc); normalised so the
    # long-run average equals the reported Konya average yield.
    ky = out["code"].map(crops["ky"])
    out["raw_factor"] = (1 - ky * (1 - out["rel_et"])).clip(lower=0.1)
    out["yield_factor"] = (
        out["raw_factor"] / out.groupby("code")["raw_factor"].transform("mean")
    ).clip(0.3, 1.5)
    out.loc[out["code"].map(crops["irrigated"]) == 1, "yield_factor"] = 1.0
    return out


# ---------------------------------------------------------------------------
# Rotation rules
# ---------------------------------------------------------------------------

@dataclass
class RotationRules:
    max_per_cycle: dict = field(default_factory=lambda: {
        "sugar_beet": 1, "chickpea": 1, "bean": 1, "sunflower": 1,
    })
    max_consecutive: dict = field(default_factory=lambda: {
        "wheat": 3, "barley": 2, "maize": 2,
    })
    no_back_to_back_legumes: bool = True
    # yield multipliers: (previous family or 'legume', crop family) -> factor
    precrop_effects: dict = field(default_factory=lambda: {
        ("legume", "wheat"): 1.15, ("legume", "barley"): 1.10,
        ("wheat", "wheat"): 0.90, ("barley", "wheat"): 0.95,
    })


def _cyclic_run_lengths(fams):
    n = len(fams)
    out = {}
    for i in range(n):
        run = 1
        while run < n and fams[(i + run) % n] == fams[i]:
            run += 1
        out[fams[i]] = max(out.get(fams[i], 0), run)
    return out


def _canonical(seq):
    """Smallest rotation of a cyclic sequence, so A-B-C-D == B-C-D-A."""
    return min(tuple(seq[i:] + seq[:i]) for i in range(len(seq)))


def is_allowed(seq, crops: pd.DataFrame, rules: RotationRules) -> bool:
    fams = [crops.loc[c, "family"] for c in seq]
    legs = [bool(crops.loc[c, "legume"]) for c in seq]
    for fam, cap in rules.max_per_cycle.items():
        if fams.count(fam) > cap:
            return False
    runs = _cyclic_run_lengths(fams)
    for fam, run in runs.items():
        cap = rules.max_consecutive.get(fam, 1)
        if run > cap:
            return False
    n = len(seq)
    if rules.no_back_to_back_legumes and any(legs[i] and legs[(i + 1) % n] for i in range(n)):
        return False
    return True


def generate_rotations(crops: pd.DataFrame, rules: RotationRules | None = None,
                       length: int = YEARS_IN_ROTATION) -> list[tuple]:
    rules = rules or RotationRules()
    seen = set()
    for seq in itertools.product(crops.index, repeat=length):
        canon = _canonical(list(seq))
        if canon in seen:
            continue
        if is_allowed(list(canon), crops, rules):
            seen.add(canon)
    return sorted(seen)


def precrop_factor(prev: str, cur: str, crops: pd.DataFrame, rules: RotationRules) -> float:
    pf, cf = crops.loc[prev, "family"], crops.loc[cur, "family"]
    if crops.loc[prev, "legume"] and ("legume", cf) in rules.precrop_effects:
        return rules.precrop_effects[("legume", cf)]
    return rules.precrop_effects.get((pf, cf), 1.0)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def months_bare(seq, crops: pd.DataFrame) -> float:
    """Average months per year with no crop in the field (wind-erosion exposure)."""
    covered = 0
    for c in seq:
        covered += len(crops.loc[c, "kc"])
    # a winter crop overlapping into the next year still covers only its own months
    return max(0.0, 12 - covered / len(seq))


def dry_years(climate: pd.DataFrame, share: float = 0.2) -> list[int]:
    """Driest water years (Oct-Sep precipitation) in the record."""
    p = as_matrix(climate, "p_mm")
    wy = {}
    for y in p.index[1:]:
        wy[y] = p.loc[y - 1, [10, 11, 12]].sum() + p.loc[y, list(range(1, 10))].sum()
    s = pd.Series(wy).sort_values()
    k = max(1, int(round(len(s) * share)))
    return sorted(s.index[:k].tolist())


def rotation_metrics(rotations, crops, water, rules: RotationRules | None = None,
                     water_cost_tl_m3: float = 0.0, dry=None) -> pd.DataFrame:
    rules = rules or RotationRules()
    years = sorted(water["year"].unique())
    # only years every crop has
    have = water.groupby("year")["code"].nunique()
    years = [y for y in years if have.get(y, 0) == len(crops)]
    dry = [y for y in (dry or []) if y in years]
    di = [years.index(y) for y in dry]
    sub = water[water["year"].isin(years)]
    gross = sub.pivot(index="code", columns="year", values="gross_irr_m3_da")[years]
    yfac = sub.pivot(index="code", columns="year", values="yield_factor")[years]
    # per-crop arrays over years: revenue before pre-crop effect, cost, water
    rev = {c: (crops.loc[c, "yield_kg_da"] * crops.loc[c, "price_tl_kg"]) * yfac.loc[c].values
           for c in crops.index}
    gro = {c: gross.loc[c].values for c in crops.index}
    pcache = {}
    rows = []
    for seq in rotations:
        n = len(seq)
        inc_y = np.zeros(len(years))
        wat_y = np.zeros(len(years))
        for i, c in enumerate(seq):
            key = (seq[i - 1], c)
            if key not in pcache:
                pcache[key] = precrop_factor(seq[i - 1], c, crops, rules)
            inc_y += rev[c] * pcache[key] - crops.loc[c, "cost_tl_da"] - water_cost_tl_m3 * gro[c]
            wat_y += gro[c]
        inc_y /= n
        wat_y /= n
        fams = [crops.loc[c, "family"] for c in seq]
        rows.append({
            "rotation": seq,
            "label": " → ".join(seq),
            "income_tl_da": inc_y.mean(),
            "income_dry_tl_da": inc_y[di].mean() if di else np.nan,
            "income_cv": inc_y.std() / abs(inc_y.mean()) if inc_y.mean() else np.nan,
            "water_m3_da": wat_y.mean(),
            "water_dry_m3_da": wat_y[di].mean() if di else np.nan,
            "legume_share": sum(crops.loc[c, "legume"] for c in seq) / n,
            "beet_share": fams.count("sugar_beet") / n,
            "cereal_share": sum(f in ("wheat", "barley", "maize") for f in fams) / n,
            "families": len(set(fams)),
            "bare_months": months_bare(seq, crops),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Optimisation
# ---------------------------------------------------------------------------

@dataclass
class Constraints:
    water_quota_m3_da: float | None = None   # farm-average irrigation per decare per year
    use_dry_year_water: bool = False         # check the quota against dry-year demand
    min_legume_share: float = 0.0
    max_beet_share: float = 0.25             # factory contract limit
    max_bare_months: float | None = None
    min_income_tl_da: float | None = None
    max_rotations: int = 2                   # keep the plan simple to manage


def optimize(metrics: pd.DataFrame, cons: Constraints, objective: str = "income"):
    """Choose land shares for at most K rotations.

    objective: 'income' (maximise mean income) or 'water' (minimise water).
    Returns a DataFrame of chosen rotations with their land share, or None if
    no plan meets the constraints.
    """
    from scipy.optimize import Bounds, LinearConstraint, milp

    m = metrics.reset_index(drop=True)
    n = len(m)
    wcol = "water_dry_m3_da" if cons.use_dry_year_water else "water_m3_da"
    # variables: x_0..x_{n-1} (land share), z_0..z_{n-1} (binary: rotation used)
    if objective == "income":
        c = np.concatenate([-m["income_tl_da"].values, np.zeros(n)])
    else:
        c = np.concatenate([m[wcol].values, np.zeros(n)])
    A, lb, ub = [], [], []

    def add(row_x, lo, hi):
        A.append(np.concatenate([row_x, np.zeros(n)]))
        lb.append(lo)
        ub.append(hi)

    add(np.ones(n), 1, 1)
    if cons.water_quota_m3_da is not None:
        add(m[wcol].values, -np.inf, cons.water_quota_m3_da)
    if cons.min_legume_share:
        add(m["legume_share"].values, cons.min_legume_share, np.inf)
    if cons.max_beet_share is not None:
        add(m["beet_share"].values, -np.inf, cons.max_beet_share)
    if cons.max_bare_months is not None:
        add(m["bare_months"].values, -np.inf, cons.max_bare_months)
    if cons.min_income_tl_da is not None:
        add(m["income_tl_da"].values, cons.min_income_tl_da, np.inf)
    # linking x_i <= z_i and sum z <= K
    for i in range(n):
        row = np.zeros(2 * n)
        row[i], row[n + i] = 1, -1
        A.append(row)
        lb.append(-np.inf)
        ub.append(0)
    row = np.concatenate([np.zeros(n), np.ones(n)])
    A.append(row)
    lb.append(1)
    ub.append(cons.max_rotations)

    res = milp(
        c,
        constraints=LinearConstraint(np.array(A), lb, ub),
        integrality=np.concatenate([np.zeros(n), np.ones(n)]),
        bounds=Bounds(np.zeros(2 * n), np.ones(2 * n)),
    )
    if not res.success:
        return None
    x = res.x[:n]
    chosen = m.loc[x > 1e-6].copy()
    chosen["share"] = x[x > 1e-6]
    return chosen.sort_values("share", ascending=False)


def plan_summary(plan: pd.DataFrame) -> dict:
    """Area-weighted totals for a plan (one or more rotations)."""
    w = plan["share"].values
    cols = ["income_tl_da", "income_dry_tl_da", "water_m3_da", "water_dry_m3_da",
            "legume_share", "beet_share", "cereal_share", "bare_months"]
    out = {k: float(np.dot(w, plan[k].values)) for k in cols}
    out["rotations"] = list(zip(plan["label"], plan["share"]))
    return out


def tradeoff_curve(metrics: pd.DataFrame, cons: Constraints, quotas) -> pd.DataFrame:
    """Best income achievable at each water quota (the water-income frontier)."""
    rows = []
    for q in quotas:
        c = Constraints(**{**cons.__dict__, "water_quota_m3_da": q})
        p = optimize(metrics, c)
        if p is None:
            rows.append({"quota": q, "income": np.nan, "plan": ""})
            continue
        s = plan_summary(p)
        rows.append({"quota": q, "income": s["income_tl_da"], "water": s["water_m3_da"],
                     "plan": "; ".join(f"{l} ({sh:.0%})" for l, sh in s["rotations"])})
    return pd.DataFrame(rows)
