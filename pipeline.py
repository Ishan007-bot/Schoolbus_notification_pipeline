"""Single entrypoint: ingest -> validate -> model -> metrics -> output.

Usage:
    python pipeline.py --month 2025-10
    python pipeline.py --month 2025-10 --force     # re-download even if a good pull exists

Exit codes: 0 = success or degraded, 1 = halted (critical source failed/incomplete, too many
rows failing critical validation rules, or model integrity checks failed).
"""
import argparse
import logging
import sys
from datetime import datetime, timezone

from src.config import REPO_ROOT, load_config, month_bounds, school_year_for_month
from src.ingest.common import IngestError
from src.ingest.stage import run_ingest
from src.metrics import run_metrics
from src.model import ModelError, run_model
from src.utils.http import HTTPFailure
from src.utils.logging_setup import setup_logging
from src.validate.stage import ValidationHalt, run_validate

log = logging.getLogger("pipeline")


def main(argv=None):
    parser = argparse.ArgumentParser(description="SilentDelay monthly pipeline")
    parser.add_argument("--month", required=True, help="month to process, YYYY-MM")
    parser.add_argument("--force", action="store_true", help="re-download sources even if a good pull exists")
    args = parser.parse_args(argv)

    try:
        start, end = month_bounds(args.month)
    except ValueError as e:
        parser.error(str(e))

    config = load_config()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = setup_logging(run_id, REPO_ROOT / config["paths"]["logs"])
    log.info("run %s | period %s (%s to %s) | school year %s | force=%s",
             run_id, args.month, start, end, school_year_for_month(args.month), args.force)

    try:
        status, results, notes = run_ingest(args.month, config, REPO_ROOT / config["paths"]["raw"], run_id, args.force)
    except (IngestError, HTTPFailure) as e:
        log.error("HALTED in ingest: %s", e)
        log.info("log: %s", log_path)
        return 1

    log.info("%-10s %-13s %8s", "source", "status", "rows")
    for name, r in results.items():
        log.info("%-10s %-13s %8d", name, r.status, r.rows)
    log.info("ingest finished: %s", status.upper())

    try:
        validated = run_validate(args.month, results, config, REPO_ROOT / config["paths"]["processed"],
                                 REPO_ROOT / config["paths"]["output"])
    except ValidationHalt as e:
        log.error("HALTED in validate: %s", e)
        log.info("log: %s", log_path)
        return 1

    warehouse = REPO_ROOT / config["paths"]["warehouse"]
    try:
        run_model(args.month, results, validated["validated_path"], warehouse)
    except ModelError as e:
        log.error("HALTED in model (load rolled back): %s", e)
        log.info("log: %s", log_path)
        return 1

    metrics = run_metrics(args.month, warehouse, config, REPO_ROOT / config["paths"]["output"], status, notes)
    log.info("finished: %s | scorecard: %s | log: %s", status.upper(), metrics["paths"]["scorecard_md"], log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
