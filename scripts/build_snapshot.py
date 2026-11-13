"""Rebuild the bundled NASA POWER snapshot files in data/.

Usage:  python scripts/build_snapshot.py
Needs internet access to power.larc.nasa.gov. Produces the same files that ship
with the app (one line per year: year;ETo x12;P x12;T2M x12;GWETROOT x12*100).
Code written with AI assistance (Claude, Anthropic).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cropshift.climate import DATA_DIR, SITES, fetch_power  # noqa: E402

for key, site in SITES.items():
    df, elev = fetch_power(site.lat, site.lon, 2001, 2025)
    lines = []
    for y, g in df.groupby("year"):
        g = g.sort_values("month")
        parts = [str(y),
                 " ".join(str(int(v)) for v in g["eto_mm"]),
                 " ".join(str(int(v)) for v in g["p_mm"]),
                 " ".join(f"{v:.1f}" for v in g["t_c"]),
                 " ".join(str(int(round(v * 100))) for v in g["gwetroot"])]
        lines.append(";".join(parts))
    (DATA_DIR / f"power_{key}_compact.txt").write_text("\n".join(lines) + "\n")
    print(key, "elevation", elev, "years", len(lines))
