"""Validate stage: prepare -> apply rules -> write outputs -> stop the line if too much is broken.

Writes (overwritten on every run, atomically):
  data/processed/incidents/<month>/incidents_validated.parquet   every row, with flags
  data/output/<month>/quality_report_<month>.csv                 one row per rule + totals
The quality report is written even when the stage halts, as evidence of why.
"""
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.config import month_bounds, school_year_for_month
from src.ingest.incidents import load_incidents
from src.ingest.reference import load_reference
from src.ingest.weather import load_weather
from src.utils.files import atomic_write
from src.validate.prepare import prepare_incidents
from src.validate.rules import apply_rules

log = logging.getLogger("validate")

USABLE = {"ok", "reused", "fallback"}


class ValidationHalt(Exception):
    """Too many rows failed critical rules - something upstream is broken."""


def run_validate(month, ingest_results, cfg, processed_root, output_root, now=None):
    start, end = month_bounds(month)
    school_year = school_year_for_month(month)

    raw = load_incidents(ingest_results["incidents"].raw_dir)
    routes = _load_ref(ingest_results, "routes")
    sites = _load_ref(ingest_results, "sites")

    weather_daily = None
    if ingest_results["weather"].status in USABLE:
        hourly = load_weather(ingest_results["weather"].raw_dir)
        weather_daily = hourly.assign(date=hourly["time"].str[:10]).groupby("date")[["precipitation", "snowfall"]].sum()

    routes_year = routes[routes["School_Year"] == school_year] if routes is not None else None
    sites_year = sites[sites["School_Year"] == school_year] if sites is not None else None
    ctx = {
        "start": start, "end": end, "now": now or datetime.now(),
        "validation": cfg["validation"],
        "routes_year": routes_year,
        "site_codes": set(sites_year["OPT_Code"]) if sites_year is not None and len(sites_year) else None,
        "weather_daily": weather_daily,
    }

    df = prepare_incidents(raw, routes, school_year)
    df, report = apply_rules(df, ctx)

    total, invalid = len(df), int((~df["is_valid"]).sum())
    critical_pct = round(invalid / total * 100, 2) if total else 0.0
    report = pd.concat([report, pd.DataFrame([
        {"rule_id": "TOTAL", "level": "", "description": "rows validated", "status": "", "rows_flagged": total, "pct_flagged": 100.0},
        {"rule_id": "INVALID", "level": "critical", "description": "rows failing at least one critical rule",
         "status": "", "rows_flagged": invalid, "pct_flagged": critical_pct},
    ])], ignore_index=True)

    report_path = atomic_write(Path(output_root) / month / f"quality_report_{month}.csv",
                               lambda p: report.to_csv(p, index=False))
    for row in report.itertuples():
        if row.rule_id.startswith("V"):
            log.info("%s %-8s %-7s %6d rows (%5.2f%%)  %s", row.rule_id, row.level, row.status,
                     row.rows_flagged, row.pct_flagged, row.description)
    log.info("%d rows | %d valid | %d critical-flagged (%.2f%%) | report: %s",
             total, total - invalid, invalid, critical_pct, report_path)

    threshold = cfg["validation"]["halt_if_critical_fail_pct"]
    if critical_pct > threshold:
        raise ValidationHalt(f"{critical_pct}% of rows failed critical rules (threshold {threshold}%) - see {report_path}")

    validated_path = atomic_write(Path(processed_root) / "incidents" / month / "incidents_validated.parquet",
                                  lambda p: df.to_parquet(p, index=False))
    sources = df["vendor_source"].value_counts().to_dict()
    log.info("vendor attribution: %s", ", ".join(f"{k}={v}" for k, v in sources.items()))
    return {"rows": total, "valid": total - invalid, "critical_pct": critical_pct,
            "validated_path": str(validated_path), "report_path": str(report_path)}


def _load_ref(ingest_results, name):
    result = ingest_results[name]
    if result.status in USABLE or (result.status == "missing_year" and result.raw_dir):
        return load_reference(result.raw_dir, name)
    return None
