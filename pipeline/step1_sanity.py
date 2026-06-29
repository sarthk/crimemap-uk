"""Phase-1, task 1: load West Yorkshire street CSVs and sanity-check them.

Run AFTER download.py has populated pipeline/data/raw/ with the monthly
street CSVs. This script does no network I/O — it only reads what's on disk.

Goal (straight from the brief): confirm the `LSOA code` column exists and
print the top crime categories as a sanity check, plus a few cheap integrity
signals (months covered, null-LSOA rate, unique LSOA count).

    python pipeline/step1_sanity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

import config


def find_street_csvs() -> list[Path]:
    """All West Yorkshire street CSVs under data/raw, any month subfolder."""
    pattern = f"*-{config.FORCE_ID}-street.csv"
    return sorted(config.RAW_DIR.rglob(pattern))


def load(files: list[Path]) -> pd.DataFrame:
    frames = []
    for f in files:
        df = pd.read_csv(f, dtype=str)  # everything as str; we only count here
        df["__source_file"] = f.name
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def main() -> int:
    files = find_street_csvs()
    if not files:
        print(
            f"No street CSVs found under {config.RAW_DIR}\n"
            f"  (looking for '*-{config.FORCE_ID}-street.csv')\n"
            f"Run download.py first.",
            file=sys.stderr,
        )
        return 1

    print(f"Found {len(files)} street CSV(s):")
    for f in files:
        print(f"  {f.relative_to(config.RAW_DIR)}")

    df = load(files)
    print(f"\nTotal rows: {len(df):,}")

    # --- The make-or-break column -------------------------------------------
    if config.COL_LSOA_CODE not in df.columns:
        print(
            f"\nFATAL: expected column '{config.COL_LSOA_CODE}' not present.\n"
            f"Columns seen: {list(df.columns)}",
            file=sys.stderr,
        )
        return 2
    print(f"'{config.COL_LSOA_CODE}' column: PRESENT")

    lsoa = df[config.COL_LSOA_CODE]
    n_null = lsoa.isna().sum()
    print(
        f"  rows with LSOA code: {len(df) - n_null:,} "
        f"({(1 - n_null / len(df)) * 100:.1f}%) — {n_null:,} blank"
    )
    print(f"  unique LSOA codes:  {lsoa.nunique():,}")
    # Vintage smell test: 2021 LSOA codes for E&W are E01*/W01*.
    sample = sorted(lsoa.dropna().unique())[:3]
    print(f"  sample codes:       {sample}")

    # --- Months covered ------------------------------------------------------
    if config.COL_MONTH in df.columns:
        months = sorted(df[config.COL_MONTH].dropna().unique())
        print(f"\nMonths covered ({len(months)}): {months[0]} … {months[-1]}")
        if len(months) != config.WINDOW_MONTHS:
            print(
                f"  NOTE: expected {config.WINDOW_MONTHS} months, found {len(months)}."
            )

    # --- Top crime categories (the requested sanity check) -------------------
    if config.COL_CRIME_TYPE in df.columns:
        counts = df[config.COL_CRIME_TYPE].value_counts()
        print("\nTop crime categories:")
        for cat, n in counts.items():
            print(f"  {n:>8,}  {cat}")

        # Flag anything the CSV uses that we haven't accounted for in config.
        unknown = set(counts.index) - config.ALL_CRIME_TYPES
        if unknown:
            print(f"\n  WARNING: unrecognised crime types (update config): {unknown}")

    print("\nSanity check complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
