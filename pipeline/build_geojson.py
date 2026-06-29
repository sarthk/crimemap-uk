"""Phase-1, tasks 2-3: join crime + population + boundaries → west-yorkshire.geojson.

The make-or-break step. police.uk tags each crime with its 2021 LSOA code, so
this is a plain key-join on the code — no point-in-polygon needed.

  1. Aggregate the 12 WY street CSVs to LSOA: per-category counts + monthly series.
  2. Join 2021 usual-resident population (TS001) → the per-capita denominator.
  3. Fetch the 2021 LSOA boundaries for exactly the LSOAs that appear in the crime
     data (batched ArcGIS IN-queries), so every crimed area gets a polygon.
  4. Compute the rolling-12-month rate per 1,000 residents for the DEFAULT bundle,
     a colourblind-safe percentile rank, and the low-residential flag.
  5. Write web/data/west-yorkshire.geojson (boundaries + the properties the brief
     specifies). Numbers + caveats; the map never editorialises.

Principles enforced here (the product *is* these — see CLAUDE.md):
  - Per-capita, not raw counts.   - count + rate + pop travel together.
  - Low-residential LSOAs are flagged, not painted max-severity.
  - The percentile distribution excludes flagged LSOAs so colour = real standing.

    python pipeline/build_geojson.py [--refresh-boundaries]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import config

UA = {"User-Agent": "crimemap-uk/0.1 (+OGL v3 data)"}


# --- 1. Aggregate crime -------------------------------------------------------
def load_crime() -> pd.DataFrame:
    files = sorted(config.RAW_DIR.rglob(f"*-{config.FORCE_ID}-street.csv"))
    if not files:
        sys.exit(f"No crime CSVs under {config.RAW_DIR}. Run download.py first.")
    cols = [config.COL_LSOA_CODE, config.COL_LSOA_NAME, config.COL_MONTH, config.COL_CRIME_TYPE]
    df = pd.concat([pd.read_csv(f, usecols=cols, dtype=str) for f in files], ignore_index=True)
    before = len(df)
    df = df.dropna(subset=[config.COL_LSOA_CODE])
    print(f"Crime rows: {before:,} ({before - len(df):,} dropped for blank LSOA code)")
    return df


def aggregate(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Return a per-LSOA frame (code-indexed) plus the ordered window months."""
    months = sorted(df[config.COL_MONTH].dropna().unique())

    # Full per-category breakdown (all 14 types), one row per LSOA.
    by_cat = (df.groupby([config.COL_LSOA_CODE, config.COL_CRIME_TYPE]).size()
                .unstack(fill_value=0)
                .reindex(columns=sorted(config.ALL_CRIME_TYPES), fill_value=0))

    # Default-bundle monthly series (drives the headline rate + sparkline).
    bundle = config.CATEGORY_BUNDLES[config.DEFAULT_BUNDLE]
    monthly = (df[df[config.COL_CRIME_TYPE].isin(bundle)]
                 .groupby([config.COL_LSOA_CODE, config.COL_MONTH]).size()
                 .unstack(fill_value=0)
                 .reindex(columns=months, fill_value=0)
                 .reindex(by_cat.index, fill_value=0))

    # Names: first non-null name per code (police.uk CSVs are consistent per code).
    names = (df.groupby(config.COL_LSOA_CODE)[config.COL_LSOA_NAME]
               .agg(lambda s: s.dropna().iloc[0] if s.notna().any() else ""))

    out = pd.DataFrame(index=by_cat.index)
    out["lsoa21nm"] = names
    out["all_crimes_total"] = by_cat.sum(axis=1)
    out["total_crimes"] = by_cat[bundle].sum(axis=1)       # default bundle
    out["by_category"] = [row.to_dict() for _, row in by_cat.iterrows()]
    out["monthly_counts"] = [list(map(int, row)) for _, row in monthly.iterrows()]
    return out, months


# --- 2. Population ------------------------------------------------------------
def join_population(out: pd.DataFrame) -> pd.DataFrame:
    pop = pd.read_csv(config.POP_CSV, dtype={config.POP_COL_CODE: str})
    pop_map = pop.set_index(config.POP_COL_CODE)[config.POP_COL_VALUE].astype(int)
    out["pop"] = out.index.map(pop_map)
    missing = int(out["pop"].isna().sum())
    if missing:
        # Vintage mismatch would surface here as null population.
        print(f"  WARNING: {missing} LSOAs have no 2021 population (vintage mismatch?).")
    return out


# --- 3. Boundaries (authoritative West Yorkshire region) ----------------------
def fetch_wy_boundaries(refresh: bool) -> dict:
    """Fetch the 2021 LSOA boundaries for the 5 WY districts — this defines the region."""
    if config.BOUNDARIES_CACHE.exists() and not refresh:
        gj = json.loads(config.BOUNDARIES_CACHE.read_text())
        print(f"Boundaries: cache hit ({len(gj['features'])} features).")
        return gj

    where = " OR ".join(f"{config.BOUNDARY_FIELD_NAME} LIKE '{d}%'" for d in config.WY_DISTRICTS)
    feats: list[dict] = []
    offset, PAGE = 0, 2000  # = maxRecordCount; WY (~1,404) fits in one page, but paginate anyway
    while True:
        params = {
            "where": where,
            "outFields": f"{config.BOUNDARY_FIELD_CODE},{config.BOUNDARY_FIELD_NAME}",
            "returnGeometry": "true",
            "outSR": "4326",          # WGS84 lon/lat for Leaflet
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": PAGE,
        }
        r = requests.post(config.BOUNDARIES_QUERY_URL, data=params, headers=UA, timeout=120)
        r.raise_for_status()
        page = r.json().get("features", [])
        feats.extend(page)
        print(f"  boundaries +{len(page)} (offset {offset}) …")
        if len(page) < PAGE:
            break
        offset += PAGE

    gj = {"type": "FeatureCollection", "features": feats}
    config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    config.BOUNDARIES_CACHE.write_text(json.dumps(gj, allow_nan=False))
    print(f"Boundaries: fetched {len(feats)} WY features → {config.BOUNDARIES_CACHE.name}")
    return gj


def align_to_region(out: pd.DataFrame, boundaries: dict, n_months: int) -> pd.DataFrame:
    """Restrict to the WY LSOA set; drop out-of-region crime; zero-fill crimeless WY LSOAs."""
    region = {f["properties"][config.BOUNDARY_FIELD_CODE]: f["properties"][config.BOUNDARY_FIELD_NAME]
              for f in boundaries["features"]}
    out_of_region = [c for c in out.index if c not in region]
    if out_of_region:
        n = int(out.loc[out_of_region, "all_crimes_total"].sum())
        print(f"  dropped {len(out_of_region)} out-of-region LSOAs ({n:,} crimes, "
              f"{100 * n / out['all_crimes_total'].sum():.2f}%).")
    out = out.reindex(list(region))  # keep WY set; crimeless WY LSOAs become NaN rows
    zero_cat = {c: 0 for c in sorted(config.ALL_CRIME_TYPES)}
    crimeless = out["all_crimes_total"].isna()
    for code in out.index[crimeless]:
        out.at[code, "by_category"] = dict(zero_cat)
        out.at[code, "monthly_counts"] = [0] * n_months
    out["all_crimes_total"] = out["all_crimes_total"].fillna(0).astype(int)
    out["total_crimes"] = out["total_crimes"].fillna(0).astype(int)
    out["lsoa21nm"] = out["lsoa21nm"].fillna(pd.Series(region))
    if int(crimeless.sum()):
        print(f"  zero-filled {int(crimeless.sum())} WY LSOAs with no crime in window.")
    return out


# --- 4. Metric, flags, percentile --------------------------------------------
def compute_metric(out: pd.DataFrame, n_months: int) -> pd.DataFrame:
    out["months"] = n_months
    pop = out["pop"]
    rate = out["total_crimes"] / pop * config.RATE_PER  # unrounded; rank on this
    rate = rate.where(np.isfinite(rate))                # pop 0/NaN → NaN, never ±inf
    out["rate_per_1000"] = rate.round(1)                # rounded for display only

    # (a) genuine low-residential population: the rate is a denominator ARTIFACT here,
    #     so grey + caveat (brief principle 3). Rare for 2021 LSOAs (pop floor ~1,000).
    out["low_pop_flag"] = (pop < config.LOW_POP_THRESHOLD) | pop.isna()

    # Rank over reliable-denominator LSOAs with a finite rate. The extreme-rate town
    # centres are INCLUDED — percentile is a RANK (robust to outliers), so they show at
    # their TRUE standing (dark), never hidden. Only true artifacts (low-pop / no rate)
    # are left out of the scale. Rank uses the UNROUNDED rate to avoid spurious ties.
    base_mask = ~out["low_pop_flag"] & rate.notna()
    base = rate[base_mask]
    extreme = float(np.percentile(base, config.OUTLIER_RATE_PCTL)) if len(base) else float("inf")
    out["high_rate_flag"] = (rate > extreme).fillna(False)
    out["percentile"] = base.rank(pct=True).round(3)    # null only for low-pop / no-rate

    # (b) high_rate is a CAUTION ANNOTATION, not a discard. Caveats are descriptive and
    #     hedged — never a causal claim about why crime happens (brief forbids that).
    def caveat(r):
        if r["low_pop_flag"]:
            return "Low residential population — the per-resident rate may overstate risk."
        if r["high_rate_flag"]:
            return ("Among the highest rates in West Yorkshire. Town/city-centre LSOAs often "
                    "record crime against non-residents (visitors, workers, shoppers) as well as "
                    "residents, so a per-resident rate can read high here — compare with care.")
        return None
    # dtype=object so None survives as JSON null (a plain list coerces None → NaN).
    out["caveat"] = pd.Series([caveat(r) for _, r in out.iterrows()], index=out.index, dtype=object)
    out["flagged"] = out["low_pop_flag"] | out["high_rate_flag"]  # = has a caveat to show

    print(f"  flags: {int(out['low_pop_flag'].sum())} low-pop, "
          f"{int(out['high_rate_flag'].sum())} high-rate-caution "
          f"(cutoff {extreme:.0f}/1,000); {len(out)} LSOAs total")
    return out


# --- 5. Assemble + write ------------------------------------------------------
def write_geojson(out: pd.DataFrame, boundaries: dict, n_months: int) -> None:
    by_code = {code: row for code, row in out.iterrows()}
    features, no_crime = [], 0
    for feat in boundaries["features"]:
        code = feat["properties"][config.BOUNDARY_FIELD_CODE]
        row = by_code.get(code)
        if row is None:
            no_crime += 1
            continue
        pop = row["pop"]
        feat["properties"] = {
            "lsoa21cd": code,
            "lsoa21nm": row["lsoa21nm"] or feat["properties"].get(config.BOUNDARY_FIELD_NAME, ""),
            "pop": None if pd.isna(pop) else int(pop),
            "months": int(row["months"]),
            "bundle": config.DEFAULT_BUNDLE,
            "total_crimes": int(row["total_crimes"]),
            "all_crimes_total": int(row["all_crimes_total"]),
            "rate_per_1000": None if pd.isna(row["rate_per_1000"]) else float(row["rate_per_1000"]),
            "by_category": {k: int(v) for k, v in row["by_category"].items()},
            "monthly_counts": row["monthly_counts"],
            "percentile": None if pd.isna(row["percentile"]) else float(row["percentile"]),
            "low_pop_flag": bool(row["low_pop_flag"]),      # denominator artifact → grey
            "high_rate_flag": bool(row["high_rate_flag"]),  # very high rate → show, but caution
            "flagged": bool(row["flagged"]),                # has a caveat to display
            "caveat": None if (row["caveat"] is None or pd.isna(row["caveat"])) else row["caveat"],
        }
        features.append(feat)

    rendered = {f["properties"]["lsoa21cd"] for f in features}
    missing_geom = sorted(set(by_code) - rendered)
    if missing_geom:
        print(f"  WARNING: {len(missing_geom)} crimed LSOAs have no boundary "
              f"(e.g. {missing_geom[:3]}) — they will not render.")
    if no_crime:
        print(f"  ({no_crime} fetched boundaries had no crime row — skipped.)")

    config.WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    gj = {
        "type": "FeatureCollection",
        "metadata": {
            "region": config.REGION_NAME,
            "source": "police.uk street-level crime (reported, anonymised, ~1-2mo lag) "
                      "+ ONS Census 2021 population + ONS LSOA 2021 boundaries — OGL v3",
            "default_bundle": config.DEFAULT_BUNDLE,
            "bundles": config.CATEGORY_BUNDLES,
            "window_months": n_months,
            "low_pop_threshold": config.LOW_POP_THRESHOLD,
            "note": "rate_per_1000 is REPORTED crime for the default bundle per 1,000 "
                    "residents over the rolling window; by_category lets the UI recompute "
                    "other bundles. low_pop_flag marks denominator artifacts (greyed, left "
                    "out of the colour scale). high_rate_flag marks the top ~1% of rates — "
                    "often town/city centres where crime against non-residents can elevate a "
                    "per-resident rate; these are shown at their true percentile WITH a "
                    "caveat, not hidden. See the per-feature 'caveat'.",
        },
        "features": features,
    }
    # allow_nan=False: fail loudly rather than ship NaN/Infinity (invalid JSON a browser rejects).
    config.OUTPUT_GEOJSON.write_text(json.dumps(gj, allow_nan=False))
    size_mb = config.OUTPUT_GEOJSON.stat().st_size / 1e6
    print(f"\nWrote {len(features):,} features → {config.OUTPUT_GEOJSON} ({size_mb:.1f} MB)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build west-yorkshire.geojson.")
    ap.add_argument("--refresh-boundaries", action="store_true",
                    help="ignore the cached boundaries and refetch from ArcGIS")
    args = ap.parse_args()

    df = load_crime()
    out, months = aggregate(df)
    n_months = len(months)
    print(f"LSOAs with crime: {len(out):,}; window {months[0]}..{months[-1]} ({n_months} months)")
    boundaries = fetch_wy_boundaries(refresh=args.refresh_boundaries)
    out = align_to_region(out, boundaries, n_months)  # scope to WY; drop spillover; zero-fill
    out = join_population(out)
    out = compute_metric(out, n_months)
    write_geojson(out, boundaries, n_months)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
