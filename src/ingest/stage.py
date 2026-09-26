"""Ingest stage: pull all four sources and decide halt vs degrade.

Policy (how much the KPI depends on each source):
  incidents  critical       any failure raises -> pipeline halts
  routes     critical       failed with no fallback copy -> halt; fallback / missing_year -> degraded
  sites      supplementary  failure -> degraded
  weather    supplementary  failure -> degraded
  a month that hasn't ended yet -> degraded (partial counts)
"""
import logging
from datetime import date

from src.config import month_bounds, school_year_for_month
from src.ingest.common import IngestError
from src.ingest.incidents import ingest_incidents
from src.ingest.reference import ingest_reference
from src.ingest.weather import ingest_weather

log = logging.getLogger("ingest")

DEGRADED_STATUSES = {"fallback", "missing_year", "failed"}


def run_ingest(month, cfg, raw_root, run_id, force=False, today=None):
    school_year = school_year_for_month(month)
    results = {}

    # A month that hasn't ended yet passes every completeness check but is still partial.
    period_unfinished = month_bounds(month)[1] >= (today or date.today())
    if period_unfinished:
        log.warning("%s has not finished yet - counts will be partial", month)

    results["incidents"] = ingest_incidents(month, cfg, raw_root, run_id, force)

    results["routes"] = ingest_reference("routes", school_year, cfg, raw_root, run_id, force)
    if results["routes"].status == "failed":
        raise IngestError("routes unavailable and no previous copy: incidents cannot be attributed to vendors")

    results["sites"] = ingest_reference("sites", school_year, cfg, raw_root, run_id, force)
    results["weather"] = ingest_weather(month, cfg, raw_root, run_id, force)

    degraded = [name for name, r in results.items() if r.status in DEGRADED_STATUSES]
    status = "degraded" if degraded or period_unfinished else "success"
    for name in degraded:
        log.warning("DEGRADED: %s -> %s (%s)", name, results[name].status, results[name].message)
    if period_unfinished:
        log.warning("DEGRADED: period %s is not finished", month)
    return status, results
