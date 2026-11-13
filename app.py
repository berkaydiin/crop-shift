# Crop Shift - crop-rotation decision support for the Konya Closed Basin.
# NASA Space Apps Challenge 2026, "Field Shift: Adapting Farms with NASA Data".
# Code written with AI assistance (Claude, Anthropic); see README, "Use of SI".
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from cropshift.climate import SITES, as_matrix, climate_trends, fetch_power, load_snapshot
from cropshift.model import (Constraints, RotationRules, WaterSettings, crop_water_by_year,
                             dry_years, generate_rotations, load_crops, optimize,
                             plan_summary, rotation_metrics, tradeoff_curve)

st.set_page_config(page_title="Crop Shift · Konya", page_icon="🌾", layout="wide")

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
STRATEGY_COLORS = {"Current practice": "#8a8a8a", "Best income within quota": "#1f77b4",
                   "Water saver": "#2ca02c", "Soil builder": "#9467bd"}


# ---------------------------------------------------------------------------
# Cached computations
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def get_climate(site_key: str, lat: float | None = None, lon: float | None = None):
    if site_key == "custom":
        df, elev = fetch_power(lat, lon)
        return df, elev
    return load_snapshot(site_key), SITES[site_key].elevation_m


@st.cache_data(show_spinner=False)
def get_water(climate: pd.DataFrame, crops: pd.DataFrame, eff: float):
    return crop_water_by_year(climate, crops, WaterSettings(irrigation_efficiency=eff))


@st.cache_data(show_spinner=False)
def get_metrics(crops: pd.DataFrame, water: pd.DataFrame, water_cost: float, dry: tuple,
                legume_bonus: float, second_wheat: float):
    rules = RotationRules()
    rules.precrop_effects[("legume", "wheat")] = 1 + legume_bonus
    rules.precrop_effects[("legume", "barley")] = 1 + legume_bonus * 2 / 3
    rules.precrop_effects[("wheat", "wheat")] = 1 - second_wheat
    rots = generate_rotations(crops, rules)
    return rotation_metrics(rots, crops, water, rules, water_cost, list(dry))


def fmt_tl(x):
    return f"{x:,.0f} TL"


SHORT = {"WW_I": "Wheat (irrigated)", "WW_R": "Wheat (rainfed)", "BA_R": "Barley",
         "CP_R": "Chickpea", "SB_I": "Sugar beet", "MZ_I": "Maize", "SF_I": "Sunflower",
         "DB_I": "Dry bean"}


def nice(label):
    return re.sub(r"\b[A-Z]{2}_[A-Z]\b", lambda m: SHORT.get(m.group(0), m.group(0)), label)


def plan_text(summary):
    return " + ".join(f"{nice(lbl)} ({sh:.0%} of land)" if sh < 0.999 else nice(lbl)
                      for lbl, sh in summary["rotations"])


# ---------------------------------------------------------------------------
# Sidebar: farm and priorities
# ---------------------------------------------------------------------------

crops_default = load_crops()
st.sidebar.title("🌾 Crop Shift")
st.sidebar.caption("Explore crop rotations that save water in the Konya Closed Basin, Türkiye.")

site_choice = st.sidebar.selectbox(
    "Location", list(SITES) + ["custom"],
    format_func=lambda k: SITES[k].name if k in SITES else "Other location (live NASA POWER)",
)
lat = lon = None
if site_choice == "custom":
    c1, c2 = st.sidebar.columns(2)
    lat = c1.number_input("Latitude", value=37.87, format="%.2f")
    lon = c2.number_input("Longitude", value=32.48, format="%.2f")

area = st.sidebar.number_input("Farm size (decares)", min_value=10, value=200, step=10,
                               help="1 decare (dönüm) = 1,000 m². 1 mm of water on 1 decare = 1 m³.")

codes = list(crops_default.index)
names = crops_default["name"].to_dict()
st.sidebar.markdown("**Your current 4-year rotation**")
default_current = ["DB_I", "WW_I", "SB_I", "WW_I"]  # most common irrigated rotation in a Konya survey
current = [st.sidebar.selectbox(f"Year {i + 1}", codes, index=codes.index(default_current[i]),
                                format_func=names.get, key=f"cur{i}") for i in range(4)]

st.sidebar.markdown("**Priorities and limits**")
quota_pct = st.sidebar.slider(
    "Irrigation water you can use (% of your current rotation)", 0, 120, 70, 5,
    help="Default 70%: basin withdrawals are about 6.5 billion m³/yr against roughly 4.5 billion "
         "m³/yr of safe supply, so a ~30% cut brings use in line with supply.")
plan_dry = st.sidebar.toggle("Plan for a dry year (1-in-5)", value=False,
                             help="Check the quota against water demand in the driest 20% of "
                                  "years in the NASA record instead of the average year.")
min_legume = st.sidebar.slider("Minimum share of nitrogen-fixing legumes", 0, 50, 25, 5,
                               help="Used by the Soil builder strategy.") / 100
max_beet = st.sidebar.slider("Sugar beet contract limit (% of land)", 0, 25, 25, 5) / 100
max_rot = st.sidebar.radio("Rotations to manage at once", [1, 2, 3], index=1, horizontal=True)

with st.sidebar.expander("Advanced settings"):
    eff = st.slider("Irrigation efficiency", 0.5, 0.95, 0.70, 0.05,
                    help="Share of pumped water the crop actually uses. Furrow ~0.55, "
                         "sprinkler ~0.70-0.75, drip ~0.90.")
    water_cost = st.number_input("Pumping cost (TL per m³)", 0.0, 20.0, 0.0, 0.5,
                                 help="Electricity for groundwater pumping. Leave 0 to exclude.")
    legume_bonus = st.slider("Wheat yield gain after a legume", 0.0, 0.30, 0.15, 0.01)
    second_wheat = st.slider("Yield loss for wheat after wheat", 0.0, 0.25, 0.10, 0.01)

# ---------------------------------------------------------------------------
# Crop table (editable)
# ---------------------------------------------------------------------------

st.title("Crop Shift: water-wise crop rotations for the Konya Closed Basin")
st.markdown(
    "Konya is Türkiye's largest sugar beet producer, and most of its irrigation water is "
    "groundwater that NASA GRACE shows falling year after year. Crop Shift uses **25 years of NASA POWER data** to work out how much water each "
    "crop really needs on your land, then searches **every allowed 4-year rotation** for the "
    "plan that keeps the most income under a water limit you set."
)
st.info("This is a support tool. It does not replace your own judgment or advice from local "
        "agronomists, the sugar factory or the provincial agriculture office.", icon="🤝")

with st.expander("Crop prices, yields and costs (edit to match your farm)", expanded=False):
    edit_cols = ["name", "yield_kg_da", "price_tl_kg", "cost_tl_da"]
    edited = st.data_editor(
        crops_default[edit_cols], disabled=["name"], width="stretch",
        column_config={
            "name": "Crop",
            "yield_kg_da": st.column_config.NumberColumn("Yield (kg/da)", min_value=0),
            "price_tl_kg": st.column_config.NumberColumn("Price (TL/kg)", min_value=0.0, format="%.2f"),
            "cost_tl_da": st.column_config.NumberColumn("Cost (TL/da)", min_value=0),
        })
    st.caption("Defaults are Konya averages and 2026 prices; see the Data & methods tab for sources.")
crops = crops_default.copy()
crops[["yield_kg_da", "price_tl_kg", "cost_tl_da"]] = edited[["yield_kg_da", "price_tl_kg", "cost_tl_da"]]

# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

try:
    with st.spinner("Loading NASA POWER data..."):
        climate, elev = get_climate(site_choice, lat, lon)
except Exception as e:  # network or API error for live requests
    st.error(f"Could not load NASA POWER data for this point ({e}). Try again or pick a Konya site.")
    st.stop()

water = get_water(climate, crops, eff)
dry = tuple(dry_years(climate))
metrics = get_metrics(crops, water, water_cost, dry, legume_bonus, second_wheat)

cur_key = tuple(current)
cur_canon = min(tuple(cur_key[i:] + cur_key[:i]) for i in range(4))
cur_row = metrics[metrics["rotation"] == cur_canon]
if cur_row.empty:
    # current rotation breaks a rule (e.g. beet twice): score it anyway
    cur_row = rotation_metrics([cur_canon], crops, water, water_cost_tl_m3=water_cost, dry=list(dry))
cur = cur_row.iloc[0]
wcol = "water_dry_m3_da" if plan_dry else "water_m3_da"
quota = cur[wcol] * quota_pct / 100

base = dict(water_quota_m3_da=quota, use_dry_year_water=plan_dry,
            max_beet_share=max_beet, max_rotations=max_rot)
plans = {"Current practice": pd.DataFrame([{**cur.to_dict(), "share": 1.0}])}
p_inc = optimize(metrics, Constraints(**base))
if p_inc is not None:
    plans["Best income within quota"] = p_inc
p_wat = optimize(metrics, Constraints(**{**base, "water_quota_m3_da": None,
                                         "min_income_tl_da": 0.9 * cur["income_tl_da"]}),
                 objective="water")
if p_wat is not None:
    plans["Water saver"] = p_wat
p_soil = optimize(metrics, Constraints(**base, min_legume_share=min_legume,
                                       max_bare_months=cur["bare_months"]))
if p_soil is not None:
    plans["Soil builder"] = p_soil
summ = {k: plan_summary(v) for k, v in plans.items()}

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_s, tab_t, tab_n, tab_c, tab_d = st.tabs(
    ["Compare strategies", "Water vs income", "What NASA data shows", "Field calendar", "Data & methods"])

with tab_s:
    st.subheader("Four rotation strategies for your farm")
    site_name = SITES[site_choice].name if site_choice in SITES else f"{lat:.2f}°N, {lon:.2f}°E"
    st.caption(f"{site_name} · {area:,} decares · water limit {quota:,.0f} m³ per decare per year "
               f"({quota_pct}% of current{', dry-year demand' if plan_dry else ''})")
    missing = [k for k in ["Best income within quota", "Water saver", "Soil builder"] if k not in plans]
    if missing:
        st.warning("No plan meets the limits for: " + ", ".join(missing) +
                   ". Try a higher water limit or a lower legume share.")

    cols = st.columns(len(summ))
    for col, (name, s) in zip(cols, summ.items()):
        with col:
            st.markdown(f"#### {name}")
            st.markdown(f"**{plan_text(s)}**")
            d_inc = s["income_tl_da"] - summ["Current practice"]["income_tl_da"]
            d_wat = s["water_m3_da"] - summ["Current practice"]["water_m3_da"]
            st.metric("Net income per decare per year", fmt_tl(s["income_tl_da"]),
                      None if name == "Current practice" else f"{d_inc:+,.0f} TL")
            st.metric("Irrigation water (m³/da/yr)", f"{s['water_m3_da']:,.0f}",
                      None if name == "Current practice" else f"{d_wat:+,.0f}", delta_color="inverse")
            st.metric("Farm water per year", f"{s['water_m3_da'] * area / 1000:,.0f}k m³")
            st.caption(f"Dry-year income {fmt_tl(s['income_dry_tl_da'])}/da · legumes "
                       f"{s['legume_share']:.0%} · bare months {s['bare_months']:.1f}/yr")

    st.markdown("### Why each strategy, and what to check")
    cur_s = summ["Current practice"]
    for name, s in summ.items():
        with st.expander(name, expanded=(name == "Best income within quota")):
            water_saved = 1 - s["water_m3_da"] / cur_s["water_m3_da"] if cur_s["water_m3_da"] else 0
            inc_change = s["income_tl_da"] / cur_s["income_tl_da"] - 1 if cur_s["income_tl_da"] else 0
            why = {
                "Current practice": "Your rotation as entered, scored with the same data so every "
                                    "option is compared on equal terms.",
                "Best income within quota": f"Highest average net income among all {len(metrics)} "
                                            f"allowed rotations (or mixes of up to {max_rot}) that stay "
                                            f"under {quota:,.0f} m³/da.",
                "Water saver": "Lowest irrigation water among plans that keep at least 90% of your "
                               "current income.",
                "Soil builder": f"Highest income among plans with at least {min_legume:.0%} legumes "
                                f"and no more bare months than you have now, within the water limit.",
            }[name]
            st.markdown(f"**Why it was suggested.** {why}")
            st.markdown(
                f"**Benefits.** Water {'saved' if water_saved >= 0 else 'added'}: "
                f"{abs(water_saved):.0%} ({abs(s['water_m3_da'] - cur_s['water_m3_da']) * area / 1000:,.0f} "
                f"thousand m³ a year on your farm). Income change: {inc_change:+.0%}. "
                f"Legumes: {s['legume_share']:.0%} of land (fix nitrogen, feed the next wheat). "
                f"Bare soil {s['bare_months']:.1f} months a year (wind erosion risk in Karapınar).")
            st.markdown(
                "**Evidence.** Crop water need = FAO-56 crop coefficient × reference "
                "evapotranspiration from NASA POWER (2001-2025) minus effective rain, with NASA "
                "root-zone soil wetness as the starting soil moisture. Yields and prices are Konya "
                "averages; legume and second-wheat effects come from field studies (Data & methods).")
            gaps = ["NASA POWER cells are about 50 km wide; one field can differ from the cell average.",
                    "Yields are provincial averages, not your field's history."]
            risks = []
            plan_crops = {c for lbl, _ in s["rotations"] for c in lbl.split(" → ")}
            if plan_crops & {"CP_R", "DB_I"}:
                gaps.append("Market depth for chickpea and dry bean: can local buyers take more volume?")
                risks.append("Chickpea: Ascochyta blight in wet springs; use tolerant varieties.")
            if plan_crops & {"WW_R", "BA_R", "CP_R"}:
                risks.append(f"Rainfed crops lose yield in dry years; dry-year income here is "
                             f"{fmt_tl(s['income_dry_tl_da'])}/da.")
            if "SB_I" in plan_crops:
                risks.append("Sugar beet needs a factory contract; area is capped by your quota.")
            if "SF_I" in plan_crops:
                risks.append("Sunflower: broomrape (Orobanche) risk; keep 4+ years between crops.")
            if "MZ_I" in plan_crops:
                risks.append("Maize is the second-thirstiest crop here; it is sensitive to quota cuts.")
            st.markdown("**Information gaps.** " + " ".join(gaps))
            st.markdown("**Risks needing local review.** " + (" ".join(risks) if risks else
                        "Same as your current practice."))

    comp = pd.DataFrame([{
        "Strategy": k, "Plan": plan_text(s),
        "Income TL/da": s["income_tl_da"], "Dry-year income TL/da": s["income_dry_tl_da"],
        "Water m³/da": s["water_m3_da"], "Dry-year water m³/da": s["water_dry_m3_da"],
        "Legumes": s["legume_share"], "Bare months": s["bare_months"],
    } for k, s in summ.items()])
    st.dataframe(comp, hide_index=True, width="stretch", column_config={
        "Income TL/da": st.column_config.NumberColumn(format="%.0f"),
        "Dry-year income TL/da": st.column_config.NumberColumn(format="%.0f"),
        "Water m³/da": st.column_config.NumberColumn(format="%.0f"),
        "Dry-year water m³/da": st.column_config.NumberColumn(format="%.0f"),
        "Legumes": st.column_config.NumberColumn(format="percent"),
        "Bare months": st.column_config.NumberColumn(format="%.1f"),
    })

with tab_t:
    st.subheader("How much income does each cubic metre of water buy?")
    st.markdown("Each point is the best plan the optimizer can find at that water limit. "
                "The curve shows where cutting water is cheap and where it starts to hurt.")
    top = max(cur["water_m3_da"], cur["water_dry_m3_da"]) * 1.2
    quotas = np.linspace(0, top, 13)
    tc = tradeoff_curve(metrics, Constraints(**{**base, "water_quota_m3_da": None}), quotas)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=tc["quota"], y=tc["income"], mode="lines+markers",
                             name="Best plan at this limit", text=[nice(x) if x else "" for x in tc["plan"]],
                             hovertemplate="Limit %{x:.0f} m³/da<br>Income %{y:,.0f} TL/da<br>%{text}"))
    for name, s in summ.items():
        fig.add_trace(go.Scatter(x=[s["water_m3_da"]], y=[s["income_tl_da"]], mode="markers",
                                 marker=dict(size=14, color=STRATEGY_COLORS[name], symbol="diamond"),
                                 name=name))
    fig.update_layout(xaxis_title="Irrigation water limit (m³ per decare per year)",
                      yaxis_title="Net income (TL per decare per year)", height=480)
    st.plotly_chart(fig, width="stretch")
    st.markdown("**All allowed rotations** (each dot is one 4-year rotation)")
    metrics_disp = metrics.assign(name=metrics["label"].map(nice))
    f2 = px.scatter(metrics_disp, x=wcol, y="income_tl_da", color="legume_share", hover_name="name",
                    color_continuous_scale="Viridis",
                    labels={wcol: "Irrigation water (m³/da/yr)", "income_tl_da": "Net income (TL/da/yr)",
                            "legume_share": "Legume share"})
    f2.add_vline(x=quota, line_dash="dash", annotation_text="your water limit")
    st.plotly_chart(f2, width="stretch")

with tab_n:
    st.subheader("What 25 years of NASA data say about this place")
    tr = climate_trends(climate)
    show = tr.copy()
    show["Significant?"] = np.where(show["p_value"] < 0.05, "yes (p<0.05)",
                                    np.where(show["p_value"] < 0.1, "weak (p<0.1)", "no"))
    st.dataframe(show.rename(columns={"indicator": "Indicator", "mean": "2001-2025 mean",
                                      "change_per_decade": "Change per decade", "p_value": "p-value"}),
                 hide_index=True, width="stretch",
                 column_config={"2001-2025 mean": st.column_config.NumberColumn(format="%.2f"),
                                "Change per decade": st.column_config.NumberColumn(format="%+.2f"),
                                "p-value": st.column_config.NumberColumn(format="%.3f")})
    st.caption("Linear trends (ordinary least squares) on NASA POWER monthly data. Precipitation "
               "is MERRA-2 bias-corrected (PRECTOTCORR); temperature and humidity MERRA-2; "
               "solar radiation CERES. ETo computed with FAO-56 Penman-Monteith.")
    p_y = as_matrix(climate, "p_mm").sum(axis=1)
    t_y = as_matrix(climate, "t_c").mean(axis=1)
    c1, c2 = st.columns(2)
    with c1:
        f = px.bar(x=p_y.index, y=p_y.values, labels={"x": "Year", "y": "Precipitation (mm)"},
                   title="Annual precipitation")
        z = np.polyfit(p_y.index, p_y.values, 1)
        f.add_scatter(x=p_y.index, y=np.polyval(z, p_y.index), mode="lines", name="trend")
        st.plotly_chart(f, width="stretch")
    with c2:
        f = px.line(x=t_y.index, y=t_y.values, markers=True,
                    labels={"x": "Year", "y": "Mean air temperature (°C)"}, title="Annual mean temperature")
        z = np.polyfit(t_y.index, t_y.values, 1)
        f.add_scatter(x=t_y.index, y=np.polyval(z, t_y.index), mode="lines", name="trend")
        st.plotly_chart(f, width="stretch")
    irr = water[water["code"].map(crops["irrigated"]) == 1]
    f = px.line(irr, x="year", y="gross_irr_m3_da", color=irr["code"].map(names),
                labels={"gross_irr_m3_da": "Irrigation need (m³/da)", "year": "Harvest year", "color": "Crop"},
                title="Irrigation each crop needed, year by year (from NASA data)")
    for y in dry:
        f.add_vrect(x0=y - 0.5, x1=y + 0.5, fillcolor="orange", opacity=0.12, line_width=0)
    st.plotly_chart(f, width="stretch")
    st.caption("Shaded years are the driest 20% of water years (Oct-Sep), used for the dry-year scenario.")
    st.markdown(
        "**Groundwater context (NASA GRACE).** GRACE satellite gravity shows the Konya Closed Basin "
        "losing about 3.4 cm of groundwater storage a year, linked to the spread of sinkholes "
        "(Khorrami, Arık & Gündüz 2021, using JPL GRACE mascons). The water limit in this tool is "
        "a way to turn that basin-scale signal into a field-scale decision.")

with tab_c:
    st.subheader("When is the soil covered?")
    st.markdown("Bare soil in late autumn and winter is exposed to wind erosion, a known problem "
                "around Karapınar. Winter cereals cover the ground; spring crops leave it bare "
                "from harvest until the next spring.")
    pick = st.selectbox("Strategy", list(summ))
    lbl = summ[pick]["rotations"][0][0]
    seq = lbl.split(" → ")
    grid = np.full((4, 12), "", dtype=object)
    for i, c in enumerate(seq):
        for m, _ in crops.loc[c, "kc"]:
            # winter crops: Oct-Dec months fall in the calendar year before harvest
            months = [m0 for m0, _ in crops.loc[c, "kc"]]
            row = i - 1 if (months[0] > months[-1] and m >= months[0]) else i
            grid[row % 4, m - 1] = c
    z = np.array([[0 if v == "" else 1 for v in r] for r in grid])
    tag = {"WW_I": "Wheat", "WW_R": "Wheat", "BA_R": "Barley", "CP_R": "Chickpea", "SB_I": "Beet",
           "MZ_I": "Maize", "SF_I": "Sunfl.", "DB_I": "Bean"}
    grid_txt = np.vectorize(lambda v: tag.get(v, v))(grid)
    fig = go.Figure(go.Heatmap(z=z, x=MONTHS, y=[f"Year {i + 1}" for i in range(4)], text=grid_txt,
                               texttemplate="%{text}", colorscale=[[0, "#f3e3c3"], [1, "#6aa84f"]],
                               showscale=False, xgap=2, ygap=2))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(height=320, title=f"{pick}: {nice(lbl)}")
    st.plotly_chart(fig, width="stretch")
    st.caption("Green = crop in the field, sand = bare soil. Shown for the main rotation of the plan.")

with tab_d:
    st.subheader("How the numbers are made")
    st.markdown("""
1. **Weather.** NASA POWER monthly agroclimatology (2001-2025) for the chosen point: temperature,
   humidity, wind, solar radiation, bias-corrected precipitation and root-zone soil wetness.
2. **Reference evapotranspiration (ETo).** FAO-56 Penman-Monteith at a monthly step.
3. **Crop water.** Monthly crop coefficients (FAO-56 Table 12, set to Konya planting dates) × ETo,
   in a soil-water bucket (root depth × 160 mm/m, measured at Konya) that starts from NASA
   root-zone wetness at sowing. Rain counts through the USDA-SCS effective-rain formula.
   Irrigated crops get what they lack; rainfed crops lose yield through the FAO-33 response
   factor Ky, scaled so the 25-year average equals the Konya average yield.
4. **Rotations.** Every 4-year cycle of the 8 crop options is checked against agronomic rules:
   sugar beet, chickpea, dry bean and sunflower at most once in 4 years; no legume after a
   legume; wheat after a legume gets a yield bonus, wheat after wheat a penalty.
5. **Optimizer.** A mixed-integer linear program (SciPy/HiGHS) picks land shares for up to K
   rotations to maximise income (or minimise water) under the water limit, sugar beet contract
   cap and soil-health limits.
""")
    st.markdown("**Check against field data.** The model's seasonal sugar beet water use for the Konya "
                "plain is 960-1,181 mm (mean 1,079 mm). A full-irrigation trial near Konya city measured "
                "1,002-1,177 mm (Süheri, Topak & Yavuz 2007), while a trial at the Alakova station "
                "measured 520-827 mm across its treatments (Tarı et al. 2016). The model sits at the high "
                "end, so its water demand may be overstated by up to about 20% on some fields.")
    st.markdown("**Known limits.** POWER grid cells are ~50 km; a cell next to the Taurus mountains "
                "(Çumra) showed 551 mm/yr of rain against ~380 mm on the plain, so we used plain cells. "
                "Monthly steps smooth out heat waves. Prices change every season; edit them above. "
                "Groundwater pumping cost and crop quality premiums are not included by default.")
    st.markdown("**Crop parameters**")
    st.dataframe(crops[["name", "kc_profile", "ky", "root_depth_m", "yield_kg_da",
                        "price_tl_kg", "cost_tl_da", "notes"]], width="stretch")
    st.markdown("Full source list: see the project README on GitHub.")

st.divider()
st.caption("Crop Shift · NASA Space Apps Challenge 2026 · Data: NASA POWER (NASA Langley Research "
           "Center), GRACE results from published studies. Code written with AI assistance "
           "(Claude, Anthropic). Not an official NASA product.")
