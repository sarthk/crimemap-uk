# CrimeMap UK

A web map of **reported crime in England & Wales, aggregated to LSOA and
normalised per resident** — so a renter, student, or buyer can judge how safe an
*area to live in* actually is. It shows the numbers (count + rate + population,
always together) and lets the user draw their own conclusions. It is a calm
reference tool, **not** a "crime is exploding" shock piece.

> **Phase 1 ships ONE region end-to-end: West Yorkshire (incl. Leeds).**
> National coverage is Phase 2 (PMTiles + MapLibre). See [CLAUDE.md](CLAUDE.md)
> for the full build brief and the non-negotiable principles.

## Layout

```
pipeline/   Python: download → aggregate to LSOA → join population → write GeoJSON
  config.py          metric, category bundles, thresholds, paths (single source of truth)
  download.py        fetch West Yorkshire street CSVs (rolling 12 months) + boundaries + population
  step1_sanity.py    load CSVs, confirm `LSOA code` column, print top categories  ← Phase-1 task 1
  build_geojson.py   compute 12-mo rate + bundles + flags → web/data/west-yorkshire.geojson
  data/raw/          downloaded zips + extracted CSVs (gitignored)
  data/interim/      cached boundaries + population (gitignored)
web/        Static Leaflet site (choropleth + postcode search + LSOA panel + methodology)
  data/              published west-yorkshire.geojson (committed → served by GitHub Pages)
```

## The metric

`rate_per_1000 = (crimes in LSOA over rolling 12 months / resident population) × 1000`

- **Per-capita, never raw counts** — raw counts map footfall, not resident risk.
- **Low-residential flag** — LSOAs with population `< ~500` (or extreme outlier
  rates from a tiny denominator) are greyed, not painted red.
- **Default bundle = "Residential risk"**; footfall-heavy categories and "All
  crime" are explicit, labelled toggles.

## Data sources (all free, OGL v3 — attribution required)

| What | Source |
|------|--------|
| Crime | [police.uk bulk CSV](https://data.police.uk/data/) |
| LSOA 2021 boundaries | ONS Open Geography Portal (generalised clipped GeoJSON) |
| LSOA 2021 population | ONS Census 2021 (TS001 / LSOA estimates) |
| Postcode → LSOA | [postcodes.io](https://api.postcodes.io) |

**Vintage gotcha:** police.uk now tags crimes with **2021** LSOA codes, so
boundaries *and* population must also be 2021 vintage or the join fails silently.

## Running the pipeline

```bash
python -m pip install -r pipeline/requirements.txt
python pipeline/download.py        # → pipeline/data/raw/<YYYY-MM>/...-west-yorkshire-street.csv
python pipeline/step1_sanity.py    # confirm columns + top categories
python pipeline/build_geojson.py   # → web/data/west-yorkshire.geojson
```

View the map locally (it's a static site — no build step):

```bash
python -m http.server 8000 --directory web   # then open http://localhost:8000
```

The "live tracker" is a monthly re-run + redeploy — crime data is monthly, so a
monthly refresh *is* real-time for this domain. No streaming, no backend.

## Status

- [x] Repo scaffold + config
- [x] Download — West Yorkshire, rolling 12 months (`2025-05…2026-04`), via HTTP-range
      selective extraction from the national archive (~66 MB, ~1 min, not 1.6 GB)
- [x] Population — TS001 2021 LSOA usual residents downloaded
- [x] Sanity check (`step1_sanity.py`) — `LSOA code` present, 295,480 crimes,
      categories sane; **crime→2021 population join = 100%** (vintage aligned)
- [x] `build_geojson.py` — boundaries (1,404 WY LSOAs, code-join, no spatial join),
      per-capita rate + bundles + low-pop/high-rate flags + percentile →
      `web/data/west-yorkshire.geojson` (3.9 MB, strict-JSON valid). Reviewed by a
      21-agent adversarial workflow; blocker (invalid `NaN` JSON) + honesty fixes applied.
- [x] Leaflet frontend — a single self-contained `web/index.html` (CSS + JS inlined, so it
      can't render unstyled if assets fail to resolve). Percentile choropleth (colourblind-safe
      YlOrRd, Carto Positron basemap), postcode search, detail panel (rate, stat strip, percentile
      bar, category bars, sparkline, footfall caveat), client-side bundle toggles, methodology
      dialog + OGL attribution, loading/error states. Verified in-browser.
- [ ] Deploy to GitHub Pages
