"""Single entrypoint: ingest -> validate -> model -> metrics -> output.

Usage:
    python pipeline.py --month 2025-09

Only argument parsing exists so far; each stage is added in later phases.
"""
import argparse
import sys

from src.config import load_config, month_bounds, school_year_for_month


def main(argv=None):
    parser = argparse.ArgumentParser(description="SilentDelay monthly pipeline")
    parser.add_argument("--month", required=True, help="month to process, YYYY-MM")
    parser.add_argument("--force", action="store_true", help="re-pull raw data even if already downloaded")
    args = parser.parse_args(argv)

    try:
        start, end = month_bounds(args.month)
    except ValueError as e:
        parser.error(str(e))

    config = load_config()
    print(f"period       : {args.month} ({start} to {end})")
    print(f"school year  : {school_year_for_month(args.month)}")
    print(f"sources      : {', '.join(config['sources'])}")
    print("stages       : not implemented yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
