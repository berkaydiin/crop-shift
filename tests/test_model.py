import numpy as np
import pandas as pd
import pytest

from cropshift.climate import (SITES, days_in_month, effective_rain, eto_daily_mean,
                               load_snapshot)
from cropshift.model import (Constraints, RotationRules, _canonical, crop_water_by_year,
                             generate_rotations, is_allowed, load_crops, optimize,
                             rotation_metrics)


def test_eto_matches_reference_values():
    # values computed independently in the browser from the same POWER inputs
    cases = [
        (dict(t=4.8, tmax=17.79, tmin=-5.25, rh=78.41, u2=2.49, rs=14.33), 37.87, 1215.57, 2015, 3, 58.12),
        (dict(t=24.84, tmax=36.61, tmin=14.45, rh=43.8, u2=2.7, rs=26.54), 37.87, 1215.57, 2024, 7, 231.4),
    ]
    for v, lat, z, y, m, ref in cases:
        got = eto_daily_mean(**v, lat=lat, elev=z, month=m) * days_in_month(y, m)
        assert got == pytest.approx(ref, abs=0.05)


def test_effective_rain_usda_scs():
    assert effective_rain(100) == pytest.approx(84.0)
    assert effective_rain(0) == 0
    assert effective_rain(300) == pytest.approx(155.0)


@pytest.mark.parametrize("site", list(SITES))
def test_snapshot_complete(site):
    df = load_snapshot(site)
    assert len(df) == 25 * 12
    assert df["eto_mm"].min() >= 0 and df["p_mm"].min() >= 0
    assert df["gwetroot"].between(0, 1).all()


def test_sugar_beet_etc_in_measured_range():
    # Konya field trial (Topak et al.): seasonal ETc 1002-1177 mm under full irrigation
    crops = load_crops()
    w = crop_water_by_year(load_snapshot("konya_plain"), crops)
    etc = w.loc[w.code == "SB_I", "etc_mm"].mean()
    assert 950 <= etc <= 1200


def test_rotation_rules():
    crops = load_crops()
    rules = RotationRules()
    assert not is_allowed(["SB_I", "WW_I", "SB_I", "WW_I"], crops, rules)  # beet twice
    assert not is_allowed(["CP_R", "DB_I", "WW_I", "WW_I"], crops, rules)  # legumes back to back
    assert is_allowed(["DB_I", "WW_I", "SB_I", "WW_I"], crops, rules)      # common Konya rotation
    assert _canonical(["B", "C", "D", "A"]) == ("A", "B", "C", "D")
    rots = generate_rotations(crops)
    assert len(rots) == len(set(rots))


def _demo_crops():
    crops = load_crops()
    crops["yield_kg_da"] = crops["yield_kg_da"].fillna(100)
    crops["price_tl_kg"] = crops["price_tl_kg"].fillna(10)
    crops["cost_tl_da"] = crops["cost_tl_da"].fillna(500)
    return crops


def test_optimizer_respects_quota_and_k():
    crops = _demo_crops()
    cl = load_snapshot("konya_plain")
    w = crop_water_by_year(cl, crops)
    met = rotation_metrics(generate_rotations(crops), crops, w)
    plan = optimize(met, Constraints(water_quota_m3_da=300, max_rotations=2))
    assert plan is not None
    assert plan["share"].sum() == pytest.approx(1, abs=1e-6)
    assert len(plan) <= 2
    assert float(np.dot(plan["share"], plan["water_m3_da"])) <= 300 + 1e-6
    # zero quota -> only rainfed rotations
    plan0 = optimize(met, Constraints(water_quota_m3_da=0))
    assert plan0 is not None and (plan0["water_m3_da"] < 1e-9).all()
