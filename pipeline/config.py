"""Central configuration for the CrimeMap UK pipeline.

Phase 1 ships ONE region end-to-end: West Yorkshire (includes Leeds).
Everything here is deliberately small and explicit — this file is the single
place where the project's decisions (the metric, the bundles, the thresholds)
are encoded. See CLAUDE.md for the full brief.
"""

from __future__ import annotations

from pathlib import Path

# --- Paths --------------------------------------------------------------------
PIPELINE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PIPELINE_DIR.parent
DATA_DIR = PIPELINE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"          # downloaded zips + extracted CSVs (gitignored)
INTERIM_DIR = DATA_DIR / "interim"  # boundaries GeoJSON, population CSV (gitignored)
WEB_DATA_DIR = PROJECT_DIR / "web" / "data"  # published artifact lives here (committed)
OUTPUT_GEOJSON = WEB_DATA_DIR / "west-yorkshire.geojson"

# --- Phase 2 (national) -------------------------------------------------------
NATIONAL_BOUNDARIES_CACHE = INTERIM_DIR / "national-boundaries.geojson"
NATIONAL_SLIM_GEOJSON = INTERIM_DIR / "national-slim.geojson"   # scalars only → tippecanoe input
NATIONAL_PMTILES = WEB_DATA_DIR / "national.pmtiles"            # vector tiles (committed)
NATIONAL_DETAILS = WEB_DATA_DIR / "national-details.json"       # code → {by_category, monthly} (committed)
PMTILES_LAYER = "lsoa"                                          # tippecanoe -l → MapLibre source-layer

# --- Price layer (ONS HPSSA Dataset 46 — median price paid by LSOA) -----------
# A SEPARATE, transparent layer (never blended into a black-box "area score").
# Caveat: 2011-LSOA keyed, final release = year ending Mar 2023. We join by direct
# code match (~92% of 2021 LSOAs); changed/suppressed LSOAs simply show no price.
# Fresher/2021-native would mean computing medians from Land Registry pp-complete
# (~5.5 GB) + a postcode→2021-LSOA lookup — deferred until the layer earns it.
HPSSA_PRICE_ZIP_URL = ("https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/housing/"
    "datasets/medianpricepaidbylowerlayersuperoutputareahpssadataset46/current/"
    "hpssadataset46medianpricepaidforresidentialpropertiesbylsoa.zip")
PRICE_XLS = INTERIM_DIR / "hpssa46-median-price-lsoa.xls"
PRICE_SHEET = "1a"
PRICE_HEADER_ROW = 5
PRICE_CODE_COL = "LSOA code"
PRICE_VALUE_COL = "Year ending Mar 2023"
PRICE_LABEL = "ONS · median sold price · year to Mar 2023"

# --- Region (Phase 1) ---------------------------------------------------------
# police.uk force identifier as it appears in bulk-CSV filenames:
#   <YYYY-MM>/<YYYY-MM>-<FORCE_ID>-street.csv
FORCE_ID = "west-yorkshire"
REGION_NAME = "West Yorkshire"
# The 5 metropolitan districts that ARE West Yorkshire. LSOA names are prefixed
# with the local-authority district ("Leeds 001A"), so this defines the region
# authoritatively — police.uk also tags ~0.04% of WY-force crimes to LSOAs just
# outside the county (Selby, Harrogate, …); those are dropped as out-of-region.
WY_DISTRICTS = ["Leeds", "Bradford", "Kirklees", "Calderdale", "Wakefield"]

# --- The metric ---------------------------------------------------------------
# Rate = (crimes in LSOA over period / resident population) * RATE_PER
RATE_PER = 1_000
# Rolling window. LSOA-month counts are tiny/noisy; 12 months stabilises them.
WINDOW_MONTHS = 12
# Low-residential trap: tiny denominators produce absurd rates. Flag & grey these.
LOW_POP_THRESHOLD = 500  # tune later

# --- police.uk crime types (exact, canonical strings) -------------------------
# These are the standard categories used across the police.uk street CSVs.
ALL_CRIME_TYPES = {
    "Anti-social behaviour",
    "Bicycle theft",
    "Burglary",
    "Criminal damage and arson",
    "Drugs",
    "Other crime",
    "Other theft",
    "Possession of weapons",
    "Public order",
    "Robbery",
    "Shoplifting",
    "Theft from the person",
    "Vehicle crime",
    "Violence and sexual offences",
}

# --- Category bundles (user-toggleable in the UI) -----------------------------
# Default view: what actually maps resident risk.
RESIDENTIAL_RISK = [
    "Burglary",
    "Robbery",
    "Violence and sexual offences",
    "Vehicle crime",
    "Criminal damage and arson",
    "Theft from the person",
]
# Footfall-heavy: available but OFF by default (these track footfall, not residency).
FOOTFALL_HEAVY = [
    "Shoplifting",
    "Anti-social behaviour",
    "Public order",
    "Other theft",
    "Drugs",
]
# The bundle is a *choice* and the UI must say so plainly — never hide it.
CATEGORY_BUNDLES = {
    "residential_risk": RESIDENTIAL_RISK,  # default
    "footfall_heavy": FOOTFALL_HEAVY,
    "all": sorted(ALL_CRIME_TYPES),
}
DEFAULT_BUNDLE = "residential_risk"

# --- police.uk CSV schema -----------------------------------------------------
# Street CSV header (confirmed against the brief; sanity-checked at load time):
#   Crime ID, Month, Reported by, Falls within, Longitude, Latitude, Location,
#   LSOA code, LSOA name, Crime type, Last outcome category, Context
COL_LSOA_CODE = "LSOA code"
COL_LSOA_NAME = "LSOA name"
COL_CRIME_TYPE = "Crime type"
COL_MONTH = "Month"

# --- Data source endpoints (recon-verified; see SOURCES.md) -------------------
# The dates API lists every published month.
POLICE_UK_DATES_API = "https://data.police.uk/api/crimes-street-dates"
POLICE_UK_DATA_PAGE = "https://data.police.uk/data/"

# LSOA (December 2021) Boundaries EW BGC V5 — generalised clipped, web-sized.
BOUNDARIES_QUERY_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Lower_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V5/FeatureServer/0/query"
)
BOUNDARY_FIELD_CODE = "LSOA21CD"
BOUNDARY_FIELD_NAME = "LSOA21NM"
BOUNDARIES_CACHE = INTERIM_DIR / "wy-boundaries.geojson"

# --- Population join ----------------------------------------------------------
POP_CSV = INTERIM_DIR / "census2021-ts001-lsoa.csv"
POP_COL_CODE = "geography code"
POP_COL_VALUE = "Residence type: Total; measures: Value"

# --- Low-residential / footfall caveat ----------------------------------------
# The brief's "low residential population" trap. In practice 2021 LSOAs have a
# population floor (~1,000+), so `pop < LOW_POP_THRESHOLD` almost never fires —
# the real manifestation is the town/city-centre LSOA whose rate is inflated by
# non-resident footfall (nightlife, shopping). We flag the extreme-rate tail so
# those areas are greyed + caveated (not painted max-severity) and excluded from
# the colour distribution. Numbers stay visible; only the framing is cautious.
OUTLIER_RATE_PCTL = 99          # rate at/above this percentile (of non-low-pop LSOAs) → footfall caveat
