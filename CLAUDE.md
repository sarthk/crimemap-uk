# CrimeMap UK — Build Brief (Reference Tool)

**For: Claude Code · Project owner: Sarthak**
Paste this in as the project's spec/README. It encodes the decisions; you write the code.

---

## What we're building

A web map of **reported crime in England & Wales, aggregated to LSOA and normalised per resident**, so a renter / student / property buyer can judge how safe an *area to live in* actually is. It shows numbers and lets users draw their own conclusions. It is **not** a "crime is exploding" shock piece and must never read as one.

**Phase 1 ships ONE region end-to-end: West Yorkshire (includes Leeds).** Only after that is genuinely good do we go national (Phase 2). The data and boundaries are national, but the browser performance and the polish are not — resist building national first.

---

## Non-negotiable principles (the product *is* these)

1. **Per-capita, not raw counts.** Raw counts map footfall, not resident risk. A busy-but-safe high street must never outrank a quiet street with a burglary problem. (Proven on April 2026 Leeds data: the city-centre 350m zone had **204 crimes vs 9** in a typical residential zone — 23×, almost entirely footfall.)
2. **Show count + rate + population together, always.** Never a colour with no context. The denominator is part of the truth.
3. **Handle the low-residential trap.** Per-capita has its own failure mode: a city-centre LSOA with few residents but heavy footfall crime produces an absurd rate. Flag/grey these as *"low residential population — rate may overstate risk."* Don't paint them blood-red.
4. **Label it "reported crime."** It is reported + anonymised + geocoded-to-nearest-point + ~1–2 months lagged + monthly. Say so. A methodology section is mandatory, not optional.
5. **Descriptive, never editorialising.** No "look how bad it's getting." No immigration framing or any causal claim. Numbers + caveats; the user concludes.
6. **Attribute the source.** police.uk and ONS data are Open Government Licence v3 — free to use (including commercially, later) with attribution. Credit them visibly.

---

## Data sources (all free, all OGL v3)

**1. Crime — police.uk bulk CSV** — https://data.police.uk/data/
Download monthly archives per force (West Yorkshire for Phase 1, last 12 months). Street CSV columns:
`Crime ID, Month, Reported by, Falls within, Longitude, Latitude, Location, LSOA code, LSOA name, Crime type, Last outcome category, Context`

- **KEY SIMPLIFICATION:** the CSV already tags each crime with its **LSOA code** → no point-in-polygon spatial join needed. Just `groupby('LSOA code')`.
- **VINTAGE — THE GOTCHA:** police.uk now uses **2021 LSOA** codes. Boundaries *and* population must also be **2021 vintage** or the join fails silently (symptom: null population, blank/missing areas, divide-by-zero). Use recent months for Phase 1 (all 2021 now); deep history mixes 2011/2021 vintages, so avoid it for now.
- The API (`https://data.police.uk/api`) is fine for spot checks; bulk CSV is the ingestion path. On your laptop, both download directly — the proxy hack from our chat was a sandbox-only limitation, ignore it here.

**2. LSOA 2021 boundaries — ONS Open Geography Portal**
Search **"LSOA (December 2021) Boundaries EW"** and take a **generalised / super-generalised clipped (BGC or BSC)** version as **GeoJSON**. Full-resolution is hundreds of MB; the generalised version is web-sized. ~35,000 LSOAs nationally.

**3. LSOA 2021 population — ONS Census 2021**
Usual resident population per 2021 LSOA (ONS / NOMIS — e.g. table **TS001**, or the LSOA population estimates). A CSV mapping `LSOA 2021 code → population`. This is your per-capita denominator. Verify it's 2021-coded.

**4. Postcode search — postcodes.io** (free API)
`GET https://api.postcodes.io/postcodes/{postcode}` → returns lat/lng + LSOA + codes. Powers "type your postcode, jump to your area." Verify it returns **2021** LSOA codes.

**5. Heads-up — paid incumbent: UKCrimeStats.com** (£7.49/mo) already does LSOA crime *rates*, crime-per-hectare, 2021 shapes. So per-capita is **not** the moat. The moat is honesty + UX: explicit footfall caveat, low-residential handling, renter-focused clarity, clean free/freemium access. Build the version they didn't.

---

## The metric (the actual IP)

- **Rate = (crimes in LSOA over period ÷ resident population) × 1,000.**
- **Period = rolling 12 months**, not a single month. LSOA-month counts are tiny and noisy; 12 months stabilises them. Keep monthly granularity for a trend sparkline.
- **Category bundles (user-toggleable):**
  - **Default — "Residential risk":** Burglary, Robbery, Violence and sexual offences, Vehicle crime, Criminal damage and arson, Theft from the person.
  - **Footfall-heavy (off by default, available):** Shoplifting, Anti-social behaviour, Public order, Other theft, Drugs.
  - Plus an **"All crime"** toggle. State plainly the bundle is a choice; don't hide it.
- **Low-pop flag:** if population `< ~500` (tune it) OR the rate is an extreme outlier driven by a tiny denominator → mark `low_pop_flag` and render grey/hatched with the caveat, not max-severity colour.
- **Colour scale:** sequential, colourblind-safe (e.g. ColorBrewer YlOrRd). Break by percentile (national in Phase 2, city in Phase 1) so colour = relative standing, and label what the bands mean.

---

## Pipeline output → frontend input (GeoJSON feature `properties`)

```json
{
  "lsoa21cd": "E01011...",
  "lsoa21nm": "Leeds 0XX",
  "pop": 1583,
  "months": 12,
  "total_crimes": 412,
  "rate_per_1000": 260.3,
  "by_category": { "Burglary": 21, "Violence and sexual offences": 88, "...": 0 },
  "monthly_counts": [33, 29, 41, "...12 values"],
  "percentile": 0.74,
  "low_pop_flag": false
}
```

---

## Stack & architecture

**Phase 1 (ship this) — static site, no backend, no database.**
- `pipeline/` — Python (pandas, geopandas): read West Yorkshire CSVs (12 months) → `groupby` LSOA code → join 2021 population → compute 12-mo rate + category breakdown + flags → write one `data/west-yorkshire.geojson` (boundaries + properties above).
- `web/` — static frontend: **Leaflet** choropleth + postcode search (postcodes.io) + click-to-open LSOA panel (rate, count, pop, category breakdown, trend sparkline, percentile) + methodology page. Calm chrome (see design note).
- Deploy: **GitHub Pages** (your CarbonX workflow). The "live tracker" = re-run pipeline monthly + redeploy. Crime data is monthly, so a monthly refresh *is* real-time for this domain. **Do not build streaming.**

**Phase 2 (only after Phase 1 is genuinely good) — national.**
The browser can't load ~35k LSOA polygons as raw GeoJSON. Convert national LSOA + stats to **PMTiles** (tippecanoe) and render with **MapLibre GL**. Pipeline ingests all ~43 forces. Same metric, same principles.

---

## Design note

Reference-tool aesthetic: cool, neutral, instrument-like. Muted basemap (Carto Positron). The **data colours are the only saturated thing** on the page. Postcode search front-and-centre. Monospace numerals for figures. The methodology link is prominent, not buried. (We have a working visual reference from the POC — match that restraint; don't make it feel like a shock-news dashboard.)

---

## Phase 1 task list (suggested order)

1. **Pipeline skeleton** — download + load West Yorkshire street CSVs (last 12 months); confirm the `LSOA code` column exists; print top crime categories as a sanity check.
2. **The make-or-break step** — get 2021 LSOA boundaries (generalised GeoJSON) + 2021 LSOA population; **join them to the crime LSOA codes and count unmatched rows (should be ~0).** If unmatched is high, you have a vintage mismatch — fix before going further.
3. **Compute** rate + bundles + flags; write `west-yorkshire.geojson`.
4. **Leaflet choropleth** rendering the GeoJSON; colour scale + legend.
5. **Postcode search** (postcodes.io) → fly to the user's LSOA.
6. **Click panel** — rate, count, pop, category breakdown, 12-month sparkline, percentile.
7. **Category toggles + low-pop handling + methodology section + OGL attribution.**
8. **Deploy** to GitHub Pages.

---

## Out of scope (v1)

Real-time / live feeds · backend or database · user accounts · Europe / rest of world (street-level open crime data doesn't exist there) · black-box severity scores · national coverage (that's Phase 2).
