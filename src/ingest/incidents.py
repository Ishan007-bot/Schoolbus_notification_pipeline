"""S1 - Bus Breakdown and Delays (Socrata API, paginated JSON).

Completeness is proven, not assumed:
  1. ask the API for count(*) over the month BEFORE pulling
  2. page through ordered by busbreakdown_id
  3. check received == expected, no duplicate IDs, every row inside the month, at least one row
Any failed check raises IngestError: this source is critical, so the pipeline halts.
"""
import json
import logging
from pathlib import Path

import pandas as pd

from src.config import month_bounds
from src.ingest.common import (IngestError, SourceResult, check, failed_checks, latest_success,
                               mark_success, new_run_dir)
from src.utils import http

SOURCE = "incidents"
log = logging.getLogger("ingest.incidents")


def ingest_incidents(month, cfg, raw_root, run_id, force=False, get=http.get):
    if not force:
        previous = latest_success(raw_root, SOURCE, month)
        if previous:
            run_dir, meta = previous
            log.info("reusing pull from run %s (%d rows); use --force to re-pull", run_dir.name, meta["rows"])
            return SourceResult(SOURCE, "reused", meta["rows"], str(run_dir), meta["checks"],
                                f"reused run {run_dir.name}")

    src = cfg["sources"]["incidents"]
    start, end = month_bounds(month)
    where = f"occurred_on between '{start}T00:00:00' and '{end}T23:59:59'"

    expected = int(get(src["api_url"], {"$select": "count(*)", "$where": where},
                       http_cfg=cfg["http"]).json()[0]["count"])
    log.info("API reports %d incidents for %s", expected, month)

    run_dir = new_run_dir(raw_root, SOURCE, month, run_id)
    rows, offset, page_no = [], 0, 0
    while True:
        page = get(src["api_url"], {"$where": where, "$order": "busbreakdown_id",
                                    "$limit": src["page_size"], "$offset": offset},
                   http_cfg=cfg["http"]).json()
        (run_dir / f"page_{page_no:03d}.json").write_text(json.dumps(page), encoding="utf-8")
        rows.extend(page)
        page_no += 1
        log.info("page %d: %d rows (total %d)", page_no, len(page), len(rows))
        if len(page) < src["page_size"]:
            break
        offset += src["page_size"]

    checks = completeness_checks(rows, expected, start, end)
    failures = failed_checks(checks)
    if failures:
        details = "; ".join(f"{name}: {checks[name]['detail']}" for name in failures)
        raise IngestError(f"incidents pull for {month} is incomplete ({details}). Raw pages kept in {run_dir}")

    meta = {"source": SOURCE, "month": month, "run_id": run_id, "rows": len(rows),
            "expected": expected, "pages": page_no, "checks": checks}
    mark_success(run_dir, meta)
    log.info("completeness checks passed: %d/%d rows, %d page(s)", len(rows), expected, page_no)
    return SourceResult(SOURCE, "ok", len(rows), str(run_dir), checks)


def completeness_checks(rows, expected, start, end):
    ids = [r.get("busbreakdown_id") for r in rows]
    days = [str(r.get("occurred_on", ""))[:10] for r in rows]
    outside = sum(1 for d in days if not (start.isoformat() <= d <= end.isoformat()))
    return {
        "row_count_matches": check(len(rows) == expected, f"received {len(rows)}, expected {expected}"),
        "no_duplicate_ids": check(len(ids) == len(set(ids)), f"{len(ids) - len(set(ids))} duplicate ids"),
        "all_inside_month": check(outside == 0, f"{outside} rows outside {start}..{end}"),
        "not_empty": check(len(rows) > 0, f"{len(rows)} rows"),
    }


def load_incidents(run_dir):
    """All pages of one successful pull as a DataFrame of strings (typing happens in validation)."""
    pages = sorted(Path(run_dir).glob("page_*.json"))
    rows = [row for p in pages for row in json.loads(p.read_text(encoding="utf-8"))]
    return pd.DataFrame(rows, dtype="string")
