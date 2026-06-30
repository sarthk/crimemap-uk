"""Download the inputs the pipeline needs.

Crime (default) — West Yorkshire street CSVs for the rolling 12-month window.
There are NO per-force-per-month CSV URLs on police.uk; CSVs only ship inside a
zip. Three routes (recon-verified 2026-06):

  REMOTE (DEFAULT): the national archive https://data.police.uk/data/archive/
      <latest>.zip (302 → S3) is ~1.6 GB, but a ZIP's central directory is at the
      END, and S3 honours HTTP Range. So we read just the directory, then fetch
      ONLY the ~12 WY street entries (~65 MB) via range requests. Seconds, not the
      ~50 min a full 1.6 GB GET takes. Implemented with `remotezip`.

  --archive: stream the WHOLE 1.6 GB archive to disk (resumable on stall), then
      extract the WY files. Fallback if range requests are ever blocked.

  --custom: POST the filter form at /data/ → server queues a job at
      /data/fetch/<UUID>/; poll until the (small) zip is ready. Targeted, but
      generation is slow/unbounded (observed >20 min), so NOT the default.

Population (--population) — ONS Census 2021 TS001 usual-resident population at
2021 LSOA level, via the Nomis bulk zip.

    python pipeline/download.py                  # crime, REMOTE selective extraction
    python pipeline/download.py --archive         # crime, full 1.6 GB download (resumable)
    python pipeline/download.py --custom          # crime, custom POST download (slow)
    python pipeline/download.py --population        # TS001 population only
    python pipeline/download.py --all               # crime (REMOTE) + population

Boundaries are fetched in build_geojson.py (need geopandas + the WY LSOA set).
"""

from __future__ import annotations

import argparse
import io
import re
import time
import zipfile
from pathlib import Path

import requests
from remotezip import RemoteZip

import config

UA = {"User-Agent": "crimemap-uk/0.1 (+https://github.com/; OGL v3 data)"}
DATA_BASE = "https://data.police.uk/data/"
ARCHIVE_URL = "https://data.police.uk/data/archive/{month}.zip"
TS001_ZIP_URL = "https://www.nomisweb.co.uk/output/census/2021/census2021-ts001.zip"
TS001_MEMBER = "census2021-ts001-lsoa.csv"
TS001_OUT = config.INTERIM_DIR / TS001_MEMBER


def _log(msg: str) -> None:
    print(msg, flush=True)


# --- Month window -------------------------------------------------------------
def latest_available_month() -> str:
    """Newest published month per the police.uk dates API, e.g. '2026-04'."""
    r = requests.get(config.POLICE_UK_DATES_API, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()[0]["date"]


def window_months(latest: str, n: int = config.WINDOW_MONTHS) -> list[str]:
    """The n trailing 'YYYY-MM' strings ending at `latest`, inclusive."""
    y, m = (int(x) for x in latest.split("-"))
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def _resolve_archive_url(latest: str) -> tuple[str, int]:
    """Follow the data.police.uk → S3 redirect once; return (final_url, total_bytes)."""
    head = requests.head(ARCHIVE_URL.format(month=latest), headers=UA,
                         allow_redirects=True, timeout=60)
    head.raise_for_status()
    return head.url, int(head.headers.get("Content-Length") or 0)


# --- REMOTE: range-based selective extraction (default) -----------------------
STREET_RE = re.compile(r"(?:^|/)(\d{4}-\d{2})-([a-z0-9-]+)-street\.csv$")


def download_crime_remote(latest: str, months: list[str], force: str | None = config.FORCE_ID) -> list[str]:
    """Fetch street CSVs for the window via HTTP Range — one force, or ALL forces if force is None."""
    s3_url, total = _resolve_archive_url(latest)
    win = set(months)
    written = []
    with RemoteZip(s3_url) as z:
        targets = []
        for n in z.namelist():
            mm = STREET_RE.search(n)
            if mm and mm.group(1) in win and (force is None or mm.group(2) == force):
                targets.append((mm.group(1), mm.group(2), n))
        _log(f"  remote zip: {s3_url}  ({total >> 20 if total else '?'} MB; range-extracting {len(targets)} files)")
        for month, f, n in sorted(targets):
            dest_dir = config.RAW_DIR / month
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{month}-{f}-street.csv"
            dest.write_bytes(z.read(n))
            written.append(f"{month}/{dest.name}")
    return sorted(written)


# --- Route A: full national archive (resumable; --archive) --------------------
def download_crime_route_a(latest: str, max_stalls: int = 10) -> Path:
    """Stream the cumulative national archive to disk, resuming on stalls.

    A single 1.6 GB GET is prone to the S3 socket hanging mid-transfer. We use a
    short read timeout so a stall errors quickly, then reconnect with an HTTP
    Range header to continue from the bytes already on disk (S3 supports 206).
    """
    url = ARCHIVE_URL.format(month=latest)
    dest = config.RAW_DIR / f"_police-archive-{latest}.zip"
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Resolve the final (S3) URL + total size once, so Range GETs hit S3 directly.
    head = requests.head(url, headers=UA, allow_redirects=True, timeout=60)
    head.raise_for_status()
    final_url = head.url
    total = int(head.headers.get("Content-Length") or 0)
    pos = dest.stat().st_size if dest.exists() else 0
    _log(f"  GET {url}  (total {total >> 20 if total else '?'} MB; resuming from {pos >> 20} MB)")

    next_mark = ((pos >> 20) // 128 + 1) * 128 << 20
    stalls = 0
    while total == 0 or pos < total:
        headers = dict(UA)
        if pos:
            headers["Range"] = f"bytes={pos}-"
        try:
            with requests.get(final_url, headers=headers, stream=True,
                              timeout=(10, 60), allow_redirects=True) as r:
                if pos and r.status_code == 200:  # server ignored Range → restart
                    pos, next_mark = 0, 128 << 20
                r.raise_for_status()
                with open(dest, "ab" if pos else "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        pos += len(chunk)
                        if pos >= next_mark:
                            _log(f"    …{pos >> 20} MB" + (f" / {total >> 20}" if total else ""))
                            next_mark += 128 << 20
            if total == 0 or pos >= total:
                break
        except requests.exceptions.RequestException as e:
            stalls += 1
            if stalls > max_stalls:
                raise RuntimeError(f"giving up after {stalls} stalls at {pos:,}/{total:,} bytes") from e
            pos = dest.stat().st_size if dest.exists() else 0
            _log(f"  stall ({type(e).__name__}) at {pos:,} bytes — resuming {stalls}/{max_stalls}")
            time.sleep(min(2 * stalls, 15))

    if total and pos != total:
        raise RuntimeError(f"size mismatch: got {pos:,}, expected {total:,}")
    _log(f"  downloaded {pos:,} bytes → {dest.name}")
    return dest


# --- Route B: targeted custom download ----------------------------------------
def _zip_is_ready(resp: requests.Response) -> bytes | None:
    ct = resp.headers.get("Content-Type", "").lower()
    if "zip" in ct or resp.content[:4] == b"PK\x03\x04":
        return resp.content
    return None


def download_crime_route_b(months: list[str], poll_secs: int = 8, timeout_secs: int = 2400) -> Path:
    s = requests.Session()
    s.headers.update(UA)
    form = s.get(DATA_BASE, timeout=30)
    form.raise_for_status()
    m = re.search(r"name=['\"]csrfmiddlewaretoken['\"]\s+value=['\"]([^'\"]+)", form.text)
    if not m:
        raise RuntimeError("Could not find csrfmiddlewaretoken on the download form.")
    payload = {
        "csrfmiddlewaretoken": m.group(1),
        "date_from": months[0],
        "date_to": months[-1],
        "forces": config.FORCE_ID,
        "include_crime": "on",
        "description": f"crimemap-uk {config.FORCE_ID} street {months[0]}..{months[-1]}",
    }
    posted = s.post(DATA_BASE, data=payload, headers={"Referer": DATA_BASE},
                    timeout=60, allow_redirects=False)
    loc = posted.headers.get("Location")
    if posted.status_code not in (301, 302) or not loc:
        raise RuntimeError(f"Custom-download POST did not redirect to a fetch URL (HTTP {posted.status_code}).")
    fetch_url = loc if loc.startswith("http") else "https://data.police.uk" + loc
    _log(f"  job queued: {fetch_url}")

    deadline = time.monotonic() + timeout_secs
    waited = 0
    zip_bytes = None
    while time.monotonic() < deadline:
        r = s.get(fetch_url, timeout=120)
        zip_bytes = _zip_is_ready(r)
        if zip_bytes is None:
            for href in re.findall(r'href="([^"]+)"', r.text):
                if href.lower().endswith(".zip") or "amazonaws" in href.lower():
                    link = href if href.startswith("http") else "https://data.police.uk" + href
                    zip_bytes = _zip_is_ready(s.get(link, timeout=300))
                    if zip_bytes is not None:
                        break
        if zip_bytes is not None:
            _log(f"  ready after ~{waited}s ({len(zip_bytes):,} bytes)")
            break
        time.sleep(poll_secs)
        waited += poll_secs
        if waited % 64 == 0:
            _log(f"  still generating … {waited}s")
    if zip_bytes is None:
        raise TimeoutError(f"Custom download not ready after {timeout_secs}s: {fetch_url}")

    dest = config.RAW_DIR / f"_police-custom-{months[0]}_{months[-1]}.zip"
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(zip_bytes)
    return dest


# --- Shared: extract street CSVs for the window -------------------------------
def extract_street(zip_path: Path, months: set[str], force: str | None = config.FORCE_ID) -> list[str]:
    """Extract '<month>-<force>-street.csv' (one force, or ALL forces if None) into RAW_DIR/<month>/."""
    written = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            mm = STREET_RE.search(name)
            if not mm or mm.group(1) not in months or (force is not None and mm.group(2) != force):
                continue
            month, f = mm.group(1), mm.group(2)
            dest_dir = config.RAW_DIR / month
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{month}-{f}-street.csv"
            with zf.open(name) as src, open(dest, "wb") as out:
                out.write(src.read())
            written.append(f"{month}/{dest.name}")
    return sorted(written)


def download_crime(method: str, keep_archive: bool, force: str | None = config.FORCE_ID) -> None:
    latest = latest_available_month()
    months = window_months(latest)
    scope = force if force else "ALL forces (national)"
    _log(f"Crime [{scope}]: latest {latest}; window {months[0]}..{months[-1]} ({len(months)} months)")
    if method == "remote":
        written = download_crime_remote(latest, months, force)
    else:
        zip_path = (download_crime_route_b(months) if method == "custom"
                    else download_crime_route_a(latest))
        written = extract_street(zip_path, set(months), force)
        if not keep_archive and zip_path.name.startswith("_police-"):
            zip_path.unlink(missing_ok=True)
            _log(f"  removed staging archive {zip_path.name}")
    _log(f"Extracted {len(written)} street CSV(s) into {config.RAW_DIR}")
    missing = sorted(set(months) - {w.split('/')[0] for w in written})
    if missing:
        _log(f"  NOTE: no street file for {missing} (submission gap?).")


# --- Population ---------------------------------------------------------------
def download_population() -> None:
    config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"Population: GET {TS001_ZIP_URL}")
    r = requests.get(TS001_ZIP_URL, headers=UA, timeout=300)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        member = next((n for n in zf.namelist() if n.endswith(TS001_MEMBER)), None)
        if member is None:
            raise RuntimeError(f"{TS001_MEMBER} not found in TS001 zip ({zf.namelist()})")
        TS001_OUT.write_bytes(zf.read(member))
    _log(f"  wrote {TS001_OUT}")


def download_price() -> None:
    config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"Price: GET {config.HPSSA_PRICE_ZIP_URL}")
    r = requests.get(config.HPSSA_PRICE_ZIP_URL, headers=UA, timeout=300)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        member = next((n for n in zf.namelist() if n.lower().endswith(".xls")), None)
        if member is None:
            raise RuntimeError(f"no .xls in HPSSA zip ({zf.namelist()})")
        config.PRICE_XLS.write_bytes(zf.read(member))
    _log(f"  wrote {config.PRICE_XLS} ({config.PRICE_XLS.stat().st_size >> 20} MB)")


def download_rent() -> None:
    config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"Rent: GET {config.PIPR_XLSX_URL}")
    r = requests.get(config.PIPR_XLSX_URL, headers=UA, timeout=300)
    r.raise_for_status()
    config.RENT_XLSX.write_bytes(r.content)
    _log(f"  wrote {config.RENT_XLSX} ({config.RENT_XLSX.stat().st_size >> 20} MB)")
    _log(f"Rent join: GET LSOA→LAD lookup")
    r2 = requests.get(config.LSOA_LAD_URL, headers=UA, timeout=120)
    r2.raise_for_status()
    config.LSOA_LAD_CSV.write_bytes(r2.content)
    _log(f"  wrote {config.LSOA_LAD_CSV} ({config.LSOA_LAD_CSV.stat().st_size >> 10} KB)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Download CrimeMap UK inputs.")
    ap.add_argument("--archive", action="store_true",
                    help="crime via the full 1.6 GB national archive (resumable) instead of range extraction")
    ap.add_argument("--custom", action="store_true",
                    help="crime via the targeted custom POST download (slow server-side generation)")
    ap.add_argument("--national", action="store_true",
                    help="ingest ALL forces (England & Wales) — Phase 2 — not just West Yorkshire")
    ap.add_argument("--keep-archive", action="store_true",
                    help="keep the staging zip instead of deleting it after extraction")
    ap.add_argument("--population", action="store_true", help="download TS001 population")
    ap.add_argument("--price", action="store_true", help="download ONS HPSSA median price (LSOA)")
    ap.add_argument("--rent", action="store_true", help="download ONS PIPR rent + LSOA→LAD lookup")
    ap.add_argument("--all", action="store_true", help="crime + population + price + rent")
    args = ap.parse_args()

    if (args.population or args.price or args.rent) and not args.all:  # data-only downloads
        if args.population:
            download_population()
        if args.price:
            download_price()
        if args.rent:
            download_rent()
        return 0

    force = None if args.national else config.FORCE_ID
    # Range extraction (remote) is the default for both: the single full-archive
    # stream throttles badly on S3, whereas ~500 short range reads stay fast.
    # --archive forces the full resumable download if you prefer one transfer.
    method = "custom" if args.custom else "archive" if args.archive else "remote"
    download_crime(method=method, keep_archive=args.keep_archive, force=force)
    if args.all:
        download_population()
        download_price()
        download_rent()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
