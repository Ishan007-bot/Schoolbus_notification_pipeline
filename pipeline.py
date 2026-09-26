"""Single entrypoint: ingest -> validate -> model -> metrics, for one month or a range of months.

Usage:
    python pipeline.py --month 2025-10
    python pipeline.py --month 2025-10 --force           # re-download even if a good pull exists
    python pipeline.py --range 2024-09 2026-06           # backfill; a failed month doesn't stop the others

Every month gets a run manifest (logs/manifests/, and data/output/<month>/ when outputs were produced).
Exit codes: 0 = every month succeeded or ran degraded, 1 = at least one month halted.
"""
import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.config import REPO_ROOT, is_summer, load_config, month_range, parse_month
from src.ingest.common import IngestError
from src.ingest.stage import run_ingest
from src.metrics import run_metrics
from src.model import ModelError, run_model
from src.summary import build_summary
from src.utils.http import HTTPFailure
from src.utils.logging_setup import setup_logging
from src.utils.manifest import new_manifest, write_manifest
from src.validate.stage import ValidationHalt, run_validate

log = logging.getLogger("pipeline")

EXPECTED_FAILURES = (IngestError, HTTPFailure, ValidationHalt, ModelError)
SUMMER_NOTE = ("summer service: vendors attributed by reported company name (school-year route contracts "
               "don't describe summer operators); M4 not computed")


def run_month(month, config, run_id, force=False, root=REPO_ROOT, now=None):
    """All stages for one month. Never raises: failures are recorded in the returned manifest."""
    paths = {name: Path(root) / p for name, p in config["paths"].items()}
    manifest = new_manifest(run_id, month, force)
    # scope notes explain how to read the month; unlike degraded notes they don't change the status
    manifest["scope_notes"] = [SUMMER_NOTE] if is_summer(month) else []
    stage = "ingest"
    log.info("===== %s (school year %s) =====", month, manifest["school_year"])
    try:
        status, results, notes = run_ingest(month, config, paths["raw"], run_id, force,
                                            today=now.date() if now else None)
        manifest["sources"] = {name: r.to_dict() for name, r in results.items()}
        manifest["notes"] = notes

        stage = "validate"
        validated = run_validate(month, results, config, paths["processed"], paths["output"], now=now)
        manifest["validation"] = validated

        stage = "model"
        modelled = run_model(month, results, validated["validated_path"], paths["warehouse"])
        manifest["model"] = modelled

        stage = "metrics"
        manifest["metrics"] = run_metrics(month, paths["warehouse"], config, paths["output"], status,
                                          notes + manifest["scope_notes"])
        manifest["status"] = status
    except EXPECTED_FAILURES as e:
        manifest.update(status="failed", halted_stage=stage, error=str(e))
        log.error("HALTED in %s: %s", stage, e)
    except Exception as e:                      # a bug, not a data problem: still record it, never crash silently
        manifest.update(status="failed", halted_stage=stage, error=f"unexpected {type(e).__name__}: {e}")
        log.exception("UNEXPECTED failure in %s", stage)

    written = write_manifest(manifest, paths["logs"], paths["output"], root)
    log.info("%s finished: %s | manifest: %s", month, manifest["status"].upper(), written[-1])
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="SilentDelay monthly pipeline")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--month", help="month to process, YYYY-MM")
    which.add_argument("--range", nargs=2, metavar=("FIRST", "LAST"), help="process every month FIRST..LAST")
    parser.add_argument("--force", action="store_true", help="re-download sources even if a good pull exists")
    args = parser.parse_args(argv)

    try:
        months = month_range(*args.range) if args.range else [args.month]
        for m in months:
            parse_month(m)
    except ValueError as e:
        parser.error(str(e))

    config = load_config()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = setup_logging(run_id, REPO_ROOT / config["paths"]["logs"])
    log.info("run %s | months %s..%s (%d) | force=%s", run_id, months[0], months[-1], len(months), args.force)

    manifests = [run_month(m, config, run_id, args.force) for m in months]

    if len(manifests) > 1:
        log.info("%-8s %-9s %-9s %9s %7s %6s", "month", "status", "halted", "incidents", "M1", "audit")
        for m in manifests:
            ok = m["status"] != "failed"
            log.info("%-8s %-9s %-9s %9s %7s %6s", m["period"], m["status"], m["halted_stage"] or "-",
                     m["validation"].get("rows", "-"),
                     f"{m['metrics']['headline']['m1']:.1%}" if ok else "-",
                     len(m["metrics"]["audit_list"]) if ok else "-")
        build_summary(months, REPO_ROOT / config["paths"]["output"])
    failed = [m["period"] for m in manifests if m["status"] == "failed"]
    log.info("done: %d month(s), %d failed%s | log: %s", len(manifests), len(failed),
             f" ({', '.join(failed)})" if failed else "", log_path)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
