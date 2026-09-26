"""S2 Routes and S3 Sites - full CSV bulk downloads (the file-based retrieval mode).

Each file holds every school year; checks focus on the year being processed.
Outcomes:
  ok            downloaded, all checks pass
  reused        a successful download for this school year already exists (no --force)
  missing_year  file is fine but has no rows for this school year (a real gap in OPT's data)
  fallback      download or structural checks failed, previous good copy used instead
  failed        download failed and there is no previous copy
"""
import logging

import pandas as pd

from src.ingest.common import (IngestError, SourceResult, check, failed_checks, latest_success,
                               mark_success, md5_of, new_run_dir)
from src.utils import http
from src.utils.http import HTTPFailure

log = logging.getLogger("ingest.reference")

# Column names as they appear in the CSV header (they differ from the API field names).
REQUIRED_COLUMNS = {
    "routes": ["School_Year", "Route_Number", "Service_Type", "Vendor_Code", "Vendor_Name"],
    "sites": ["School_Year", "OPT_Code", "Name", "Site_Type", "City", "Latitude", "Longitude"],
}


def ingest_reference(name, school_year, cfg, raw_root, run_id, force=False, get=http.get):
    previous = latest_success(raw_root, name, school_year)

    # Reuse unless forced - but a --force backfill still downloads each school year only once per run.
    if previous and (not force or previous[0].name == run_id):
        run_dir, meta = previous
        status = "reused" if meta["year_rows"] > 0 else "missing_year"
        log.info("%s: reusing download from run %s (%d rows for %s)", name, run_dir.name, meta["year_rows"], school_year)
        return SourceResult(name, status, meta["year_rows"], str(run_dir), meta["checks"], f"reused run {run_dir.name}")

    try:
        response = get(cfg["sources"][name]["csv_url"], http_cfg=cfg["http"])
        run_dir = new_run_dir(raw_root, name, school_year, run_id)
        path = run_dir / f"{name}.csv"
        path.write_bytes(response.content)

        previous_rows = previous[1]["year_rows"] if previous else None
        checks, total_rows, year_rows = file_checks(
            path, name, school_year, previous_rows, cfg["validation"]["reference_rowcount_tolerance_pct"])
        structural = [c for c in failed_checks(checks) if c != "has_school_year"]
        if structural:
            raise IngestError("; ".join(f"{c}: {checks[c]['detail']}" for c in structural))
    except (HTTPFailure, IngestError) as e:
        if previous:
            run_dir, meta = previous
            log.warning("%s: %s -> falling back to previous copy from run %s", name, e, run_dir.name)
            return SourceResult(name, "fallback", meta["year_rows"], str(run_dir), meta["checks"],
                                f"new download unusable ({e}); using run {run_dir.name}")
        log.error("%s: %s and no previous copy exists", name, e)
        return SourceResult(name, "failed", message=str(e))

    meta = {"source": name, "school_year": school_year, "run_id": run_id, "total_rows": total_rows,
            "year_rows": year_rows, "bytes": path.stat().st_size, "md5": md5_of(path), "checks": checks}
    mark_success(run_dir, meta)

    if year_rows == 0:
        log.warning("%s: file has no rows for %s - vendor/site attribution will be limited", name, school_year)
        return SourceResult(name, "missing_year", 0, str(run_dir), checks, f"no {school_year} rows in file")

    log.info("%s: %d rows total, %d for %s, md5 %s", name, total_rows, year_rows, school_year, meta["md5"][:8])
    return SourceResult(name, "ok", year_rows, str(run_dir), checks)


def file_checks(path, name, school_year, previous_year_rows, tolerance_pct):
    size = path.stat().st_size
    if size == 0:
        return {"not_empty": check(False, "0 bytes")}, 0, 0

    # dtype=str keeps leading zeros in codes such as OPT_Code "01001"
    df = pd.read_csv(path, dtype=str)
    missing = [c for c in REQUIRED_COLUMNS[name] if c not in df.columns]
    year_rows = int((df["School_Year"] == school_year).sum()) if "School_Year" in df.columns else 0

    checks = {
        "not_empty": check(len(df) > 0, f"{size:,} bytes, {len(df):,} rows"),
        "expected_columns": check(not missing, f"missing: {missing}" if missing else "all present"),
        "has_school_year": check(year_rows > 0, f"{year_rows} rows for {school_year}"),
    }
    if previous_year_rows:
        change = abs(year_rows - previous_year_rows) / previous_year_rows * 100
        checks["rowcount_stable"] = check(change <= tolerance_pct,
                                          f"{year_rows} vs previous {previous_year_rows} ({change:.1f}% change)")
    return checks, len(df), year_rows


def load_reference(run_dir, name):
    return pd.read_csv(f"{run_dir}/{name}.csv", dtype=str)
