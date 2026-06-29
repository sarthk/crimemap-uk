# Data sources — verified endpoints (recon 2026-06-29)

All four sources confirmed live against real requests on 2026-06-29. All are
free under the Open Government Licence v3 (attribution required). Pin the dated
URLs for reproducible monthly builds.

## 1. Crime — police.uk street CSVs

No per-force-per-month CSV URL exists; CSVs only ship inside a zip.

- **Latest published month:** discover via `GET https://data.police.uk/api/crimes-street-dates`
  → JSON array, newest first; `[0].date` = latest (was **2026-04** on 2026-06-29).
  ⚠️ The per-element `stop-and-search` force list is **stop-and-search only** —
  it does NOT confirm street-level coverage. Verify by checking the extracted zip.
- **Rolling 12-month window (as of 2026-06):** `2025-05 … 2026-04`.
- **Route B (default, targeted ~tens of MB):** POST the filter form at
  `https://data.police.uk/data/` with `csrfmiddlewaretoken` (scraped from the GET),
  `date_from`, `date_to`, `forces=west-yorkshire`, `include_crime=on`, `Referer` header.
  → `302 /data/fetch/<UUID>/`. Poll that URL until it serves the zip
  ("…being generated… come back later" while building).
- **Route A (fallback, ~1.6 GB):** `GET https://data.police.uk/data/archive/<YYYY-MM>.zip`
  (or `latest.zip`) → 302 to `policeuk-data.s3.amazonaws.com`. Cumulative: one zip
  holds every force × ~3 years. Extract only WY street files.
- **Force id:** `west-yorkshire`. **In-zip path:** `<YYYY-MM>/<YYYY-MM>-west-yorkshire-street.csv`.
- **CSV columns:** `Crime ID, Month, Reported by, Falls within, Longitude, Latitude,
  Location, LSOA code, LSOA name, Crime type, Last outcome category, Context`.
- **Vintage:** LSOA codes are **2021** (changelog: switched 2011→2021 in June 2023).
- **Caveats:** `Crime ID` blank for some rows (e.g. anti-social behaviour); coords
  anonymised/snapped; some forces had submission gaps in early-2026 months — don't
  assume all 12 WY files exist, check.

## 2. LSOA 2021 boundaries — ONS Open Geography Portal (ArcGIS)

- **Service (FeatureServer, layer 0):**
  `https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Lower_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V5/FeatureServer/0`
  (BGC = generalised clipped, web-sized; BSC_V4 also available if smaller needed).
- **Query for GeoJSON:** `/query?where=<sql>&outFields=LSOA21CD,LSOA21NM&f=geojson`
- **Fields:** `LSOA21CD` (code), `LSOA21NM` (name), plus FID/LAT/LONG/BNG_*/GlobalID.
- **maxRecordCount = 2000.** National ~35k needs pagination (`resultOffset`/`resultRecordCount`) — that's Phase 2.
- **West Yorkshire = 1,404 LSOAs** (districts Leeds, Bradford, Kirklees, Calderdale,
  Wakefield) → **fits in ONE query** (1404 < 2000). WY filter:
  `LSOA21NM LIKE 'Leeds%' OR LSOA21NM LIKE 'Bradford%' OR LSOA21NM LIKE 'Kirklees%' OR LSOA21NM LIKE 'Calderdale%' OR LSOA21NM LIKE 'Wakefield%'`
  (or, more robust, filter to the LSOA codes present in the WY crime data).

## 3. LSOA 2021 population — ONS Census 2021 TS001 (via Nomis)

- **Bulk zip:** `https://www.nomisweb.co.uk/output/census/2021/census2021-ts001.zip`
  (~1.87 MB) → extract `census2021-ts001-lsoa.csv` (35,672 rows = 33,755 E01 + 1,917 W01).
- **Key column:** `geography code` (E01…/W01…, 2021 vintage).
- **Population column:** `Residence type: Total; measures: Value`
  (= household + communal establishment; do NOT use a single residence sub-type).
- **API alt (paginated, 25k/page):** `NM_2021_1.data.csv?...&geography=TYPE151&c2021_restype_3=0&measures=20100`.

## 4. Postcode → LSOA — postcodes.io

- **Single:** `GET https://api.postcodes.io/postcodes/{postcode}` (URL-encode space → `%20`).
  404 + `result:null` on bad postcode.
- **2021 LSOA code:** `result.codes.lsoa21` (the generic `result.codes.lsoa` currently
  mirrors 2021 but read `lsoa21` explicitly). Name: `result.lsoa21`. Coords:
  `result.latitude` / `result.longitude`.
- **Bulk:** `POST https://api.postcodes.io/postcodes` with `{"postcodes":[…]}` (≤100/req).
