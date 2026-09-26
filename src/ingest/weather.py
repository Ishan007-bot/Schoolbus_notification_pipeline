"""S4 - Open-Meteo historical weather for one NYC point (JSON API).

Completeness: one row per hour of the month (±1 for daylight-saving changeover),
and no missing precipitation values. The archive lags a few days behind today, so
a very recent month can legitimately fail this check - weather is supplementary,
so failure means a degraded run, not a halt.
"""
import json
import logging
from pathlib import Path

import pandas as pd

from src.config import month_bounds
from src.ingest.common import (SourceResult, check, failed_checks, latest_success, mark_success,
                               new_run_dir)
from src.utils import http
from src.utils.http import HTTPFailure

SOURCE = "weather"
log = logging.getLogger("ingest.weather")


def ingest_weather(month, cfg, raw_root, run_id, force=False, get=http.get):
    if not force:
        previous = latest_success(raw_root, SOURCE, month)
        if previous:
            run_dir, meta = previous
            log.info("reusing pull from run %s", run_dir.name)
            return SourceResult(SOURCE, "reused", meta["rows"], str(run_dir), meta["checks"], f"reused run {run_dir.name}")

    w = cfg["sources"]["weather"]
    start, end = month_bounds(month)
    params = {"latitude": w["latitude"], "longitude": w["longitude"], "timezone": w["timezone"],
              "start_date": str(start), "end_date": str(end), "hourly": ",".join(w["hourly"])}
    try:
        data = get(w["api_url"], params, http_cfg=cfg["http"]).json()
    except HTTPFailure as e:
        log.error("weather download failed: %s", e)
        return SourceResult(SOURCE, "failed", message=str(e))

    run_dir = new_run_dir(raw_root, SOURCE, month, run_id)
    (run_dir / "weather.json").write_text(json.dumps(data), encoding="utf-8")

    checks = completeness_checks(data, expected_hours=24 * end.day)
    failures = failed_checks(checks)
    hours = len(data.get("hourly", {}).get("time", []))
    if failures:
        message = "; ".join(f"{c}: {checks[c]['detail']}" for c in failures)
        log.warning("weather incomplete (%s)", message)
        return SourceResult(SOURCE, "failed", hours, str(run_dir), checks, message)

    mark_success(run_dir, {"source": SOURCE, "month": month, "run_id": run_id, "rows": hours, "checks": checks})
    log.info("weather: %d hourly rows", hours)
    return SourceResult(SOURCE, "ok", hours, str(run_dir), checks)


def completeness_checks(data, expected_hours):
    hourly = data.get("hourly", {})
    hours = len(hourly.get("time", []))
    missing_precip = sum(1 for v in hourly.get("precipitation", []) if v is None)
    return {
        "hour_count": check(abs(hours - expected_hours) <= 1, f"{hours} hours, expected {expected_hours}"),
        "precipitation_complete": check(hours > 0 and missing_precip == 0, f"{missing_precip} missing values"),
    }


def load_weather(run_dir):
    data = json.loads((Path(run_dir) / "weather.json").read_text(encoding="utf-8"))
    return pd.DataFrame(data["hourly"])
