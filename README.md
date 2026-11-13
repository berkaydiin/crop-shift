# Crop Shift 🌾

**Water-wise crop rotations for the Konya Closed Basin, Türkiye.** · **Live app: https://crop-shift.streamlit.app** · **Demo slides: [docs/crop-shift-demo.pdf](docs/crop-shift-demo.pdf)**
NASA Space Apps Challenge 2026 · Challenge: *Field Shift: Adapting Farms with NASA Data* · Team Crop Shift (Ankara local event)

Konya is Türkiye's grain store and its largest sugar beet producer, and most of its irrigation water is pumped from the ground. NASA GRACE data show the basin losing about 3.4 cm of groundwater storage per year (Khorrami et al. 2021), and AFAD had mapped 739 sinkholes in the basin by August 2026. Farmers are being asked to use less water but have no simple way to see what that costs them or which rotation to switch to.

Crop Shift takes 25 years of NASA POWER weather data for a farm's location, works out how much irrigation each crop really needed in every one of those years, and then searches every agronomically allowed 4-year rotation for the plan that keeps the most income under a water limit the farmer chooses. It compares at least four strategies side by side (current practice, best income within the water limit, water saver, soil builder) and explains for each one why it was suggested, what the evidence is, what it gains, where the information gaps are, and what needs local review.

## What it does

- **Strategy comparison.** Income, irrigation water, dry-year income, legume share and bare-soil months for each strategy, with plain-language reasoning and risks.
- **Water vs income curve.** The best income achievable at every water limit, so a farmer can see where cutting water is cheap and where it starts to hurt.
- **What NASA data shows.** 2001-2025 trends for precipitation, temperature, reference evapotranspiration and root-zone soil wetness, plus each crop's irrigation need year by year with dry years marked.
- **Field calendar.** Month-by-month soil cover for a rotation, to show wind-erosion exposure (a known problem around Karapınar).
- **Editable assumptions.** Yields, prices and costs can be changed in the app; a custom location fetches live NASA POWER data.

## How it works

1. **Weather (NASA POWER, monthly, 2001-2025).** T2M, T2M_MAX, T2M_MIN, RH2M, WS2M, ALLSKY_SFC_SW_DWN, PRECTOTCORR, GWETROOT for two Konya grid cells (central plain 37.87N 32.48E; Karapınar/Ereğli 37.72N 33.55E).
2. **Reference evapotranspiration.** FAO-56 Penman-Monteith at monthly step (`cropshift/climate.py`).
3. **Crop water.** Monthly crop coefficients (FAO-56 Table 12 stage values set to Konya planting dates) x ETo in a soil-water bucket: root depth x 160 mm/m available water (measured at the Alakova station, Tarı et al. 2016), starting from NASA POWER root-zone soil wetness at sowing; rain enters through the USDA-SCS effective-rain formula. Irrigated crops receive what they lack (management allowed depletion 50%, irrigation efficiency 70% by default). Rainfed crops lose yield through the FAO-33 Ky response, scaled so the 25-year average equals the Konya average yield.
4. **Rotations.** Every 4-year cycle of 8 crop options (626 after rules): sugar beet, chickpea, dry bean and sunflower at most once in 4 years; no legume directly after a legume; up to 3 wheat crops in a row; wheat after a legume +15% yield; wheat after wheat -10% (all adjustable).
5. **Optimizer.** Mixed-integer linear program (SciPy `milp`, HiGHS) choosing land shares for up to K rotations to maximise income (or minimise water) subject to the water limit (average or dry-year demand), sugar beet contract cap, minimum legume share and maximum bare months.

### Check against field data
Modelled seasonal sugar beet water use on the Konya plain: 960-1,181 mm (mean 1,079 mm). A full-irrigation trial at the Konya Soil and Water Resources Research Institute measured 1,002 mm (2006) and 1,177 mm (2005) (Süheri, Topak & Yavuz 2007). A later trial at the Alakova station measured 520-827 mm across its irrigation treatments (Tarı et al. 2016). The model sits at the high end of field values, so its irrigation demand may be overstated by up to about 20% on some fields; the water limit is set as a share of the modelled current use, which partly cancels this.

### First results (central Konya plain, default settings)
- Current rotation (dry bean → wheat → sugar beet → wheat): about 840 m³ of irrigation per decare per year.
- Cutting water by 30% (the gap between basin withdrawals and safe supply) keeps about 80% of income if the farm shifts towards chickpea → wheat → dry bean → wheat with a smaller beet share.
- The water saver plan cuts water by 15% while keeping 90% of income.
- NASA POWER shows annual precipitation falling by about 38 mm per decade (p = 0.047) and mean temperature rising by 0.6 °C per decade (p = 0.004) over 2001-2025.

## Known limits
- NASA POWER cells are about 50 km wide. A cell next to the Taurus mountains (Çumra) gave 551 mm/yr of rain against about 380 mm on the plain, so plain cells are used. Field-scale data (GPM IMERG, Landsat, ECOSTRESS) would sharpen this.
- Monthly time steps smooth out short heat waves.
- **Economics are rough 2026 estimates.** Prices are official 2026 purchase prices where available; yields are 2023 Konya provincial averages; costs per decare were not published for Konya and are estimated (see below). All are editable in the app.
- The tool supports, and does not replace, farmer judgment and local agronomic advice.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py
python -m pytest -q          # unit and app tests
python scripts/build_snapshot.py   # optional: rebuild the bundled NASA POWER files
```

## Data and references

**NASA data**
- NASA POWER Project, NASA Langley Research Center. Monthly point API v2, community AG, 2001-2025. https://power.larc.nasa.gov/ — "These data were obtained from the NASA Langley Research Center (LaRC) POWER Project funded through the NASA Earth Science/Applied Science Program."
- GRACE/GRACE-FO JPL mascon results as reported in: Khorrami B., Arık F., Gündüz O. (2021). Land deformation and sinkhole occurrence in response to the fluctuations of groundwater storage: an integrated assessment of GRACE gravity measurements, ICESat/ICESat-2 altimetry data, and hydrologic models. *GIScience & Remote Sensing* 58(8), 1518-1542. https://doi.org/10.1080/15481603.2021.2000349 (dataset: https://podaac.jpl.nasa.gov/dataset/TELLUS_GRAC-GRFO_MASCON_GRID_RL06.3_V4)

**Methods**
- Allen R.G. et al. (1998). FAO Irrigation and Drainage Paper 56: Crop evapotranspiration.
- Doorenbos J., Kassam A.H. (1979). FAO Irrigation and Drainage Paper 33: Yield response to water.
- USDA-SCS effective rainfall method as implemented in FAO CROPWAT.

**Konya and crops**
- Süheri S., Topak R., Yavuz D. (2007). Farklı sulama programlarının şeker pancarı verimine ve su kullanım randımanına etkisi. *Selçuk Üniversitesi Ziraat Fakültesi Dergisi* 21(43), 37-45. https://dergipark.org.tr/en/download/article-file/3081270
- Tarı A.F., Özbahçe A., Ata G., Bilgiç C. (2016). Farklı sulama programlarının şekerpancarında kaliteye etkisi. *Tarla Bitkileri Merkez Araştırma Enstitüsü Dergisi* 25 (Özel sayı-2), 55-60. https://dergipark.org.tr/tr/download/article-file/263591
- Konya rotations survey: https://dergipark.org.tr/en/download/article-file/3113338
- Basin water balance (about 6.5 billion m³ used vs 4.5 billion m³ available a year; Minister of Agriculture, March 2020): https://hurriyetdailynews.com/turkeys-biggest-basin-struggles-with-drought-153228
- AFAD sinkhole mapping (739 in the Konya Closed Basin), reported by Anadolu Agency, August 2026: https://www.intellinews.com/number-of-mapped-sinkholes-in-turkey-s-konya-basin-reaches-739-462578/
- Konya as the leading sugar beet province: https://www.cumhuriyet.com.tr/yasam/seker-pancari-cennetinde-hasat-devam-ediyor-7-milyon-ton-beklenti-2131387
- Konya provincial yields 2023 (TÜİK via Konya İl Tarım ve Orman Müdürlüğü): https://konya.tarimorman.gov.tr/
- TMO 2026 wheat and barley prices: https://www.bloomberght.com/tmo-2026-yili-hububat-alim-ve-satis-fiyatlarini-acikladi-3778801
- Türkşeker 2026-27 sugar beet price (3.72 TL/kg); sunflower 2026 advance price (Karadenizbirlik, 34 TL/kg); maize (TÜRİB, 14.95 TL/kg); chickpea and dry bean from 2026 press reports (estimates).
- Rotation evidence: Şeker ve Hammaddeleri Yönetmeliği (2021); Ertürk & Ökten (2013) beet cyst nematode in Konya; TAGEM chickpea Ascochyta guidance; wheat-after-legume trials at Haymana (Ankara) and Bahri Dağdaş (Konya), https://dergipark.org.tr/en/download/article-file/390551 and https://core.ac.uk/download/232019243.pdf; UC IPM sugar beet nematode guidelines https://ipm.ucanr.edu/agriculture/sugarbeet/nematodes

**How the default costs were set (TL per decare, 2026, estimates).** No official Konya per-decare costs were found. Relative costs come from Erbaş (2025, *Tarım Ekonomisi Dergisi*, Yozgat, 2023 prices: wheat 1,815; barley 1,788; chickpea 1,742; sugar beet 7,497; dry bean 3,761), scaled by 2.5 for 2023-2026 input inflation. Irrigated wheat = 1.6 x rainfed wheat. Maize from Adana Çiftçiler Birliği 2025 (9.26 TL/kg) adjusted to 2026; sunflower set between wheat and maize. Treat these as placeholders and edit them for your farm.

**Software:** Streamlit, pandas, NumPy, SciPy (HiGHS), Plotly, requests — all open-source (Apache-2.0 / BSD / MIT).

## Use of SI (AI) tools
This project was built with help from Claude (Anthropic), a large language model. It was used for background research (finding and summarising sources on Konya crops, prices and rotation rules), for writing and testing the Python code in this repository, and for drafting the documentation and presentation text. The team chose the challenge, the region and the approach, reviewed the outputs, and checked key numbers against their sources (see "Check against field data"). AI-assisted files carry a note in their header. No AI-generated images, audio or video are used.

## License
Code: MIT (see LICENSE). NASA POWER data are freely available from NASA LaRC. Not an official NASA product.
